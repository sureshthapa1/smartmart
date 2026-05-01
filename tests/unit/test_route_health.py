from smart_mart.services.route_health import audit_routes


def test_route_registry_health(app):
    report = audit_routes(app)

    assert report["total_routes"] >= 300
    assert report["issues"] == []
    assert report["blueprints"]["offers"] >= 10
    assert report["blueprints"]["bi"] >= 25
