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


def _try_correct_to_target(detected: float, target: float, tolerance: float = 0.15):
    if detected <= 0 or target <= 0:
        return None
    candidates = [
        detected,
        detected * 2,
        detected / 2,
        detected * 4 / 3,
        detected * 3 / 4,
        detected * 3 / 2,
        detected * 2 / 3,
    ]
    best = min(candidates, key=lambda c: abs(c - target) / target)
    if abs(best - target) / target <= tolerance:
        return best
    return None


async def detect_bpm(
    audio: np.ndarray, sr: int, expected_bpm: Optional[float] = None
) -> float | None:
    try:
        import essentia.standard as es
        loop = asyncio.get_event_loop()
        audio_mono = librosa.to_mono(audio) if audio.ndim == 2 else audio

        def process():
            audio_float = audio_mono.astype(np.float32)

            essentia_bpm = 0.0
            essentia_conf = 0.0
            try:
                rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
                result = rhythm_extractor(audio_float)
                essentia_bpm = float(result[0])
                essentia_conf = float(result[2]) if len(result) > 2 else 0.0
            except Exception as e:
                print(f"Essentia failed: {e}")

            librosa_bpm = None
            try:
                tempo, _ = librosa.beat.beat_track(y=audio_float, sr=sr)
                if hasattr(tempo, "__len__"):
                    librosa_bpm = float(tempo[0]) if len(tempo) > 0 else None
                else:
                    librosa_bpm = float(tempo) if tempo else None
                print(f"Librosa raw: {librosa_bpm}")
            except Exception as e:
                print(f"Librosa failed: {e}")

            return essentia_bpm, essentia_conf, librosa_bpm

        essentia_bpm, confidence, librosa_bpm = await loop.run_in_executor(
            executor, process
        )

        essentia_to_target = (
            _try_correct_to_target(essentia_bpm, expected_bpm)
            if expected_bpm and expected_bpm > 0
            else None
        )
        librosa_to_target = (
            _try_correct_to_target(librosa_bpm, expected_bpm)
            if librosa_bpm and expected_bpm and expected_bpm > 0
            else None
        )

        if (
            essentia_to_target is not None
            and librosa_to_target is not None
            and abs(essentia_to_target - librosa_to_target) / max(essentia_to_target, 1)
            < 0.05
        ):
            consensus = (essentia_to_target + librosa_to_target) / 2
            print(f"Consensus: {consensus:.2f}")
            return consensus

        if essentia_to_target is not None and librosa_to_target is None:
            print(f"Only Essentia matches target: {essentia_to_target:.2f}")
            return essentia_to_target

        if librosa_to_target is not None and essentia_to_target is None:
            print(f"Only Librosa matches target: {librosa_to_target:.2f}")
            return librosa_to_target

        if essentia_to_target is not None and librosa_to_target is not None:
            err_ess = abs(essentia_to_target - expected_bpm)
            err_lib = abs(librosa_to_target - expected_bpm)
            if err_ess <= err_lib:
                print(
                    f"Conflict, Essentia is closer to target: {essentia_to_target:.2f}"
                )
                return essentia_to_target
            else:
                print(f"Conflict, Librosa is closer to target: {librosa_to_target:.2f}")
                return librosa_to_target

        if essentia_bpm > 0 and librosa_bpm and librosa_bpm > 0:

            def normalize_bpm(bpm):
                while bpm >= 200:
                    bpm /= 2
                while bpm < 60:
                    bpm *= 2
                return bpm

            norm_ess = normalize_bpm(essentia_bpm)
            norm_lib = normalize_bpm(librosa_bpm)

            if abs(norm_ess - norm_lib) / max(norm_ess, 1) < 0.1:
                final = (norm_ess + norm_lib) / 2
                print(f"No target, but zone agreement: {final:.2f}")
                return final

        print(f"No reliable BPM: essentia={essentia_bpm:.2f}, librosa={librosa_bpm}")
        return None

    except Exception as e:
        print(f"BPM detection failed: {e}")
        return None


def detect_percussive_content(audio: np.ndarray, sr: int) -> bool:
    audio_mono = librosa.to_mono(audio) if audio.ndim == 2 else audio
    audio_float = audio_mono.astype(np.float32)
    duration = len(audio_float) / sr

    try:
        onsets = librosa.onset.onset_detect(
            y=audio_float, sr=sr, units="time", backtrack=False
        )
        onset_density = len(onsets) / duration if duration > 0 else 0
        onset_says_perc = onset_density > 2.0
    except Exception:
        onset_density = 0
        onset_says_perc = False

    try:
        harmonic, percussive = librosa.effects.hpss(audio_float)
        energy_h = float(np.sum(harmonic**2))
        energy_p = float(np.sum(percussive**2))
        total = energy_h + energy_p
        perc_ratio = energy_p / total if total > 0 else 0
        hpss_says_perc = perc_ratio > 0.5
    except Exception:
        perc_ratio = 0
        hpss_says_perc = False

    is_perc = onset_says_perc or hpss_says_perc

    print(
        f"🥁 Content analysis: onset_density={onset_density:.2f}/s, "
        f"hpss_perc_ratio={perc_ratio:.2f} → "
        f"{'PERCUSSIVE' if is_perc else 'MELODIC'}"
    )
    return is_perc


def stretch_audio_to_bpm(
    audio: np.ndarray,
    sr: int,
    detected_bpm: Optional[float],
    target_bpm: Optional[float],
    max_bpm_diff: float = 0.5,
    max_stretch_for_percussive: float = 0.20,
    max_stretch_for_melodic: float = 0.15,
    force_stretch: bool = False,
) -> np.ndarray:
    if detected_bpm is None or target_bpm is None:
        print("⚠️ Skipping stretch: BPM is None")
        return audio

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
        print(f"✅ BPM in groove ({detected_bpm:.2f} vs {target_bpm:.2f})")
        return audio

    ratio = target_bpm / detected_bpm
    deviation = abs(ratio - 1.0)

    if not force_stretch and (ratio < 0.5 or ratio > 2.0):
        print(
            f"⚠️ Stretch ratio extreme ({ratio:.3f}), skipping "
            f"(BPM detection probably wrong)"
        )
        return audio

    is_percussive = detect_percussive_content(audio, sr)

    if not force_stretch:
        if is_percussive and deviation > max_stretch_for_percussive:
            print(
                f"⚠️ Stretch ratio {ratio:.4f} ({deviation*100:.1f}%) too aggressive "
                f"for percussive content. Keeping audio at {detected_bpm:.1f} BPM "
                f"(target was {target_bpm:.1f})."
            )
            return audio

        if not is_percussive and deviation > max_stretch_for_melodic:
            print(
                f"⚠️ Stretch ratio {ratio:.4f} ({deviation*100:.1f}%) too aggressive "
                f"for melodic content. Keeping audio at {detected_bpm:.1f} BPM "
                f"(target was {target_bpm:.1f})."
            )
            return audio

    forced_tag = " [FORCED]" if force_stretch else ""
    print(
        f"🔧 Time-stretch{forced_tag} ({'R2+crisp6' if is_percussive else 'R3'}): "
        f"{detected_bpm:.2f} → {target_bpm:.2f} BPM (ratio={ratio:.4f})"
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

        if is_percussive:
            cmd = [
                "rubberband",
                "-T",
                f"{detected_bpm:.4f}:{target_bpm:.4f}",
                "--detector-perc",
                "--bl-transients",
                "--crisp",
                "5",
                "-q",
                in_path,
                out_path,
            ]
        else:
            cmd = [
                "rubberband-r3",
                "-T",
                f"{detected_bpm:.4f}:{target_bpm:.4f}",
                "-q",
                in_path,
                out_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0 and (
            "not found" in str(result.stderr).lower()
            or "no such" in str(result.stderr).lower()
        ):
            fallback_engine = "rubberband-r3" if is_percussive else "rubberband"
            print(f"⚠️ Engine not found, fallback to {fallback_engine}...")
            cmd[0] = fallback_engine
            if fallback_engine == "rubberband-r3":
                cmd = [c for c in cmd if c not in ("--crisp", "6")]
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"Rubberband CLI error: {result.stderr}")

        stretched_audio, _ = librosa.load(out_path, sr=sr, mono=False)
        if stretched_audio.ndim == 1:
            stretched_audio = np.array([stretched_audio, stretched_audio])

        return stretched_audio

    except Exception as e:
        print(f"⚠️ Rubberband failed: {e}, falling back to librosa")
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
    snapped_bpm: float | None,
    detected_bpm: float | None,
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
        "X-Detected-BPM": str(detected_bpm) if detected_bpm else "",
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
