"""
Shekel Budget App — Application Factory

Creates and configures the Flask application.  Call create_app()
with an optional config_name ('development', 'testing', 'production')
to get a fully wired Flask instance.
"""

import os

from flask import Flask, render_template

from app.config import CONFIG_MAP
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.utils.logging_config import setup_logging


def create_app(config_name=None):
    """Build and return the configured Flask application.

    Args:
        config_name: One of 'development', 'testing', 'production'.
                     Defaults to the FLASK_ENV environment variable
                     or 'development' if unset.

    Returns:
        A fully configured Flask app instance.
    """
    app = Flask(__name__)

    # --- Configuration ---------------------------------------------------
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")
    config_class = CONFIG_MAP.get(config_name)
    if config_class is None:
        raise ValueError(f"Unknown config_name: {config_name!r}")
    app.config.from_object(config_class)

    # --- Logging ---------------------------------------------------------
    setup_logging(app)

    # --- Extensions ------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Flask-Login user loader callback.
    from app.models.user import User  # pylint: disable=import-outside-toplevel

    @login_manager.user_loader
    def load_user(user_id):
        """Load a user by ID for Flask-Login session hydration."""
        return db.session.get(User, int(user_id))

    # --- Template Filters --------------------------------------------------
    @app.template_filter("format_account_type")
    def format_account_type(value):
        """Convert a raw account type name to a user-friendly display string.

        Uses an explicit mapping for names that need special formatting
        (e.g. acronyms, parenthetical notation) and falls back to
        replacing underscores with spaces and title-casing.
        """
        if value is None:
            return ""

        # Explicit mapping for known account types
        display_names = {
            "checking": "Checking",
            "savings": "Savings",
            "hysa": "HYSA",
            "mortgage": "Mortgage",
            "auto_loan": "Auto Loan",
            "401k": "401(k)",
            "roth_401k": "Roth 401(k)",
            "traditional_ira": "Traditional IRA",
            "roth_ira": "Roth IRA",
            "brokerage": "Brokerage",
        }
        return display_names.get(value, value.replace("_", " ").title())

    # --- Context Processors -----------------------------------------------
    _register_context_processors(app)

    # --- Blueprints ------------------------------------------------------
    _register_blueprints(app)

    # --- Error Handlers ---------------------------------------------------
    _register_error_handlers(app)

    # --- Security Headers -------------------------------------------------
    _register_security_headers(app)

    # --- Create schemas (development convenience) ------------------------
    # In production, schemas are managed by Alembic migrations.
    if config_name in ("development", "testing"):
        with app.app_context():
            _ensure_schemas()

    app.logger.info("Shekel app created with config=%s", config_name)
    return app


def _register_context_processors(app):
    """Register Jinja2 context processors."""

    @app.context_processor
    def inject_onboarding():
        """Inject onboarding status so base.html can show/hide the welcome banner."""
        from flask_login import current_user  # pylint: disable=import-outside-toplevel

        if not current_user.is_authenticated:
            return {}

        from sqlalchemy import exists  # pylint: disable=import-outside-toplevel
        from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel

        uid = current_user.id
        has_periods = db.session.query(
            exists().where(PayPeriod.user_id == uid)
        ).scalar()
        has_salary = db.session.query(
            exists().where(SalaryProfile.user_id == uid)
        ).scalar()
        has_templates = db.session.query(
            exists().where(TransactionTemplate.user_id == uid)
        ).scalar()

        return {
            "onboarding": {
                "has_periods": has_periods,
                "has_salary": has_salary,
                "has_templates": has_templates,
                "complete": has_periods and has_salary and has_templates,
            }
        }


def _register_blueprints(app):
    """Import and register all route blueprints."""
    # pylint: disable=import-outside-toplevel
    from app.routes.auth import auth_bp
    from app.routes.grid import grid_bp
    from app.routes.transactions import transactions_bp
    from app.routes.templates import templates_bp
    from app.routes.pay_periods import pay_periods_bp
    from app.routes.accounts import accounts_bp
    from app.routes.categories import categories_bp
    from app.routes.settings import settings_bp
    from app.routes.salary import salary_bp
    from app.routes.transfers import transfers_bp
    from app.routes.savings import savings_bp
    from app.routes.mortgage import mortgage_bp
    from app.routes.auto_loan import auto_loan_bp
    from app.routes.investment import investment_bp
    from app.routes.retirement import retirement_bp
    from app.routes.charts import charts_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(grid_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(pay_periods_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(salary_bp)
    app.register_blueprint(transfers_bp)
    app.register_blueprint(savings_bp)
    app.register_blueprint(mortgage_bp)
    app.register_blueprint(auto_loan_bp)
    app.register_blueprint(investment_bp)
    app.register_blueprint(retirement_bp)
    app.register_blueprint(charts_bp)


def _register_error_handlers(app):
    """Register custom error pages for common HTTP errors."""

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template("errors/500.html"), 500


def _register_security_headers(app):
    """Add security headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response


def _ensure_schemas():
    """Create PostgreSQL schemas if they don't exist (dev/test only)."""
    for schema_name in ("ref", "auth", "budget", "salary", "system"):
        db.session.execute(
            db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        )
    db.session.commit()
