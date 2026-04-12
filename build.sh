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

    from smart_mart.models.user import User
    from smart_mart.services.authenticator import hash_password

    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@1234")
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")

    # Always upsert the admin — ensures password is correct on every deploy
    admin = db.session.execute(
        db.select(User).where(User.username == admin_username)
    ).scalar_one_or_none()

    if not admin:
        admin = User(
            username=admin_username,
            password_hash=hash_password(admin_password),
            role="admin",
        )
        db.session.add(admin)
        db.session.commit()
        print(f"Admin user created: {admin_username}")
    else:
        # Update password on every deploy so env var always wins
        admin.password_hash = hash_password(admin_password)
        db.session.commit()
        print(f"Admin password updated: {admin_username}")

    print(f"Login with: username={admin_username}  password={admin_password}")

    from smart_mart.models.shop_settings import ShopSettings
    ShopSettings.get()
    print("Shop settings ready.")
EOF

echo "=== Build complete ==="
