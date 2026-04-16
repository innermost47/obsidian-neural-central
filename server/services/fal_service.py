import fal_client
from typing import Dict, Any, List
from server.config import settings
from sqlalchemy.orm import Session
from sqlalchemy import desc
import os
import json
import re
from server.core.concurrency import EXTERNAL_API_SEMAPHORE
from server.prompts import MUSICAL_VISION_SYSTEM_PROMPT, get_system_prompt

os.environ["FAL_KEY"] = settings.FAL_KEY


class FalService:
    MAX_MESSAGES_PER_USER = 10

    @staticmethod
    def _save_message(db: Session, user_id: int, role: str, content: str):
        from server.core.database import ConversationMessage

        new_message = ConversationMessage(user_id=user_id, role=role, content=content)
        db.add(new_message)
        db.flush()

        message_count = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.user_id == user_id,
                ConversationMessage.role != "system",
            )
            .count()
        )

        if message_count > FalService.MAX_MESSAGES_PER_USER:
            messages_to_delete = message_count - FalService.MAX_MESSAGES_PER_USER

            old_messages = (
                db.query(ConversationMessage.id)
                .filter(
                    ConversationMessage.user_id == user_id,
                    ConversationMessage.role != "system",
                )
                .order_by(ConversationMessage.created_at)
                .limit(messages_to_delete)
                .all()
            )

            old_ids = [msg.id for msg in old_messages]

            db.query(ConversationMessage).filter(
                ConversationMessage.id.in_(old_ids)
            ).delete(synchronize_session=False)

        db.commit()

    @staticmethod
    async def optimize_prompt_with_llm(
        user_prompt: str,
        context: Dict,
        user_id: int,
        db: Session,
        key: str,
        forced_model: str,
        bpm: float,
    ) -> str:
        async with EXTERNAL_API_SEMAPHORE:
            try:
                history = [
                    {
                        "role": "system",
                        "content": get_system_prompt(
                            key=key, forced_model=forced_model, bpm=bpm
                        ),
                    }
                ]

                user_message = f"""⚠️ NEW USER PROMPT ⚠️
Keywords: {user_prompt}

Context:
- Tempo: {context.get('bpm', 126)} BPM
- Key: {context.get('key', 'C minor')}

IMPORTANT: This new prompt has PRIORITY. If it's different from your previous generation, ABANDON the previous style completely and focus on this new prompt."""

                FalService._save_message(db, user_id, "user", user_message)

                history.append({"role": "user", "content": user_message})

                print(f"🧠 LLM with {len(history)} history messages...")

                conversation_text = "\n\n".join(
                    [f"{msg['role'].upper()}: {msg['content']}" for msg in history]
                )

                handle = await fal_client.submit_async(
                    "fal-ai/any-llm",
                    arguments={
                        "prompt": conversation_text,
                        "model": "google/gemini-2.5-flash",
                        "priority": "latency",
                        "max_tokens": 512,
                        "temperature": 0.7,
                    },
                )
                result = await handle.get()
                result_text = result.get("output", "")

                print("✅ LLM generation complete!")

                try:
                    json_match = re.search(r"({.*})", result_text, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                        decision = json.loads(json_str)
                        optimized_prompt = (
                            decision.get("parameters", {})
                            .get("sample_details", {})
                            .get("musicgen_prompt", user_prompt)
                        )
                    else:
                        optimized_prompt = user_prompt
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"⚠️ JSON parse error: {e}")
                    optimized_prompt = user_prompt

                FalService._save_message(db, user_id, "assistant", result_text)

                return f"{context.get('bpm', 126)} BPM {optimized_prompt} {context.get('key', 'C minor')}"

            except Exception as e:
                print(f"❌ LLM error: {e}")
                return f"{context.get('bpm', 126)} BPM {user_prompt} {context.get('key', 'C minor')}"

    @staticmethod
    async def generate_audio(prompt: str, duration: int) -> Dict[str, Any]:
        async with EXTERNAL_API_SEMAPHORE:
            try:
                handle = await fal_client.submit_async(
                    "fal-ai/stable-audio",
                    arguments={
                        "prompt": prompt,
                        "seconds_total": duration,
                        "steps": 50,
                    },
                )
                result = await handle.get()

                audio_file: dict = result.get("audio_file", {})
                audio_url = audio_file.get("url")

                if not audio_url:
                    print(f"⚠️ Warning: No audio URL in response. Full result: {result}")
                    return {"success": False, "error": "No audio URL in response"}

                return {
                    "success": True,
                    "audio_url": audio_url,
                    "data": result,
                }
            except Exception as e:
                print(f"❌ Audio generation error: {e}")
                return {"success": False, "error": str(e)}

    @staticmethod
    async def analyze_drawing_with_vlm(
        image_base64: str,
        bpm: int,
        scale: str,
        user_id: int,
        db: Session,
        keywords=None,
    ) -> str:
        async with EXTERNAL_API_SEMAPHORE:
            try:
                system_prompt = MUSICAL_VISION_SYSTEM_PROMPT

                user_message = f"""Translate this image into a sonic/musical description.

CONTEXT:
- Tempo: {bpm} BPM
- Key: {scale}"""

                if keywords and len(keywords) > 0:
                    keywords_str = ", ".join(keywords)
                    user_message += f"""
- Additional keywords: {keywords_str}

IMPORTANT: These user-selected keywords MUST be incorporated and emphasized in your musicgen_prompt. They represent the desired sonic direction alongside the visual interpretation."""

                user_message += """

Your description must work within these constraints."""
                image_data_uri = f"data:image/png;base64,{image_base64}"
                handle = await fal_client.submit_async(
                    "openrouter/router/vision",
                    arguments={
                        "image_urls": [image_data_uri],
                        "prompt": user_message,
                        "system_prompt": system_prompt,
                        "model": "google/gemini-2.5-flash",
                        "max_tokens": 1000,
                        "temperature": 0.7,
                        "reasoning": False,
                    },
                )

                result = await handle.get()
                response_text = result.get("output", "")

                response_text = response_text.strip()
                if response_text.startswith("```json"):
                    response_text = response_text.split("```json")[1].split("```")[0]

                sonic_json = json.loads(response_text)

                base_prompt = sonic_json["parameters"]["sample_details"][
                    "musicgen_prompt"
                ]
                final_prompt = f"{bpm} BPM {base_prompt} {scale}"

                FalService._save_message(
                    db, user_id, "user", f"[Drawing analysis] BPM: {bpm}, Key: {scale}"
                )
                FalService._save_message(db, user_id, "assistant", response_text)

                return final_prompt

            except Exception as e:
                print(f"❌ VLM analysis error: {e}")
                return f"{bpm} BPM abstract musical texture {scale}"
