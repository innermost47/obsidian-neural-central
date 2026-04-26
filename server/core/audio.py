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
import essentia.standard as es

executor = ThreadPoolExecutor(max_workers=4)


async def detect_bpm(
    audio: np.ndarray, sr: int, expected_bpm: Optional[float] = None
) -> float | None:
    try:
        loop = asyncio.get_event_loop()
        audio_mono = librosa.to_mono(audio) if audio.ndim == 2 else audio

        def process():
            audio_float = audio_mono.astype(np.float32)
            rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
            result = rhythm_extractor(audio_float)
            bpm = float(result[0])
            confidence = float(result[2]) if len(result) > 2 else 0.0
            return bpm, confidence

        detected, confidence = await loop.run_in_executor(executor, process)
        raw_detected = detected

        if confidence < 1.5:
            if expected_bpm and expected_bpm > 0 and detected > 0:
                while detected > (expected_bpm * 1.5):
                    detected /= 2.0
                while detected < (expected_bpm * 0.67):
                    detected *= 2.0

                ratio_to_target = detected / expected_bpm
                if 0.85 <= ratio_to_target <= 1.15:
                    print(
                        f"🎯 Low confidence ({confidence:.2f}) but octave-corrected "
                        f"to plausible value: raw={raw_detected:.2f} → {detected:.2f} "
                        f"(target={expected_bpm})"
                    )
                    return detected

            print(
                f"⚠️ Low confidence BPM detection ({confidence:.2f}), "
                f"detected={raw_detected:.1f} — skipping stretch"
            )
            return None

        if expected_bpm and expected_bpm > 0:
            while detected > (expected_bpm * 1.5):
                detected /= 2.0
            while detected < (expected_bpm * 0.67):
                detected *= 2.0
        else:
            while detected >= 200:
                detected /= 2.0
            while detected < 60:
                detected *= 2.0

        print(
            f"🎯 BPM detected (Essentia): raw={raw_detected:.2f} → "
            f"corrected={detected:.2f} (confidence={confidence:.2f})"
        )
        return detected

    except Exception as e:
        print(f"⚠️ Essentia BPM failed: {e}")
        return None


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
        print(f"✅ BPM in groove ({detected_bpm:.2f} vs {target_bpm})")
        return audio

    ratio = target_bpm / detected_bpm
    if ratio < 0.5 or ratio > 2.0:
        print(
            f"⚠️ Stretch ratio extreme ({ratio:.3f}), audio quality will suffer. "
            f"Skipping stretch — BPM detection probably wrong."
        )
        return audio

    print(
        f"🔧 Time-stretch (Rubberband R3): {detected_bpm:.2f} → {target_bpm:.2f} BPM "
        f"(ratio={ratio:.4f})"
    )

    in_path = None
    out_path = None

    try:
        if audio.ndim == 2 and audio.shape[0] == 2:
            audio_to_write = np.ascontiguousarray(audio.T)
        else:
            audio_to_write = audio

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_in:
            in_path = f_in.name
        out_path = in_path.replace(".wav", "_stretched.wav")

        sf.write(in_path, audio_to_write, sr, subtype="PCM_16")

        cmd = [
            "rubberband-r3",
            "-T",
            f"{detected_bpm:.4f}:{target_bpm:.4f}",
            "--fine",
            "-q",
            in_path,
            out_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0 and (
            "not found" in str(result.stderr).lower()
            or "no such" in str(result.stderr).lower()
        ):
            print("⚠️ R3 engine not found, fallback to R2...")
            cmd = [
                "rubberband",
                "-T",
                f"{detected_bpm:.4f}:{target_bpm:.4f}",
                "--crisp",
                "6",
                "-q",
                in_path,
                out_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"Rubberband CLI error: {result.stderr}")

        stretched_audio, _ = librosa.load(out_path, sr=sr, mono=False)
        if stretched_audio.ndim == 1:
            stretched_audio = np.array([stretched_audio, stretched_audio])

        return stretched_audio

    except Exception as e:
        print(
            f"⚠️ Rubberband failed: {e}, falling back to librosa "
            f"(audio will sound bad)"
        )
        time_ratio = target_bpm / detected_bpm
        if audio.ndim == 2 and audio.shape[0] == 2:
            left = librosa.effects.time_stretch(audio[0], rate=time_ratio)
            right = librosa.effects.time_stretch(audio[1], rate=time_ratio)
            return np.array([left, right])
        return librosa.effects.time_stretch(audio, rate=time_ratio)

    finally:
        if in_path and os.path.exists(in_path):
            os.remove(in_path)
        if out_path and os.path.exists(out_path):
            os.remove(out_path)


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
