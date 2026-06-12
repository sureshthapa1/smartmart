import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_caching import Cache

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
migrate = Migrate()

# ── Rate limiter — use Redis in production, memory in dev ────────────────────
# Set REDIS_URL env var (e.g. redis://localhost:6379/0) to enable shared
# rate-limit counters across Gunicorn workers.  Falls back to in-process
# memory storage if no Redis URL is configured (dev/single-worker only).
_redis_url = os.environ.get("REDIS_URL", "")
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_redis_url if _redis_url else "memory://",
)

# ── Cache — use Redis in production, SimpleCache in dev ─────────────────────
# Replaces the custom cache_service dict and store_ai_service._cache dict.
# Config is applied in create_app() via app.config["CACHE_TYPE"].
cache = Cache()

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id: str):
    from .models.user import User
    return db.session.get(User, int(user_id))
