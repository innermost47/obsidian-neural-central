import httpx
import asyncio
import hashlib
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session
from server.services.fal_service import FalService
from server.core.database import Provider
from server.config import settings
from server.core.security import decrypt_server_key

LLM_INFER_TIMEOUT = 120.0
PING_TIMEOUT = 5.0
COSINE_SIMILARITY_THRESHOLD = 0.75


class LLMConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str


class ProviderLLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response: str
    model: str
    embeddings: Dict[str, list[float]]
    provider_key: str


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x**2 for x in a))
    nb = math.sqrt(sum(x**2 for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _verify_embeddings(
    embeddings: Dict[str, list[float]],
) -> tuple[bool, str]:

    for key in ("system", "user", "response"):
        if key not in embeddings:
            return False, f"Missing embedding key: '{key}'"

    dims = {k: len(v) for k, v in embeddings.items()}
    unique_dims = set(dims.values())
    if len(unique_dims) > 1:
        return False, f"Inconsistent embedding dimensions: {dims}"

    sim = _cosine_similarity(embeddings["user"], embeddings["response"])
    if sim < COSINE_SIMILARITY_THRESHOLD:
        return False, (
            f"Semantic similarity too low between user_message and response: "
            f"{sim:.4f} < {COSINE_SIMILARITY_THRESHOLD}"
        )

    sim_sys_user = _cosine_similarity(embeddings["system"], embeddings["user"])
    if sim_sys_user > 0.999:
        return False, "system and user embeddings are identical — likely faked"

    return True, "ok"


class ProviderLLMService:

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

            from server.services.admin_notification_service import (
                AdminNotificationService,
            )

            AdminNotificationService.notify_provider_banned(
                provider.name, provider_id, reason
            )

    @staticmethod
    async def _ping_provider(url: str, encoded_server_auth_key: str) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
                response = await client.post(
                    f"{url.rstrip('/')}/process",
                    headers={
                        **settings.BROWSER_HEADERS,
                        "X-API-Key": decrypt_server_key(encoded_server_auth_key),
                    },
                    json={"action": "status"},
                )
                if response.status_code == 200:
                    data = response.json()
                    if (
                        not data.get("generating", False)
                        and not data.get("generating_llm", False)
                        and not data.get("generating_image", False)
                    ):
                        return data
        except Exception:
            pass
        return None

    @staticmethod
    async def _find_available_provider(db: Session) -> Optional[Dict]:
        providers = (
            db.query(Provider)
            .filter(
                Provider.is_active == True,
                Provider.is_banned == False,
                Provider.is_disposable == True,
                Provider.activation_token_used == True,
            )
            .all()
        )

        if not providers:
            print("📭 No active providers for LLM")
            return None

        import random

        random.shuffle(providers)
        print(f"🔍 Pinging {len(providers)} provider(s) for LLM...")

        ping_tasks = [
            ProviderLLMService._ping_provider(p.url, p.encoded_server_auth_key)
            for p in providers
        ]
        results = await asyncio.gather(*ping_tasks, return_exceptions=True)

        for provider, result in zip(providers, results):
            if isinstance(result, Exception) or not result:
                continue

            returned_key = result.get("api_key", "")
            key_hash = hashlib.sha256(returned_key.encode()).hexdigest()
            if key_hash != provider.api_key:
                print(f"⚠️ {provider.name} — invalid API key in status")
                ProviderLLMService._ban_provider(
                    db, provider.id, "Invalid API key returned in status response"
                )
                continue

            provider.last_ping = datetime.now(timezone.utc)
            db.commit()
            print(f"✅ LLM provider available: {provider.name}")
            return {
                "id": provider.id,
                "name": provider.name,
                "url": provider.url,
                "server_api_key": decrypt_server_key(provider.encoded_server_auth_key),
            }

        print("📭 No LLM provider available right now")
        return None

    @staticmethod
    async def _infer_at_provider(
        provider: Dict,
        system_prompt: str,
        history: list[LLMConversationMessage],
        user_message: str,
        image_base64: Optional[str],
        db: Session,
    ) -> Optional[Dict]:
        try:
            p = db.query(Provider).filter(Provider.id == provider["id"]).first()
            if p:
                p.is_generating_llm = True
                db.commit()

            async with httpx.AsyncClient(timeout=LLM_INFER_TIMEOUT) as client:
                response = await client.post(
                    f"{provider['url'].rstrip('/')}/process",
                    headers={
                        **settings.BROWSER_HEADERS,
                        "X-API-Key": provider["server_api_key"],
                    },
                    json={
                        "action": "llm_infer",
                        "system_prompt": system_prompt,
                        "history": [m.model_dump() for m in history],
                        "user_message": user_message,
                        "image_base64": image_base64,
                    },
                )

            if response.status_code != 200:
                print(
                    f"❌ Provider {provider['name']} LLM returned HTTP {response.status_code}"
                )
                return None

            try:
                data = response.json()
                llm_response = ProviderLLMResponse(**data)
            except (ValidationError, Exception) as e:
                print(f"🚫 {provider['name']} — invalid LLM response format: BAN")
                ProviderLLMService._ban_provider(
                    db, provider["id"], f"Invalid LLM response format: {e}"
                )
                return None

            if llm_response.model != "gemma4:e2b":
                print(
                    f"🚫 {provider['name']} — wrong model '{llm_response.model}': BAN"
                )
                ProviderLLMService._ban_provider(
                    db,
                    provider["id"],
                    f"Wrong LLM model: {llm_response.model} (expected gemma4:e2b)",
                )
                return None

            db_provider = (
                db.query(Provider).filter(Provider.id == provider["id"]).first()
            )
            key_hash = hashlib.sha256(llm_response.provider_key.encode()).hexdigest()
            if key_hash != db_provider.api_key:
                print(f"🚫 {provider['name']} — invalid key in LLM response: BAN")
                ProviderLLMService._ban_provider(
                    db, provider["id"], "Invalid API key in LLM response"
                )
                return None

            is_valid, reason = _verify_embeddings(
                llm_response.embeddings,
            )
            if not is_valid:
                print(
                    f"🚫 {provider['name']} — embedding verification failed: {reason}: BAN"
                )
                ProviderLLMService._ban_provider(
                    db, provider["id"], f"Embedding verification failed: {reason}"
                )
                return None

            if db_provider:
                db_provider.llm_jobs_done += 1
                db_provider.last_seen = datetime.now(timezone.utc)
                db.commit()

            print(f"✅ LLM infer successful via {provider['name']}")
            return {
                "success": True,
                "response": llm_response.response,
                "provider_name": provider["name"],
            }

        except httpx.TimeoutException:
            print(f"⏱️ Provider {provider['name']} LLM timeout")
            return None
        except Exception as e:
            print(f"❌ Provider {provider['name']} LLM error: {e}")
            return None
        finally:
            try:
                p = db.query(Provider).filter(Provider.id == provider["id"]).first()
                if p:
                    p.is_generating_llm = False
                    db.commit()
            except Exception:
                pass

    @staticmethod
    async def infer(
        system_prompt: str,
        history: list[LLMConversationMessage],
        user_message: str,
        image_base64: Optional[str],
        db: Session,
    ) -> Dict[str, Any]:

        provider = await ProviderLLMService._find_available_provider(db)

        if provider:
            result = await ProviderLLMService._infer_at_provider(
                provider, system_prompt, history, user_message, image_base64, db
            )
            if result and result["success"]:
                return result
            print("⚠️ Provider LLM failed, falling back to fal.ai...")

        print("🔄 Falling back to fal.ai for LLM...")
        return await ProviderLLMService._fallback_fal(
            system_prompt, history, user_message, image_base64
        )

    @staticmethod
    async def _fallback_fal(
        system_prompt: str,
        history: list[LLMConversationMessage],
        user_message: str,
        image_base64: Optional[str],
    ) -> Dict[str, Any]:
        try:
            if image_base64:
                result = await FalService.analyze_drawing_with_vlm(
                    image_base64=image_base64,
                    bpm=0,
                    scale="",
                    user_id=0,
                    db=None,
                )
                return {
                    "success": True,
                    "response": result,
                    "provider_name": "fal.ai (fallback)",
                }
            else:
                import fal_client

                conversation_text = f"SYSTEM: {system_prompt}\n\n"
                for msg in history:
                    conversation_text += f"{msg.role.upper()}: {msg.content}\n\n"
                conversation_text += f"USER: {user_message}"

                handle = await fal_client.submit_async(
                    "fal-ai/any-llm",
                    arguments={
                        "prompt": conversation_text,
                        "model": "google/gemini-2.5-flash",
                        "priority": "latency",
                        "max_tokens": 1000,
                        "temperature": 0.7,
                    },
                )
                result = await handle.get()
                return {
                    "success": True,
                    "response": result.get("output", ""),
                    "provider_name": "fal.ai (fallback)",
                }
        except Exception as e:
            print(f"❌ fal.ai LLM fallback error: {e}")
            return {
                "success": False,
                "error": str(e),
                "provider_name": "fal.ai (fallback)",
            }
