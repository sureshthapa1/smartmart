# smart_mart/app.py  — PATCH INSTRUCTIONS
# ==========================================
# In your existing create_app() function, ADD these two lines
# alongside your existing db.init_app(app), login_manager.init_app(app), etc.
#
#   from smart_mart.extensions import migrate, limiter
#   migrate.init_app(app, db)
#   limiter.init_app(app)
#
# Also add a startup warning if SQLite is active in production:

import os
import sys
import logging

def _check_db_safety(app):
    """Warn loudly if SQLite is running in production."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    env = app.config.get("ENV", os.environ.get("FLASK_ENV", "production"))
    if "sqlite" in uri and env == "production":
        app.logger.error(
            "⚠️  DANGER: SQLite is active in production! "
            "Set DATABASE_URL to your PostgreSQL connection string on Render."
        )

# Call _check_db_safety(app) at the bottom of create_app(), before return app.
