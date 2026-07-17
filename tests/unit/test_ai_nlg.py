from datetime import datetime, timezone

from smart_mart.extensions import db
from smart_mart.models.sale import Sale
from smart_mart.models.user import User
from smart_mart.services.ai_nlg import generate_daily_report


def test_daily_report_includes_week_to_date_sales(db, monkeypatch):
    # generate_daily_report() is template-based and doesn't call any AI
    # provider directly (the Gemini-powered narrative overlay is optional
    # and falls back gracefully), so no API key needs to be unset here.
    user = User(username="nlg_admin", password_hash="hash", role="admin")
    db.session.add(user)
    db.session.flush()
    db.session.add(
        Sale(
            user_id=user.id,
            total_amount=250,
            sale_date=datetime.now(timezone.utc),
            payment_method="cash",
        )
    )
    db.session.commit()

    report = generate_daily_report()

    assert report["data"]["week_sales"] == 250.0
