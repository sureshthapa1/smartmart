"""Route registry audit helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


IGNORED_METHODS = {"HEAD", "OPTIONS"}


def audit_routes(app) -> dict[str, Any]:
    """Return a lightweight health summary for Flask route registration."""
    rules = sorted(app.url_map.iter_rules(), key=lambda rule: (rule.rule, rule.endpoint))
    route_rows = []
    duplicate_keys = []
    seen = set()
    by_blueprint: dict[str, int] = defaultdict(int)

    for rule in rules:
        methods = tuple(sorted(set(rule.methods or []) - IGNORED_METHODS))
        key = (rule.rule, methods)
        if key in seen:
            duplicate_keys.append({"rule": rule.rule, "methods": list(methods)})
        seen.add(key)

        blueprint = rule.endpoint.split(".", 1)[0] if "." in rule.endpoint else "app"
        by_blueprint[blueprint] += 1
        route_rows.append({
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "methods": list(methods),
            "blueprint": blueprint,
        })

    endpoint_counts = Counter(row["endpoint"] for row in route_rows)
    duplicate_endpoints = [
        endpoint for endpoint, count in endpoint_counts.items() if count > 1
    ]

    issues = []
    if duplicate_keys:
        issues.append({
            "type": "duplicate_rule_methods",
            "items": duplicate_keys,
        })
    if duplicate_endpoints:
        issues.append({
            "type": "duplicate_endpoints",
            "items": duplicate_endpoints,
        })

    return {
        "total_routes": len(route_rows),
        "blueprints": dict(sorted(by_blueprint.items())),
        "issues": issues,
        "routes": route_rows,
    }
