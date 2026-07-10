import os
from datetime import timedelta


def _fix_db_url(url: str | None) -> str | None:
    """Render gives postgres:// but SQLAlchemy 2.x needs postgresql://"""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-secret-key-NOT-for-production"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024   # 16 MB max upload size — blocks DoS via huge files
    # CSRF tokens expire after 24 hours. None (infinite) would leave leaked
    # tokens valid forever; 86400s is a reasonable balance between UX and security.
    WTF_CSRF_TIME_LIMIT = 86400
    LOW_STOCK_THRESHOLD = 10
    EXPIRY_WARNING_DAYS = 30
    HIGH_DEMAND_THRESHOLD = 50
    SMARTMART_PREVENT_NEGATIVE_STOCK = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    # Cache TTL in seconds for dashboard/BI endpoints
    DASHBOARD_CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", 180))
    # Log level
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # ── Flask-Caching ────────────────────────────────────────────────────────
    # Overridden in subclasses based on REDIS_URL availability
    CACHE_TYPE = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get("DASHBOARD_CACHE_TTL", 180))

    # ── i18n (Flask-Babel) ───────────────────────────────────────────────────
    # English + Nepali. Add more codes here as translations are added —
    # see translations/README.md for the extract/update/compile workflow.
    BABEL_DEFAULT_LOCALE = "en"
    BABEL_SUPPORTED_LOCALES = ["en", "ne"]
    BABEL_TRANSLATION_DIRECTORIES = os.path.join(
        os.path.dirname(__file__), "translations"
    )


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = _fix_db_url(
        os.environ.get("DATABASE_URL")
    ) or "sqlite:///smart_mart_dev.db"

    # Warn loudly if using the insecure fallback key so it's never silently
    # used in a staging/shared environment.
    if not os.environ.get("SECRET_KEY"):
        import warnings
        warnings.warn(
            "SECRET_KEY is not set — using insecure dev fallback. "
            "Set SECRET_KEY in your .env before running on any shared machine.",
            stacklevel=2,
        )

    # Use Redis cache if available in dev, otherwise SimpleCache
    _redis = os.environ.get("REDIS_URL", "")
    if _redis:
        CACHE_TYPE = "RedisCache"
        CACHE_REDIS_URL = _redis
    else:
        CACHE_TYPE = "SimpleCache"


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = _fix_db_url(
        os.environ.get("DATABASE_URL")
    ) or "sqlite:///smart_mart.db"
    WTF_CSRF_ENABLED = True
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024   # 16 MB max upload size — blocks DoS via huge files

    # ── Static file caching ───────────────────────────────────────────────
    # Tell browsers to cache static assets for 1 year.
    # On deploy, hashed filenames (or the ?v= query string we use in
    # sw.js) ensure users always get the updated file.
    SEND_FILE_MAX_AGE_DEFAULT = 60 * 60 * 24 * 365  # 1 year in seconds

    # ── Secure session cookies (HTTPS only) ──────────────────────────────
    SESSION_COOKIE_SECURE   = True   # only sent over HTTPS
    SESSION_COOKIE_HTTPONLY = True   # no JS access to session cookie
    SESSION_COOKIE_SAMESITE = "Lax"  # CSRF mitigation
    REMEMBER_COOKIE_SECURE   = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # ── PostgreSQL connection pool — prevents Render idle connection drops ──
    # PostgreSQL on Render's free tier closes idle connections after ~5 min.
    # pool_recycle=280 ensures SQLAlchemy recycles connections before that.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 280,
        "pool_pre_ping": True,
        "pool_timeout": 20,
        "pool_size": 5,
        "max_overflow": 10,
        "connect_args": {"connect_timeout": 10},
    }

    # ── Cache: Redis if available, fallback to SimpleCache ──────────────────
    _redis = os.environ.get("REDIS_URL", "")
    if _redis:
        CACHE_TYPE = "RedisCache"
        CACHE_REDIS_URL = _redis
    else:
        CACHE_TYPE = "SimpleCache"

    @classmethod
    def init_app(cls, app):
        secret = os.environ.get("SECRET_KEY")
        if not secret:
            raise RuntimeError("SECRET_KEY must be set in production environment.")


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = False
    CACHE_TYPE = "SimpleCache"


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
