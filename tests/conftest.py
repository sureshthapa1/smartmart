import pytest
from smart_mart.app import create_app
from smart_mart.extensions import db as _db


@pytest.fixture(scope="session")
def app():
    """Create a Flask application configured for testing (in-memory SQLite)."""
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope="function")
def db(app):
    """Provide a clean database for each test function."""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope="function")
def client(app, db):
    """Flask test client with a clean database."""
    return app.test_client()
