import httpx
import librosa
import soundfile as sf
import io
import numpy as np
import asyncio
import pyrubberband as pyrb
from concurrent.futures import ThreadPoolExecutor
import essentia.standard as es

executor = ThreadPoolExecutor(max_workers=4)


def applicate_lite_fade_in_fade_out(audio: np.ndarray, sr: int) -> np.ndarray:
    fade_ms = 15
    fade_samples = int(sr * (fade_ms / 1000.0))

    is_stereo = audio.ndim == 2 and audio.shape[0] == 2
    num_samples = audio.shape[1] if is_stereo else len(audio)

    if num_samples <= 2 * fade_samples:
        fade_samples = num_samples // 2

    if fade_samples == 0:
        return audio

    fade_in_ramp = np.linspace(0.0, 1.0, fade_samples, dtype=audio.dtype)
    fade_out_ramp = np.linspace(1.0, 0.0, fade_samples, dtype=audio.dtype)

    if is_stereo:
        audio[:, :fade_samples] *= fade_in_ramp
        audio[:, -fade_samples:] *= fade_out_ramp
    else:
        audio[:fade_samples] *= fade_in_ramp
        audio[-fade_samples:] *= fade_out_ramp

    return audio


def stretch_audio_to_bpm(
    audio: np.ndarray,
    sr: int,
    detected_bpm: float,
    target_bpm: float,
    max_bpm_diff: float = 0.5,
) -> np.ndarray:
    if detected_bpm <= 0 or target_bpm <= 0:
        return audio

    while detected_bpm >= 200:
        detected_bpm /= 2.0
    while detected_bpm < 60:
        detected_bpm *= 2.0
    while target_bpm >= 200:
        target_bpm /= 2.0
    while target_bpm < 60:
        target_bpm *= 2.0

    if abs(detected_bpm - target_bpm) <= max_bpm_diff:
        print(f"✅ BPM in groove ({detected_bpm:.1f} vs {target_bpm})")
        return audio

    stretch_rate = target_bpm / detected_bpm
    print(f"🔧 Time-stretch (Rubberband): {detected_bpm:.1f} → {target_bpm} BPM")

    try:
        rb_settings = [pyrb.RUBBERBAND_OPTION_PROCESS_TRANSIENTS_STRETCH]

        if audio.ndim == 2 and audio.shape[0] == 2:
            left = pyrb.time_stretch(audio[0], sr, stretch_rate, rb_settings)
            right = pyrb.time_stretch(audio[1], sr, stretch_rate, rb_settings)
            return np.array([left, right])
        else:
            return pyrb.time_stretch(audio, sr, stretch_rate, rb_settings)

    except Exception as e:
        print(f"⚠️ Pyrb failed: {e}, using Librosa.")
        if audio.ndim == 2 and audio.shape[0] == 2:
            left = librosa.effects.time_stretch(audio[0], rate=stretch_rate)
            right = librosa.effects.time_stretch(audio[1], rate=stretch_rate)
            return np.array([left, right])
        return librosa.effects.time_stretch(audio, rate=stretch_rate)


async def load_audio_original(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    def _load():
        buffer = io.BytesIO(audio_bytes)
        audio, sr = librosa.load(buffer, sr=None, mono=False)
        return audio, sr

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _load)


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio

    print(f"🔄 Final resampling {orig_sr}Hz → {target_sr}Hz...")
    if audio.ndim == 2:
        return np.array(
            [
                librosa.resample(audio[0], orig_sr=orig_sr, target_sr=target_sr),
                librosa.resample(audio[1], orig_sr=orig_sr, target_sr=target_sr),
            ]
        )
    else:
        return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)


async def detect_bpm(audio: np.ndarray, sr: int) -> float | None:
    try:
        loop = asyncio.get_event_loop()
        audio_mono = librosa.to_mono(audio) if audio.ndim == 2 else audio

        def process():
            audio_float = audio_mono.astype(np.float32)
            rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
            bpm = rhythm_extractor(audio_float)[0]
            return float(bpm)

        detected = await loop.run_in_executor(executor, process)
        print(f"🎯 BPM detected (Essentia): {detected:.2f}")
        return detected
    except Exception as e:
        print(f"⚠️ Essentia BPM failed: {e}")
        return None


def audio_to_wav_bytes(audio: np.ndarray, sr: int) -> tuple[bytes, float]:
    buffer = io.BytesIO()

    try:
        if audio.ndim == 2:
            sf.write(buffer, audio.T, sr, format="WAV")
            duration = audio.shape[1] / sr
        else:
            sf.write(buffer, audio, sr, format="WAV")
            duration = len(audio) / sr

        wav_bytes = buffer.getvalue()
        return wav_bytes, duration

    finally:
        buffer.close()


def sanitize_header(value: str) -> str:
    return value.encode("latin-1", errors="replace").decode("latin-1")


async def fetch_audio_bytes(result: dict) -> bytes:
    if "wav_bytes" in result:
        return result["wav_bytes"]
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(result["audio_url"])
        response.raise_for_status()
        return response.content


def build_response_headers(
    duration: float,
    request_bpm: int,
    detected_bpm: float | None,
    key: str | None,
    remaining_after: int,
    credits_needed: int,
    target_sr: int,
    provider_name: str,
    used_fallback: bool,
) -> dict:
    return {
        "X-Duration": str(duration),
        "X-BPM": str(request_bpm),
        "X-Detected-BPM": str(detected_bpm) if detected_bpm else "",
        "X-Key": sanitize_header(str(key or "")),
        "X-Credits-Remaining": str(remaining_after),
        "X-Credits-Used": str(credits_needed),
        "X-Sample-Rate": str(target_sr),
        "X-Provider": sanitize_header(provider_name),
        "X-Used-Fallback": str(used_fallback),
    }
