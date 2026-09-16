import os
import traceback

# ── Load .env before importing the app ────────────────────────────────────
# smart_mart.config reads os.environ at class-definition time, so importing
# create_app first would miss these values entirely — SECRET_KEY would fall
# back to the insecure dev key and GEMINI_API_KEY would be absent, silently
# degrading the AI features. load_dotenv does not override real environment
# variables, so Render/production values still win.
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from smart_mart.app import create_app

config_name = os.environ.get("FLASK_ENV", "development")
app = create_app(config_name)


# ── Debug traceback page (non-production only) ────────────────────────────
# This returns the full stack trace in the response body, which leaks source
# paths and internals to whoever triggered the error. Production serves via
# gunicorn against create_app("production") (see Procfile) and uses the error
# handlers in smart_mart/app.py, but run.py is also the --app target for
# `flask db upgrade` on Render, so guard the registration explicitly.
if config_name != "production":
    @app.errorhandler(Exception)
    def _debug_exception(e):
        from flask import request as _r
        tb = traceback.format_exc()
        print(tb)
        return (
            "<pre style='padding:2rem;background:#111;color:#f66;font-size:13px'>"
            f"ERROR on {_r.path}\n\n{tb}</pre>"
        ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
