import httpx
import asyncio
import tempfile
import os
import random
import hashlib
import subprocess
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from collections import defaultdict
from server.services.fal_service import FalService
from server.services.credits_service import CreditsService
from server.core.database import Provider, ProviderJob, OwnershipLog
from server.config import settings
from server.core.security import encrypt_server_key, decrypt_server_key

PING_TIMEOUT = 5.0
GENERATE_TIMEOUT = 180.0
MAX_WAV_SIZE_MB = 50


_provider_queues: dict[int, asyncio.Queue] = defaultdict(
    lambda: asyncio.Queue(maxsize=10)
)
_provider_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_provider_workers: dict[int, bool] = defaultdict(bool)


class ProviderService:

    @staticmethod
    def _write_ownership(
        public_user_id: str,
        provider_name: str,
        prompt: str,
        duration: float,
        file_hash: str,
        db: Session,
    ):
        import hashlib
        from datetime import datetime, timezone

        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
        try:
            entry = OwnershipLog(
                public_user_id=public_user_id,
                provider_name=provider_name,
                prompt_hash=prompt_hash,
                duration=round(duration, 2),
                audio_content_hash=file_hash,
                generated_at=datetime.now(timezone.utc),
            )
            db.add(entry)
            db.commit()
            print(f"📝 Ownership logged for user {public_user_id}")
        except Exception as e:
            db.rollback()
            print(f"⚠️ Failed to write ownership: {e}")
        finally:
            db.close()

    @staticmethod
    async def _ping_provider(url: str, encoded_server_auth_key: str) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/status",
                    headers={
                        **settings.BROWSER_HEADERS,
                        "X-API-Key": decrypt_server_key(encoded_server_auth_key),
                    },
                )
                if response.status_code == 200:
                    return response.json()
        except Exception:
            return None
        return None

    @staticmethod
    async def _find_available_provider(db: Session) -> Optional[Dict]:
        from server.core.database import Provider

        providers = (
            db.query(Provider)
            .filter(Provider.is_active == True, Provider.is_banned == False)
            .all()
        )
        random.shuffle(providers)
        if not providers:
            print("📭 No active providers in pool")
            return None

        print(f"🔍 Pinging {len(providers)} provider(s)...")

        ping_tasks = [
            ProviderService._ping_provider(p.url, p.encoded_server_auth_key)
            for p in providers
        ]
        results = await asyncio.gather(*ping_tasks, return_exceptions=True)

        for provider, result in zip(providers, results):
            if isinstance(result, Exception) or not result:
                continue

            if not result.get("available", False):
                continue

            returned_key = result.get("api_key", "")
            key_hash = hashlib.sha256(returned_key.encode()).hexdigest()
            if key_hash != provider.api_key:
                print(f"⚠️ {provider.name} — invalid API key in status response")
                ProviderService._ban_provider(
                    db, provider.id, "Invalid API key returned in /status response"
                )
                continue

            print(f"✅ Provider available: {provider.name} ({provider.url})")
            provider.last_ping = datetime.now(timezone.utc)
            db.commit()
            return {
                "id": provider.id,
                "name": provider.name,
                "url": provider.url,
                "model": result.get("model", "stable-audio-open"),
                "server_api_key": decrypt_server_key(provider.encoded_server_auth_key),
            }

        print("📭 No provider available right now")
        return None

    @staticmethod
    def _validate_wav_ffmpeg(wav_bytes: bytes) -> bool:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            size_mb = len(wav_bytes) / (1024 * 1024)
            if size_mb > MAX_WAV_SIZE_MB:
                print(f"❌ WAV too large: {size_mb:.1f}MB > {MAX_WAV_SIZE_MB}MB")
                return False

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=format_name,duration",
                    "-show_entries",
                    "stream=codec_type,codec_name,sample_rate,channels",
                    "-of",
                    "json",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                print(f"❌ FFprobe validation failed: {result.stderr}")
                return False

            import json

            probe_data = json.loads(result.stdout)
            fmt = probe_data.get("format", {})
            streams = probe_data.get("streams", [])

            format_name = fmt.get("format_name", "")
            if "wav" not in format_name and "pcm" not in format_name:
                print(f"❌ Invalid format: {format_name} (expected wav)")
                return False

            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            if not audio_streams:
                print("❌ No audio stream found")
                return False

            duration = float(fmt.get("duration", 0))
            if duration < 1 or duration > 60:
                print(f"❌ Invalid duration: {duration:.1f}s (expected 1-60s)")
                return False

            print(f"✅ WAV valid: {duration:.1f}s, format={format_name}")
            return True

        except subprocess.TimeoutExpired:
            print("❌ FFprobe timeout")
            return False
        except Exception as e:
            print(f"❌ WAV validation error: {e}")
            return False
        finally:
            if tmp and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass

    @staticmethod
    def _ban_provider(db: Session, provider_id: int, reason: str):
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
            print(f"🚫 Provider {provider.name} BANNED: {reason}")

    @staticmethod
    async def _generate_at_provider(
        provider: Dict, prompt: str, duration: int, db: Session
    ) -> Optional[bytes]:
        try:
            print(f"🎵 Sending generation to provider: {provider['name']}")
            async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT) as client:
                response = await client.post(
                    f"{provider['url'].rstrip('/')}/generate",
                    headers={
                        **settings.BROWSER_HEADERS,
                        "X-API-Key": provider["server_api_key"],
                    },
                    json={
                        "prompt": prompt,
                        "duration": duration,
                    },
                )

                if response.status_code != 200:
                    print(
                        f"❌ Provider {provider['name']} returned HTTP {response.status_code}"
                    )
                    return None

                returned_key = response.headers.get("X-Provider-Key", "")
                key_hash = hashlib.sha256(returned_key.encode()).hexdigest()
                db_provider = (
                    db.query(Provider).filter(Provider.id == provider["id"]).first()
                )
                if not db_provider or key_hash != db_provider.api_key:
                    print(f"❌ {provider['name']} — invalid key in generation response")
                    ProviderService._ban_provider(
                        db, provider["id"], "Invalid API key in response"
                    )
                    return None

                content_type = response.headers.get("content-type", "")
                if "audio" not in content_type and "octet-stream" not in content_type:
                    print(f"❌ Provider returned invalid content-type: {content_type}")
                    return None

                return response.content

        except httpx.TimeoutException:
            print(f"⏱️  Provider {provider['name']} timeout after {GENERATE_TIMEOUT}s")
            return None
        except Exception as e:
            print(f"❌ Provider {provider['name']} error: {e}")
            return None

    @staticmethod
    async def generate_audio(
        prompt: str,
        duration: int,
        user_id: int,
        public_user_id: str,
        db: Session,
    ) -> Dict[str, Any]:

        job = ProviderJob(
            user_id=user_id,
            prompt=prompt,
            duration=duration,
            status="pending",
        )
        db.add(job)
        db.commit()

        provider = await ProviderService._find_available_provider(db)

        if provider:
            queue = _provider_queues[provider["id"]]

            if queue.full():
                print(
                    f"⚠️  Provider {provider['name']} queue full, falling back to fal.ai..."
                )
            else:
                job.provider_id = provider["id"]
                job.status = "queued"
                db.commit()

                future = asyncio.get_event_loop().create_future()
                await queue.put((prompt, duration, public_user_id, future, job.id))

                if not _provider_workers[provider["id"]]:
                    asyncio.create_task(ProviderService._process_queue(provider))

                try:
                    result = await asyncio.wait_for(future, timeout=180.0)

                    if result["success"]:
                        return result

                    print(f"⚠️  Provider returned error, falling back to fal.ai...")

                except asyncio.TimeoutError:
                    print(f"⚠️  Provider timeout after 180s, falling back to fal.ai...")
                    job.status = "failed"
                    job.error_message = "Timeout waiting for provider"
                    db.commit()

        print("🔄 Falling back to fal.ai...")
        job.status = "fallback"
        job.used_fallback = True
        db.commit()

        fal_result = await FalService.generate_audio(prompt, duration)

        if fal_result["success"]:
            job.status = "done"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

            return {
                "success": True,
                "audio_url": fal_result["audio_url"],
                "used_fallback": True,
                "provider_name": "fal.ai",
                "provider_model": "stable-audio",
            }
        else:
            job.status = "failed"
            job.error_message = fal_result.get("error", "fal.ai fallback failed")
            db.commit()

            return {
                "success": False,
                "error": fal_result.get("error", "All generation methods failed"),
                "used_fallback": True,
            }

    @staticmethod
    async def _process_queue(provider: dict):
        from server.core.database import SessionLocal

        provider_id = provider["id"]
        _provider_workers[provider_id] = True

        queue = _provider_queues[provider_id]
        lock = _provider_locks[provider_id]

        try:
            while not queue.empty():
                prompt, duration, public_user_id, future, job_id = await queue.get()

                async with lock:
                    db = SessionLocal()
                    try:
                        from server.core.database import (
                            ProviderJob,
                            Provider as ProviderModel,
                        )

                        job = (
                            db.query(ProviderJob)
                            .filter(ProviderJob.id == job_id)
                            .first()
                        )
                        if job:
                            job.status = "processing"
                            db.commit()

                        wav_bytes = await ProviderService._generate_at_provider(
                            provider, prompt, duration, db
                        )

                        if wav_bytes:
                            is_valid = ProviderService._validate_wav_ffmpeg(wav_bytes)

                            if is_valid:
                                file_hash = hashlib.sha256(wav_bytes).hexdigest()
                                p = (
                                    db.query(ProviderModel)
                                    .filter(ProviderModel.id == provider_id)
                                    .first()
                                )
                                if p:
                                    p.jobs_done += 1
                                    p.billable_jobs += 1
                                    p.last_seen = datetime.now(timezone.utc)

                                if job:
                                    job.status = "done"
                                    job.completed_at = datetime.now(timezone.utc)
                                db.commit()

                                estimated_duration = len(wav_bytes) / (44100 * 2 * 2)
                                ProviderService._write_ownership(
                                    public_user_id=public_user_id,
                                    provider_name=provider["name"],
                                    prompt=prompt,
                                    duration=estimated_duration,
                                    file_hash=file_hash,
                                    db=db,
                                )

                                print(
                                    f"✅ Generation successful via provider: {provider['name']}"
                                )

                                if not future.done():
                                    future.set_result(
                                        {
                                            "success": True,
                                            "wav_bytes": wav_bytes,
                                            "used_fallback": False,
                                            "provider_name": provider["name"],
                                            "provider_model": provider.get(
                                                "model", "unknown"
                                            ),
                                        }
                                    )
                            else:
                                ProviderService._ban_provider(
                                    db, provider_id, "Returned invalid WAV file"
                                )

                                p = (
                                    db.query(ProviderModel)
                                    .filter(ProviderModel.id == provider_id)
                                    .first()
                                )
                                if p:
                                    p.jobs_failed += 1

                                if job:
                                    job.status = "failed"
                                    job.error_message = (
                                        "Invalid WAV returned by provider"
                                    )
                                db.commit()

                                print(
                                    f"⚠️  Provider {provider['name']} banned — invalid WAV"
                                )

                                if not future.done():
                                    future.set_result(
                                        {"success": False, "error": "Invalid WAV"}
                                    )
                        else:
                            p = (
                                db.query(ProviderModel)
                                .filter(ProviderModel.id == provider_id)
                                .first()
                            )
                            if p:
                                p.jobs_failed += 1
                            db.commit()

                            if job:
                                job.status = "failed"
                                job.error_message = "Provider did not return audio"
                                db.commit()

                            print(f"⚠️  Provider {provider['name']} failed to respond")

                            if not future.done():
                                future.set_result(
                                    {
                                        "success": False,
                                        "error": "Provider did not respond",
                                    }
                                )

                    except Exception as e:
                        print(
                            f"❌ Error processing queue item for {provider['name']}: {e}"
                        )
                        if not future.done():
                            future.set_exception(e)
                    finally:
                        db.close()
                        queue.task_done()

        finally:
            _provider_workers[provider_id] = False

    @staticmethod
    def generate_api_key(length: int = 48) -> str:
        alphabet = string.ascii_letters + string.digits
        return "op_" + "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def add_provider(
        db: Session,
        name: str,
        url: str,
        stripe_account_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Dict:

        api_key = ProviderService.generate_api_key()
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        s_auth_key = ProviderService.generate_api_key()
        s_auth_key_encrypted = encrypt_server_key(s_auth_key)

        provider = Provider(
            name=name,
            url=url,
            api_key=api_key_hash,
            encoded_server_auth_key=s_auth_key_encrypted,
            stripe_account_id=stripe_account_id if stripe_account_id else None,
            is_active=True,
            is_banned=False,
            user_id=user_id or None,
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

        if user_id:
            from server.core.database import User

            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.subscription_tier = "provider"
                user.subscription_status = "active"
                CreditsService.refill_credits(db, user.id, "provider")
                print(f"✅ Provider tier activated for user {user.email}")

        print(f"✅ Provider added: {name} ({url})")
        return {
            "id": provider.id,
            "name": provider.name,
            "url": provider.url,
            "api_key": api_key,
            "server_auth_key": s_auth_key,
        }

    @staticmethod
    def get_pool_status(db: Session) -> Dict:
        from server.core.database import Provider

        providers = db.query(Provider).all()
        active = [p for p in providers if p.is_active and not p.is_banned]
        banned = [p for p in providers if p.is_banned]

        return {
            "total": len(providers),
            "active": len(active),
            "banned": len(banned),
            "providers": [
                {
                    "name": p.name,
                    "is_active": p.is_active,
                    "jobs_done": p.jobs_done,
                    "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                }
                for p in providers
                if not p.is_banned
            ],
        }

    @staticmethod
    def _update_daily_stats(db: Session, provider_id: int, minutes_to_add: float):
        from server.core.database import ProviderDailyStats
        from sqlalchemy.dialects.postgresql import insert

        today = datetime.now(timezone.utc).date()

        stmt = insert(ProviderDailyStats).values(
            provider_id=provider_id, date=today, total_presence_minutes=minutes_to_add
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=["provider_id", "date"],
            set_={
                "total_presence_minutes": ProviderDailyStats.total_presence_minutes
                + minutes_to_add
            },
        )

        try:
            db.execute(stmt)
            db.commit()
        except Exception as e:
            print(f"❌ Error updating daily stats for provider {provider_id}: {e}")
            db.rollback()
