import httpx
import librosa
import soundfile as sf
import os
import tempfile
import numpy as np
import asyncio
import pyrubberband as pyrb
from concurrent.futures import ThreadPoolExecutor


executor = ThreadPoolExecutor(max_workers=4)


def applicate_lite_fade_in_fade_out(audio, sr):
    fade_ms = 5
    fade_samples = int(sr * (fade_ms / 1000.0))

    is_stereo = audio.ndim == 2 and audio.shape[0] == 2

    if is_stereo:
        num_samples = audio.shape[1]

        if num_samples > 2 * fade_samples:
            fade_out_ramp = np.linspace(1.0, 0.0, fade_samples)
            fade_in_ramp = np.linspace(0.0, 1.0, fade_samples)

            for channel in range(2):
                end_part = audio[channel, -fade_samples:]
                start_part = audio[channel, :fade_samples]

                audio[channel, :fade_samples] = (
                    start_part * fade_in_ramp + end_part * fade_out_ramp
                )

                audio[channel, -fade_samples:] = end_part * fade_out_ramp
        else:
            print(f"ℹ️  Audio too short for {fade_ms}ms crossfade (stereo).")

    else:
        num_samples = len(audio)

        if num_samples > 2 * fade_samples:
            end_part = audio[-fade_samples:]
            start_part = audio[:fade_samples]

            fade_out_ramp = np.linspace(1.0, 0.0, fade_samples)
            fade_in_ramp = np.linspace(0.0, 1.0, fade_samples)

            audio[:fade_samples] = start_part * fade_in_ramp + end_part * fade_out_ramp
            audio[-fade_samples:] = end_part * fade_out_ramp
        else:
            print(f"ℹ️  Audio too short for {fade_ms}ms crossfade (mono).")

    return audio


def stretch_audio_to_bpm(
    audio: np.ndarray,
    sr: int,
    detected_bpm: float,
    target_bpm: float,
    threshold: float = 0.01,
) -> np.ndarray:
    if detected_bpm <= 0 or target_bpm <= 0:
        print("⚠️ Invalid BPM, no stretch")
        return audio

    stretch_ratio = detected_bpm / target_bpm

    if abs(stretch_ratio - 1.0) <= threshold:
        print(f"✅ BPM already correct ({detected_bpm:.1f} → {target_bpm}), no stretch")
        return audio

    print(
        f"🔧 Time-stretch: {detected_bpm:.1f} → {target_bpm} BPM (ratio {stretch_ratio:.4f})"
    )

    try:
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
        tempo, _ = await loop.run_in_executor(
            executor, lambda: librosa.beat.beat_track(y=audio_mono, sr=sr)
        )
        detected = float(tempo)
        print(f"🎯 BPM detected: {detected:.2f}")
        return detected
    except Exception as e:
        print(f"⚠️ BPM detection failed: {e}")
        return None


def audio_to_wav_bytes(audio: np.ndarray, sr: int) -> tuple[bytes, float]:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    try:
        if audio.ndim == 2:
            sf.write(tmp.name, audio.T, sr)
            duration = audio.shape[1] / sr
        else:
            sf.write(tmp.name, audio, sr)
            duration = len(audio) / sr
        tmp.close()
        with open(tmp.name, "rb") as f:
            return f.read(), duration
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


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
