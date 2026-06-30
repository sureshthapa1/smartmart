"""Tests for the i18n (Flask-Babel) infrastructure.

Locks in: the locale-switcher cookie actually changes rendered output, the
Nepali catalog is compiled and non-empty, and a couple of representative
strings translate correctly. Not exhaustive coverage of every translatable
string — see smart_mart/translations/README.md for how to extend coverage.
"""
import os


def test_default_locale_is_english(client):
    resp = client.get("/store/")
    html = resp.data.decode("utf-8")
    assert 'lang="en"' in html
    assert "Track Order" in html


def test_lang_cookie_switches_rendered_locale(client):
    client.set_cookie("lang", "ne")
    resp = client.get("/store/")
    html = resp.data.decode("utf-8")
    assert 'lang="ne"' in html
    assert "अर्डर ट्र्याक" in html  # "Track Order"


def test_set_language_route_sets_cookie_and_redirects(client):
    resp = client.get("/set-language/ne", headers={"Referer": "/store/"})
    assert resp.status_code == 302
    assert "lang=ne" in resp.headers.get("Set-Cookie", "")


def test_set_language_rejects_unsupported_locale(client):
    resp = client.get("/set-language/fr", headers={"Referer": "/store/"})
    assert resp.status_code == 302
    assert "lang=fr" not in resp.headers.get("Set-Cookie", "")


def test_compiled_nepali_catalog_exists_and_is_nonempty():
    import smart_mart
    base = os.path.dirname(smart_mart.__file__)
    mo_path = os.path.join(base, "translations", "ne", "LC_MESSAGES", "messages.mo")
    assert os.path.exists(mo_path), "Nepali .mo catalog is missing — run `pybabel compile -d smart_mart/translations -l ne`"
    assert os.path.getsize(mo_path) > 100
