"""
Shekel Budget App -- Application Factory

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

    if not app.config.get("TOTP_ENCRYPTION_KEY"):
        app.logger.warning(
            "TOTP_ENCRYPTION_KEY is not set. MFA/TOTP will be unavailable "
            "until this key is configured. See .env.example for details."
        )

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
        """Load a user by ID for Flask-Login session hydration.

        Returns None (forcing re-login) if the user's sessions have been
        invalidated after the current session was created.
        """
        user = db.session.get(User, int(user_id))
        if user is None:
            return None
        # Check whether this session was created before the most recent
        # "log out all sessions" or password change event.
        if user.session_invalidated_at is not None:
            from flask import session  # pylint: disable=import-outside-toplevel
            session_created = session.get("_session_created_at")
            if session_created is not None:
                from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
                created_dt = datetime.fromisoformat(session_created)
                if created_dt < user.session_invalidated_at:
                    return None
        return user

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
            "money_market": "Money Market",
            "cd": "CD",
            "hsa": "HSA",
            "credit_card": "Credit Card",
            "mortgage": "Mortgage",
            "auto_loan": "Auto Loan",
            "student_loan": "Student Loan",
            "personal_loan": "Personal Loan",
            "heloc": "HELOC",
            "401k": "401(k)",
            "roth_401k": "Roth 401(k)",
            "traditional_ira": "Traditional IRA",
            "roth_ira": "Roth IRA",
            "brokerage": "Brokerage",
            "529": "529 Plan",
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

    # --- Create schemas & seed ref data (development convenience) --------
    # In production, schemas are managed by Alembic migrations and
    # reference data is seeded by the Docker entrypoint.
    if config_name in ("development", "testing"):
        with app.app_context():
            _ensure_schemas()
            _seed_ref_tables()

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
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel

        uid = current_user.id
        has_account = db.session.query(
            exists().where(Account.user_id == uid, Account.is_active.is_(True))
        ).scalar()
        has_categories = db.session.query(
            exists().where(Category.user_id == uid)
        ).scalar()
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
                "has_account": has_account,
                "has_categories": has_categories,
                "has_periods": has_periods,
                "has_salary": has_salary,
                "has_templates": has_templates,
                "complete": has_periods and has_salary and has_templates,
            }
        }

    @app.context_processor
    def inject_recurrence_labels():
        """Inject recurrence pattern labels into all template contexts."""
        return {
            "recurrence_pattern_labels": {
                "every_period": "Every paycheck",
                "every_n_periods": "Every N paychecks",
                "monthly": "Monthly (specific day)",
                "monthly_first": "Monthly (first paycheck of month)",
                "quarterly": "Quarterly",
                "semi_annual": "Every 6 months",
                "annual": "Yearly",
                "once": "One-time",
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
    from app.routes.health import health_bp

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
    app.register_blueprint(health_bp)


def _register_error_handlers(app):
    """Register custom error pages for common HTTP errors."""

    @app.errorhandler(400)
    def bad_request(e):
        """Handle 400 Bad Request errors.

        Common triggers: CSRF token validation failure (Flask-WTF
        rejects the request), malformed form data, or invalid
        request syntax.
        """
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(e):
        """Handle 403 Forbidden errors.

        Common triggers: permission denied, accessing a resource
        that exists but the user is not authorized to view.
        """
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def page_not_found(e):
        """Handle 404 Not Found errors.

        Triggers when the requested URL does not match any route.
        """
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        """Return the 429 error page with a Retry-After header."""
        response = app.make_response(
            (render_template("errors/429.html"), 429)
        )
        # 900 seconds = 15 minutes, matching the rate limit window.
        response.headers["Retry-After"] = "900"
        return response

    @app.errorhandler(500)
    def internal_server_error(e):
        """Handle 500 Internal Server Error.

        Triggers on unhandled exceptions in route handlers or
        service layer code.
        """
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


# Hardcoded allowlist of PostgreSQL schemas used by the application.
# Used by _ensure_schemas() for DDL statements that cannot use bind
# parameters.  Any addition here requires a corresponding Alembic
# migration and updates to tests/conftest.py and scripts/init_db.sql.
_ALLOWED_SCHEMAS = frozenset({"ref", "auth", "budget", "salary", "system"})


def _ensure_schemas():
    """Create PostgreSQL schemas if they do not exist (dev/test only).

    Schema names are validated against _ALLOWED_SCHEMAS, a hardcoded
    frozenset.  DDL identifiers (schema names, table names) cannot use
    bind parameters in PostgreSQL, so an f-string is required.  The
    allowlist ensures only known-safe values are interpolated.
    """
    for schema_name in _ALLOWED_SCHEMAS:
        # DDL identifiers cannot use bind parameters.  Schema names
        # are from _ALLOWED_SCHEMAS -- not user input.
        db.session.execute(
            db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        )
    db.session.commit()


def _seed_ref_tables():
    """Seed reference lookup tables if empty (dev/test only).

    In production, this is handled by the Docker entrypoint.
    Idempotent -- skips rows that already exist.  Silently skips if
    the tables haven't been created yet (e.g. first test-session run
    before create_all()).
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy.exc import ProgrammingError
    from app.models.ref import (
        AccountType, CalcMethod, DeductionTiming, FilingStatus,
        RaiseType, RecurrencePattern, Status, TaxType, TransactionType,
    )

    ref_data = {
        AccountType: [
            "checking", "savings", "hysa", "money_market", "cd", "hsa",
            "credit_card", "mortgage", "auto_loan", "student_loan",
            "personal_loan", "heloc",
            "401k", "roth_401k", "traditional_ira", "roth_ira",
            "brokerage", "529",
        ],
        TransactionType: ["income", "expense"],
        Status: ["projected", "done", "received", "credit", "cancelled", "settled"],
        RecurrencePattern: [
            "every_period", "every_n_periods", "monthly", "monthly_first",
            "quarterly", "semi_annual", "annual", "once",
        ],
        FilingStatus: ["single", "married_jointly", "married_separately", "head_of_household"],
        DeductionTiming: ["pre_tax", "post_tax"],
        CalcMethod: ["flat", "percentage"],
        TaxType: ["flat", "none", "bracket"],
        RaiseType: ["merit", "cola", "custom"],
    }

    try:
        for model, names in ref_data.items():
            for name in names:
                if not db.session.query(model).filter_by(name=name).first():
                    db.session.add(model(name=name))
        db.session.flush()

        # Backfill category on account types.
        category_map = {
            "checking": "asset", "savings": "asset", "hysa": "asset",
            "money_market": "asset", "cd": "asset", "hsa": "asset",
            "credit_card": "liability", "mortgage": "liability",
            "auto_loan": "liability", "student_loan": "liability",
            "personal_loan": "liability", "heloc": "liability",
            "401k": "retirement", "roth_401k": "retirement",
            "traditional_ira": "retirement", "roth_ira": "retirement",
            "brokerage": "investment", "529": "investment",
        }
        for type_name, category in category_map.items():
            at = db.session.query(AccountType).filter_by(name=type_name).first()
            if at and at.category != category:
                at.category = category

        db.session.commit()
    except ProgrammingError:
        db.session.rollback()
