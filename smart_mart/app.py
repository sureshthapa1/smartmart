import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, redirect, url_for, render_template, session
from sqlalchemy.exc import SQLAlchemyError
from .config import config
from .extensions import db, login_manager, bcrypt, csrf
from .services.schema_migrations import run_pending_migrations


def _run_startup_migrations(app):
    """Apply versioned schema migrations and surface failures in logs."""
    applied = run_pending_migrations(app)
    if applied:
        app.logger.info("Startup migrations applied: %s", ", ".join(applied))
    return
    from sqlalchemy import text, inspect

    def _col_exists(conn, table, column):
        try:
            inspector = inspect(conn)
            cols = [c["name"] for c in inspector.get_columns(table)]
            return column in cols
        except Exception:
            return True  # assume exists if we can't check

    def safe_add(conn, table, column, col_type):
        try:
            if not _col_exists(conn, table, column):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"))
                conn.commit()
                app.logger.info(f"Migration: added {table}.{column}")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass

    migrations = [
        # customers
        ("customers", "birthday", "DATE"),
        ("customers", "email", "VARCHAR(120)"),
        # products
        ("products", "reorder_point", "INTEGER DEFAULT 10"),
        # shop_settings
        ("shop_settings", "logo_filename", "VARCHAR(255)"),
        ("shop_settings", "logo_data", "TEXT"),
        ("shop_settings", "loyalty_points_per_rupee", "NUMERIC(8,4) DEFAULT 0.01"),
        ("shop_settings", "loyalty_rupee_per_point", "NUMERIC(8,4) DEFAULT 1.00"),
        # ai_retraining_log
        ("ai_retraining_log", "model_name", "VARCHAR(80)"),
        ("ai_retraining_log", "samples_used", "INTEGER"),
        ("ai_retraining_log", "new_accuracy", "FLOAT"),
        ("ai_retraining_log", "improvement", "FLOAT"),
        ("ai_retraining_log", "error_message", "TEXT"),
        # customer_risk_scores
        ("customer_risk_scores", "risk_score", "INTEGER DEFAULT 0"),
        ("customer_risk_scores", "risk_tier", "VARCHAR(20) DEFAULT 'safe'"),
        ("customer_risk_scores", "override_tier", "VARCHAR(20)"),
        ("customer_risk_scores", "override_by", "INTEGER"),
        ("customer_risk_scores", "override_at", "TIMESTAMP"),
        ("customer_risk_scores", "last_computed_at", "TIMESTAMP"),
        # user_permissions — all new columns
        ("user_permissions", "can_manage_categories", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_variants", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_print_labels", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_stock_take", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_stock_take", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_customer_statement", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_supplier_returns", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_supplier_returns", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_purchase_orders", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_purchase_orders", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_customers", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_customers", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_expenses", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_expenses", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_reports", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_sales_report", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_profit_report", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_stock_report", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_credit_report", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_promotions", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_promotions", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_transfers", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_transfers", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_ai_insights", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_view_advisor", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_credits", "BOOLEAN DEFAULT false"),
        ("user_permissions", "can_manage_cash_session", "BOOLEAN DEFAULT false"),
    ]

    try:
        with db.engine.connect() as conn:
            for table, column, col_type in migrations:
                safe_add(conn, table, column, col_type)
    except Exception as e:
        app.logger.warning(f"Migration connection failed: {e}")


def create_app(config_name="development"):
    pkg_dir = os.path.dirname(__file__)
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "templates"),
        static_folder=os.path.join(pkg_dir, "static"),
    )
    app.config.from_object(config[config_name])

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

    from . import models  # noqa: F401

    _register_blueprints(app)

    # ── HTTP Security Headers ─────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Allow CDN resources used by the app
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
            "font-src 'self' fonts.gstatic.com cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            "connect-src 'self';"
        )
        return response

    # ── Auto-create DB tables + run column migrations at startup ─────────
    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.warning("db.create_all() failed: %s", exc)
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
            from .services.bs_date import bs_format
            # Apply NST offset first if it's a datetime
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
    def inject_now():
        return {"now": _dt.now(_tz.utc)}

    # ── Alert count context processor (sidebar badge) ────────────────────
    @app.context_processor
    def inject_alert_count():
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                from .services.alert_engine import get_low_stock_alerts, get_expiry_alerts
                from .models.dismissed_alert import DismissedAlert
                from .models.online_order import OnlineOrder
                dismissed = set(
                    db.session.execute(
                        db.select(DismissedAlert.alert_key)
                        .where(DismissedAlert.user_id == current_user.id)
                    ).scalars().all()
                )
                low_stock = [p for p in get_low_stock_alerts() if f"low_stock:{p.id}" not in dismissed]
                expiry = [p for p in get_expiry_alerts() if f"expiry:{p.id}" not in dismissed]
                count = len(low_stock) + len(expiry)
                # Pending online orders badge (admin only)
                pending_orders = 0
                if current_user.role == "admin":
                    pending_orders = db.session.execute(
                        db.select(db.func.count(OnlineOrder.id))
                        .where(OnlineOrder.status == "pending")
                    ).scalar() or 0
                return {"global_alert_count": count, "pending_orders_count": pending_orders}
        except Exception:
            pass
        return {"global_alert_count": 0, "pending_orders_count": 0}

    # ── Root redirect ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

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
        (".bi.routes", "bi_bp"),
        (".bi.routes", "bi_dashboard_bp"),
    ]

    for module_path, bp_name in blueprints:
        try:
            import importlib
            module = importlib.import_module(module_path, package="smart_mart")
            bp = getattr(module, bp_name)
            app.register_blueprint(bp)
        except ImportError:
            pass  # Blueprint not yet implemented
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
