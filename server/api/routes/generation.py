from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from server.api.models import GenerateRequest
from server.api.dependencies import get_user_from_api_key
from server.core.database import get_db, User
from server.core.audio import (
    fetch_audio_bytes,
    audio_to_wav_bytes,
    build_response_headers,
    load_audio_original,
    resample_audio,
)
from server.services.provider_service import ProviderService
from server.services.credits_service import CreditsService
import asyncio
import random

router = APIRouter(tags=["Generation"])


@router.post("/generate")
async def generate_audio(
    request: GenerateRequest,
    current_user: User = Depends(get_user_from_api_key),
    db: Session = Depends(get_db),
):
    try:
        if not request.prompt or request.prompt.strip() == "":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_REQUEST",
                    "message": "Prompt is required for text-to-audio generation",
                },
            )
        request.key = request.key.replace("Aeolian", "minor").replace("Ionian", "major")
        credits_needed = 1
        remaining_after = 0
        if not (current_user.is_admin or current_user.is_provider):
            remaining = CreditsService.get_user_credits(db, current_user.id)
            if remaining < credits_needed:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "CREDITS_EXHAUSTED",
                        "message": f"Not enough credits. Need {credits_needed}, have {remaining}",
                    },
                )

        resolved = {
            "model": request.model,
            "prompt": request.prompt,
            "bpm": request.bpm,
            "key": request.key,
            "bars": None,
            "duration": int(request.generation_duration),
        }
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
        snapped_bpm = result.get("snapped_bpm")

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
                snapped_bpm=snapped_bpm,
                request_bpm=resolved["bpm"],
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
