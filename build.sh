#!/usr/bin/env bash
set -e

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Initialising database ==="
python - <<'EOF'
import os, sys
os.environ.setdefault("FLASK_ENV", "production")
from smart_mart.app import create_app
from smart_mart.extensions import db

app = create_app("production")
with app.app_context():
    db.create_all()
    print("Tables created.")

    # Create default admin if none exists
    from smart_mart.models.user import User
    from smart_mart.services.authenticator import hash_password
    admin = db.session.execute(
        db.select(User).where(User.role == "admin").limit(1)
    ).scalar_one_or_none()
    if not admin:
        admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
        admin = User(
            username="admin",
            password_hash=hash_password(admin_password),
            role="admin",
        )
        db.session.add(admin)
        db.session.commit()
        print(f"Admin user created. Username: admin")
    else:
        print(f"Admin user already exists: {admin.username}")

    # Create default shop settings
    from smart_mart.models.shop_settings import ShopSettings
    ShopSettings.get()
    print("Shop settings ready.")
EOF

echo "=== Build complete ==="
