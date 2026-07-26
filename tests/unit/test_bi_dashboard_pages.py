"""Regression tests for the BI & Intelligence dashboard pages.

Locks in the fix for: every bi/*.html template referenced page-view links
as url_for('bi.XXX_page') when the actual registered endpoint is
'bi_dashboard.XXX_page' (bi.* is reserved for the JSON API blueprint,
bi_dashboard.* is the HTML page blueprint). This caused a 500 BuildError
on every BI dashboard page load.
"""
import pytest


BI_PAGES = [
    "/bi/dashboard",
    "/bi/batches",
    "/bi/opex",
    "/bi/pricing-rules",
    "/bi/ledger",
    "/bi/contribution",
    "/bi/inventory-efficiency",
    "/bi/break-even",
    "/bi/profit-leakage",
    "/bi/roi",
]


@pytest.mark.parametrize("path", BI_PAGES)
def test_bi_dashboard_pages_render_for_admin(client, db, path):
    from tests.test_goldkernel_features import _admin, _login
    user = _admin("bi_test_admin")
    _login(client, user)

    resp = client.get(path)
    assert resp.status_code == 200, (
        f"{path} failed to render — likely a url_for() endpoint mismatch "
        f"between the bi.* (API) and bi_dashboard.* (page) blueprints"
    )


def test_bi_nav_link_uses_correct_endpoint():
    """base.html's 'BI Dashboard' sidebar link must point to the page
    blueprint (bi_dashboard.bi_dashboard_page), not the API blueprint."""
    with open("smart_mart/templates/base.html", encoding="utf-8") as f:
        content = f.read()
    assert "url_for('bi_dashboard.bi_dashboard_page')" in content
    assert "url_for('bi.bi_dashboard_page')" not in content
