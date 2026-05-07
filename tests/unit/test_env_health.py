import pytest

from smart_mart.app import create_app
from smart_mart.services.env_health import validate_environment


def test_validate_environment_requires_production_secrets():
    report = validate_environment({}, production=True)

    assert report["ok"] is False
    assert report["missing"] == ["SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD"]


def test_validate_environment_accepts_complete_production_env():
    report = validate_environment(
        {
            "SECRET_KEY": "x" * 64,
            "DATABASE_URL": "postgresql://user:pass@host/db",
            "ADMIN_PASSWORD": "strong-password",
            "BOT_SECRET": "bot-secret",
            "APP_URL": "https://smart-mart.example.com",
        },
        production=True,
    )

    assert report == {"ok": True, "missing": [], "warnings": []}


def test_create_app_calls_production_config_validation(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app("production")
