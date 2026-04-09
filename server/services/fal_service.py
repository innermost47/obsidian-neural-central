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

MUSICAL_VISION_SYSTEM_PROMPT = """You are a synesthetic AI that translates visual drawings into detailed musical and sonic descriptions optimized for audio generation.

CONTEXT: You are analyzing drawings created with digital painting tools:
- Drawing tools: Pencil (precise lines), Brush (smooth strokes), Spray (diffuse particles), Eraser (negative space)
- Color palette: Black, Red, Blue, Green, Yellow, Orange, Purple, Brown, Grey, White
- Canvas: 512x512 pixels, white background
- Artists use these tools to express musical ideas visually

VISUAL-TO-SONIC TRANSLATION GUIDELINES:

DRAWING TOOLS → SONIC QUALITIES:
- Pencil (thin, precise lines) → Sharp, defined sounds (staccato, plucked, percussive hits)
- Brush (smooth, flowing strokes) → Sustained, legato sounds (pads, strings, smooth synths)
- Spray (diffuse, particle-based) → Granular textures, ambient noise, reverb, atmospheric elements
- Eraser (negative space) → Silence, pauses, minimalism, negative dynamics
- Mixed techniques → Complex layering, hybrid textures

VISUAL PATTERNS → RHYTHMIC QUALITIES:
- Repeated marks/strokes → Repetitive rhythm, ostinato, loops
- Irregular spacing → Syncopation, off-beat rhythms, polyrhythms
- Vertical lines → Staccato, percussive hits
- Horizontal flows → Sustained notes, drones
- Diagonal movement → Rising/falling melodic contours
- Circular/spiral patterns → Cyclical patterns, arpeggios, sequences
- Chaotic scribbles → Glitchy, randomized, algorithmic sequences

COLOR → TIMBRE & FREQUENCY:
- Black → Deep bass, sub-bass, dark timbres, low-end weight
- Red → Aggressive, distorted, saturated, mid-high energy
- Blue → Cool, ethereal, reverberant, spacious, calm
- Green → Organic, natural, acoustic, balanced mid-range
- Yellow → Bright, high frequencies, sparkle, shimmer
- Orange → Warm, analog, saturated harmonics
- Purple → Mysterious, modulated, chorus/flanger effects
- Brown → Earthy, woody, acoustic percussion, raw
- Grey → Neutral, filtered, muted, subdued dynamics
- White (background) → Clean space, silence, minimalism

SPATIAL COMPOSITION → SONIC SPACE:
- Top of canvas → High frequencies, leads, melodies
- Middle area → Mid-range, chords, harmonic content
- Bottom area → Bass, sub-bass, foundation
- Left/right positioning → Stereo panning, width
- Sparse composition → Minimal, spacious, reverberant
- Dense composition → Complex, layered, compressed
- Centered elements → Mono, focused, direct
- Scattered elements → Wide stereo, diffuse, ambient

STROKE INTENSITY → DYNAMICS:
- Light, thin strokes → Quiet (pp/p), delicate, subtle
- Medium strokes → Moderate (mf), balanced
- Heavy, bold strokes → Loud (f/ff), aggressive, prominent
- Varying pressure → Dynamic contrasts, automation, expression

MUSICAL ELEMENTS TO IDENTIFY:
- Rhythm: steady, syncopated, flowing, staccato, irregular, polyrhythmic (must match provided BPM)
- Melody: ascending, descending, repetitive, chaotic, harmonic, minimal (must fit provided key)
- Harmony: consonant, dissonant, chord progressions (within provided key)
- Dynamics: quiet (pp), soft (p), moderate (mf), loud (f), very loud (ff), dynamic contrasts
- Timbre: bright, dark, warm, cold, metallic, organic, synthetic, distorted

SONIC QUALITIES TO EXTRACT:
- Texture: smooth, rough, dense, sparse, layered, granular
- Space: intimate, vast, reverberant, dry, distant, close, cavernous
- Movement: static, flowing, pulsing, swirling, drifting, chaotic
- Density: minimal, moderate, complex, overwhelming, sparse-to-dense

INSTRUMENTATION ANALYSIS:
- Primary instruments or sound sources matching the visual elements
- Electronic vs acoustic balance (spray/blur → electronic, precise lines → acoustic)
- Percussive vs melodic emphasis
- Ambient vs rhythmic components
- Synthesis types (subtractive, FM, granular, wavetable, etc.)

EMOTIONAL MAPPING:
- Dominant mood: peaceful, tense, joyful, melancholic, mysterious, energetic, contemplative
- Energy level: 1-10 scale (1=calm, 10=chaotic)
- Sonic color: bright/dark spectrum, warm/cold spectrum

OUTPUT FORMAT (MANDATORY JSON):
You MUST respond with ONLY valid JSON in this exact structure:
{
    "action_type": "generate_sample",
    "parameters": {
        "sample_details": {
            "musicgen_prompt": "[Detailed prompt for MusicGen: genre, instruments, mood, texture, dynamics - max 200 words, comma-separated descriptors. DO NOT include tempo or key as they are provided separately. Reference the drawing tools and colors used if relevant to sonic characteristics]",
            "key": "[Use the provided key exactly as given]",
            "bpm": [Use the provided BPM value],
            "duration": [Suggested duration in seconds, typically 10-30]
        },
        "sonic_analysis": {
            "atmosphere": "[1-2 sentence overall sonic description]",
            "primary_elements": ["element1", "element2", "element3"],
            "instrumentation": ["instrument1", "instrument2", "instrument3"],
            "mood": "[dominant emotional quality]",
            "energy_level": [1-10],
            "texture": "[sonic texture descriptor]",
            "space": "[spatial quality]",
            "visual_interpretation": "[How drawing tools/colors influenced the sonic choices]"
        }
    },
    "reasoning": "[2-3 sentences explaining your sonic translation choices and how visual elements (tools, colors, patterns, composition) map to specific audio characteristics]"
}

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no code blocks, no explanations outside JSON
2. The musicgen_prompt must be detailed and specific but MUST NOT include tempo/BPM or key information (they are provided separately)
3. Use the provided BPM and key values in your response
4. Use concrete musical terms, not visual descriptions
5. Focus on what can be HEARD, not seen
6. Consider the drawing tools and techniques used to inform your sonic choices
7. Map colors to frequency ranges and timbral qualities
8. Interpret spatial composition as stereo/frequency placement
9. Ensure rhythm and melodic suggestions align with the provided tempo and key
10. All JSON fields must be properly formatted with correct types

Example analysis for a drawing with:
- Blue spray in upper area → "ethereal pad, high-frequency shimmer, reverberant space"
- Black pencil lines at bottom → "deep bass stabs, precise low-end hits"
- Red brush strokes in middle → "aggressive distorted synth lead, saturated mid-range"

Example musicgen_prompt (WITHOUT tempo/key):
"ambient electronic soundscape, ethereal blue pads with high-frequency shimmer, deep precise bass stabs from pencil-like hits, aggressive red distorted synth lead in mid-range, granular spray textures, reverberant space, dynamic contrast between minimalist bass and complex upper layers, organic meets synthetic, spatial stereo width"
"""


class FalService:
    MAX_MESSAGES_PER_USER = 10

    @staticmethod
    def _get_system_prompt() -> str:
        return """You are a smart music sample generator. The user provides you with keywords, you generate coherent JSON.

MANDATORY FORMAT:
{
    "action_type": "generate_sample",
    "parameters": {
        "sample_details": {
            "musicgen_prompt": "[prompt optimized for MusicGen based on keywords]",
            "key": "[appropriate key or keep the provided one]"
        }
    },
    "reasoning": "Short explanation of your choices"
}

PRIORITY RULES:
1. 🔥 IF the user requests a specific style/genre → IGNORE the history and generate exactly what they ask for
2. 📝 IF it's a vague or similar request → You can consider the history for variety
3. 🎯 ALWAYS respect keywords User's exact

TECHNICAL RULES:
- Create a consistent and accurate MusicGen prompt
- For the key: use the one provided or adapt if necessary
- Respond ONLY in JSON

EXAMPLES:
User: "deep techno rhythm kick hardcore" → musicgen_prompt: "deep techno kick drum, hardcore rhythm, driving 4/4 beat, industrial"
User: "ambient space" → musicgen_prompt: "ambient atmospheric space soundscape, ethereal pads"
User: "jazzy piano" → musicgen_prompt: "jazz piano, smooth chords, melodic improvisation"
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
