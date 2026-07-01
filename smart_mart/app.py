import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, flash, redirect, url_for, render_template, request, session
from sqlalchemy.exc import SQLAlchemyError
from .config import config
from .extensions import db, login_manager, bcrypt, csrf, limiter, migrate, cache, babel
from .services.schema_migrations import run_pending_migrations


def _run_startup_migrations(app):
    """Apply versioned schema migrations and surface failures in logs."""
    applied = run_pending_migrations(app)
    if applied:
        app.logger.info("Startup migrations applied: %s", ", ".join(applied))


def create_app(config_name="development"):
    pkg_dir = os.path.dirname(__file__)
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "templates"),
        static_folder=os.path.join(pkg_dir, "static"),
    )
    config_object = config[config_name]
    app.config.from_object(config_object)
    if hasattr(config_object, "init_app"):
        config_object.init_app(app)

    # ── Logging ───────────────────────────────────────────────────────────
    if not app.debug:
        logs_dir = os.path.join(os.path.dirname(pkg_dir), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(logs_dir, "smart_mart.log"),
            maxBytes=1_000_000, backupCount=5
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)

    # ── Flask-Mail configuration from environment ─────────────────────────
    import os as _os
    app.config.setdefault("MAIL_SERVER",         _os.environ.get("MAIL_SERVER", ""))
    app.config.setdefault("MAIL_PORT",           int(_os.environ.get("MAIL_PORT", 587)))
    app.config.setdefault("MAIL_USE_TLS",        _os.environ.get("MAIL_USE_TLS", "true").lower() == "true")
    app.config.setdefault("MAIL_USE_SSL",        False)
    app.config.setdefault("MAIL_USERNAME",       _os.environ.get("MAIL_USERNAME", ""))
    app.config.setdefault("MAIL_PASSWORD",       _os.environ.get("MAIL_PASSWORD", ""))
    app.config.setdefault("MAIL_DEFAULT_SENDER", _os.environ.get("MAIL_DEFAULT_SENDER", "GoldKernel <noreply@goldkernel.com>"))
    app.config.setdefault("MAIL_SUPPRESS_SEND",  not bool(_os.environ.get("MAIL_SERVER", "")))

    # Init Flask-Mail (graceful — app runs fine without email configured)
    try:
        from flask_mail import Mail
        mail = Mail(app)
        app.extensions["mail"] = mail
    except ImportError:
        pass
    limiter.init_app(app)

    def _select_locale():
        from flask import request as _req
        supported = app.config.get("BABEL_SUPPORTED_LOCALES", ["en"])
        # 1. Explicit choice via the language-switcher cookie
        cookie_lang = _req.cookies.get("lang")
        if cookie_lang in supported:
            return cookie_lang
        # 2. Browser preference
        best = _req.accept_languages.best_match(supported)
        if best:
            return best
        # 3. Default
        return app.config.get("BABEL_DEFAULT_LOCALE", "en")

    babel.init_app(app, locale_selector=_select_locale)

    from flask_babel import gettext as _gettext, ngettext as _ngettext, get_locale as _get_locale
    app.jinja_env.globals["_"] = _gettext
    app.jinja_env.globals["gettext"] = _gettext
    app.jinja_env.globals["ngettext"] = _ngettext
    app.jinja_env.globals["get_locale"] = _get_locale

    @app.route("/set-language/<lang_code>")
    def set_language(lang_code):
        """Language switcher — sets a cookie read by the Babel locale_selector
        and redirects back to wherever the request came from."""
        from flask import redirect as _redirect, request as _req

        supported = app.config.get("BABEL_SUPPORTED_LOCALES", ["en"])
        resp = _redirect(_req.referrer or url_for("store.home"))
        if lang_code in supported:
            # ~1 year; harmless preference cookie, no PII
            resp.set_cookie("lang", lang_code, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    from . import models  # noqa: F401

    if os.environ.get("FLASK_ENV") == "production":
        db_url = os.environ.get("DATABASE_URL")
        db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        if not db_url or db_uri.startswith("sqlite"):
            app.logger.warning(
                "Production environment is using SQLite because DATABASE_URL is not set. "
                "Use PostgreSQL for production data safety."
            )

    _register_blueprints(app)

    # now is injected via inject_now context processor below
    from datetime import datetime as _dt


    # ── HTTP Security Headers ─────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Prevent admin/API pages from being indexed by search engines
        if request.path.startswith(("/admin", "/api", "/dashboard", "/auth")):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        # HSTS: tell browsers to always use HTTPS (production only)
        if not app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        # Allow CDN resources used by the app
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
            "font-src 'self' fonts.gstatic.com cdn.jsdelivr.net; "
            "img-src 'self' data: blob: https://res.cloudinary.com https://images.pexels.com; "
            "connect-src 'self' rc-epay.esewa.com.np uat.esewa.com.np khalti.com;"
        )
        return response

    # ── Auto-create DB tables + run column migrations at startup ─────────
    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.warning("db.create_all() failed: %s", exc)

        # Seed knowledge base FAQ articles (once, if table empty)
        try:
            from .services.knowledge_base_seed import seed_knowledge_base
            _n = seed_knowledge_base()
            if _n:
                app.logger.info("Knowledge base seeded: %d default articles", _n)
        except Exception as _kb_exc:
            app.logger.debug("KB seed skipped: %s", _kb_exc)
        # Run safe column migrations so new columns are always present
        try:
            _run_startup_migrations(app)
        except Exception as exc:
            app.logger.warning("Startup migrations failed: %s", exc)
        # Backfill any existing expenses that have no BI mirror yet
        try:
            from .services.expense_sync import backfill as _expense_backfill
            n = _expense_backfill()
            if n:
                app.logger.info("expense_sync backfill: %d rows created", n)
        except Exception as exc:
            app.logger.warning("expense_sync backfill failed (non-fatal): %s", exc)

    # ── Session permanent (8hr timeout from config) ───────────────────────
    @app.before_request
    def make_session_permanent():
        session.permanent = True

    # ── Track page views per active session ───────────────────────────────
    @app.before_request
    def track_page_view():
        from flask import session as flask_session, request
        if request.endpoint and not request.endpoint.startswith("static"):
            try:
                activity_id = flask_session.get("activity_id")
                if activity_id:
                    from .models.user_activity import UserActivity
                    act = db.session.get(UserActivity, activity_id)
                    if act and act.logout_at is None:
                        act.page_views = (act.page_views or 0) + 1
                        db.session.commit()
            except Exception:
                pass

    # ── Jinja2 custom filters ─────────────────────────────────────────────
    import json as _json
    from datetime import timedelta as _td

    # Nepal Standard Time = UTC + 5:45
    _NST_OFFSET = _td(hours=5, minutes=45)

    # ── product_image_url as Jinja2 global + filter ──────────────────────
    try:
        from .services.image_service import product_image_url as _piu
        app.jinja_env.globals["product_image_url"] = _piu
        app.jinja_env.filters["product_image_url"] = _piu
    except Exception:
        import os as _os
        def _fallback_img_url(f, width=400):
            if not f:
                return None
            if f.startswith("cld:") or f.startswith("http"):
                return f if f.startswith("http") else None
            return f"/static/uploads/products/{_os.path.basename(f)}"
        app.jinja_env.globals["product_image_url"] = _fallback_img_url
        app.jinja_env.filters["product_image_url"] = _fallback_img_url

    # ── Markdown filter for product descriptions ─────────────────────────
    def _make_md_filter():
        try:
            import markdown as _md
            def _md_filter(text):
                if not text:
                    return ""
                return _md.markdown(text, extensions=["nl2br", "sane_lists"], output_format="html")
            return _md_filter
        except ImportError:
            import re as _re2
            def _md_fallback(text):
                if not text:
                    return ""
                t = _re2.sub(r"[*][*](.+?)[*][*]", r"<strong>\1</strong>", text)
                t = _re2.sub(r"[*](.+?)[*]", r"<em>\1</em>", t)
                parts = []
                in_ul = False
                for line in t.split("\n"):
                    s = line.strip()
                    if s.startswith("- ") or s.startswith("* "):
                        if not in_ul:
                            parts.append("<ul>")
                            in_ul = True
                        parts.append("<li>" + s[2:] + "</li>")
                    else:
                        if in_ul:
                            parts.append("</ul>")
                            in_ul = False
                        if s:
                            parts.append("<p>" + s + "</p>")
                if in_ul:
                    parts.append("</ul>")
                return "\n".join(parts)
            return _md_fallback
    app.jinja_env.filters["markdown"] = _make_md_filter()

    @app.template_filter("nst")
    def nst_filter(dt, fmt="%Y-%m-%d %H:%M"):
        """Convert a naive UTC datetime to Nepal Standard Time (UTC+5:45)."""
        if dt is None:
            return ""
        try:
            return (dt + _NST_OFFSET).strftime(fmt)
        except Exception:
            return str(dt)

    @app.template_filter("bs_date")
    def bs_date_filter(dt, nepali: bool = True) -> str:
        """Convert a date/datetime to Bikram Sambat string.
        Usage in templates: {{ sale.sale_date | bs_date }}          → २०८१ बैशाख १
                            {{ sale.sale_date | bs_date(nepali=False) }} → 2081 Baisakh 1
        """
        if dt is None:
            return ""
        try:
            from .utils.nepali_date import ad_to_bs, format_bs
            if hasattr(dt, "hour"):
                dt = dt + _NST_OFFSET
            return format_bs(*ad_to_bs(dt))
        except Exception:
            try:
                from .services.bs_date import bs_format
                if hasattr(dt, "hour"):
                    dt = dt + _NST_OFFSET
                return bs_format(dt, nepali=nepali)
            except Exception:
                return ""

    @app.template_filter("from_json")
    def from_json_filter(value):
        try:
            return _json.loads(value)
        except Exception:
            return {}

    # ── Global context: inject `now` for templates ────────────────────────
    from datetime import datetime as _dt, timezone as _tz

    @app.context_processor
    def inject_globals():
        """Inject common variables into every template context."""
        from datetime import datetime as _dt2, timezone as _tz2
        ctx = {"now": _dt2.now(_tz2.utc)}
        # Inject active product categories for nav/footer use
        try:
            from .models.product import Product as _Prod
            from .extensions import db as _db2
            cats = _db2.session.execute(
                _db2.select(_Prod.category)
                .where(_Prod.is_active.isnot(False), _Prod.quantity > 0, _Prod.category.isnot(None))
                .distinct()
                .order_by(_Prod.category)
            ).scalars().all()
            ctx["categories"] = [c for c in cats if c]
        except Exception:
            ctx["categories"] = []
        return ctx

    # ── Alert count context processor (sidebar badge) ────────────────────
    # Returns cached values only — actual DB query is done by /api/alert-count
    # (called via AJAX every 60s from base.html). This prevents a DB round-trip
    # on every single page render for every logged-in user.
    @app.context_processor
    def inject_alert_count():
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                from .services.cache_service import get as _cache_get
                cached = _cache_get(f"alert_count:u{current_user.id}")
                if cached is not None:
                    return cached
                # No cache yet — return zeros; AJAX endpoint will populate on first poll
                return {"global_alert_count": 0, "pending_orders_count": 0}
        except Exception:
            pass
        return {"global_alert_count": 0, "pending_orders_count": 0}

    # ── Root redirect ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # ── Root-level SEO endpoints (search engines read these at /) ─────────
    @app.route("/robots.txt")
    def robots_global():
        """Root robots.txt so all crawlers find it at the canonical path."""
        from flask import make_response
        base = request.url_root.rstrip("/")
        content = (
            "User-agent: *\n"
            "Allow: /store/\n"
            "Disallow: /store/checkout\n"
            "Disallow: /store/account\n"
            "Disallow: /store/cart\n"
            "Disallow: /dashboard/\n"
            "Disallow: /admin/\n"
            "Disallow: /api/\n"
            "Disallow: /mcp/\n"
            "Disallow: /bi/\n"
            f"Sitemap: {base}/store/sitemap.xml\n"
        )
        resp = make_response(content, 200)
        resp.headers["Content-Type"] = "text/plain"
        return resp

    @app.route("/sitemap.xml")
    def sitemap_global():
        """Root sitemap redirect to the store sitemap."""
        return redirect(url_for("store.sitemap", _external=False))

    # ── Health check (Render / uptime monitors) ───────────────────────────
    @app.route("/health")
    def health_check():
        """Lightweight health-check endpoint. Returns 200 when the app and DB are up."""
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        status = {"status": "ok", "timestamp": _dt.now(_tz.utc).isoformat()}
        http_code = 200
        # Quick DB ping
        try:
            db.session.execute(db.text("SELECT 1"))
            status["db"] = "ok"
        except Exception as _db_exc:
            status["db"] = "error"
            status["db_error"] = str(_db_exc)[:80]
            status["status"] = "degraded"
            http_code = 503
        # Redis ping (if configured)
        try:
            import os as _os2
            if _os2.environ.get("REDIS_URL"):
                import redis as _redis
                _r = _redis.from_url(_os2.environ["REDIS_URL"], socket_connect_timeout=2)
                _r.ping()
                status["redis"] = "ok"
        except Exception:
            status["redis"] = "unavailable"
        from flask import Response
        return Response(
            _json.dumps(status, indent=2),
            status=http_code,
            mimetype="application/json",
        )

    # ── Global error handlers ─────────────────────────────────────────────
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled server error: %s", e)
        return render_template("errors/500.html"), 500

    @app.errorhandler(SQLAlchemyError)
    def db_error(e):
        app.logger.exception("Database error: %s", e)
        db.session.rollback()
        # If it's a missing column error, try to run migrations and give helpful message
        err_str = str(e).lower()
        if "column" in err_str and ("does not exist" in err_str or "no such column" in err_str):
            try:
                _run_startup_migrations(app)
                app.logger.info("Auto-migration triggered by missing column error")
            except Exception as migration_exc:
                app.logger.exception("Auto-migration retry failed: %s", migration_exc)
        return render_template("errors/500.html"), 500

    @app.errorhandler(429)
    def rate_limit_error(e):
        flash("Too many login attempts. Please wait 1 minute and try again.", "danger")
        if request.endpoint and request.endpoint.startswith("auth."):
            from datetime import datetime as _dt
            return render_template("auth/login.html", now=_dt.now()), 429
        return render_template("errors/500.html"), 429

    return app


def _register_blueprints(app):
    import traceback

    blueprints = [
        (".blueprints.auth", "auth_bp"),
        (".blueprints.dashboard", "dashboard_bp"),
        (".blueprints.inventory", "inventory_bp"),
        (".blueprints.sales", "sales_bp"),
        (".blueprints.returns", "returns_bp"),
        (".blueprints.purchases", "purchases_bp"),
        (".blueprints.reports", "reports_bp"),
        (".blueprints.alerts", "alerts_bp"),
        (".blueprints.admin", "admin_bp"),
        (".blueprints.ai", "ai_bp"),
        (".blueprints.online_orders", "online_orders_bp"),
        (".blueprints.settings", "settings_bp"),
        (".blueprints.operations", "operations_bp"),
        (".blueprints.ecommerce_api", "ecommerce_api_bp"),
        (".blueprints.store", "store_bp"),
        (".blueprints.api", "api_bp"),
        (".blueprints.expenses", "expenses_bp"),
        (".blueprints.advisor", "advisor_bp"),
        (".blueprints.purchase_orders", "po_bp"),
        (".blueprints.transfers", "transfers_bp"),
        (".blueprints.supplier_returns", "supplier_returns_bp"),
        (".blueprints.promotions", "promotions_bp"),
        (".blueprints.stock_take", "stock_take_bp"),
        (".blueprints.customers", "customers_bp"),
        (".blueprints.finance", "finance_bp"),
        (".blueprints.offers", "offers_bp"),
        (".blueprints.bundles", "bundles_bp"),
        (".blueprints.waste", "waste_bp"),
        (".blueprints.targets", "targets_bp"),
        (".blueprints.loyalty", "loyalty_bp"),
        (".blueprints.ai_chat", "ai_chat_bp"),
        (".blueprints.mcp", "mcp_bp"),
        (".bi.routes", "bi_bp"),
        (".bi.routes", "bi_dashboard_bp"),
    ]

    for module_path, bp_name in blueprints:
        try:
            import importlib
            module = importlib.import_module(module_path, package="smart_mart")
            bp = getattr(module, bp_name)
            app.register_blueprint(bp)
        except ImportError as exc:
            # Log at WARNING — a real import error (missing dependency,
            # syntax error in a blueprint) looks identical to "not yet
            # implemented" without this line. Debug by checking the
            # message: a genuinely stub blueprint won't have a traceback.
            app.logger.warning(
                "Blueprint %s not loaded (ImportError: %s)", bp_name, exc
            )
        except Exception as exc:
            app.logger.error(
                "Failed to register blueprint %s: %s\n%s",
                bp_name, exc, traceback.format_exc()
            )
    # CSRF exemptions — endpoints called by external services (cron, curl)
    # that cannot include a CSRF token
    try:
        from .blueprints.api.routes import run_bots
        csrf.exempt(run_bots)
    except Exception:
        pass

    try:
        from .blueprints.ecommerce_api import ecommerce_api_bp as _ecommerce_api_bp
        csrf.exempt(_ecommerce_api_bp)
    except Exception:
        pass

    # csrf.exempt(login) was previously here but is unnecessary — the login
    # template already renders {{ csrf_token() }} so CSRF protection works
    # naturally. Removing it restores defense-in-depth against login CSRF
    # (forcing a victim to authenticate as an attacker-controlled account).
    # Logout remains exempt: it's a GET-based redirect with no persistent
    # state mutation beyond ending the session (acceptable tradeoff).
    try:
        from .blueprints.auth.routes import logout
        csrf.exempt(logout)
    except Exception:
        pass

    # ── CSRF error handler — redirect back with a clear message ──────────
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        from flask import request as _req, flash as _flash, redirect as _redir
        _flash(
            "Your session has expired or the form was submitted twice. "
            "Please try again.",
            "warning",
        )
        # Redirect back to the page the user came from, or home
        referrer = _req.referrer or url_for("dashboard.index")
        return _redir(referrer), 303
