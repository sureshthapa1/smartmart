"""AI Feature 7: Voice Assistant

Backend processes voice-transcribed text using the existing chatbot engine.
Frontend uses Web Speech API (browser built-in) for speech-to-text and text-to-speech.
No external API needed.
"""

from .ai_engine import chatbot_query


def process_voice_command(transcript: str) -> dict:
    """Process a voice command transcript and return response with TTS text."""
    if not transcript or not transcript.strip():
        return {
            "transcript": transcript,
            "reply": "I didn't catch that. Please try again.",
            "tts_text": "I didn't catch that. Please try again.",
        }

    reply = chatbot_query(transcript)

    # Clean reply for TTS (remove emojis and markdown)
    import re
    tts_text = re.sub(r'[^\w\s\.,!?:NPR%\-]', '', reply)
    tts_text = re.sub(r'\s+', ' ', tts_text).strip()

    return {
        "transcript": transcript,
        "reply": reply,
        "tts_text": tts_text,
    }
