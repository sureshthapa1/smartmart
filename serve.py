"""
Windows-compatible server launcher using Waitress.
Run via start_server.bat or: .venv/Scripts/python.exe serve.py
"""
import os
import sys
import logging
import traceback

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from smart_mart.app import create_app
from waitress import serve

app = create_app("development")

# ── Full traceback logging for Waitress (which hides 500 errors by default) ──
log_path = os.path.join(os.path.dirname(__file__), "logs", "waitress-errors.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)

file_handler = logging.FileHandler(log_path, encoding="utf-8")
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
logging.getLogger().addHandler(file_handler)

@app.errorhandler(Exception)
def _log_exception(e):
    tb = traceback.format_exc()
    # Write to log file
    logging.getLogger(__name__).error("Unhandled exception:\n%s", tb)
    # Also print to console so bat window shows it
    print(tb, file=sys.stderr)
    return (
        "<div style='padding:2rem;font-family:sans-serif;'>"
        "<h2 style='color:#b91c1c;'>500 — Internal Server Error</h2>"
        "<p>An unexpected error occurred. The server team has been notified.</p>"
        "</div>"
    ), 500

host = "0.0.0.0"
port = int(os.environ.get("PORT", 5000))

print(f"\n  Server running at http://localhost:{port}")
print(f"  Also accessible on your network at http://<your-ip>:{port}\n")
print("  Press Ctrl+C to stop.\n")

serve(app, host=host, port=port, threads=8)
