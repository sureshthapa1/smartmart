"""Production readiness check.

Validates all required env vars, security settings, DB connectivity,
admin account, cron config, and notification provider.

Usage:
    python -m smart_mart.services.prod_check
    # or from within app context:
    from smart_mart.services.prod_check import run_checks
    results = run_checks(app)
"""
from __future__ import annotations

import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Check definitions ─────────────────────────────────────────────────────────

def _check(name: str, passed: bool, detail: str, critical: bool = True) -> dict:
    return {"name": name, "passed": passed, "detail": detail, "critical": critical}


def run_checks(app) -> dict[str, Any]:
    """Run all production readiness checks. Returns a report dict."""
    checks = []

    with app.app_context():
        # ── 1. SECRET_KEY ─────────────────────────────────────────────────
        secret = os.environ.get("SECRET_KEY", "")
        checks.append(_check(
            "SECRET_KEY",
            bool(secret) and secret != "dev" and len(secret) >= 24,
            f"Length: {len(secret)} chars" if secret else "NOT SET",
        ))

        # ── 2. DATABASE_URL ───────────────────────────────────────────────
        db_url = os.environ.get("DATABASE_URL", "")
        is_postgres = db_url.startswith("postgresql") or db_url.startswith("postgres")
        checks.append(_check(
            "DATABASE_URL",
            bool(db_url) and is_postgres,
            "PostgreSQL configured" if is_postgres else (
                "SQLite (dev only)" if "sqlite" in db_url else "NOT SET"
            ),
        ))

        # ── 3. DB connectivity ────────────────────────────────────────────
        try:
            from smart_mart.extensions import db
            db.session.execute(db.text("SELECT 1"))
            checks.append(_check("DB connectivity", True, "Connected successfully"))
        except Exception as e:
            checks.append(_check("DB connectivity", False, str(e)[:100]))

        # ── 4. ADMIN_PASSWORD ─────────────────────────────────────────────
        admin_pw = os.environ.get("ADMIN_PASSWORD", "")
        checks.append(_check(
            "ADMIN_PASSWORD",
            bool(admin_pw) and len(admin_pw) >= 8,
            f"Length: {len(admin_pw)} chars" if admin_pw else "NOT SET",
        ))

        # ── 5. Admin user exists in DB ────────────────────────────────────
        try:
            from smart_mart.models.user import User
            admin = db.session.execute(
                db.select(User).where(User.role == "admin", User.is_active == True)
            ).scalar_one_or_none()
            checks.append(_check(
                "Admin user in DB",
                admin is not None,
                f"Username: {admin.username}" if admin else "No active admin found",
            ))
        except Exception as e:
            checks.append(_check("Admin user in DB", False, str(e)[:100]))

        # ── 6. BOT_SECRET ─────────────────────────────────────────────────
        bot_secret = os.environ.get("BOT_SECRET", "")
        checks.append(_check(
            "BOT_SECRET",
            bool(bot_secret) and len(bot_secret) >= 16,
            f"Length: {len(bot_secret)} chars" if bot_secret else "NOT SET",
            critical=False,
        ))

        # ── 7. APP_URL (for cron) ─────────────────────────────────────────
        app_url = os.environ.get("APP_URL", "")
        checks.append(_check(
            "APP_URL",
            bool(app_url) and app_url.startswith("https://"),
            app_url or "NOT SET",
            critical=False,
        ))

        # ── 8. Notification provider ──────────────────────────────────────
        try:
            from smart_mart.services.notification_service import validate_provider_config
            prov = validate_provider_config()
            checks.append(_check(
                "Notification provider",
                prov["configured"],
                prov["warning"] or f"Provider: {prov['provider']}",
                critical=False,
            ))
        except Exception as e:
            checks.append(_check("Notification provider", False, str(e)[:100], critical=False))

        # ── 9. FLASK_ENV = production ─────────────────────────────────────
        flask_env = os.environ.get("FLASK_ENV", "development")
        checks.append(_check(
            "FLASK_ENV",
            flask_env == "production",
            f"FLASK_ENV={flask_env}",
        ))

        # ── 10. DEBUG = False ─────────────────────────────────────────────
        checks.append(_check(
            "DEBUG disabled",
            not app.debug,
            "DEBUG=False" if not app.debug else "DEBUG=True (UNSAFE for production)",
        ))

        # ── 11. Backup plan — at least one backup log ─────────────────────
        try:
            from smart_mart.models.backup_log import BackupLog
            backup_count = db.session.execute(
                db.select(db.func.count(BackupLog.id))
            ).scalar() or 0
            checks.append(_check(
                "Backup history",
                backup_count > 0,
                f"{backup_count} backup(s) on record",
                critical=False,
            ))
        except Exception as e:
            checks.append(_check("Backup history", False, str(e)[:100], critical=False))

        # ── 12. Cron job configured (render.yaml APP_URL) ─────────────────
        checks.append(_check(
            "Cron APP_URL set",
            bool(app_url),
            "Required for daily bot (expiry, reminders, alerts)",
            critical=False,
        ))

    # Summary
    critical_failures = [c for c in checks if not c["passed"] and c["critical"]]
    warnings = [c for c in checks if not c["passed"] and not c["critical"]]
    passed = [c for c in checks if c["passed"]]

    return {
        "checks": checks,
        "passed": len(passed),
        "critical_failures": len(critical_failures),
        "warnings": len(warnings),
        "total": len(checks),
        "production_ready": len(critical_failures) == 0,
    }


def print_checks(app) -> None:
    """Print a human-readable production readiness report."""
    report = run_checks(app)
    print("\n" + "=" * 60)
    print("PRODUCTION READINESS CHECK")
    print("=" * 60)
    for c in report["checks"]:
        icon = "OK" if c["passed"] else ("FAIL" if c["critical"] else "WARN")
        print(f"  [{icon:4s}] {c['name']}: {c['detail']}")
    print("-" * 60)
    print(f"  Passed: {report['passed']}/{report['total']}")
    if report["critical_failures"]:
        print(f"  CRITICAL FAILURES: {report['critical_failures']} — NOT production ready")
    elif report["warnings"]:
        print(f"  Warnings: {report['warnings']} — review before deploying")
    else:
        print("  All checks passed — production ready!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from smart_mart.app import create_app
    _app = create_app(os.environ.get("FLASK_ENV", "production"))
    print_checks(_app)
    report = run_checks(_app)
    sys.exit(0 if report["production_ready"] else 1)
