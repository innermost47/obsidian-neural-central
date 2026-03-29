from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from server.api.models import GenerateRequest
from server.api.dependencies import get_user_from_api_key
from server.core.database import get_db, User
from server.core.audio import applicate_lite_fade_in_fade_out
from server.services.fal_service import FalService
from server.services.provider_service import ProviderService
from server.services.credits_service import CreditsService
import httpx
import librosa
import soundfile as sf
import os
import tempfile
import re
import base64
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
import numpy as np

router = APIRouter(tags=["Generation"])

executor = ThreadPoolExecutor(max_workers=4)


def clean_base64(base64_string: str) -> str:
    cleaned = re.sub(r"\s+", "", base64_string)
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[1]
    missing_padding = len(cleaned) % 4
    if missing_padding:
        cleaned += "=" * (4 - missing_padding)
    return cleaned


def sanitize_header(value: str) -> str:
    return value.encode("latin-1", errors="replace").decode("latin-1")


@router.post("/generate")
async def generate_audio(
    request: GenerateRequest,
    current_user: User = Depends(get_user_from_api_key),
    db: Session = Depends(get_db),
):
    try:
        temp_file = None
        credits_needed = 1
        remaining = 0
        remaining_after = 0
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

        context = {"bpm": request.bpm, "key": request.key}

        if request.use_image and request.image_base64:
            print(f"🎨 Image-to-audio mode activated")

            cleaned_base64 = clean_base64(request.image_base64)
            print(f"✅ Cleaned base64 length: {len(cleaned_base64)} chars")

            try:
                image_bytes = base64.b64decode(cleaned_base64, validate=True)
            except Exception as e:
                print(f"⚠️  Standard decode failed: {e}, retrying...")
                image_bytes = base64.b64decode(cleaned_base64, validate=False)

            print(f"✅ Image decoded: {len(image_bytes)} bytes")

            if not image_bytes.startswith(b"\x89PNG"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "INVALID_IMAGE",
                        "message": "Image must be in PNG format",
                    },
                )

            print(f"🔍 Analyzing image with VLM (BPM={request.bpm}, Key={request.key})")

            if request.keywords and len(request.keywords) > 0:
                print(f"🏷️  User keywords: {', '.join(request.keywords)}")

            final_prompt = await FalService.analyze_drawing_with_vlm(
                image_base64=cleaned_base64,
                bpm=request.bpm,
                scale=request.key,
                user_id=current_user.id,
                db=db,
                keywords=request.keywords,
            )

            print(f"🎵 VLM generated prompt: {final_prompt}")

        elif request.use_image and not request.image_base64:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_REQUEST",
                    "message": "use_image=true but no image_base64 provided",
                },
            )
        else:
            print(f"📝 Text-to-audio mode")
            final_prompt = await FalService.optimize_prompt_with_llm(
                request.prompt,
                context=context,
                user_id=current_user.id,
                db=db,
            )

        result = await ProviderService.generate_audio(
            prompt=final_prompt,
            duration=int(request.generation_duration),
            user_id=current_user.id,
            db=db,
            public_user_id=current_user.public_id,
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        if "wav_bytes" in result:
            audio_data = result["wav_bytes"]
        else:
            audio_url = result["audio_url"]
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(audio_url)
                response.raise_for_status()
                audio_data = response.content

        print(
            f"🎛️  Generation via: {result.get('provider_name', 'unknown')} "
            f"({'fallback' if result.get('used_fallback') else 'provider'})"
        )

        target_sr = (
            int(request.sample_rate) if hasattr(request, "sample_rate") else 44100
        )

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_file.write(audio_data)
        temp_file.close()

        audio, sr_original = librosa.load(temp_file.name, sr=None, mono=False)

        print(f"📊 Original sample rate: {sr_original}Hz, Target: {target_sr}Hz")
        print(
            f"🎵 Audio shape: {audio.shape} ({'stereo' if audio.ndim == 2 else 'mono'})"
        )

        if sr_original != target_sr:
            print(f"🔄 Resampling from {sr_original}Hz to {target_sr}Hz...")
            if audio.ndim == 2:
                audio = np.array(
                    [
                        librosa.resample(
                            audio[0], orig_sr=sr_original, target_sr=target_sr
                        ),
                        librosa.resample(
                            audio[1], orig_sr=sr_original, target_sr=target_sr
                        ),
                    ]
                )
            else:
                audio = librosa.resample(
                    audio, orig_sr=sr_original, target_sr=target_sr
                )
            sr = target_sr
        else:
            print(f"✅ No resampling needed, already at {target_sr}Hz")
            sr = sr_original

        audio = applicate_lite_fade_in_fade_out(audio, sr)

        loop = asyncio.get_event_loop()
        if audio.ndim == 2:
            audio_mono = librosa.to_mono(audio)
            bpm_task = loop.run_in_executor(
                executor, lambda: librosa.beat.beat_track(y=audio_mono, sr=sr)
            )
        else:
            bpm_task = loop.run_in_executor(
                executor, lambda: librosa.beat.beat_track(y=audio, sr=sr)
            )

        output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        if audio.ndim == 2:
            sf.write(output_file.name, audio.T, target_sr)
        else:
            sf.write(output_file.name, audio, target_sr)
        output_file.close()

        if audio.ndim == 2:
            duration = audio.shape[1] / target_sr
        else:
            duration = len(audio) / target_sr

        with open(output_file.name, "rb") as f:
            wav_data = f.read()

        detected_bpm = None
        try:
            tempo, _ = await bpm_task
            detected_bpm = float(tempo)
            print(
                f"🎯 BPM détecté par librosa: {detected_bpm:.2f} BPM (attendu: {request.bpm} BPM)"
            )
        except Exception as e:
            print(f"⚠️  Échec détection BPM: {e}")

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

        os.remove(temp_file.name)
        os.remove(output_file.name)

        headers = {
            "X-Duration": str(duration),
            "X-BPM": str(request.bpm),
            "X-Detected-BPM": str(detected_bpm) if detected_bpm else "",
            "X-Key": sanitize_header(str(request.key or "")),
            "X-Credits-Remaining": str(remaining_after),
            "X-Credits-Used": str(credits_needed),
            "X-Sample-Rate": str(target_sr),
            "X-Provider": sanitize_header(result.get("provider_name", "unknown")),
            "X-Used-Fallback": str(result.get("used_fallback", False)),
        }

        print(f"✅ Audio generated: {duration:.1f}s @ {target_sr}Hz")

        return Response(
            content=wav_data,
            media_type="audio/wav",
            headers=headers,
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
    finally:
        if temp_file and os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except:
                pass


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
