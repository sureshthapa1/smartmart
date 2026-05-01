"""Property-based tests for authentication.

Properties covered:
  Property 1: Invalid credentials are always rejected
  Property 2: Unauthenticated requests to protected routes are redirected
  Property 3: Role-based access control is enforced on all restricted routes
  Property 4: Passwords are never stored as plaintext
"""
# Feature: smart-mart-inventory

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.services import authenticator, user_manager


@pytest.fixture(scope="module")
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(autouse=True)
def app_ctx(app):
    with app.app_context():
        yield


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


# ── Property 1: Invalid credentials are always rejected ──────────────────────

@settings(max_examples=100, deadline=None)
@given(
    username=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    password=st.text(min_size=1, max_size=50),
)
def test_invalid_credentials_rejected(app, username, password):
    # Feature: smart-mart-inventory, Property 1: Invalid credentials are always rejected
    with app.app_context():
        result = authenticator.login(username, password)
        assert result is None


# ── Property 2: Unauthenticated requests redirect to login ───────────────────

PROTECTED_ROUTES = [
    "/dashboard/",
    "/inventory/",
    "/sales/",
    "/purchases/",
    "/reports/sales",
    "/alerts/",
    "/admin/users",
    "/expenses/",
]


@pytest.mark.parametrize("route", PROTECTED_ROUTES)
def test_unauthenticated_redirect(client, route):
    # Feature: smart-mart-inventory, Property 2: Unauthenticated requests to protected routes are redirected
    response = client.get(route, follow_redirects=False)
    assert response.status_code in (302, 301), f"Expected redirect for {route}, got {response.status_code}"
    assert "/auth/login" in response.headers.get("Location", "")


# ── Property 3: Role-based access control ────────────────────────────────────

ADMIN_ONLY_ROUTES = [
    "/admin/users",
    "/admin/users/create",
    "/admin/permissions",
    "/admin/audit-log",
]


def test_staff_cannot_access_admin_routes(app, client):
    # Feature: smart-mart-inventory, Property 3: Role-based access control is enforced on all restricted routes
    with app.app_context():
        user_manager.create_user("staff_rbac_test", "pass123", "staff")

    with client.session_transaction() as sess:
        pass  # ensure clean session

    # Login as staff
    client.post("/auth/login", data={"username": "staff_rbac_test", "password": "pass123"})

    for route in ADMIN_ONLY_ROUTES:
        response = client.get(route, follow_redirects=False)
        assert response.status_code in (302, 403), (
            f"Staff should not access {route}, got {response.status_code}"
        )

    client.get("/auth/logout")


# ── Property 4: Passwords are never stored as plaintext ──────────────────────

@settings(max_examples=100, deadline=None)
@given(password=st.text(min_size=6, max_size=50, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"))).filter(lambda s: len(s.encode("utf-8")) <= 72))
def test_password_never_stored_as_plaintext(app, password):
    # Feature: smart-mart-inventory, Property 4: Passwords are never stored as plaintext
    # Note: bcrypt has a 72-byte limit; we filter to stay within it
    with app.app_context():
        hashed = authenticator.hash_password(password)
        assert hashed != password
        assert authenticator.check_password(password, hashed)

