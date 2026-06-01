"""AI Feature 7: Voice Assistant

Backend processes voice-transcribed text.
Uses Claude API when ANTHROPIC_API_KEY is set, falls back to keyword engine.
Frontend uses Web Speech API (browser built-in) — no external dependencies needed.
"""

from __future__ import annotations
import os
import re


def _claude_voice_reply(transcript: str) -> str | None:
    """Get a voice-optimised reply from Claude API. Returns None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request, json
        # Build live data context using keyword engine for grounding
        from .ai_engine import chatbot_query
        kw_reply = chatbot_query(transcript)

        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 150,
            "system": (
                "You are Smart Mart's voice assistant for a Nepal retail shop. "
                "Answer in 1-2 SHORT sentences only — the reply will be read aloud. "
                "Use simple words. No markdown, no bullet points, no emojis. "
                "Currency is NPR (Nepali Rupees). "
                "Use this live business data to answer: " + kw_reply[:400]
            ),
            "messages": [{"role": "user", "content": transcript}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read())
        return result["content"][0]["text"].strip()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Voice Claude API failed: %s", exc)
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

    # Try Claude API first (short, voice-optimised reply)
    reply = _claude_voice_reply(transcript)
    source = "claude_api"

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
