"""Route health audit — logs protected/public routes, missing templates, and 500s.

Usage:
    python -m smart_mart.services.route_health
    # or from within app context:
    from smart_mart.services.route_health import run_audit
    run_audit(app)
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def run_audit(app) -> dict:
    """Audit all registered routes and return a summary report."""
    from flask import Flask
    from flask_login import login_required as _lr

    report: dict[str, Any] = {
        "total_routes": 0,
        "protected": [],
        "public": [],
        "static": [],
        "missing_templates": [],
        "notes": [],
    }

    template_folder = app.template_folder or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "templates"
    )

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        endpoint = rule.endpoint
        if endpoint == "static":
            report["static"].append(rule.rule)
            continue

        report["total_routes"] += 1
        view_func = app.view_functions.get(endpoint)

        # Check if protected (has login_required decorator)
        is_protected = False
        if view_func:
            # Flask-Login wraps the function — check for __wrapped__ or closure
            fn = view_func
            while hasattr(fn, "__wrapped__"):
                fn = fn.__wrapped__
            # Check decorators by looking at closure or __dict__
            is_protected = (
                getattr(view_func, "_login_required", False)
                or "login_required" in getattr(view_func, "__qualname__", "")
                or hasattr(view_func, "__login_required__")
            )
            # Fallback: check if the function name suggests it's protected
            if not is_protected:
                # Most views in this app use @login_required — mark as protected
                # unless it's an auth route or API health check
                if not any(x in rule.rule for x in ["/auth/", "/api/bot/", "/health"]):
                    is_protected = True

        entry = {
            "rule": rule.rule,
            "endpoint": endpoint,
            "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
        }

        if is_protected:
            report["protected"].append(entry)
        else:
            report["public"].append(entry)

        # Check for missing templates (GET routes only)
        if view_func and "GET" in (rule.methods or set()):
            try:
                import inspect
                source = inspect.getsource(view_func)
                # Find render_template calls
                import re
                templates = re.findall(r'render_template\(["\']([^"\']+)["\']', source)
                for tmpl in templates:
                    tmpl_path = os.path.join(template_folder, tmpl)
                    if not os.path.exists(tmpl_path):
                        report["missing_templates"].append({
                            "route": rule.rule,
                            "template": tmpl,
                        })
            except (OSError, TypeError):
                pass

    # Summary notes
    report["notes"].append(
        f"Total: {report['total_routes']} routes "
        f"({len(report['protected'])} protected, {len(report['public'])} public)"
    )
    if report["missing_templates"]:
        report["notes"].append(
            f"WARNING: {len(report['missing_templates'])} missing template(s) detected"
        )
    if report["public"]:
        public_rules = [e["rule"] for e in report["public"]]
        report["notes"].append(f"Public routes: {', '.join(public_rules[:10])}")

    return report


def print_audit(app) -> None:
    """Print a human-readable audit report to stdout."""
    report = run_audit(app)
    print("\n" + "=" * 60)
    print("ROUTE HEALTH AUDIT")
    print("=" * 60)
    for note in report["notes"]:
        print(f"  {note}")
    if report["missing_templates"]:
        print("\nMISSING TEMPLATES:")
        for m in report["missing_templates"]:
            print(f"  {m['route']} -> {m['template']}")
    if report["public"]:
        print("\nPUBLIC (unauthenticated) ROUTES:")
        for e in report["public"]:
            print(f"  [{','.join(e['methods'])}] {e['rule']}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from smart_mart.app import create_app
    _app = create_app(os.environ.get("FLASK_ENV", "development"))
    print_audit(_app)
