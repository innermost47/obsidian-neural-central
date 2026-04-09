import fal_client
from typing import Dict, Any, List
from server.config import settings
from sqlalchemy.orm import Session
from sqlalchemy import desc
import os
import json
import re
from server.core.concurrency import EXTERNAL_API_SEMAPHORE

os.environ["FAL_KEY"] = settings.FAL_KEY

MUSICAL_VISION_SYSTEM_PROMPT = """You are a synesthetic AI that translates visual drawings into detailed musical and sonic descriptions optimized for audio generation using the Foundation-1 model.

[CORE CONTEXT]
- Analyzing 512x512 digital drawings (Pencil, Brush, Spray, Eraser).
- 10-color palette. 
- Goal: Generate a professional sample description that matches the visual intent.

[FOUNDATION-1 TECHNICAL DICTIONARY]
*You MUST integrate these specific terms when the visual context allows:*
- FAMILIES: Synth, Keys, Bass, Bowed Strings, Mallet, Wind, Guitar, Brass, Vocal, Plucked Strings.
- SUB-FAMILIES: Synth Lead, Synth Bass, Digital Piano, Pluck, Grand Piano, Bell, Pad, Atmosphere, Digital Strings, FM Synth, Violin, Digital Organ, Supersaw, Wavetable Bass, Rhodes Piano, Cello, Texture, Flute, Reese Bass, Wavetable Synth, Electric Bass, Marimba, Trumpet, Pan Flute, Choir, Harp, Church Organ, Acoustic Guitar, Hammond Organ, Celesta, Vibraphone, Glockenspiel, Ocarina, Clarinet, French Horn, Tuba, Oboe.
- TIMBRE TAGS (Use for Color mapping): Warm, Bright, Wide, Airy, Thick, Rich, Tight, Full, Gritty, Clean, Retro, Saw, Crisp, Focused, Metallic, Chiptune, Dark, 303, Shiny, Analog, Present, Sparkly, Ambient, Soft, Smooth, Cold, Buzzy, Deep, Formant Vocal, Round, Punchy, Nasal, Vintage, Growl, Breathy, Glassy, Noisy, Synthetic Vox, Supersaw, Bitcrushed, Dreamy.
- FX TAGS: Low/Medium/High Reverb, Plate Reverb, Low/Medium/High Delay, Ping Pong Delay, Stereo Delay, Cross Delay, Low/Medium/High Distortion, Phaser, Bitcrush.
- STRUCTURE TAGS: chord progression, melody, top melody, arp, triplets, simple, complex, rising, falling, strummed, sustained, catchy, epic, slow, fast.

[VISUAL-TO-SONIC TRANSLATION GUIDELINES]

DRAWING TOOLS → SONIC QUALITIES:
- Pencil (thin, precise) → Sharp, defined sounds (staccato, plucked, percussive hits, Tight, Pluck).
- Brush (smooth strokes) → Sustained, legato sounds (pads, strings, smooth synths, Chord progression).
- Spray (diffuse) → Granular textures, ambient noise, Reverb, Atmospheric, Texture.
- Eraser → Silence, minimalism, negative dynamics.

VISUAL PATTERNS → RHYTHMIC QUALITIES:
- Repeated marks → Repetitive rhythm, ostinato, loops, Arp.
- Vertical lines → Staccato, percussive hits.
- Horizontal flows → Sustained, drones, Chord progression.
- Circular/Spiral → Arpeggios, sequences, Cyclical.

COLOR → TIMBRE & FREQUENCY (FOUNDATION-1 MAPPING):
- Black → Deep bass, sub-bass, Dark, Growl, Reese Bass.
- Red → Aggressive, Distorted, Saturated, Buzzy, Saw, Supersaw.
- Blue → Ethereal, Wide, Cold, Airy, Dreamy, Ambient.
- Green → Organic, Natural, Acoustic, Clean, Woody.
- Yellow/Orange → Bright, Sparkly, Analog, Warm, Shiny, Present.
- Purple → Mysterious, Modulated, Phaser, Formant Vocal.

[OUTPUT FORMAT (MANDATORY JSON)]
Response must be ONLY valid JSON. 

{
    "action_type": "generate_sample",
    "parameters": {
        "sample_details": {
            "musicgen_prompt": "[Foundation-1 Style Prompt: Start with Sub-family, then 2-3 Timbres, then Notation, then FX. Detailed but tag-focused. No BPM/Key here]",
            "key": "[Provided Key]",
            "bpm": [Provided BPM],
            "duration": [10-30],
            "bars": "4 Bars"
        },
        "sonic_analysis": {
            "atmosphere": "[1-2 sentence description]",
            "primary_elements": ["List of sub-families used"],
            "instrumentation": ["List of instruments"],
            "mood": "[Dominant emotion]",
            "energy_level": [1-10],
            "texture": "[Descriptor]",
            "space": "[Spatial quality]",
            "visual_interpretation": "[Explain how tools/colors became these specific tags]"
        }
    },
    "reasoning": "[Link visual elements to sonic translation choices]"
}

CRITICAL RULES:
1. DO NOT include tempo/BPM or key in the 'musicgen_prompt'.
2. Prioritize Foundation-1 tags (Sub-family, Timbre, FX) for the final prompt string.
3. Maintain the synesthetic connection (visual intensity = dynamic intensity).
4. Output ONLY the JSON.
"""


class FalService:
    MAX_MESSAGES_PER_USER = 10

    @staticmethod
    def _get_system_prompt() -> str:
        return """You are a smart music sample generator optimized for the Foundation-1 audio model.
The user provides keywords, and you translate them into a structured production prompt.

MANDATORY FOUNDATION-1 STRUCTURE:
Your 'musicgen_prompt' MUST follow this hierarchy:
[Sub-Family], [1-3 Timbre Tags], [Musical Notation/Structure], [FX Tags]

TECHNICAL DICTIONARY:
- SUB-FAMILIES: Synth Lead, Synth Bass, Digital Piano, Pluck, Grand Piano, Bell, Pad, Atmosphere, Digital Strings, FM Synth, Violin, Digital Organ, Supersaw, Wavetable Bass, Rhodes Piano, Cello, Texture, Flute, Reese Bass, Wavetable Synth, Electric Bass, Marimba, Trumpet, Pan Flute, Choir, Harp, Church Organ, Acoustic Guitar, Hammond Organ, Celesta, Vibraphone, Glockenspiel, Ocarina, Clarinet, French Horn, Tuba, Oboe.
- TIMBRES: Warm, Bright, Wide, Airy, Thick, Rich, Tight, Full, Gritty, Clean, Retro, Saw, Crisp, Focused, Metallic, Chiptune, Dark, 303, Shiny, Analog, Present, Sparkly, Ambient, Soft, Smooth, Cold, Buzzy, Deep, Formant Vocal, Round, Punchy, Nasal, Vintage, Growl, Breathy, Glassy, Noisy, Synthetic Vox, Supersaw, Bitcrushed, Dreamy.
- NOTATION: chord progression, melody, top melody, arp, triplets, simple, complex, rising, falling, strummed, sustained, catchy, epic, slow, fast.
- FX: Low/Medium/High Reverb, Plate Reverb, Low/Medium/High Delay, Ping Pong Delay, Stereo Delay, Low/Medium/High Distortion, Phaser, Bitcrush.

MANDATORY JSON FORMAT:
{
    "action_type": "generate_sample",
    "parameters": {
        "sample_details": {
            "musicgen_prompt": "[Structured tags: Sub-Family, Timbres, Notation, FX]",
            "key": "[provided key]",
            "bpm": [provided bpm],
            "bars": "4 Bars"
        }
    },
    "reasoning": "Brief technical explanation of tag choices"
}

PRIORITY RULES:
1. 🔥 Specific Style: If user asks for a genre, map it to the closest Sub-Family + Timbre (ex: "Acid" -> "303, Gritty").
2. 🎯 Exact Keywords: Respect the user's intent but translate it into Foundation-1 tags.
3. 🚫 No BPM/Key in prompt: Never put "120 BPM" or "C Major" inside the musicgen_prompt string.

EXAMPLES:
User: "deep techno reese bass" -> musicgen_prompt: "Reese Bass, Deep, Dark, Sustained, Low Distortion"
User: "angelic voices space" -> musicgen_prompt: "Choir, Airy, Dreamy, Melody, High Reverb"
User: "8bit lead fast" -> musicgen_prompt: "Synth Lead, Chiptune, Crisp, Arp, Fast, Low Delay"
"""

    @staticmethod
    def _get_conversation_history(db: Session, user_id: int) -> List[Dict[str, str]]:
        from server.core.database import ConversationMessage

        messages = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.user_id == user_id,
                ConversationMessage.role != "system",
            )
            .order_by(desc(ConversationMessage.created_at))
            .limit(FalService.MAX_MESSAGES_PER_USER)
            .all()
        )

        messages.reverse()

        history = [{"role": "system", "content": FalService._get_system_prompt()}]

        for msg in messages:
            history.append({"role": msg.role, "content": msg.content})

        return history

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
        user_prompt: str, context: Dict, user_id: int, db: Session
    ) -> str:
        async with EXTERNAL_API_SEMAPHORE:
            try:
                history = FalService._get_conversation_history(db, user_id)

                user_message = f"""⚠️ NEW USER REQUEST - FOUNDATION-1 TARGET ⚠️
Keywords: {user_prompt}

Technical Context (MUST be used in JSON fields):
- Project Tempo: {context.get('bpm', 126)} BPM
- Project Key: {context.get('key', 'C minor')}

INSTRUCTIONS:
1. Translate the Keywords into the [Sub-Family, Timbres, Notation, FX] hierarchy.
2. Ensure the "key" and "bpm" fields in the JSON match the Technical Context exactly.
3. If the Keywords imply a specific rhythm (e.g., "triplets", "fast"), ensure the Notation tag reflects this.
4. ABSOLUTE PRIORITY: Abandon any previous style. Generate a fresh sample based ONLY on these keywords.

Respond with the MANDATORY JSON format only."""

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

                user_message = f"""Translate this image into a professional Foundation-1 sonic description.

FOUNDATION-1 CONTEXT:
- Tempo: {bpm} BPM (Must be integrated as '{bpm} BPM')
- Key: {scale} (Must match exactly)
- Loop Target: 4 Bars"""

                if keywords and len(keywords) > 0:
                    keywords_str = ", ".join(keywords)
                    user_message += f"""
- Core Sonic Intent: {keywords_str}

IMPORTANT: These keywords are your primary anchors. Use them to select the correct 'Sub-Family' and 'Timbre' tags from your system prompt, then use the drawing's textures to fill in the 'Notation' and 'FX' tags."""

                user_message += """
INSTRUCTION: 
1. Map visual intensity to 'Dynamics' and 'Timbre'.
2. Map visual patterns to 'Notation' (arp, triplets, sustained).
3. Combine everything into the mandatory JSON structure with a tag-rich 'musicgen_prompt'."""

                image_data_uri = f"data:image/png;base64,{image_base64}"
                handle = await fal_client.submit_async(
                    "openrouter/router/vision",
                    arguments={
                        "image_urls": [image_data_uri],
                        "prompt": user_message,
                        "system_prompt": system_prompt,
                        "model": "google/gemini-2.5-flash",
                        "max_tokens": 1000,
                        "temperature": 0.5,
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
