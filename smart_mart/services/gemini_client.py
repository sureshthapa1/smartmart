"""
gemini_client.py
================
Shared Google Gemini AI client for SmartMart.

Replaces all Anthropic/Claude usage with Google Gemini.
API key is read from the GEMINI_API_KEY environment variable.

Usage
-----
    from .gemini_client import gemini_generate, gemini_available

    if gemini_available():
        reply = gemini_generate("Your prompt here", max_tokens=200)
        # reply is a plain string or None on failure

Security
--------
- API key is never logged or included in error messages.
- All failures are caught and logged at DEBUG level only.
- Returns None gracefully so callers can fall back to keyword engines.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# Gemini REST endpoint (no SDK dependency — uses standard urllib)
_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_DEFAULT_MODEL   = "gemini-flash-lite-latest"   # fast + free tier friendly
_VISION_MODEL    = "gemini-flash-lite-latest"   # supports vision/image inputs


def _api_key() -> str:
    """Return the Gemini API key from environment. Never log the value."""
    return os.environ.get("GEMINI_API_KEY", "")


def gemini_available() -> bool:
    """Return True if GEMINI_API_KEY is set in the environment."""
    return bool(_api_key())


def gemini_generate(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 400,
    model: str = _DEFAULT_MODEL,
    history: list[dict] | None = None,
    temperature: float = 0.7,
) -> Optional[str]:
    """
    Generate text using the Gemini REST API.

    Args:
        prompt:     The user's message / instruction.
        system:     Optional system instruction for the model.
        max_tokens: Maximum output tokens.
        model:      Gemini model name (default: gemini-2.0-flash).
        history:    Optional conversation history as
                    [{"role": "user"|"model", "parts": [{"text": "..."}]}]
        temperature: Sampling temperature (0.0–1.0).

    Returns:
        Generated text string, or None if the call fails.
    """
    key = _api_key()
    if not key:
        logger.debug("gemini_generate: GEMINI_API_KEY not set — skipping")
        return None

    url = f"{_GEMINI_API_BASE}/{model}:generateContent?key={key}"

    # Build contents list — Gemini requires alternating user/model turns
    contents: list[dict] = []

    # Prepend conversation history if provided
    if history:
        for turn in history[-6:]:          # cap to last 6 turns
            role = turn.get("role", "user")
            if role not in ("user", "model"):
                continue
            parts = turn.get("parts") or [{"text": turn.get("content", "")}]
            contents.append({"role": role, "parts": parts})

    # Add the current user prompt
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    # System instruction (separate field in Gemini API)
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())

        # Extract text from Gemini response structure
        candidates = result.get("candidates", [])
        if not candidates:
            logger.debug("gemini_generate: empty candidates in response")
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return None
        return parts[0].get("text", "").strip() or None

    except urllib.error.HTTPError as exc:
        # Log status code but NOT the key
        logger.warning("gemini_generate: HTTP %s from Gemini API", exc.code)
        return None
    except Exception as exc:
        logger.debug("gemini_generate failed: %s", exc)
        return None


def gemini_vision(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    max_tokens: int = 300,
    model: str = _VISION_MODEL,
) -> Optional[str]:
    """
    Analyse an image using Gemini's multimodal vision capability.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WEBP, GIF).
        mime_type:   e.g. "image/jpeg", "image/png".
        prompt:      Text instruction for the model.
        max_tokens:  Maximum output tokens.
        model:       Gemini model (must support vision).

    Returns:
        Generated text string, or None on failure.
    """
    import base64

    key = _api_key()
    if not key:
        logger.debug("gemini_vision: GEMINI_API_KEY not set — skipping")
        return None

    url = f"{_GEMINI_API_BASE}/{model}:generateContent?key={key}"

    img_b64 = base64.b64encode(image_bytes).decode()
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": img_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }

    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())

        candidates = result.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "").strip() if parts else None

    except urllib.error.HTTPError as exc:
        logger.warning("gemini_vision: HTTP %s from Gemini API", exc.code)
        return None
    except Exception as exc:
        logger.debug("gemini_vision failed: %s", exc)
        return None
