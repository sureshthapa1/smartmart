"""Password reset service — generates and validates time-limited reset tokens.

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


def _secret() -> bytes:
    return os.environ.get("SECRET_KEY", "dev-secret-key").encode()


def generate_reset_token(user_id: int) -> str:
    """Return a signed token: <user_id>.<timestamp>.<signature>"""
    ts = int(time.time())
    payload = f"{user_id}{_SEP}{ts}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}{_SEP}{sig}"


def verify_reset_token(token: str) -> User | None:
    """Return the User if the token is valid and not expired, else None."""
    try:
        parts = token.split(_SEP)
        if len(parts) != 3:
            return None
        user_id, ts_str, sig = parts
        payload = f"{user_id}{_SEP}{ts_str}"
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(time.time()) - int(ts_str) > _TOKEN_TTL:
            return None
        return db.session.get(User, int(user_id))
    except Exception:
        return None
