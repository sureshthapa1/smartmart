from smart_mart.extensions import db
from smart_mart.models.user import User


def _login_as_admin(client):
    user = User(username="smoke_admin", password_hash="hash", role="admin")
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return user


def test_core_pages_render_for_admin(client, db):
    _login_as_admin(client)

    response = client.get("/dashboard/")
    assert response.status_code == 200

    response = client.get("/sales/")
    assert response.status_code == 200

    response = client.get("/purchases/")
    assert response.status_code == 200

    response = client.get("/reports/inventory-valuation")
    assert response.status_code == 200


def test_login_page_renders(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
