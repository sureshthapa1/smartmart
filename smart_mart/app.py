import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, redirect, url_for, render_template, session
from sqlalchemy.exc import SQLAlchemyError
from .config import config
from .extensions import db, login_manager, bcrypt


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

    from . import models  # noqa: F401

    _register_blueprints(app)

    # ── HTTP Security Headers ─────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # ── Auto-create DB tables on first request ────────────────────────────
    @app.before_request
    def create_tables():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.warning("db.create_all() failed: %s", exc)
        app.before_request_funcs[None].remove(create_tables)

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

    # ── Alert count context processor (sidebar badge) ────────────────────
    @app.context_processor
    def inject_alert_count():
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                from .services.alert_engine import get_low_stock_alerts, get_expiry_alerts
                count = len(get_low_stock_alerts()) + len(get_expiry_alerts())
                return {"global_alert_count": count}
        except Exception:
            pass
        return {"global_alert_count": 0}

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
