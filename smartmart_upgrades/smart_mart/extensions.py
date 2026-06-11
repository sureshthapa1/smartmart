# smart_mart/extensions.py
# DROP-IN REPLACEMENT for your existing extensions.py
# Adds: Flask-Migrate, Flask-Limiter

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()
migrate = Migrate()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],          # no global limit; apply per-route
    storage_uri="memory://",    # swap to "redis://..." if you add Redis later
)

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
