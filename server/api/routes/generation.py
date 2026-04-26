from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from server.api.models import GenerateRequest
from server.api.dependencies import get_user_from_api_key
from server.core.database import get_db, User
from server.core.audio import (
    stretch_audio_to_bpm,
    fetch_audio_bytes,
    audio_to_wav_bytes,
    build_response_headers,
    detect_bpm,
    load_audio_original,
    resample_audio,
)
from server.services.fal_service import FalService
from server.services.provider_service import ProviderService
from server.services.credits_service import CreditsService
from server.services.provider_llm_service import (
    ProviderLLMService,
)
from server.prompts import get_vision_system_prompt, get_system_prompt
import re
import base64
import asyncio
import random
import json


router = APIRouter(tags=["Generation"])

DEFAULT_MODEL = "stable-audio-open-1.0"


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


def _parse_llm_decision(decision: dict, request) -> dict:
    sample = decision.get("parameters", {}).get("sample_details", {})
    model = decision.get("model", DEFAULT_MODEL)
    prompt = sample.get("prompt", request.prompt)
    key = sample.get("key") or request.key
    bpm = sample.get("bpm") or request.bpm
    bars = sample.get("bars")
    duration = sample.get("duration")

    return {
        "model": model,
        "prompt": prompt,
        "key": key,
        "bpm": bpm,
        "bars": bars,
        "duration": duration,
    }


async def _resolve_prompt(request: GenerateRequest, current_user, db) -> dict:
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

IMPORTANT: These user-selected keywords MUST be incorporated and emphasized in your prompt."""

        user_message += "\n\nYour description must work within these constraints."

        result = await ProviderLLMService.infer(
            system_prompt=get_vision_system_prompt(
                forced_model=request.model, key=request.key, bpm=request.bpm
            ),
            history=[],
            user_message=user_message,
            image_base64=cleaned_base64,
            db=db,
        )

        if result["success"]:
            try:
                decision = json.loads(_extract_json(result["response"]))
                parsed = _parse_llm_decision(decision, request)
                FalService._save_message(
                    db,
                    current_user.id,
                    "user",
                    f"[Drawing analysis] BPM: {request.bpm}, Key: {request.key}",
                )
                FalService._save_message(
                    db, current_user.id, "assistant", result["response"]
                )
                print(
                    f"🎵 VLM prompt via {result['provider_name']} [{parsed['model']}]: {parsed['prompt']}"
                )
                return parsed
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️ JSON parse error: {e} — falling back to fal.ai...")
        else:
            print("⚠️ ProviderLLMService failed, falling back to fal.ai...")

        prompt = await FalService.analyze_drawing_with_vlm(
            image_base64=cleaned_base64,
            bpm=request.bpm,
            scale=request.key,
            user_id=current_user.id,
            db=db,
            keywords=request.keywords,
            forced_model=request.model,
            key=request.key,
        )
        print(f"🎵 VLM prompt via fal.ai fallback: {prompt}")
        return {
            "model": DEFAULT_MODEL,
            "prompt": prompt,
            "bpm": request.bpm,
            "key": request.key,
            "bars": None,
            "duration": int(request.generation_duration),
        }

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
        current_key = request.key if request.key else context.get("key", "C minor")

        llm_messages = []

        user_message = f"""⚠️ NEW USER PROMPT ⚠️
Keywords: {request.prompt}

Context:
- Tempo: {context.get('bpm', 126)} BPM
- Key: {current_key}

STRICT INSTRUCTIONS:
- Key field in JSON MUST BE: "{current_key}"
- Prompt field MUST NOT contain: "BPM", "{current_key}", or any scale names (Aeolian, Minor, etc.).
- Focus only on the requested sound: {request.prompt}"""

        result = await ProviderLLMService.infer(
            system_prompt=get_system_prompt(
                key=current_key, forced_model=request.model, bpm=request.bpm
            ),
            history=llm_messages,
            user_message=user_message,
            image_base64=None,
            db=db,
        )

        if result["success"]:
            try:
                decision = json.loads(_extract_json(result["response"]))
                parsed = _parse_llm_decision(decision, request)
                FalService._save_message(db, current_user.id, "user", user_message)
                FalService._save_message(
                    db, current_user.id, "assistant", result["response"]
                )
                print(
                    f"🎵 LLM prompt via {result['provider_name']} [{parsed['model']}]: {parsed['prompt']}"
                )
                return parsed
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️ JSON parse error: {e} — raw: {result['response'][:500]}")
        else:
            print("⚠️ ProviderLLMService failed, falling back to fal.ai...")

        prompt = await FalService.optimize_prompt_with_llm(
            request.prompt,
            context=context,
            user_id=current_user.id,
            db=db,
            key=request.key,
            forced_model=request.model,
            bpm=request.bpm,
        )
        return {
            "model": DEFAULT_MODEL,
            "prompt": prompt,
            "bpm": request.bpm,
            "key": request.key,
            "bars": None,
            "duration": int(request.generation_duration),
        }


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
        request.key = request.key.replace("Aeolian", "minor").replace("Ionian", "major")
        print(
            f"DEBUG 1 [VST IN]: Key='{request.key}', BPM={request.bpm}, Model='{request.model}'"
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

        resolved = await _resolve_prompt(request, current_user, db)
        print(
            f"DEBUG 2 [LLM OUT]: Resolved Prompt='{resolved.get('prompt')}', Resolved Key='{resolved.get('key')}'"
        )
        result = await ProviderService.generate_audio(
            prompt=resolved["prompt"],
            duration=resolved["duration"] or int(request.generation_duration),
            user_id=current_user.id,
            db=db,
            public_user_id=current_user.public_id,
            model=request.model,
            bpm=resolved["bpm"],
            bars=resolved["bars"],
            key=request.key,
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        print(
            f"🎛️  Generation via: {result.get('provider_name', 'unknown')} "
            f"[{resolved['model']}] "
            f"({'fallback' if result.get('used_fallback') else 'provider'})"
        )

        target_sr = (
            int(request.sample_rate) if hasattr(request, "sample_rate") else 44100
        )
        audio_data = await fetch_audio_bytes(result)
        audio, original_sr = await load_audio_original(audio_data)
        IMPRECISE_MODELS = [
            "stable-audio-open-1.0",
            "stablebeat",
            "sao-instrumental",
            "gluten-v1",
        ]
        if resolved["model"] in IMPRECISE_MODELS:
            detected_bpm = await detect_bpm(
                audio, original_sr, expected_bpm=float(resolved["bpm"])
            )
            if detected_bpm is not None:
                audio = stretch_audio_to_bpm(
                    audio, original_sr, detected_bpm, float(resolved["bpm"])
                )
            else:
                print(
                    f"⚠️ Skipping stretch for {resolved['model']}: "
                    f"BPM detection unreliable, audio used as-is"
                )
        else:
            snapped_bpm = result.get("snapped_bpm")
            detected_bpm = float(snapped_bpm) if snapped_bpm else None
            if snapped_bpm and int(snapped_bpm) != int(resolved["bpm"]):
                audio = stretch_audio_to_bpm(
                    audio, original_sr, float(snapped_bpm), float(resolved["bpm"])
                )

        target_samples = int(round(float(request.generation_duration) * original_sr))
        if audio.ndim == 2:
            if audio.shape[1] > target_samples:
                audio = audio[:, :target_samples]
        else:
            if len(audio) > target_samples:
                audio = audio[:target_samples]

        audio = resample_audio(audio, original_sr, target_sr)

        wav_bytes, duration = audio_to_wav_bytes(audio, target_sr)

        generation_details = {
            "prompt": request.prompt,
            "model": resolved["model"],
            "bpm": resolved["bpm"],
            "key": resolved["key"],
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

        print(f"✅ Audio généré: {duration:.1f}s @ {target_sr}Hz [{resolved['model']}]")

        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers=build_response_headers(
                duration=duration,
                request_bpm=resolved["bpm"],
                detected_bpm=detected_bpm,
                key=resolved["key"],
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
