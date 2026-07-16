"""AI Feature 7: Voice Assistant

Backend processes voice-transcribed text.
Uses Gemini API when GEMINI_API_KEY is set, falls back to keyword engine.
Frontend uses Web Speech API (browser built-in) — no external dependencies needed.
"""

from __future__ import annotations
import re


def _gemini_voice_reply(transcript: str) -> str | None:
    """Get a voice-optimised reply from Gemini API. Returns None on failure."""
    from .gemini_client import gemini_generate, gemini_available
    if not gemini_available():
        return None
    try:
        # Build live data context using keyword engine for grounding
        from .ai_engine import chatbot_query
        kw_reply = chatbot_query(transcript)

        system = (
            "You are Goldkernel's voice assistant for a Nepal retail shop. "
            "Answer in 1-2 SHORT sentences only — the reply will be read aloud. "
            "Use simple words. No markdown, no bullet points, no emojis. "
            "Currency is NPR (Nepali Rupees). "
            "Use this live business data to answer: " + kw_reply[:400]
        )
        return gemini_generate(transcript, system=system, max_tokens=150)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Voice Gemini API failed: %s", exc)
        return None


def process_voice_command(transcript: str) -> dict:
    """Process a voice command and return a voice-optimised response."""
    if not transcript or not transcript.strip():
        return {
            "transcript": transcript,
            "reply": "I didn't catch that. Please try again.",
            "tts_text": "I didn't catch that. Please try again.",
            "source": "none",
        }

    # Try Gemini API first (short, voice-optimised reply)
    reply = _gemini_voice_reply(transcript)
    source = "gemini_api"

    if not reply:
        # Fallback: keyword engine (always works, no API key needed)
        from .ai_engine import chatbot_query
        reply = chatbot_query(transcript)
        source = "keyword_engine"

    # Clean for TTS: strip emojis, markdown symbols, and excess whitespace
    tts_text = re.sub(r"[*_`#>\[\]()]", "", reply)
    tts_text = re.sub(r"[^\w\s.,!?:NPR%\-]", "", tts_text)
    tts_text = re.sub(r"\s+", " ", tts_text).strip()

    return {
        "transcript": transcript,
        "reply": reply,
        "tts_text": tts_text,
        "source": source,
    }
