"""Deployment environment validation helpers."""

from __future__ import annotations

from collections.abc import Mapping


REQUIRED_PRODUCTION_VARS = ("SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD")
RECOMMENDED_PRODUCTION_VARS = ("BOT_SECRET", "APP_URL")


def validate_environment(env: Mapping[str, str], production: bool = True) -> dict:
    """Return missing/weak environment configuration without exposing secrets."""
    required = REQUIRED_PRODUCTION_VARS if production else ()
    missing = [key for key in required if not env.get(key)]
    warnings = []

    secret = env.get("SECRET_KEY", "")
    if production and secret and len(secret) < 32:
        warnings.append("SECRET_KEY should be at least 32 characters.")
    if production and env.get("DATABASE_URL", "").startswith("sqlite"):
        warnings.append("Production DATABASE_URL should use PostgreSQL, not SQLite.")
    for key in RECOMMENDED_PRODUCTION_VARS:
        if production and not env.get(key):
            warnings.append(f"{key} is recommended for cron/bot automation.")

    return {
        "ok": not missing,
        "missing": missing,
        "warnings": warnings,
    }
