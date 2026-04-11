from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from server.api.models import GenerateRequest
from server.api.dependencies import get_user_from_api_key
from server.core.database import get_db, User
from server.core.audio import (
    applicate_lite_fade_in_fade_out,
    stretch_audio_to_bpm,
    fetch_audio_bytes,
    audio_to_wav_bytes,
    build_response_headers,
    detect_bpm,
    load_and_resample,
)
from server.services.fal_service import FalService
from server.services.provider_service import ProviderService
from server.services.credits_service import CreditsService
from server.services.provider_llm_service import (
    ProviderLLMService,
    LLMConversationMessage,
)
from server.prompts import MUSICAL_VISION_SYSTEM_PROMPT, get_system_prompt
import re
import base64
import asyncio
import random
import json


router = APIRouter(tags=["Generation"])


def clean_base64(base64_string: str) -> str:
    cleaned = re.sub(r"\s+", "", base64_string)
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[1]
    missing_padding = len(cleaned) % 4
    if missing_padding:
        cleaned += "=" * (4 - missing_padding)
    return cleaned


def _extract_json(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return match.group(1)
    return text.strip()


async def _resolve_prompt(request, current_user, db) -> str:
    context = {"bpm": request.bpm, "key": request.key}

    if request.use_image and request.image_base64:
        print("🎨 Image-to-audio mode")
        cleaned_base64 = clean_base64(request.image_base64)
        try:
            image_bytes = base64.b64decode(cleaned_base64, validate=True)
        except Exception:
            image_bytes = base64.b64decode(cleaned_base64, validate=False)
        if not image_bytes.startswith(b"\x89PNG"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_IMAGE",
                    "message": "Image must be in PNG format",
                },
            )
        if request.keywords:
            print(f"🏷️  Keywords: {', '.join(request.keywords)}")

        user_message = f"""Translate this image into a sonic/musical description.

CONTEXT:
- Tempo: {request.bpm} BPM
- Key: {request.key}"""

        if request.keywords:
            keywords_str = ", ".join(request.keywords)
            user_message += f"""
- Additional keywords: {keywords_str}

IMPORTANT: These user-selected keywords MUST be incorporated and emphasized in your musicgen_prompt."""

        user_message += "\n\nYour description must work within these constraints."

        result = await ProviderLLMService.infer(
            system_prompt=MUSICAL_VISION_SYSTEM_PROMPT,
            history=[],
            user_message=user_message,
            image_base64=cleaned_base64,
            db=db,
        )

        if result["success"]:
            response_text = _extract_json(result["response"])
            try:
                sonic_json = json.loads(response_text)
                base_prompt = sonic_json["parameters"]["sample_details"][
                    "musicgen_prompt"
                ]
                prompt = f"{request.bpm} BPM {base_prompt} {request.key}"
                print(f"🎵 VLM prompt via {result['provider_name']}: {prompt}")
                FalService._save_message(
                    db,
                    current_user.id,
                    "user",
                    f"[Drawing analysis] BPM: {request.bpm}, Key: {request.key}",
                )
                FalService._save_message(
                    db, current_user.id, "assistant", result["response"]
                )
                return prompt
            except (json.JSONDecodeError, KeyError):
                print(
                    "⚠️ JSON parse error on provider response, falling back to fal.ai..."
                )
        else:
            print("⚠️ ProviderLLMService failed, falling back to fal.ai...")

        prompt = await FalService.analyze_drawing_with_vlm(
            image_base64=cleaned_base64,
            bpm=request.bpm,
            scale=request.key,
            user_id=current_user.id,
            db=db,
            keywords=request.keywords,
        )
        print(f"🎵 VLM prompt via fal.ai fallback: {prompt}")
        return prompt

    elif request.use_image and not request.image_base64:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_REQUEST",
                "message": "use_image=true but no image_base64 provided",
            },
        )

    else:
        print("📝 Text-to-audio mode")

        history = FalService._get_conversation_history(db, current_user.id)
        llm_messages = [
            LLMConversationMessage(role=m["role"], content=m["content"])
            for m in history
            if m["role"] != "system"
        ]

        user_message = f"""⚠️ NEW USER PROMPT ⚠️
Keywords: {request.prompt}

Context:
- Tempo: {context.get('bpm', 126)} BPM
- Key: {context.get('key', 'C minor')}

IMPORTANT: This new prompt has PRIORITY. If it's different from your previous generation, ABANDON the previous style completely and focus on this new prompt."""

        result = await ProviderLLMService.infer(
            system_prompt=get_system_prompt(),
            history=llm_messages,
            user_message=user_message,
            image_base64=None,
            db=db,
        )

        if result["success"]:
            try:
                response_text = _extract_json(result["response"])
                decision = json.loads(response_text)
                optimized_prompt = (
                    decision.get("parameters", {})
                    .get("sample_details", {})
                    .get("musicgen_prompt", request.prompt)
                )
                FalService._save_message(db, current_user.id, "user", user_message)
                FalService._save_message(
                    db, current_user.id, "assistant", result["response"]
                )
                prompt = f"{request.bpm} BPM {optimized_prompt} {request.key}"
                print(f"🎵 LLM prompt via {result['provider_name']}: {prompt}")
                return prompt
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️ JSON parse error: {e} — raw: {result['response'][:500]}")
        else:
            print("⚠️ ProviderLLMService failed, falling back to fal.ai...")

        return await FalService.optimize_prompt_with_llm(
            request.prompt,
            context=context,
            user_id=current_user.id,
            db=db,
        )


@router.post("/generate")
async def generate_audio(
    request: GenerateRequest,
    current_user: User = Depends(get_user_from_api_key),
    db: Session = Depends(get_db),
):
    try:
        if not request.use_image and (
            not request.prompt or request.prompt.strip() == ""
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_REQUEST",
                    "message": "Prompt is required for text-to-audio generation",
                },
            )

        credits_needed = 1
        remaining_after = 0
        if not current_user.is_admin:
            remaining = CreditsService.get_user_credits(db, current_user.id)
            if remaining < credits_needed:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "CREDITS_EXHAUSTED",
                        "message": f"Not enough credits. Need {credits_needed}, have {remaining}",
                    },
                )

        final_prompt = await _resolve_prompt(request, current_user, db)

        result = await ProviderService.generate_audio(
            prompt=final_prompt,
            duration=int(request.generation_duration),
            user_id=current_user.id,
            db=db,
            public_user_id=current_user.public_id,
        )
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        print(
            f"🎛️  Generation via: {result.get('provider_name', 'unknown')} "
            f"({'fallback' if result.get('used_fallback') else 'provider'})"
        )

        target_sr = (
            int(request.sample_rate) if hasattr(request, "sample_rate") else 44100
        )
        audio_data = await fetch_audio_bytes(result)
        audio, sr = await load_and_resample(audio_data, target_sr)
        audio = applicate_lite_fade_in_fade_out(audio, sr)
        detected_bpm = await detect_bpm(audio, sr)
        audio = stretch_audio_to_bpm(audio, sr, detected_bpm, float(request.bpm))
        wav_bytes, duration = audio_to_wav_bytes(audio, sr)

        generation_details = {
            "prompt": request.prompt,
            "bpm": request.bpm,
            "duration": request.generation_duration,
        }
        if not current_user.is_admin:
            CreditsService.consume_credits(
                db,
                current_user.id,
                credits_needed,
                generation_details=generation_details,
            )
            remaining_after = CreditsService.get_user_credits(db, current_user.id)
        else:
            CreditsService.create_generation(
                db=db,
                user_id=current_user.id,
                generation_details=generation_details,
                credits_cost=0,
                status="completed",
                commit=True,
            )

        print(f"✅ Audio généré: {duration:.1f}s @ {target_sr}Hz")

        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers=build_response_headers(
                duration=duration,
                request_bpm=request.bpm,
                detected_bpm=detected_bpm,
                key=request.key,
                remaining_after=remaining_after,
                credits_needed=credits_needed,
                target_sr=target_sr,
                provider_name=result.get("provider_name", "unknown"),
                used_fallback=result.get("used_fallback", False),
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "SERVER_ERROR",
                "message": f"Audio generation failed: {str(e)}",
            },
        )


@router.post("/generate/test")
async def generate_audio_test():
    from server.core.concurrency import EXTERNAL_API_SEMAPHORE

    async with EXTERNAL_API_SEMAPHORE:
        await asyncio.sleep(random.uniform(1.0, 2.0))

    async with EXTERNAL_API_SEMAPHORE:
        await asyncio.sleep(random.uniform(5.0, 10.0))

    return {
        "status": "success",
        "message": "Test generation completed",
        "simulated_duration": 10.0,
        "credits_used": 0,
    }
