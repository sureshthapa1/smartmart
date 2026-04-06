"""Smoke tests for Task 1: project scaffolding and configuration."""


def test_app_factory_creates_app(app):
    assert app is not None
    assert app.name == "smart_mart.app"


def test_testing_config(app):
    assert app.config["TESTING"] is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert app.config["WTF_CSRF_ENABLED"] is False


def test_db_fixture_creates_tables(db):
    # db fixture should work without error; engine is accessible
    assert db is not None


def test_client_fixture(client):
    # A request to a non-existent route should return 404, not crash
    response = client.get("/nonexistent-route")
    assert response.status_code == 404
