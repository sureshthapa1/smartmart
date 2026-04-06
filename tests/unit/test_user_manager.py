"""Unit tests for smart_mart/services/user_manager.py."""

import pytest
from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.services import user_manager, authenticator


@pytest.fixture(scope="function")
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(autouse=True)
def app_ctx(app):
    with app.app_context():
        yield


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------

def test_create_user_returns_user():
    user = user_manager.create_user("alice", "secret123", "staff")
    assert user.id is not None
    assert user.username == "alice"
    assert user.role == "staff"


def test_create_user_hashes_password():
    user = user_manager.create_user("bob", "plaintext", "admin")
    assert user.password_hash != "plaintext"
    assert authenticator.check_password("plaintext", user.password_hash)


def test_create_user_duplicate_raises_value_error():
    user_manager.create_user("charlie", "pass1", "staff")
    with pytest.raises(ValueError, match="already taken"):
        user_manager.create_user("charlie", "pass2", "admin")


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

def test_update_user_username():
    user = user_manager.create_user("dave", "pw", "staff")
    updated = user_manager.update_user(user.id, {"username": "david"})
    assert updated.username == "david"


def test_update_user_role():
    user = user_manager.create_user("eve", "pw", "staff")
    updated = user_manager.update_user(user.id, {"role": "admin"})
    assert updated.role == "admin"


def test_update_user_duplicate_username_raises():
    user_manager.create_user("frank", "pw", "staff")
    user2 = user_manager.create_user("grace", "pw", "staff")
    with pytest.raises(ValueError, match="already taken"):
        user_manager.update_user(user2.id, {"username": "frank"})


# ---------------------------------------------------------------------------
# reset_password
# ---------------------------------------------------------------------------

def test_reset_password_stores_new_hash():
    user = user_manager.create_user("heidi", "oldpass", "staff")
    user_manager.reset_password(user.id, "newpass")
    assert authenticator.check_password("newpass", user.password_hash)
    assert not authenticator.check_password("oldpass", user.password_hash)


def test_reset_password_does_not_store_plaintext():
    user = user_manager.create_user("ivan", "mypassword", "staff")
    user_manager.reset_password(user.id, "mypassword")
    assert user.password_hash != "mypassword"


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------

def test_delete_user_removes_user():
    user = user_manager.create_user("judy", "pw", "staff")
    uid = user.id
    # Need a different current_user_id
    admin = user_manager.create_user("admin_user", "pw", "admin")
    user_manager.delete_user(uid, admin.id)
    users = user_manager.list_users()
    assert all(u.id != uid for u in users)


def test_delete_user_self_deletion_raises():
    user = user_manager.create_user("mallory", "pw", "admin")
    with pytest.raises(ValueError, match="cannot delete your own account"):
        user_manager.delete_user(user.id, user.id)


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

def test_list_users_ordered_by_username():
    user_manager.create_user("zara", "pw", "staff")
    user_manager.create_user("anna", "pw", "staff")
    user_manager.create_user("mike", "pw", "staff")
    users = user_manager.list_users()
    usernames = [u.username for u in users]
    assert usernames == sorted(usernames)


def test_list_users_returns_all():
    user_manager.create_user("user1", "pw", "staff")
    user_manager.create_user("user2", "pw", "admin")
    users = user_manager.list_users()
    assert len(users) >= 2
