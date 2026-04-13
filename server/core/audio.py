import httpx
import librosa
import soundfile as sf
import os
import io
import tempfile
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
        print("⚠️ Invalid BPM, no stretch")
        return audio

    while detected_bpm > 150:
        detected_bpm /= 2.0
    while detected_bpm < 70:
        detected_bpm *= 2.0

    while target_bpm > 150:
        target_bpm /= 2.0
    while target_bpm < 70:
        target_bpm *= 2.0

    bpm_difference = abs(detected_bpm - target_bpm)

    if bpm_difference <= max_bpm_diff:
        print(
            f"✅ BPM in groove ({detected_bpm:.1f} vs {target_bpm}), no stretch needed (diff: {bpm_difference:.2f})"
        )
        return audio

    stretch_ratio = detected_bpm / target_bpm

    print(
        f"🔧 Time-stretch (Rubberband Rhythm): {detected_bpm:.1f} → {target_bpm} BPM (ratio {stretch_ratio:.4f})"
    )

    try:
        rb_settings = pyrb.RubberbandOption.RUBBERBAND_OPTION_PROCESS_TRANSIENTS_STRETCH

        if audio.ndim == 2:
            left = pyrb.time_stretch(audio[0], sr, stretch_ratio, rb_settings)
            right = pyrb.time_stretch(audio[1], sr, stretch_ratio, rb_settings)
            return np.array([left, right])
        else:
            return pyrb.time_stretch(audio, sr, stretch_ratio, rb_settings)

    except AttributeError:
        print("⚠️ Pyrb old version detected, stretching without rhythm optimization...")
        if audio.ndim == 2:
            left = pyrb.time_stretch(audio[0], sr, stretch_ratio)
            right = pyrb.time_stretch(audio[1], sr, stretch_ratio)
            return np.array([left, right])
        else:
            return pyrb.time_stretch(audio, sr, stretch_ratio)

    except Exception as e:
        print(f"⚠️ Time-stretch failed, audio unchanged: {e}")
        return audio


async def load_and_resample(
    audio_data: bytes, target_sr: int
) -> tuple[np.ndarray, int]:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    try:
        tmp.write(audio_data)
        tmp.close()
        audio, sr_original = librosa.load(tmp.name, sr=None, mono=False)
        print(f"📊 Sample rate original: {sr_original}Hz, target: {target_sr}Hz")
        print(f"🎵 Shape: {audio.shape} ({'stereo' if audio.ndim == 2 else 'mono'})")

        if sr_original != target_sr:
            print(f"🔄 Resampling {sr_original}Hz → {target_sr}Hz...")
            if audio.ndim == 2:
                audio = np.array(
                    [
                        librosa.resample(
                            audio[0], orig_sr=sr_original, target_sr=target_sr
                        ),
                        librosa.resample(
                            audio[1], orig_sr=sr_original, target_sr=target_sr
                        ),
                    ]
                )
            else:
                audio = librosa.resample(
                    audio, orig_sr=sr_original, target_sr=target_sr
                )
        else:
            print(f"✅ No resampling required")

        return audio, target_sr
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


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
