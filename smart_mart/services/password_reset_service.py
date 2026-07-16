"""Password reset service — generates and validates time-limited, single-use reset tokens.

No email required. Tokens are printed to the server log so the server operator
can relay them to the user. This is appropriate for a single-shop deployment
where the admin has server/log access.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from ..extensions import db
from ..models.user import User

# Token valid for 30 minutes
_TOKEN_TTL = 1800
_SEP = "."

# In-memory store of consumed tokens — prevents replay within the TTL window.
# Key: token signature, Value: consumed_at timestamp
# Pruned automatically when new tokens are verified to prevent unbounded growth.
_consumed_tokens: dict[str, float] = {}


def _secret() -> bytes:
    key = os.environ.get("SECRET_KEY")
    if not key:
        # In production SECRET_KEY must always be set. Falling back to a
        # hardcoded value makes password-reset tokens forgeable — raise so
        # misconfigured deployments fail loudly rather than silently insecure.
        if not os.environ.get("FLASK_DEBUG") and not os.environ.get("TESTING"):
            raise RuntimeError(
                "SECRET_KEY environment variable is not set. "
                "Password reset tokens cannot be signed securely."
            )
        key = "dev-secret-key"  # only reached in local dev / test
    return key.encode()


def _prune_consumed() -> None:
    """Remove expired entries from the consumed-tokens store."""
    cutoff = time.time() - _TOKEN_TTL
    expired = [sig for sig, ts in _consumed_tokens.items() if ts < cutoff]
    for sig in expired:
        del _consumed_tokens[sig]


def generate_reset_token(user_id: int) -> str:
    """Return a signed token: <user_id>.<timestamp>.<signature>"""
    ts = int(time.time())
    payload = f"{user_id}{_SEP}{ts}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}{_SEP}{sig}"


def verify_reset_token(token: str) -> User | None:
    """Return the User if the token is valid, not expired, and not already used.

    Single-use enforcement: once a token is verified successfully, its signature
    is recorded so subsequent verification attempts with the same token fail,
    even if they arrive within the 30-minute TTL window.
    """
    try:
        _prune_consumed()

        parts = token.split(_SEP)
        if len(parts) != 3:
            return None
        user_id, ts_str, sig = parts
        payload = f"{user_id}{_SEP}{ts_str}"

        # Timing-safe signature check
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None

        # Expiry check
        if int(time.time()) - int(ts_str) > _TOKEN_TTL:
            return None

        # Single-use check — reject if this token has already been consumed
        if sig in _consumed_tokens:
            return None

        # Mark as consumed BEFORE returning — prevents TOCTOU race
        _consumed_tokens[sig] = time.time()

        return db.session.get(User, int(user_id))
    except Exception:
        return None


# ── Customer password reset (phone-based, cross-browser) ─────────────────────

def generate_customer_reset_token(phone: str) -> str:
    """Return a signed token for customer phone-based password reset.
    Format: <phone_b64>.<timestamp>.<signature>
    Works cross-browser (unlike session-based tokens).
    """
    import base64 as _b64
    ts = int(time.time())
    phone_b64 = _b64.urlsafe_b64encode(phone.encode()).decode().rstrip("=")
    payload = f"{phone_b64}{_SEP}{ts}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}{_SEP}{sig}"


def verify_customer_reset_token(phone: str, token: str) -> bool:
    """Verify a customer reset token. Returns True if valid, not expired, not used.
    Single-use: consumed set prevents replay within the 30-minute window.
    """
    try:
        import base64 as _b64
        _prune_consumed()
        parts = token.split(_SEP)
        if len(parts) != 3:
            return False
        phone_b64, ts_str, sig = parts
        payload = f"{phone_b64}{_SEP}{ts_str}"

        # Verify signature
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False

        # Verify phone matches token
        decoded_phone = _b64.urlsafe_b64decode(phone_b64 + "==").decode()
        if decoded_phone != phone:
            return False

        # Check expiry
        if int(time.time()) - int(ts_str) > _TOKEN_TTL:
            return False

        # Single-use check
        if sig in _consumed_tokens:
            return False

        # Mark consumed
        _consumed_tokens[sig] = time.time()
        return True
    except Exception:
        return False
