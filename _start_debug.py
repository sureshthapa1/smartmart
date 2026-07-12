import sys, os, traceback, time

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "startup_debug.txt")
os.makedirs(os.path.dirname(log_path), exist_ok=True)

def log(msg):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

log(f"=== startup === Python {sys.version.split()[0]}")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    log("dotenv loaded")
except Exception as e:
    log(f"dotenv skip: {e}")

try:
    from smart_mart.app import create_app
    log("import OK")
except Exception as e:
    log(f"IMPORT FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

try:
    app = create_app("development")
    log(f"create_app OK — {len(list(app.url_map.iter_rules()))} routes")
except Exception as e:
    log(f"CREATE_APP FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

log("Starting Flask on 0.0.0.0:5000 ...")
app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
