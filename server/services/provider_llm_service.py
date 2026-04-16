import httpx
import hashlib
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import ValidationError
from sqlalchemy.orm import Session
from server.services.fal_service import FalService
from server.core.database import Provider
from server.api.models import LLMConversationMessage, ProviderLLMResponse
from server.config import settings
from server.services.provider_service import ProviderService
import ollama

LLM_INFER_TIMEOUT = 120.0
PING_TIMEOUT = 5.0
COSINE_SIMILARITY_THRESHOLD = 0.60


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x**2 for x in a))
    nb = math.sqrt(sum(x**2 for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _verify_echo(
    sent_system_prompt: str,
    sent_history: list[LLMConversationMessage],
    sent_user_message: str,
    received: ProviderLLMResponse,
) -> tuple[bool, str]:

    if received.system_prompt != sent_system_prompt:
        print(f"❌ [echo] system_prompt mismatch")
        return False, "system_prompt echo mismatch"
    print(f"✅ [echo] system_prompt OK")

    if received.user_message != sent_user_message:
        print(f"❌ [echo] user_message mismatch")
        return False, "user_message echo mismatch"
    print(f"✅ [echo] user_message OK")

    if len(received.history) != len(sent_history):
        print(
            f"❌ [echo] history length mismatch: sent {len(sent_history)}, got {len(received.history)}"
        )
        return (
            False,
            f"history length mismatch: sent {len(sent_history)}, got {len(received.history)}",
        )
    print(f"✅ [echo] history length OK ({len(sent_history)} messages)")

    for i, (sent, received_msg) in enumerate(zip(sent_history, received.history)):
        if sent.role != received_msg.role or sent.content != received_msg.content:
            print(f"❌ [echo] history[{i}] mismatch")
            return False, f"history message {i} mismatch"
    print(f"✅ [echo] history content OK")

    return True, "ok"


async def _get_nomic_embedding(text: str) -> list[float]:
    client = ollama.AsyncClient()
    response = await client.embeddings(
        model="nomic-embed-text",
        prompt=text,
    )
    return response.embedding


class ProviderLLMService:

    _ban_provider = ProviderService._ban_provider

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

            is_valid, reason = _verify_echo(
                system_prompt, history, user_message, llm_response
            )
            if not is_valid:
                ProviderLLMService._ban_provider(
                    db, provider["id"], f"Echo mismatch: {reason}"
                )
                return None
            try:
                nomic_user = await _get_nomic_embedding(user_message)
                nomic_response = await _get_nomic_embedding(llm_response.response)
                sim = _cosine_similarity(nomic_user, nomic_response)
                if sim < COSINE_SIMILARITY_THRESHOLD:
                    print(
                        f"⚠️ [semantic] Low similarity: {sim:.4f} < {COSINE_SIMILARITY_THRESHOLD} — warning only, no ban"
                    )
                    try:
                        from server.core.database import ProviderSemanticWarning

                        warning = ProviderSemanticWarning(
                            provider_id=provider["id"],
                            user_message=user_message[:2000],
                            llm_response=llm_response.response[:2000],
                            similarity_score=round(sim, 6),
                            threshold=COSINE_SIMILARITY_THRESHOLD,
                        )
                        db.add(warning)
                        db.commit()
                    except Exception as db_err:
                        print(f"⚠️ [semantic] Failed to save warning: {db_err}")
                        db.rollback()
                else:
                    print(f"✅ [semantic] similarity: {sim:.4f}")
            except Exception as e:
                print(f"⚠️ [semantic] nomic check failed (skipping): {e}")
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

        provider = await ProviderService._find_available_provider(db)

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
                    system_prompt=system_prompt,
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
