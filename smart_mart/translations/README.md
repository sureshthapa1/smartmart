# i18n / Translations

SmartMart's customer storefront supports English (`en`, default) and Nepali
(`ne`) via [Flask-Babel](https://github.com/python-babel/flask-babel).

## Current coverage

Infrastructure (locale detection, language switcher, Jinja `_()`/`gettext()`/
`ngettext()` globals) is fully wired up app-wide. String coverage is currently
focused on the highest-traffic customer pages:

- `templates/store/base_store.html` (nav, footer, mobile bottom nav)
- `templates/store/home.html` (hero, category strip, sort, empty state)
- `templates/store/checkout.html` (delivery form, payment method, order summary)
- `templates/store/_product_card.html` (shared product card, used everywhere)

The rest of the storefront (login/register, product detail, cart, order
tracking/detail, wishlist, FAQ/about/contact, etc.) and the entire
internal POS/admin dashboard are **not yet wrapped** — they'll render in
English regardless of the selected locale until someone wraps their strings
the same way (see "Adding a new page" below).

## How it works

- `smart_mart/config.py` sets `BABEL_DEFAULT_LOCALE`, `BABEL_SUPPORTED_LOCALES`,
  and `BABEL_TRANSLATION_DIRECTORIES`.
- `smart_mart/app.py`'s `_select_locale()` picks the active locale per
  request: explicit `lang` cookie → browser `Accept-Language` → default `en`.
- `GET /set-language/<lang_code>` (also in `app.py`) sets the cookie and
  redirects back to the referring page. The nav language switcher
  (EN / ने) in `base_store.html` links here.
- `gettext`, `ngettext`, `_` (alias for `gettext`), and `get_locale` are
  registered as Jinja globals in `create_app()`.

## Adding a new page / wrapping more strings

1. In templates, wrap any customer-visible text:
   ```jinja
   {{ _('Add to Cart') }}
   {{ _('You have %(n)s items', n=count) }}
   {{ ngettext('%(num)s item', '%(num)s items', count) }}
   ```
   In Python code, `from flask_babel import gettext as _` and use the same way.

2. Re-extract all translatable strings into the template file:
   ```bash
   pybabel extract -F babel.cfg -o smart_mart/translations/messages.pot .
   ```

3. Merge new/changed strings into the Nepali catalog (preserves existing
   translations, marks changed ones `#, fuzzy` for review):
   ```bash
   pybabel update -i smart_mart/translations/messages.pot -d smart_mart/translations -l ne
   ```

4. Open `smart_mart/translations/ne/LC_MESSAGES/messages.po` and fill in
   `msgstr ""` entries with the Nepali translation. Don't add a `python-format`
   string with a different set of `%(...)`-style placeholders than the
   `msgid` — `pybabel compile` will reject it.

5. Compile to the binary catalog Flask-Babel actually reads at runtime:
   ```bash
   pybabel compile -d smart_mart/translations -l ne
   ```
   (This also runs automatically in `build.sh` on deploy, so a stale or
   missing `.mo` won't break the site — it just falls back to English for
   anything not yet compiled.)

6. Commit both the updated `.po` *and* the recompiled `.mo` file.

## Adding a third language later

```bash
pybabel init -i smart_mart/translations/messages.pot -d smart_mart/translations -l <code>
```
Then add `<code>` to `BABEL_SUPPORTED_LOCALES` in `config.py`, add a switcher
link next to the EN/ने links in `base_store.html`, translate, and compile as
above.

## Notes

- Plural rules: Nepali's `Plural-Forms` here is the simple `nplurals=2`
  fallback Babel generates by default. If pluralization turns out to behave
  oddly for some count in real use, this is the line to revisit in the `.po`
  header.
- The product/order data itself (names, categories, addresses, etc.) is
  *not* translated — only the surrounding UI chrome. Product names are
  whatever the shop owner entered.
