"""Simple in-process TTL cache for dashboard/report endpoints.

No external dependencies — uses a plain dict with timestamps.

IMPORTANT — Multi-worker limitation:
  This cache lives in each worker process's memory. With Gunicorn --workers > 1
  each worker has its own independent cache, so a cache invalidation in one worker
  is NOT seen by other workers. This means stale data can be served for up to
  ttl seconds after an update when running multiple workers.

  To fix for production:
    1. Switch to Redis: pip install flask-caching redis
       and replace this module with Flask-Caching (CACHE_TYPE="redis").
    2. Or keep --workers 1 (single-process) with --threads N for concurrency.
       The current Render start command uses --workers 2; reduce to 1 if
       cache consistency matters more than CPU parallelism.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_store: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _make_key(prefix: str, *args, **kwargs) -> str:
    raw = json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=str)
    digest = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"


def get(key: str) -> Any | None:
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del _store[key]
            return None
        return value


def set(key: str, value: Any, ttl: int = 60) -> None:
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def delete(key: str) -> None:
    with _lock:
        _store.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    """Remove all keys that start with *prefix*."""
    with _lock:
        to_delete = [k for k in _store if k.startswith(prefix)]
        for k in to_delete:
            del _store[k]


def cached(prefix: str, ttl: int = 120):
    """Decorator: cache the return value of a function for *ttl* seconds."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(prefix, *args, **kwargs)
            hit = get(key)
            if hit is not None:
                logger.debug("Cache HIT %s", key)
                return hit
            result = fn(*args, **kwargs)
            set(key, result, ttl)
            logger.debug("Cache SET %s (ttl=%ds)", key, ttl)
            return result
        return wrapper
    return decorator
