import asyncio
import io
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import httpx
import librosa
import numpy as np
from scipy.spatial.distance import cosine
from sqlalchemy.orm import Session
from server.config import settings


logger = logging.getLogger(__name__)


class ProviderVerificationService:

    @staticmethod
    def _get_mel_fingerprint(wav_bytes: bytes, duration: int) -> Optional[np.ndarray]:
        try:
            buf = io.BytesIO(wav_bytes)
            y, sr = librosa.load(buf, sr=22050, mono=True, duration=duration)
            mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
            mel_db = librosa.power_to_db(mel, ref=np.max)
            return mel_db.mean(axis=1).astype(np.float32)
        except Exception as e:
            logger.error(f"Failed to compute mel fingerprint: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        return round(1.0 - float(cosine(a, b)), 4)

    @staticmethod
    async def _request_verification(
        provider_url: str,
        server_api_key: str,
        prompt: str,
        seed: int,
        duration: int,
        provider_api_key_hash: str,
        encoded_server_auth_key: str,
    ) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=settings.VERIFY_TIMEOUT) as client:
                response = await client.post(
                    f"{provider_url.rstrip('/')}/generate",
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": server_api_key,
                    },
                    json={"prompt": prompt, "seed": seed, "duration": duration},
                )
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "audio" in content_type or "octet-stream" in content_type:
                        from server.services.integrity_service import (
                            verify_provider_hash,
                        )

                        provider_hash = response.headers.get("x-provider-hash", "")
                        if not verify_provider_hash(
                            provider_hash,
                            provider_api_key_hash,
                            encoded_server_auth_key,
                        ):
                            logger.warning(
                                f"❌ {provider_url} — code integrity check failed on verify"
                            )
                            return None
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
    def _store_sample(
        db: Session,
        prompt: str,
        seed: int,
        model: str,
        fingerprint: np.ndarray,
        duration: int,
    ) -> bool:
        from server.core.database import VerificationSample
        from server.core.security import encrypt_fingerprint

        existing = (
            db.query(VerificationSample)
            .filter(
                VerificationSample.prompt == prompt,
                VerificationSample.seed == seed,
                VerificationSample.model == model,
            )
            .first()
        )
        if existing:
            return False

        sample = VerificationSample(
            prompt=prompt,
            seed=seed,
            model=model,
            duration=duration,
            encrypted_fingerprint=encrypt_fingerprint(fingerprint),
        )
        db.add(sample)
        db.commit()
        logger.info(
            f"📦 Sample stored — prompt: '{prompt}' seed: {seed} model: {model}"
        )
        return True

    @staticmethod
    def _get_sample(
        db: Session,
        prompt: str,
        seed: int,
        model: str,
    ) -> Optional[np.ndarray]:
        from server.core.database import VerificationSample
        from server.core.security import decrypt_fingerprint

        sample = (
            db.query(VerificationSample)
            .filter(
                VerificationSample.prompt == prompt,
                VerificationSample.seed == seed,
                VerificationSample.model == model,
            )
            .first()
        )
        if not sample:
            return None
        try:
            return decrypt_fingerprint(sample.encrypted_fingerprint)
        except Exception as e:
            logger.error(f"Failed to decrypt sample fingerprint: {e}")
            return None

    @staticmethod
    def _flag_provider(db: Session, provider_id: int) -> bool:
        from server.core.database import Provider

        provider = db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return False
        provider.verification_failures = (provider.verification_failures or 0) + 1
        db.commit()
        return provider.verification_failures >= settings.MAX_CONSECUTIVE_FAILS

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
    def _get_trusted_provider(db: Session):
        from server.core.database import Provider

        return (
            db.query(Provider)
            .filter(
                Provider.is_trusted == True,
                Provider.is_online == True,
                Provider.is_banned == False,
                Provider.is_active == True,
            )
            .first()
        )

    @staticmethod
    def _make_report(prompt: str, seed: int) -> Dict:
        return {
            "status": "completed",
            "prompt": prompt,
            "seed": seed,
            "reference_source": None,
            "reference_model": None,
            "total": 0,
            "responded": 0,
            "failed": 0,
            "results": [],
        }

    @staticmethod
    def _report_skipped(report: Dict, provider, note: str) -> None:
        logger.warning(f"  ⏭️  {provider.name} skipped — {note}")
        report["results"].append(
            {
                "provider_id": provider.id,
                "provider_name": provider.name,
                "model": None,
                "similarity": None,
                "passed": True,
                "note": note,
            }
        )

    @staticmethod
    async def _wait_and_lock_providers(
        db: Session,
        providers: list,
        report: Dict,
    ) -> list:
        wait_results = await asyncio.gather(
            *[
                ProviderVerificationService._wait_for_provider_free(db, p.id, p.name)
                for p in providers
            ],
            return_exceptions=True,
        )

        free_providers = []
        for provider, is_free in zip(providers, wait_results):
            if is_free is True:
                free_providers.append(provider)
            else:
                ProviderVerificationService._report_skipped(
                    report, provider, "skipped_busy"
                )

        lock_results = await asyncio.gather(
            *[
                ProviderVerificationService._lock_provider_for_test(db, p.id)
                for p in free_providers
            ],
            return_exceptions=True,
        )

        locked = []
        for provider, lock_ok in zip(free_providers, lock_results):
            if lock_ok is True:
                locked.append(provider)
            else:
                ProviderVerificationService._report_skipped(
                    report, provider, "skipped_lock_race"
                )

        return locked

    @staticmethod
    async def _fetch_trusted_model(trusted_url: str, decrypted_key: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    f"{trusted_url.rstrip('/')}/status",
                    headers={"X-API-Key": decrypted_key},
                )
                if r.status_code == 200:
                    return r.json().get("model", "unknown")
        except Exception:
            pass
        return "unknown"

    @staticmethod
    async def _fill_sample_bank_from_trusted(
        db: Session,
        trusted,
        decrypted_key: str,
        model: str,
    ) -> None:
        from server.core.database import VerificationSample
        from server.services.provider_service import ProviderService

        existing_count = (
            db.query(VerificationSample)
            .filter(VerificationSample.model == model)
            .count()
        )
        needed = max(0, settings.TRUSTED_SAMPLE_TARGET - existing_count)

        if needed == 0:
            logger.info(
                f"📦 Sample bank full ({existing_count}/{settings.TRUSTED_SAMPLE_TARGET})"
                f"for model '{model}' — no fill needed"
            )
            return

        logger.info(
            f"📦 Filling sample bank: {needed} sample(s) needed "
            f"({existing_count}/{settings.TRUSTED_SAMPLE_TARGET}) for model '{model}'"
        )
        for _ in range(needed):
            s_seed = random.randint(0, 2**31 - 1)
            s_prompt = random.choice(settings.VERIFICATION_PROMPTS)
            s_duration = random.randint(
                settings.VERIFY_DURATION_MIN, settings.VERIFY_DURATION_MAX
            )
            result = await ProviderVerificationService._request_verification(
                trusted.url,
                decrypted_key,
                s_prompt,
                s_seed,
                s_duration,
                trusted.api_key,
                trusted.encoded_server_auth_key,
            )
            if not result or not result.get("wav"):
                logger.warning(f"  ⚠️  Trusted failed to generate sample '{s_prompt}'")
                continue
            if not ProviderService._validate_wav_ffmpeg(result["wav"]):
                logger.warning(
                    f"  ⚠️  Trusted returned invalid WAV for sample '{s_prompt}'"
                )
                continue
            fp = ProviderVerificationService._get_mel_fingerprint(
                result["wav"], s_duration
            )
            if fp is None:
                logger.warning(f"  ⚠️  Bad fingerprint for sample '{s_prompt}'")
                continue
            ok = ProviderVerificationService._store_sample(
                db, s_prompt, s_seed, model, fp, s_duration
            )
            if ok:
                logger.info(f"  ✅ Sample stored — prompt: '{s_prompt}' seed: {s_seed}")

    @staticmethod
    def _draw_reference_from_bank(
        db: Session,
        model_filter: Optional[str] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[str], str]:
        from server.core.database import VerificationSample
        from server.core.security import decrypt_fingerprint

        query = db.query(VerificationSample)
        if model_filter:
            query = query.filter(VerificationSample.model == model_filter)
        samples = query.all()

        if not samples:
            return None, None, "none", settings.VERIFY_DURATION

        chosen = random.choice(samples)
        source = "sample_bank_random" if model_filter else "sample_bank_random_fallback"
        try:
            fp = decrypt_fingerprint(chosen.encrypted_fingerprint)
            logger.info(
                f"🎲 Reference drawn from bank — prompt: '{chosen.prompt}' "
                f"seed: {chosen.seed} model: {chosen.model} "
                f"(pool: {len(samples)} samples)"
            )
            return fp, chosen.model, source, chosen.duration
        except Exception as e:
            logger.error(f"Failed to decrypt chosen sample: {e}")
            return None, None, "none", settings.VERIFY_DURATION

    @staticmethod
    async def _build_reference(
        db: Session,
        trusted,
        report: Dict,
    ) -> Tuple[Optional[np.ndarray], Optional[str], str, int]:
        from server.core.security import decrypt_server_key

        if not trusted:
            return ProviderVerificationService._draw_reference_from_bank(db)

        trusted_locked_list = (
            await ProviderVerificationService._wait_and_lock_providers(
                db, [trusted], report
            )
        )
        trusted_locked = bool(trusted_locked_list)

        if not trusted_locked:
            logger.warning(
                f"⚠️  Trusted '{trusted.name}' busy — falling back to sample bank"
            )
            return ProviderVerificationService._draw_reference_from_bank(db)

        try:
            decrypted_key = decrypt_server_key(trusted.encoded_server_auth_key)
            model = await ProviderVerificationService._fetch_trusted_model(
                trusted.url, decrypted_key
            )
            await ProviderVerificationService._fill_sample_bank_from_trusted(
                db, trusted, decrypted_key, model
            )
            fp, ref_model, source, ref_duration = (
                ProviderVerificationService._draw_reference_from_bank(
                    db, model_filter=model
                )
            )
            if fp is None:
                logger.warning("⚠️  Sample bank still empty after fill attempt")
            return fp, ref_model, source, ref_duration
        except Exception as e:
            logger.error(f"Failed during trusted reference phase: {e}")
            return None, None, "none", settings.VERIFY_DURATION
        finally:
            ProviderVerificationService._unlock_provider_after_test(db, trusted.id)

    @staticmethod
    async def _request_verifications_from_providers(
        db: Session,
        providers: list,
        prompt: str,
        seed: int,
        duration: int,
        report: Dict,
    ) -> Tuple[list, list]:

        from server.core.security import decrypt_server_key

        locked_providers = await ProviderVerificationService._wait_and_lock_providers(
            db, providers, report
        )

        if not locked_providers:
            return [], []

        tasks = []
        for p in locked_providers:
            try:
                decrypted_key = decrypt_server_key(p.encoded_server_auth_key)
                tasks.append(
                    ProviderVerificationService._request_verification(
                        p.url,
                        decrypted_key,
                        prompt,
                        seed,
                        duration,
                        p.api_key,
                        p.encoded_server_auth_key,
                    )
                )
            except Exception as e:
                logger.error(f"Failed to decrypt key for {p.name}: {e}")
                tasks.append(asyncio.sleep(0, result=None))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        return locked_providers, list(raw_results)

    @staticmethod
    def _score_provider_result(
        db: Session,
        provider,
        result,
        reference_fp: np.ndarray,
        reference_model: Optional[str],
        reference_source: str,
        prompt: str,
        reference_duration: int,
        seed: int,
        report: Dict,
    ) -> None:
        from server.services.provider_service import ProviderService

        if isinstance(result, Exception) or result is None:
            logger.warning(f"  ❌ {provider.name} — no response")
            report["failed"] += 1
            should_ban = ProviderVerificationService._flag_provider(db, provider.id)
            ProviderVerificationService._save_result(
                db, provider.id, prompt, seed, None, False
            )
            if should_ban:
                ProviderVerificationService._ban_provider(
                    db,
                    provider.id,
                    f"Proof-of-work: {settings.MAX_CONSECUTIVE_FAILS} consecutive non-responses",
                )
            report["results"].append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "model": None,
                    "similarity": None,
                    "passed": False,
                    "note": "no_response",
                }
            )
            return

        wav = result.get("wav")
        provider_model = result.get("model", "unknown")

        if not ProviderService._validate_wav_ffmpeg(wav):
            logger.warning(f"  ❌ {provider.name} — WAV failed ffmpeg validation")
            report["failed"] += 1
            should_ban = ProviderVerificationService._flag_provider(db, provider.id)
            ProviderVerificationService._save_result(
                db, provider.id, prompt, seed, None, False
            )
            if should_ban:
                ProviderVerificationService._ban_provider(
                    db,
                    provider.id,
                    f"Proof-of-work: {settings.MAX_CONSECUTIVE_FAILS} consecutive invalid WAV responses",
                )
            report["results"].append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "model": provider_model,
                    "similarity": None,
                    "passed": False,
                    "note": "invalid_wav",
                }
            )
            return
        fp = ProviderVerificationService._get_mel_fingerprint(wav, reference_duration)

        if fp is None:
            logger.warning(f"  ❌ {provider.name} — invalid audio")
            report["failed"] += 1
            should_ban = ProviderVerificationService._flag_provider(db, provider.id)
            ProviderVerificationService._save_result(
                db, provider.id, prompt, seed, None, False
            )
            if should_ban:
                ProviderVerificationService._ban_provider(
                    db,
                    provider.id,
                    f"Proof-of-work: {settings.MAX_CONSECUTIVE_FAILS} consecutive invalid audio responses",
                )
            report["results"].append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "model": provider_model,
                    "similarity": None,
                    "passed": False,
                    "note": "invalid_audio",
                }
            )
            return

        report["responded"] += 1

        if reference_model and provider_model != reference_model:
            logger.info(
                f"  ⏭️  {provider.name} — model mismatch "
                f"(provider: {provider_model} / reference: {reference_model})"
            )
            ProviderVerificationService._reset_failures(db, provider.id)
            ProviderVerificationService._save_result(
                db, provider.id, prompt, seed, None, True
            )
            report["results"].append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "model": provider_model,
                    "similarity": None,
                    "passed": True,
                    "note": "model_mismatch_skipped",
                }
            )
            return

        similarity = ProviderVerificationService._cosine_similarity(fp, reference_fp)
        passed = similarity >= settings.SIMILARITY_THRESHOLD

        logger.info(
            f"  {'✅' if passed else '❌'} {provider.name} "
            f"— similarity: {similarity:.4f} ({'pass' if passed else 'FAIL'}) "
            f"| model: {provider_model} | ref: {reference_source}"
        )

        ProviderVerificationService._save_result(
            db, provider.id, prompt, seed, similarity, passed
        )

        if passed:
            ProviderVerificationService._reset_failures(db, provider.id)
        else:
            should_ban = ProviderVerificationService._flag_provider(db, provider.id)
            if should_ban:
                ProviderVerificationService._ban_provider(
                    db,
                    provider.id,
                    f"Proof-of-work: similarity {similarity:.4f} < {settings.SIMILARITY_THRESHOLD} "
                    f"for {settings.MAX_CONSECUTIVE_FAILS} consecutive rounds "
                    f"(model: {provider_model}, ref: {reference_source})",
                )
                logger.warning(
                    f"🚫 {provider.name} banned after {settings.MAX_CONSECUTIVE_FAILS} failures"
                )

        report["results"].append(
            {
                "provider_id": provider.id,
                "provider_name": provider.name,
                "model": provider_model,
                "similarity": similarity,
                "passed": passed,
                "note": None,
            }
        )

    @staticmethod
    async def run_verification_round(db: Session) -> Dict:
        from server.core.database import Provider

        all_providers = (
            db.query(Provider)
            .filter(
                Provider.is_active == True,
                Provider.is_banned == False,
                Provider.is_trusted == False,
            )
            .all()
        )

        if not all_providers:
            logger.info("⚡ No non-trusted providers to verify")
            return {"status": "skipped", "reason": "no_providers"}

        sample_size = max(1, int(len(all_providers) * settings.VERIFY_POOL_PCT))
        selected = random.sample(all_providers, min(sample_size, len(all_providers)))

        prompt = settings.build_verification_prompt()
        seed = random.randint(0, 2**31 - 1)

        logger.info(
            f"🔍 Verification round — {len(selected)}/{len(all_providers)} providers "
            f"| prompt: '{prompt}' | seed: {seed}"
        )

        report = ProviderVerificationService._make_report(prompt, seed)
        report["total"] = len(selected)

        trusted = ProviderVerificationService._get_trusted_provider(db)
        reference_fp, reference_model, reference_source, reference_duration = (
            await ProviderVerificationService._build_reference(db, trusted, report)
        )

        if reference_fp is None:
            logger.warning("⏸️  No reference available — round BYPASSED")
            return {
                "status": "bypassed",
                "reason": "no_reference_available",
                "prompt": prompt,
                "seed": seed,
            }

        report["reference_source"] = reference_source
        report["reference_model"] = reference_model

        locked_providers, raw_results = (
            await ProviderVerificationService._request_verifications_from_providers(
                db, selected, prompt, seed, reference_duration, report
            )
        )

        if not locked_providers:
            logger.info("⚡ No providers ready for verification this round")
            report["status"] = "skipped"
            report["reason"] = "all_providers_busy"
            return report

        try:
            for provider, result in zip(locked_providers, raw_results):
                ProviderVerificationService._score_provider_result(
                    db,
                    provider,
                    result,
                    reference_fp,
                    reference_model,
                    reference_source,
                    prompt,
                    reference_duration,
                    seed,
                    report,
                )
        finally:
            for p in locked_providers:
                ProviderVerificationService._unlock_provider_after_test(db, p.id)

        passed_count = sum(1 for r in report["results"] if r["passed"])
        logger.info(
            f"✅ Verification round complete — "
            f"{passed_count}/{len(report['results'])} passed "
            f"| reference: {reference_source} (model: {reference_model})"
        )

        return report

    @staticmethod
    async def _wait_for_provider_free(
        db: Session,
        provider_id: int,
        provider_name: str,
        timeout: int = settings.WAIT_FOR_FREE_TIMEOUT,
        poll_interval: int = settings.WAIT_FOR_FREE_POLL_INTERVAL,
    ) -> bool:
        from server.core.database import Provider, SessionLocal

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            with SessionLocal() as check_db:
                p = check_db.query(Provider).filter(Provider.id == provider_id).first()
                if p and not p.is_generating:
                    return True
            await asyncio.sleep(poll_interval)

        logger.warning(
            f"⏱️  {provider_name} still generating after {timeout}s — skipping for this round"
        )
        return False

    @staticmethod
    async def _lock_provider_for_test(db: Session, provider_id: int) -> bool:
        from server.core.database import Provider

        p = db.query(Provider).filter(Provider.id == provider_id).first()
        if not p or p.is_generating or not p.is_disposable:
            return False
        p.is_disposable = False
        db.commit()
        return True

    @staticmethod
    def _unlock_provider_after_test(db: Session, provider_id: int):
        from server.core.database import Provider

        try:
            p = db.query(Provider).filter(Provider.id == provider_id).first()
            if p:
                p.is_disposable = True
                db.commit()
        except Exception as e:
            logger.error(f"Failed to unlock provider {provider_id}: {e}")

    @staticmethod
    async def run_forever():
        from server.core.database import SessionLocal

        while True:
            delay = random.randint(
                settings.VERIFY_INTERVAL_MIN, settings.VERIFY_INTERVAL_MAX
            )
            hours = delay // 3600
            mins = (delay % 3600) // 60
            logger.info(f"⏳ Next verification round in {hours}h{mins:02d}m")

            await asyncio.sleep(delay)

            with SessionLocal() as db:
                try:
                    await ProviderVerificationService.run_verification_round(db)
                except Exception as e:
                    logger.error(f"❌ Verification round error: {e}")
