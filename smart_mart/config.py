import os
from datetime import timedelta


def _fix_db_url(url: str | None) -> str | None:
    """Render gives postgres:// but SQLAlchemy 2.x needs postgresql://"""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    LOW_STOCK_THRESHOLD = 10
    EXPIRY_WARNING_DAYS = 30
    HIGH_DEMAND_THRESHOLD = 50
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = _fix_db_url(
        os.environ.get("DATABASE_URL")
    ) or "sqlite:///smart_mart_dev.db"


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = _fix_db_url(
        os.environ.get("DATABASE_URL")
    ) or "sqlite:///smart_mart.db"
    WTF_CSRF_ENABLED = True

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


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
