"""
Windows-compatible server launcher using Waitress.
Run via start_server.bat or: .venv/Scripts/python.exe serve.py
"""
import os
import sys

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from smart_mart.app import create_app
from waitress import serve

app = create_app("development")

host = "0.0.0.0"
port = int(os.environ.get("PORT", 5000))

print(f"\n  Server running at http://localhost:{port}")
print(f"  Also accessible on your network at http://<your-ip>:{port}\n")
print("  Press Ctrl+C to stop.\n")

serve(app, host=host, port=port, threads=8)
