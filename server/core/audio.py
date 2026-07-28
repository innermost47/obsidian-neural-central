import httpx
import librosa
import soundfile as sf
import io
import subprocess
import tempfile
import os
from typing import Optional
import numpy as np
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)


async def fetch_audio_bytes(result: dict) -> bytes:
    raw_content = None
    if "wav_bytes" in result:
        raw_content = result["wav_bytes"]
    else:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(result["audio_url"])
            response.raise_for_status()
            raw_content = response.content

    try:
        audio_data, sr = librosa.load(io.BytesIO(raw_content), sr=None, mono=False)

        mono_for_trim = (
            librosa.to_mono(audio_data) if audio_data.ndim == 2 else audio_data
        )
        non_silent = librosa.effects.split(
            mono_for_trim, top_db=60, frame_length=2048, hop_length=512
        )

        if len(non_silent) > 0:
            start_sample = non_silent[0][0]
            preroll = int(0.01 * sr)
            start_sample = max(0, start_sample - preroll)

            if audio_data.ndim == 2:
                trimmed_audio = audio_data[:, start_sample:]
            else:
                trimmed_audio = audio_data[start_sample:]

            removed_ms = (start_sample / sr) * 1000
            if removed_ms > 5:
                print(f"✂️ Trimmed {removed_ms:.1f}ms of leading silence")
        else:
            trimmed_audio = audio_data

        buffer = io.BytesIO()
        sf.write(
            buffer,
            trimmed_audio.T if trimmed_audio.ndim > 1 else trimmed_audio,
            sr,
            format="WAV",
        )
        return buffer.getvalue()

    except Exception as e:
        print(f"⚠️ Error while trimming silence: {e}")
        return raw_content


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


def build_response_headers(
    duration: float,
    request_bpm: int,
    snapped_bpm: float | None,
    key: str | None,
    remaining_after: int,
    credits_needed: int,
    target_sr: int,
    provider_name: str,
    used_fallback: bool,
) -> dict:
    response_headers = {
        "X-Duration": str(duration),
        "X-BPM": str(request_bpm),
        "X-Detected-BPM": "",
        "X-Key": sanitize_header(str(key or "")),
        "X-Credits-Remaining": str(remaining_after),
        "X-Credits-Used": str(credits_needed),
        "X-Sample-Rate": str(target_sr),
        "X-Provider": sanitize_header(provider_name),
        "X-Used-Fallback": str(used_fallback),
    }
    if snapped_bpm:
        response_headers["X-Snapped-BPM"] = str(snapped_bpm)
    return response_headers
