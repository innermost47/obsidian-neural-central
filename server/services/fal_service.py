import fal_client
from typing import Dict, Any
from server.config import settings
import os
from server.core.concurrency import EXTERNAL_API_SEMAPHORE

os.environ["FAL_KEY"] = settings.FAL_KEY


class FalService:
    MAX_MESSAGES_PER_USER = 10

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
                    print(
                        f"⚠️ Warning: No audio URL in response. Full result: {result}"
                    )
                    return {"success": False, "error": "No audio URL in response"}

                return {
                    "success": True,
                    "audio_url": audio_url,
                    "data": result,
                }
            except Exception as e:
                print(f"❌ Audio generation error: {e}")
                return {"success": False, "error": str(e)}
