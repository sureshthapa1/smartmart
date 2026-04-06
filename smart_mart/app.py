import os
from flask import Flask, redirect, url_for, render_template
from sqlalchemy.exc import SQLAlchemyError
from .config import config
from .extensions import db, login_manager, bcrypt


def create_app(config_name="development"):
    # Resolve template and static folders relative to this package directory
    pkg_dir = os.path.dirname(__file__)
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "templates"),
        static_folder=os.path.join(pkg_dir, "static"),
    )
    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    # Import models so SQLAlchemy registers them with the metadata
    from . import models  # noqa: F401

    # Register blueprints
    _register_blueprints(app)

    # Auto-create any missing DB tables on first request (safe, idempotent)
    @app.before_request
    def create_tables():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.warning("db.create_all() failed: %s", exc)
        app.before_request_funcs[None].remove(create_tables)

    # Root redirect
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # Global error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    if not app.debug:
        @app.errorhandler(500)
        def server_error(e):
            return render_template("errors/500.html"), 500

        @app.errorhandler(SQLAlchemyError)
        def db_error(e):
            app.logger.exception("Database error: %s", e)
            return render_template("errors/500.html"), 500

    # Create DB tables on first run — use a CLI command or seed.py instead
    # db.create_all() removed from here to avoid nested app context issues

    return app


def _register_blueprints(app):
    # Blueprints will be registered here as they are implemented in subsequent tasks.
    # Each import is guarded so the app factory works even before the blueprint
    # modules exist.
    try:
        from .blueprints.auth import auth_bp
        app.register_blueprint(auth_bp)
    except ImportError:
        pass

    try:
        from .blueprints.dashboard import dashboard_bp
        app.register_blueprint(dashboard_bp)
    except ImportError:
        pass

    try:
        from .blueprints.inventory import inventory_bp
        app.register_blueprint(inventory_bp)
    except ImportError:
        pass

    try:
        from .blueprints.sales import sales_bp
        app.register_blueprint(sales_bp)
    except ImportError:
        pass

    try:
        from .blueprints.purchases import purchases_bp
        app.register_blueprint(purchases_bp)
    except ImportError:
        pass

    try:
        from .blueprints.reports import reports_bp
        app.register_blueprint(reports_bp)
    except ImportError:
        pass

    try:
        from .blueprints.alerts import alerts_bp
        app.register_blueprint(alerts_bp)
    except ImportError:
        pass

    try:
        from .blueprints.admin import admin_bp
        app.register_blueprint(admin_bp)
    except ImportError:
        pass

    try:
        from .blueprints.settings import settings_bp
        app.register_blueprint(settings_bp)
    except ImportError:
        pass

    try:
        from .blueprints.api import api_bp
        app.register_blueprint(api_bp)
    except ImportError:
        pass
