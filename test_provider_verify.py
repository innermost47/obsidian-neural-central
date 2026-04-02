import argparse
import asyncio
import io
import os
import random
import sys
import time
from pathlib import Path
import hashlib
import httpx
import librosa
import numpy as np
from scipy.spatial.distance import cosine
from server.config import settings

DEFAULT_RUNS = 3
DEFAULT_DURATION = 5
DEFAULT_PROMPT = "steady kick drum loop 120bpm"
DEFAULT_TIMEOUT = 120.0
DIVERGENCE_DURATION = 12
SIMILARITY_THRESHOLD = 0.85

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(msg):
    print(f"{GREEN}  ✅ {msg}{RESET}")


def fail(msg):
    print(f"{RED}  ❌ {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}  ⚠️  {msg}{RESET}")


def info(msg):
    print(f"{CYAN}  →  {msg}{RESET}")


def dim(msg):
    print(f"{DIM}     {msg}{RESET}")


def title(msg):
    print(f"\n{BOLD}{msg}{RESET}")


def sep():
    print(f"{DIM}{'─' * 55}{RESET}")


def mel_fingerprint(wav_bytes: bytes, duration: int) -> np.ndarray | None:
    try:
        buf = io.BytesIO(wav_bytes)
        y, sr = librosa.load(buf, sr=22050, mono=True, duration=float(duration))
        mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
        mel_db = librosa.power_to_db(mel, ref=np.max)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
        fingerprint = np.concatenate(
            [
                mel_db.mean(axis=1),
                mfcc.mean(axis=1),
                chroma.mean(axis=1),
                contrast.mean(axis=1),
            ]
        )
        return fingerprint

    except Exception as e:
        fail(f"Mel fingerprint error: {e}")
        return None


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return round(1.0 - float(cosine(a, b)), 4)


def save_wav(wav_bytes: bytes, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(wav_bytes)


async def test_health(client: httpx.AsyncClient, url: str) -> bool:
    title("① Health check")
    try:
        r = await client.get(
            f"{url}/health",
            timeout=5.0,
            headers={
                **settings.BROWSER_HEADERS,
                "X-API-Key": settings.SERVER_TO_PROVIDER_KEY,
            },
        )
        if r.status_code == 200:
            data = r.json()
            ok(f"Server is up — model loaded: {data.get('model_loaded')}")
            return True
        else:
            fail(f"HTTP {r.status_code}")
            return False
    except Exception as e:
        fail(f"Cannot reach provider: {e}")
        return False


async def test_status(client: httpx.AsyncClient, url: str) -> dict | None:
    title("② Status check")
    try:
        r = await client.get(
            f"{url}/status",
            timeout=5.0,
            headers={
                **settings.BROWSER_HEADERS,
                "X-API-Key": settings.SERVER_TO_PROVIDER_KEY,
            },
        )
        if r.status_code == 200:
            data = r.json()
            ok(f"Available: {data.get('available')}")
            info(f"Model   : {data.get('model_id', data.get('model', '—'))}")
            info(f"Device  : {data.get('device', '—')}")
            if data.get("vram_total_gb"):
                info(
                    f"VRAM    : {data.get('vram_used_gb', 0):.1f} / {data.get('vram_total_gb', 0):.1f} GB"
                )
            if not data.get("api_key"):
                warn("api_key missing from /status response — proof-of-work won't work")
            else:
                ok(f"api_key returned ({data['api_key'][:12]}...)")
            return data
        else:
            fail(f"HTTP {r.status_code}")
            return None
    except Exception as e:
        fail(f"Status error: {e}")
        return None


async def test_single_generate(
    client: httpx.AsyncClient,
    url: str,
    prompt: str,
    duration: int,
    save_dir: str | None = None,
) -> bytes | None:
    title("③ Single generation test (random seed)")
    seed = random.randint(0, 2**31 - 1)
    info(f"Prompt : {prompt}")
    info(f"Seed   : {seed}")
    info(f"Duration: {duration}s")
    try:
        t0 = time.time()
        r = await client.post(
            f"{url}/generate",
            json={"prompt": prompt, "duration": duration},
            headers={
                **settings.BROWSER_HEADERS,
                "X-API-Key": settings.SERVER_TO_PROVIDER_KEY,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            wav = r.content
            ok(f"Generated in {elapsed:.1f}s — {len(wav)/1024:.1f} KB")
            if save_dir:
                path = f"{save_dir}/test_single.wav"
                save_wav(wav, path)
                dim(f"Saved → {path}")
            pkey = r.headers.get("X-Provider-Key", "")
            if pkey:
                ok(f"X-Provider-Key present ({pkey[:12]}...)")
            else:
                warn("X-Provider-Key missing from response headers")
            return wav
        else:
            fail(f"HTTP {r.status_code} — {r.text[:200]}")
            return None
    except httpx.TimeoutException:
        fail(f"Timeout after {DEFAULT_TIMEOUT}s")
        return None
    except Exception as e:
        fail(f"Generation error: {e}")
        return None


async def test_verify_determinism(
    client: httpx.AsyncClient,
    url: str,
    prompt: str,
    seed: int,
    duration: int,
    runs: int,
    save_dir: str | None = None,
) -> bool:
    title(f"④ Proof-of-work determinism test ({runs} runs, same seed)")
    info(f"Prompt : {prompt}")
    info(f"Seed   : {seed}")
    info(f"Duration: {duration}s")
    sep()

    wavs = []
    fingerprints = []
    elapsed_times = []

    for i in range(runs):
        print(f"\n  Run {i+1}/{runs}")
        try:
            t0 = time.time()
            r = await client.post(
                f"{url}/verify",
                json={"prompt": prompt, "seed": seed, "duration": duration},
                timeout=DEFAULT_TIMEOUT,
                headers={
                    **settings.BROWSER_HEADERS,
                    "X-API-Key": settings.SERVER_TO_PROVIDER_KEY,
                },
            )
            elapsed = time.time() - t0
            elapsed_times.append(elapsed)

            if r.status_code == 200:
                wav = r.content
                ok(f"Generated in {elapsed:.1f}s — {len(wav)/1024:.1f} KB")

                returned_seed = r.headers.get("X-Seed", "")
                if returned_seed == str(seed):
                    dim(f"X-Seed matches: {returned_seed}")
                elif returned_seed:
                    warn(f"X-Seed mismatch: sent {seed}, got {returned_seed}")

                if save_dir:
                    path = f"{save_dir}/verify_run_{i+1}.wav"
                    save_wav(wav, path)
                    dim(f"Saved → {path}")

                fp = mel_fingerprint(wav, duration)
                if fp is not None:
                    fingerprints.append(fp)
                    wavs.append(wav)
                else:
                    fail("Could not compute mel fingerprint")
            else:
                fail(f"HTTP {r.status_code} — {r.text[:200]}")

        except httpx.TimeoutException:
            fail(f"Timeout after {DEFAULT_TIMEOUT}s")
        except Exception as e:
            fail(f"Error: {e}")

    title("⑤ Similarity analysis")
    sep()

    if len(fingerprints) < 2:
        fail(f"Not enough successful runs to compare ({len(fingerprints)}/{runs})")
        return False

    pairs = []
    for i in range(len(fingerprints)):
        for j in range(i + 1, len(fingerprints)):
            sim = cosine_sim(fingerprints[i], fingerprints[j])
            pairs.append((i + 1, j + 1, sim))
            label = "PASS ✅" if sim >= SIMILARITY_THRESHOLD else "FAIL ❌"
            print(f"  Run {i+1} vs Run {j+1} : {BOLD}{sim:.4f}{RESET} — {label}")

    avg_sim = sum(p[2] for p in pairs) / len(pairs)
    all_pass = all(p[2] >= SIMILARITY_THRESHOLD for p in pairs)

    sep()
    print(f"\n  Average similarity : {BOLD}{avg_sim:.4f}{RESET}")
    print(f"  Threshold          : {SIMILARITY_THRESHOLD}")
    print(f"  Avg generation time: {sum(elapsed_times)/len(elapsed_times):.1f}s")

    if all_pass:
        print(
            f"\n{GREEN}{BOLD}  ✅ PROOF-OF-WORK PASSED — Provider is deterministic{RESET}"
        )
        print(f"{DIM}  The provider produces consistent audio for the same seed.")
        print(f"  It will pass the server's verification rounds.{RESET}")
    else:
        failed = [(a, b, s) for a, b, s in pairs if s < SIMILARITY_THRESHOLD]
        print(
            f"\n{RED}{BOLD}  ❌ PROOF-OF-WORK FAILED — Provider is NOT deterministic{RESET}"
        )
        for a, b, s in failed:
            print(
                f"{RED}  Run {a} vs Run {b}: similarity {s:.4f} < {SIMILARITY_THRESHOLD}{RESET}"
            )
        print(f"\n{YELLOW}  Possible causes:")
        print(f"  - Model not loaded correctly")
        print(f"  - Seed not properly passed to the generator")
        print(f"  - Different model versions between runs")
        print(f"  - Non-deterministic sampling (check generate_with_seed()){RESET}")

    return all_pass


DIVERGENT_PAIRS = [
    (
        "steady kick drum loop 120bpm",
        "soft ambient wind texture",
    ),
    (
        "deep sub bass pulse 60hz",
        "high pitched glass harmonica tone",
    ),
    (
        "fast snare roll percussion",
        "slow cello sustained drone",
    ),
    (
        "clap hi-hat trap pattern 140bpm",
        "gentle piano chord Cmaj7",
    ),
    (
        "electric guitar distorted power chord",
        "rain on window white noise texture",
    ),
]

DIVERGENCE_THRESHOLD = 0.98


async def test_divergence(
    client: httpx.AsyncClient,
    url: str,
    duration: int,
    save_dir: str | None = None,
) -> bool:
    title("⑥ Divergence test — two radically different prompts")
    info("This verifies that the similarity metric actually works:")
    info("different prompts must produce DISSIMILAR audio.")
    sep()

    pair = random.choice(DIVERGENT_PAIRS)
    prompt_a, prompt_b = pair
    seed_a = random.randint(0, 2**31 - 1)
    seed_b = random.randint(0, 2**31 - 1)

    info(f"Prompt A : {prompt_a}")
    info(f"Prompt B : {prompt_b}")
    info(f"Seed A   : {seed_a}")
    info(f"Seed B   : {seed_b}")
    sep()

    wavs = {}
    fps = {}

    for label, prompt, seed in [("A", prompt_a, seed_a), ("B", prompt_b, seed_b)]:
        print(f"\n  Generating sound {label} — '{prompt}'")
        try:
            t0 = time.time()
            r = await client.post(
                f"{url}/verify",
                json={"prompt": prompt, "seed": seed, "duration": duration},
                timeout=DEFAULT_TIMEOUT,
                headers={"X-API-Key": settings.SERVER_TO_PROVIDER_KEY},
            )
            elapsed = time.time() - t0
            if r.status_code == 200:
                wav = r.content
                ok(f"Generated in {elapsed:.1f}s — {len(wav)/1024:.1f} KB")
                if save_dir:
                    path = f"{save_dir}/divergence_{label.lower()}.wav"
                    save_wav(wav, path)
                    dim(f"Saved → {path}")
                fp = mel_fingerprint(wav, duration)
                if fp is not None:
                    wavs[label] = wav
                    fps[label] = fp
                else:
                    fail(f"Could not compute fingerprint for sound {label}")
            else:
                fail(f"HTTP {r.status_code} for sound {label}")
        except Exception as e:
            fail(f"Error for sound {label}: {e}")

    if len(fps) < 2:
        fail("Could not generate both sounds — skipping divergence analysis")
        return False

    sim = cosine_sim(fps["A"], fps["B"])

    sep()
    print(f"\n  Similarity A vs B : {BOLD}{sim:.4f}{RESET}")
    print(f"  Divergence threshold : < {DIVERGENCE_THRESHOLD} expected")
    print()
    print(f"  Prompt A : {prompt_a}")
    print(f"  Prompt B : {prompt_b}")

    hash_a = hashlib.md5(wavs["A"]).hexdigest()
    hash_b = hashlib.md5(wavs["B"]).hexdigest()
    print(f"  MD5 A : {hash_a}")
    print(f"  MD5 B : {hash_b}")
    if hash_a == hash_b:
        fail(
            "WAVs are IDENTICAL — provider is returning the same file regardless of prompt"
        )

    passed = sim < DIVERGENCE_THRESHOLD

    if passed:
        print(
            f"\n{GREEN}{BOLD}  ✅ DIVERGENCE PASSED — different prompts produce different audio{RESET}"
        )
        dim(
            f"  Similarity {sim:.4f} is below threshold {DIVERGENCE_THRESHOLD} — the metric is working correctly."
        )
    else:
        print(
            f"\n{YELLOW}{BOLD}  ⚠️  DIVERGENCE BORDERLINE — similarity {sim:.4f} >= {DIVERGENCE_THRESHOLD}{RESET}"
        )
        print(f"{YELLOW}  The two prompts produced surprisingly similar audio.")
        print(f"  This may indicate the model is not generating meaningfully")
        print(f"  different content for different prompts.{RESET}")

    return passed


async def main():
    parser = argparse.ArgumentParser(
        description="OBSIDIAN Neural — Provider verification test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_provider_verify.py
  python test_provider_verify.py --url [URL]
  python test_provider_verify.py --runs 5 --prompt "deep bass drone" --seed 1337
  python test_provider_verify.py --save-wavs --output ./test_output
        """,
    )
    parser.add_argument("--url", help=f"Provider URL", required=True)
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of verify runs (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=f"Test prompt (default: '{DEFAULT_PROMPT}')",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Fixed seed (random if not set)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Audio duration in seconds (default: {DEFAULT_DURATION})",
    )
    parser.add_argument(
        "--save-wavs", action="store_true", help="Save generated WAVs to disk"
    )
    parser.add_argument(
        "--output",
        default="./test_output",
        help="Output directory for WAVs (default: ./test_output)",
    )
    parser.add_argument(
        "--skip-single", action="store_true", help="Skip single generation test"
    )
    parser.add_argument(
        "--skip-divergence", action="store_true", help="Skip divergence test"
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)
    save_dir = args.output if args.save_wavs else None
    url = args.url.rstrip("/")

    print(f"\n{BOLD}{'='*55}")
    print(f"  OBSIDIAN Neural — Provider Verification Test")
    print(f"{'='*55}{RESET}")
    print(f"  URL      : {url}")
    print(f"  Prompt   : {args.prompt}")
    print(f"  Seed     : {seed}")
    print(f"  Runs     : {args.runs}")
    print(f"  Duration : {args.duration}s")
    print(f"  Save WAVs: {'yes → ' + args.output if save_dir else 'no'}")
    print(f"  Skip div : {args.skip_divergence}")
    print(f"{DIM}{'='*55}{RESET}\n")

    results = {}

    async with httpx.AsyncClient() as client:

        results["health"] = await test_health(client, url)
        if not results["health"]:
            print(f"\n{RED}Provider unreachable — aborting.{RESET}\n")
            sys.exit(1)

        status_data = await test_status(client, url)
        results["status"] = status_data is not None

        if not args.skip_single:
            wav = await test_single_generate(
                client, url, args.prompt, args.duration, save_dir
            )
            results["single_generate"] = wav is not None
        else:
            results["single_generate"] = None

        results["determinism"] = await test_verify_determinism(
            client, url, args.prompt, seed, args.duration, args.runs, save_dir
        )

        if not args.skip_divergence:
            results["divergence"] = await test_divergence(
                client, url, DIVERGENCE_DURATION, save_dir
            )
        else:
            results["divergence"] = None

    print(f"\n{BOLD}{'='*55}")
    print(f"  Final Report")
    print(f"{'='*55}{RESET}")

    checks = {
        "Health check": results.get("health"),
        "Status endpoint": results.get("status"),
        "Single generation": results.get("single_generate"),
        "Proof-of-work (determinism)": results.get("determinism"),
    }
    div = results.get("divergence")
    if div is not None:
        label = f"{GREEN}✅" if div else f"{YELLOW}⚠️ "
        print(f"  {label} Divergence — informational only{RESET}")
    all_ok = True
    for label, passed in checks.items():
        if passed is None:
            print(f"  {DIM}—  {label} (skipped){RESET}")
        elif passed:
            print(f"  {GREEN}✅ {label}{RESET}")
        else:
            print(f"  {RED}❌ {label}{RESET}")
            all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}  Provider ready for production. ✅{RESET}")
    else:
        print(f"{RED}{BOLD}  Provider NOT ready — fix the issues above. ❌{RESET}")

    print(f"{DIM}{'='*55}{RESET}\n")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
