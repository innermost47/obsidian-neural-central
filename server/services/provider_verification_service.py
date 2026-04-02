import asyncio
from collections import defaultdict
import io
import logging
import os
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import librosa
import numpy as np
from scipy.spatial.distance import cosine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT = float(os.getenv("VERIFY_TIMEOUT", "120.0"))
VERIFY_POOL_PCT = float(os.getenv("VERIFY_POOL_PCT", "0.30"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.98"))
MAX_CONSECUTIVE_FAILS = int(os.getenv("MAX_CONSECUTIVE_FAILS", "3"))
VERIFY_DURATION = int(os.getenv("VERIFY_DURATION", "5"))

VERIFY_INTERVAL_MIN = int(os.getenv("VERIFY_INTERVAL_MIN", str(1 * 3600)))
VERIFY_INTERVAL_MAX = int(os.getenv("VERIFY_INTERVAL_MAX", str(5 * 3600)))

VERIFICATION_PROMPTS = [
    "steady kick drum loop 120bpm",
    "hi-hat pattern 120bpm",
    "clap snare pattern 90bpm",
    "rhythmic click track 100bpm",
    "deep punchy kick drum hit",
    "fast snare roll",
    "shaker groove loop",
    "rimshot pattern 140bpm",
    "conga rhythm loop",
    "tambourine steady beat",
    "open hi-hat swing groove",
    "cowbell pattern disco",
    "tribal drum circle rhythm",
    "breakbeat loop 95bpm",
    "trap hi-hat triplet pattern",
    "low bass drone",
    "deep sub bass pulse",
    "smooth bass guitar riff",
    "modular synth bass sequence",
    "dark ambient drone texture",
    "low frequency rumble",
    "sustained cello bass note",
    "upright bass walking line",
    "808 bass hit",
    "synth bass arpeggio minor",
    "simple piano note C major",
    "electric piano chord Fmaj7",
    "mellow guitar strum Am",
    "soft flute melody",
    "sustained string pad",
    "warm Rhodes chord",
    "vibraphone single note",
    "nylon guitar pluck",
    "music box melody short",
    "marimba phrase",
    "harp arpeggio",
    "organ chord sustained",
    "banjo picking pattern",
    "steel drum hit",
    "hammered dulcimer note",
    "simple sine wave 440hz",
    "white noise sweep",
    "ambient wind texture",
    "short percussive hit",
    "analog synth pad warm",
    "FM bell tone",
    "granular texture shimmer",
    "choir aaah vowel",
    "distant thunder rumble",
    "vinyl crackle loop",
    "tape hiss texture",
    "glass harmonica tone",
    "crystal bowl resonance",
    "rain on window ambient",
    "soft underwater bubbles",
    "reverse cymbal swell",
    "riser synth sweep up",
    "white noise downlifter",
    "laser zap sound effect",
    "camera shutter click",
    "door creak short",
    "water drop single",
    "wind chime gentle",
    "typewriter keystroke",
    "match strike spark",
]


class ProviderVerificationService:

    @staticmethod
    def _get_mel_fingerprint(wav_bytes: bytes) -> Optional[np.ndarray]:
        try:
            buf = io.BytesIO(wav_bytes)
            y, sr = librosa.load(buf, sr=22050, mono=True, duration=VERIFY_DURATION)

            mel = librosa.feature.melspectrogram(
                y=y,
                sr=sr,
                n_mels=128,
                fmax=8000,
            )
            mel_db = librosa.power_to_db(mel, ref=np.max)

            fingerprint = mel_db.mean(axis=1)
            return fingerprint

        except Exception as e:
            logger.error(f"Failed to compute mel fingerprint: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        dist = cosine(a, b)
        return round(1.0 - dist, 4)

    @staticmethod
    async def _request_verification(
        provider_url: str,
        prompt: str,
        seed: int,
        duration: int,
    ) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=VERIFY_TIMEOUT) as client:
                response = await client.post(
                    f"{provider_url.rstrip('/')}/verify",
                    headers={"Content-Type": "application/json"},
                    json={"prompt": prompt, "seed": seed, "duration": duration},
                )
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "audio" in content_type or "octet-stream" in content_type:
                        return {
                            "wav": response.content,
                            "model": response.headers.get("X-Model", "unknown"),
                        }
            logger.warning(f"Verify request failed: HTTP {response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Verify request error: {e}")
            return None

    @staticmethod
    def _flag_provider(db: Session, provider_id: int) -> bool:
        from server.core.database import Provider

        provider = db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return False

        provider.verification_failures = (provider.verification_failures or 0) + 1
        db.commit()

        if provider.verification_failures >= MAX_CONSECUTIVE_FAILS:
            return True
        return False

    @staticmethod
    def _reset_failures(db: Session, provider_id: int):
        from server.core.database import Provider

        provider = db.query(Provider).filter(Provider.id == provider_id).first()
        if provider:
            provider.verification_failures = 0
            db.commit()

    @staticmethod
    def _ban_provider(db: Session, provider_id: int, reason: str):
        from server.core.database import Provider

        provider = db.query(Provider).filter(Provider.id == provider_id).first()
        if provider:
            provider.is_banned = True
            provider.is_active = False
            provider.ban_reason = reason
            if provider.user_id:
                from server.core.database import User

                user = db.query(User).filter(User.id == provider.user_id).first()
                if user and user.subscription_tier == "provider":
                    user.subscription_tier = "free"
                    user.subscription_status = "inactive"
            db.commit()
            logger.warning(f"🚫 Provider {provider.name} BANNED: {reason}")

    @staticmethod
    def _save_result(
        db: Session,
        provider_id: int,
        prompt: str,
        seed: int,
        similarity_score: Optional[float],
        passed: bool,
    ):
        from server.core.database import ProviderVerification

        record = ProviderVerification(
            provider_id=provider_id,
            prompt=prompt,
            seed=seed,
            similarity_score=similarity_score,
            passed=passed,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()

    @staticmethod
    async def run_verification_round(db: Session) -> Dict:
        from server.core.database import Provider

        providers = (
            db.query(Provider)
            .filter(Provider.is_active == True, Provider.is_banned == False)
            .all()
        )

        if len(providers) < 2:
            logger.info("⚡ Not enough providers for verification (need ≥ 2)")
            return {"status": "skipped", "reason": "not_enough_providers"}

        sample_size = max(2, int(len(providers) * VERIFY_POOL_PCT))
        selected = random.sample(providers, min(sample_size, len(providers)))

        seed = random.randint(0, 2**31 - 1)
        prompt = random.choice(VERIFICATION_PROMPTS)

        logger.info(
            f"🔍 Verification round — {len(selected)}/{len(providers)} providers "
            f"| prompt: '{prompt}' | seed: {seed}"
        )

        tasks = [
            ProviderVerificationService._request_verification(
                p.url, prompt, seed, VERIFY_DURATION
            )
            for p in selected
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        groups: Dict[str, List[Tuple[int, np.ndarray]]] = defaultdict(list)
        failed_ids: List[int] = []

        for provider, result in zip(selected, raw_results):
            if isinstance(result, Exception) or result is None:
                logger.warning(f"  ❌ {provider.name} — no response")
                failed_ids.append(provider.id)
                continue

            wav = result.get("wav")
            model = result.get("model", "unknown")

            fp = ProviderVerificationService._get_mel_fingerprint(wav)
            if fp is None:
                logger.warning(f"  ❌ {provider.name} — invalid audio")
                failed_ids.append(provider.id)
                continue

            groups[model].append((provider.id, fp))
            logger.info(f"  ✅ {provider.name} — model: {model} — fingerprint computed")

        total_responded = sum(len(v) for v in groups.values())

        report = {
            "status": "completed",
            "seed": seed,
            "prompt": prompt,
            "total": len(selected),
            "responded": total_responded,
            "failed": len(failed_ids),
            "groups": list(groups.keys()),
            "results": [],
        }

        for model_name, fps_in_group in groups.items():
            logger.info(f"  🔬 Model '{model_name}' — {len(fps_in_group)} provider(s)")

            if len(fps_in_group) == 1:
                provider_id = fps_in_group[0][0]
                provider = db.query(Provider).filter(Provider.id == provider_id).first()
                logger.info(
                    f"  ⏭️  {provider.name if provider else provider_id} — "
                    f"sole provider on model '{model_name}', skipping comparison"
                )
                ProviderVerificationService._reset_failures(db, provider_id)
                ProviderVerificationService._save_result(
                    db, provider_id, prompt, seed, None, True
                )
                report["results"].append(
                    {
                        "provider_id": provider_id,
                        "provider_name": provider.name if provider else "unknown",
                        "model": model_name,
                        "similarity": None,
                        "passed": True,
                        "note": "sole_provider_on_model",
                    }
                )
                continue

            reference = np.mean([fp for _, fp in fps_in_group], axis=0)

            for provider_id, fp in fps_in_group:
                provider = db.query(Provider).filter(Provider.id == provider_id).first()
                similarity = ProviderVerificationService._cosine_similarity(
                    fp, reference
                )
                passed = similarity >= SIMILARITY_THRESHOLD

                logger.info(
                    f"  {'✅' if passed else '❌'} {provider.name if provider else provider_id} "
                    f"— similarity: {similarity:.4f} ({'pass' if passed else 'FAIL'})"
                )

                ProviderVerificationService._save_result(
                    db, provider_id, prompt, seed, similarity, passed
                )

                if passed:
                    ProviderVerificationService._reset_failures(db, provider_id)
                else:
                    should_ban = ProviderVerificationService._flag_provider(
                        db, provider_id
                    )
                    if should_ban:
                        ProviderVerificationService._ban_provider(
                            db,
                            provider_id,
                            f"Proof-of-work: similarity {similarity:.4f} < {SIMILARITY_THRESHOLD} "
                            f"for {MAX_CONSECUTIVE_FAILS} consecutive rounds (model: {model_name})",
                        )
                        logger.warning(
                            f"🚫 {provider.name if provider else provider_id} banned "
                            f"after {MAX_CONSECUTIVE_FAILS} failures"
                        )

                report["results"].append(
                    {
                        "provider_id": provider_id,
                        "provider_name": provider.name if provider else "unknown",
                        "model": model_name,
                        "similarity": similarity,
                        "passed": passed,
                    }
                )

        for pid in failed_ids:
            provider = db.query(Provider).filter(Provider.id == pid).first()
            should_ban = ProviderVerificationService._flag_provider(db, pid)
            ProviderVerificationService._save_result(db, pid, prompt, seed, None, False)
            if should_ban:
                ProviderVerificationService._ban_provider(
                    db,
                    pid,
                    f"Proof-of-work: {MAX_CONSECUTIVE_FAILS} consecutive non-responses",
                )
            report["results"].append(
                {
                    "provider_id": pid,
                    "provider_name": provider.name if provider else "unknown",
                    "model": None,
                    "similarity": None,
                    "passed": False,
                }
            )

        passed_count = sum(1 for r in report["results"] if r["passed"])
        logger.info(
            f"✅ Verification round complete — "
            f"{passed_count}/{len(report['results'])} passed "
            f"| {len(groups)} model group(s)"
        )

        return report

    @staticmethod
    async def run_forever():
        from server.core.database import SessionLocal

        while True:
            delay = random.randint(VERIFY_INTERVAL_MIN, VERIFY_INTERVAL_MAX)
            hours = delay // 3600
            mins = (delay % 3600) // 60
            logger.info(f"⏳ Next verification round in {hours}h{mins:02d}m")

            await asyncio.sleep(delay)

            with SessionLocal() as db:
                try:
                    await ProviderVerificationService.run_verification_round(db)
                except Exception as e:
                    logger.error(f"❌ Verification round error: {e}")
