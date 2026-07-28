import os
import traceback
from smart_mart.app import create_app

config_name = os.environ.get("FLASK_ENV", "development")
app = create_app(config_name)

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
