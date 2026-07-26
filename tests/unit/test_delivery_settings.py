"""Tests for admin-configurable delivery charge/free-delivery threshold.

Locks in a fix for a real bug: the admin Settings page let shop owners
configure ShopSettings.delivery_charge and .free_delivery_above_npr, but
checkout's actual pricing math (_calc_delivery) and every customer-facing
page displaying the threshold (home, checkout, FAQ, promos, order success,
payment pending) all used hardcoded module-level constants that completely
ignored the setting. Changing the delivery fee in Settings had zero effect
on what customers were actually charged.

Also locks in the migration fix: delivery_charge/free_delivery_above_npr
columns were previously only added via a raw ALTER in build.sh, which (a)
never ran on local dev environments at all, and (b) had a broken
indentation that caused the ENTIRE build.sh database-init script to fail
to parse — meaning every deploy since that commit landed would have
failed at the "Initialising database" step (build.sh has `set -e`).
"""
import ast

from smart_mart.extensions import db
from smart_mart.models.shop_settings import ShopSettings


def test_build_sh_python_heredoc_has_no_syntax_errors():
    """Regression test for the IndentationError that broke every deploy.
    build.sh's database-init step is a single ~290-line Python heredoc
    (`python - <<'PYEOF' ... PYEOF`) — if any line in it has a syntax
    error, the ENTIRE script fails to parse, and since build.sh has
    `set -e`, the whole deploy fails at that step."""
    with open("build.sh", encoding="utf-8") as f:
        lines = f.readlines()

    start = end = None
    for i, line in enumerate(lines):
        if "<<'PYEOF'" in line:
            start = i + 1
        elif line.strip() == "PYEOF" and start is not None:
            end = i
            break

    assert start is not None and end is not None, "could not find the PYEOF heredoc in build.sh"
    heredoc_source = "".join(lines[start:end])

    try:
        ast.parse(heredoc_source)
    except SyntaxError as e:
        raise AssertionError(
            f"build.sh's database-init Python heredoc has a syntax error at "
            f"heredoc-relative line {e.lineno}: {e.msg}. This breaks EVERY "
            f"deploy (build.sh has `set -e`), not just the specific migration "
            f"near that line."
        )


def test_shop_settings_has_delivery_columns_after_fresh_migration(app, db):
    """The delivery_charge / free_delivery_above_npr columns must exist
    after schema migrations run — this is the proper versioned migration
    path (schema_migrations.py), not the build.sh-only ALTER that never
    ran locally."""
    settings = ShopSettings.get()
    assert hasattr(settings, "delivery_charge")
    assert hasattr(settings, "free_delivery_above_npr")
    # Should not raise — proves the columns genuinely exist in the DB,
    # not just on the Python model definition
    db.session.execute(db.select(ShopSettings.delivery_charge)).all()
    db.session.execute(db.select(ShopSettings.free_delivery_above_npr)).all()


def test_calc_delivery_uses_fallback_when_unconfigured(app, db):
    from smart_mart.blueprints.store.routes import _calc_delivery, _delivery_charge, _free_delivery_threshold

    settings = ShopSettings.get()
    settings.delivery_charge = 0
    settings.free_delivery_above_npr = 0
    db.session.commit()

    assert _delivery_charge() == 100.0
    assert _free_delivery_threshold() == 2000.0
    assert _calc_delivery(500) == 100.0
    assert _calc_delivery(2500) == 0.0


def test_calc_delivery_uses_configured_values(app, db):
    """The core regression test: once an admin sets real values, checkout
    pricing must actually reflect them."""
    from smart_mart.blueprints.store.routes import _calc_delivery, _delivery_charge, _free_delivery_threshold

    settings = ShopSettings.get()
    settings.delivery_charge = 150
    settings.free_delivery_above_npr = 3000
    db.session.commit()

    assert _delivery_charge() == 150.0
    assert _free_delivery_threshold() == 3000.0
    assert _calc_delivery(2500) == 150.0, "below the new threshold — should charge the new fee"
    assert _calc_delivery(3500) == 0.0, "above the new threshold — should be free"


def test_home_page_reflects_configured_free_delivery_threshold(client, db):
    settings = ShopSettings.get()
    settings.delivery_charge = 150
    settings.free_delivery_above_npr = 3000
    db.session.commit()

    resp = client.get("/store/")
    assert resp.status_code == 200
    assert b"3,000" in resp.data


def test_faq_page_reflects_configured_delivery_settings(client, db):
    settings = ShopSettings.get()
    settings.delivery_charge = 150
    settings.free_delivery_above_npr = 3000
    db.session.commit()

    resp = client.get("/store/faq")
    assert resp.status_code == 200
    assert b"3,000" in resp.data


def test_promos_page_reflects_configured_free_delivery_threshold(client, db):
    settings = ShopSettings.get()
    settings.delivery_charge = 150
    settings.free_delivery_above_npr = 3000
    db.session.commit()

    resp = client.get("/store/promos")
    assert resp.status_code == 200
    assert b"3,000" in resp.data
