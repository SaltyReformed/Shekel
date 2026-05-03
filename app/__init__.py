"""
Shekel Budget App -- Application Factory

Creates and configures the Flask application.  Call create_app()
with an optional config_name ('development', 'testing', 'production')
to get a fully wired Flask instance.
"""

import os

import sqlalchemy.exc
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
        # Flask-Login's UserMixin.is_authenticated always returns True.
        # We must explicitly reject inactive users here so that deactivating
        # a user immediately invalidates all of their existing sessions.
        if not user.is_active:
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
        """Convert an account type name to a user-friendly display string.

        After the Commit #2 migration, database names are already
        stored as properly formatted display strings (e.g. 'HYSA',
        '401(k)').  This filter now acts as a pass-through for all
        seeded types but retains the signature so existing templates
        continue to work without modification.
        """
        if value is None:
            return ""
        return value

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

    # --- Reference Cache & Jinja Globals ---------------------------------
    # Initialize the ref_cache after seeding so enum members resolve to
    # database IDs.  Then expose cached status IDs as Jinja globals so
    # templates can compare status_id without querying the database.
    #
    # The init may fail during migrations (columns not yet added) or on
    # a fresh database before the first migration.  In that case, log a
    # warning and skip -- the cache will initialize on the next startup
    # after the migration completes.
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import (  # pylint: disable=import-outside-toplevel
        AcctCategoryEnum, AcctTypeEnum, CalcMethodEnum,
        DeductionTimingEnum, GoalModeEnum, IncomeUnitEnum,
        RecurrencePatternEnum, StatusEnum, TxnTypeEnum,
    )
    try:
        with app.app_context():
            ref_cache.init(db.session)

        # Status IDs
        app.jinja_env.globals["STATUS_PROJECTED"] = ref_cache.status_id(StatusEnum.PROJECTED)
        app.jinja_env.globals["STATUS_DONE"] = ref_cache.status_id(StatusEnum.DONE)
        app.jinja_env.globals["STATUS_RECEIVED"] = ref_cache.status_id(StatusEnum.RECEIVED)
        app.jinja_env.globals["STATUS_CREDIT"] = ref_cache.status_id(StatusEnum.CREDIT)
        app.jinja_env.globals["STATUS_CANCELLED"] = ref_cache.status_id(StatusEnum.CANCELLED)
        app.jinja_env.globals["STATUS_SETTLED"] = ref_cache.status_id(StatusEnum.SETTLED)

        # Transaction type IDs
        app.jinja_env.globals["TXN_TYPE_INCOME"] = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        app.jinja_env.globals["TXN_TYPE_EXPENSE"] = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

        # Account type IDs -- all types registered so templates can use
        # integer comparisons instead of string-based name checks.
        app.jinja_env.globals["ACCT_TYPE_CHECKING"] = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
        app.jinja_env.globals["ACCT_TYPE_SAVINGS"] = ref_cache.acct_type_id(AcctTypeEnum.SAVINGS)
        app.jinja_env.globals["ACCT_TYPE_HYSA"] = ref_cache.acct_type_id(AcctTypeEnum.HYSA)
        app.jinja_env.globals["ACCT_TYPE_MONEY_MARKET"] = ref_cache.acct_type_id(AcctTypeEnum.MONEY_MARKET)
        app.jinja_env.globals["ACCT_TYPE_CD"] = ref_cache.acct_type_id(AcctTypeEnum.CD)
        app.jinja_env.globals["ACCT_TYPE_HSA"] = ref_cache.acct_type_id(AcctTypeEnum.HSA)
        app.jinja_env.globals["ACCT_TYPE_CREDIT_CARD"] = ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)
        app.jinja_env.globals["ACCT_TYPE_MORTGAGE"] = ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE)
        app.jinja_env.globals["ACCT_TYPE_AUTO_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN)
        app.jinja_env.globals["ACCT_TYPE_STUDENT_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.STUDENT_LOAN)
        app.jinja_env.globals["ACCT_TYPE_PERSONAL_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.PERSONAL_LOAN)
        app.jinja_env.globals["ACCT_TYPE_HELOC"] = ref_cache.acct_type_id(AcctTypeEnum.HELOC)
        app.jinja_env.globals["ACCT_TYPE_401K"] = ref_cache.acct_type_id(AcctTypeEnum.K401)
        app.jinja_env.globals["ACCT_TYPE_ROTH_401K"] = ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K)
        app.jinja_env.globals["ACCT_TYPE_TRADITIONAL_IRA"] = ref_cache.acct_type_id(AcctTypeEnum.TRADITIONAL_IRA)
        app.jinja_env.globals["ACCT_TYPE_ROTH_IRA"] = ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA)
        app.jinja_env.globals["ACCT_TYPE_BROKERAGE"] = ref_cache.acct_type_id(AcctTypeEnum.BROKERAGE)
        app.jinja_env.globals["ACCT_TYPE_529"] = ref_cache.acct_type_id(AcctTypeEnum.PLAN_529)

        # Recurrence pattern IDs
        app.jinja_env.globals["REC_EVERY_N_PERIODS"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS)
        app.jinja_env.globals["REC_MONTHLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
        app.jinja_env.globals["REC_MONTHLY_FIRST"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY_FIRST)
        app.jinja_env.globals["REC_QUARTERLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.QUARTERLY)
        app.jinja_env.globals["REC_SEMI_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.SEMI_ANNUAL)
        app.jinja_env.globals["REC_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ANNUAL)
        app.jinja_env.globals["REC_ONCE"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE)

        # Account category IDs
        app.jinja_env.globals["ACCT_CAT_ASSET"] = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
        app.jinja_env.globals["ACCT_CAT_LIABILITY"] = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
        app.jinja_env.globals["ACCT_CAT_RETIREMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
        app.jinja_env.globals["ACCT_CAT_INVESTMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)

        # Deduction timing IDs
        app.jinja_env.globals["TIMING_PRE_TAX"] = ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
        app.jinja_env.globals["TIMING_POST_TAX"] = ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)

        # Calc method IDs
        app.jinja_env.globals["CALC_PERCENTAGE"] = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        app.jinja_env.globals["CALC_FLAT"] = ref_cache.calc_method_id(CalcMethodEnum.FLAT)

        # Goal mode IDs
        app.jinja_env.globals["GOAL_MODE_FIXED"] = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

        # Income unit IDs
        app.jinja_env.globals["INCOME_UNIT_PAYCHECKS"] = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        app.jinja_env.globals["INCOME_UNIT_MONTHS"] = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as exc:
        app.logger.warning(
            "ref_cache initialization skipped (%s). "
            "Jinja globals will not be available until next restart.",
            type(exc).__name__,
        )

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

        # Onboarding is meaningless for companion users -- they share the
        # linked owner's budget data via linked_owner_id and cannot create
        # their own accounts, categories, pay periods, salary profiles, or
        # templates.  Omit the dict entirely so the banner's `onboarding is
        # defined` guard in base.html evaluates False, and skip the five
        # exists() queries that would otherwise run on every companion page.
        from app import ref_cache as _rc  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum as _RoleEnum  # pylint: disable=import-outside-toplevel
        try:
            if current_user.role_id == _rc.role_id(_RoleEnum.COMPANION):
                return {}
        except (RuntimeError, KeyError):
            # ref_cache not yet initialized (e.g. during migration).  Fall
            # through to the existing query path; owner users are the common
            # case during those windows and the queries still give the right
            # answer.
            pass

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
    def inject_role_ids():
        """Make role IDs available in all templates for role-based rendering."""
        from app import ref_cache as _rc  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum as _RoleEnum  # pylint: disable=import-outside-toplevel
        try:
            return {"COMPANION_ROLE_ID": _rc.role_id(_RoleEnum.COMPANION)}
        except (RuntimeError, KeyError):
            # ref_cache not yet initialized (e.g. during migration).
            return {"COMPANION_ROLE_ID": None}

    @app.context_processor
    def inject_recurrence_labels():
        """Inject recurrence pattern labels into all template contexts."""
        return {
            "recurrence_pattern_labels": {
                "Every Period": "Every paycheck",
                "Every N Periods": "Every N paychecks",
                "Monthly": "Monthly (specific day)",
                "Monthly First": "Monthly (first paycheck of month)",
                "Quarterly": "Quarterly",
                "Semi-Annual": "Every 6 months",
                "Annual": "Yearly",
                "Once": "One-time",
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
    from app.routes.loan import loan_bp
    from app.routes.investment import investment_bp
    from app.routes.retirement import retirement_bp
    from app.routes.charts import charts_bp
    from app.routes.analytics import analytics_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.debt_strategy import debt_strategy_bp
    from app.routes.obligations import obligations_bp
    from app.routes.health import health_bp
    from app.routes.entries import entries_bp
    from app.routes.companion import companion_bp

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
    app.register_blueprint(loan_bp)
    app.register_blueprint(investment_bp)
    app.register_blueprint(retirement_bp)
    app.register_blueprint(charts_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(debt_strategy_bp)
    app.register_blueprint(obligations_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(entries_bp)
    app.register_blueprint(companion_bp)


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
        service layer code.  The rollback clears any failed transaction
        so context-processor queries (e.g. inject_onboarding) can run
        and the custom error template renders instead of a blank page.
        """
        db.session.rollback()
        return render_template("errors/500.html"), 500


# Content Security Policy directives.  Bound at module load (not on
# every request) because the policy is static -- there is no per-request
# nonce or hash.  Built as a tuple so the join order is stable and
# reviewable in diffs.
#
# Each origin must be 'self'.  All third-party JS, CSS, and fonts are
# vendored under app/static/vendor/ (see app/static/vendor/VERSIONS.txt)
# so external origins can be dropped entirely.  See audit findings
# F-036, F-037, F-097.
_CSP_DIRECTIVES = (
    "default-src 'self'",
    # Scripts: self only.  No CDN origins, no 'unsafe-inline', no
    # 'unsafe-eval'.  Inline event handlers (onclick=, onchange=) and
    # inline <script> blocks are blocked by this policy; all behaviour
    # is in external JS files under app/static/js/.
    "script-src 'self'",
    # Styles: self only.  No 'unsafe-inline' (closes the CSS attribute-
    # selector keylogging path documented in F-036).  No CDN origins.
    # Inline style="..." attributes are blocked.  Dynamic per-element
    # styling (e.g. progress-bar widths) lives behind data-* attributes
    # and a tiny JS module that sets el.style.* via DOM property setters
    # (which are allowed by script-src 'self', not style-src).
    "style-src 'self'",
    # Fonts: self only.  Inter and JetBrains Mono are vendored; Bootstrap
    # Icons font travels with its CSS.
    "font-src 'self'",
    # Images: self plus data: URIs (used for inline favicons and the
    # MFA QR code rendered as a data: URL by mfa_service).
    "img-src 'self' data:",
    # XHR / fetch / WebSocket: self only.  HTMX uses fetch under the hood.
    "connect-src 'self'",
    # Modern clickjacking control.  Authoritative on browsers that
    # implement CSP Level 2; X-Frame-Options: DENY (set below) is the
    # legacy fallback.  See audit finding F-097.
    "frame-ancestors 'none'",
    # Locks the document base URL to this origin so injected <base href=>
    # cannot redirect relative URLs through an attacker-controlled prefix.
    "base-uri 'self'",
    # Form posts must target this origin.  Defends against an injected
    # <form action="https://evil/"> exfiltrating credentials.
    "form-action 'self'",
)
_CSP_HEADER = "; ".join(_CSP_DIRECTIVES)


def _register_security_headers(app):
    """Add security headers to every response.

    The headers and the CSP closed in this hook implement the audit
    Phase-1 hardening bundle (Commit C-02).  See findings F-017, F-018,
    F-019, F-036, F-037, F-096, F-097 for the per-control rationale.
    """
    # pylint: disable=import-outside-toplevel
    from flask import request

    @app.after_request
    def set_security_headers(response):
        # Defense-in-depth headers.  X-Content-Type-Options blocks MIME
        # sniffing; X-Frame-Options is the legacy clickjacking control
        # superseded by CSP frame-ancestors but still useful for older
        # browsers.  Referrer-Policy avoids leaking full URLs to
        # third-party origins on outbound clicks.  Permissions-Policy
        # disables three sensors the app never uses.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = _CSP_HEADER

        # HSTS: 1 year max-age, includeSubDomains.  All Shekel traffic
        # already arrives via HTTPS (Cloudflare Tunnel + nginx), so the
        # header simply locks browsers into that posture for a year.
        # 'preload' is intentionally OFF.  Adding 'preload' is a one-way
        # commitment to the public HSTS preload list (delisting takes
        # months); see docs/runbook.md "HSTS preload" for the procedure
        # to enable it later if desired.  Audit finding F-018.
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # Cache-Control: no-store on every dynamic response so a
        # logged-out user cannot reconstruct authenticated pages from
        # the browser back button.  Audit finding F-019.
        #
        # Static assets are intentionally excluded -- they carry no
        # session data, are versioned by content (vendor/), and need
        # to be cacheable so the user does not re-fetch Bootstrap on
        # every navigation.  In production nginx serves /static/
        # before Flask sees the request and sets its own
        # ``Cache-Control: public, immutable``; in dev/test Flask's
        # built-in static handler is used and we must opt out here.
        if request.endpoint != "static":
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate"
            )
            # HTTP/1.0 fallback for caches that ignore Cache-Control.
            response.headers["Pragma"] = "no-cache"
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
        AccountType, AccountTypeCategory, CalcMethod, DeductionTiming,
        FilingStatus, GoalMode, IncomeUnit, RaiseType, RecurrencePattern,
        Status, TaxType, TransactionType, UserRole,
    )

    try:
        # ── Seed AccountTypeCategory (must precede AccountType) ──────
        category_seeds = ["Asset", "Liability", "Retirement", "Investment"]
        for cat_name in category_seeds:
            if not db.session.query(AccountTypeCategory).filter_by(name=cat_name).first():
                db.session.add(AccountTypeCategory(name=cat_name))
        db.session.flush()

        # Build category name->id lookup for AccountType seeding.
        cat_lookup = {
            c.name: c.id
            for c in db.session.query(AccountTypeCategory).all()
        }

        # ── Seed AccountType with FK, booleans, metadata ──────────────
        from app.ref_seeds import ACCT_TYPE_SEEDS as acct_type_seeds  # pylint: disable=import-outside-toplevel
        for (name, cat_name, has_params, has_amort,
             has_int, is_pre, is_liq, icon, max_term) in acct_type_seeds:
            existing = db.session.query(AccountType).filter_by(name=name).first()
            if existing:
                existing.has_parameters = has_params
                existing.has_amortization = has_amort
                existing.has_interest = has_int
                existing.is_pretax = is_pre
                existing.is_liquid = is_liq
                existing.icon_class = icon
                existing.max_term_months = max_term
            else:
                db.session.add(AccountType(
                    name=name,
                    category_id=cat_lookup[cat_name],
                    has_parameters=has_params,
                    has_amortization=has_amort,
                    has_interest=has_int,
                    is_pretax=is_pre,
                    is_liquid=is_liq,
                    icon_class=icon,
                    max_term_months=max_term,
                ))

        # ── Seed remaining ref tables ────────────────────────────────
        ref_data = {
            TransactionType: ["Income", "Expense"],
            Status: [
                {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
                {"name": "Paid", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
                {"name": "Received", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
                {"name": "Credit", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
                {"name": "Cancelled", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
                {"name": "Settled", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
            ],
            RecurrencePattern: [
                "Every Period", "Every N Periods", "Monthly", "Monthly First",
                "Quarterly", "Semi-Annual", "Annual", "Once",
            ],
            FilingStatus: ["single", "married_jointly", "married_separately", "head_of_household"],
            DeductionTiming: ["pre_tax", "post_tax"],
            CalcMethod: ["flat", "percentage"],
            TaxType: ["flat", "none", "bracket"],
            RaiseType: ["merit", "cola", "custom"],
            GoalMode: ["Fixed", "Income-Relative"],
            IncomeUnit: ["Paychecks", "Months"],
            UserRole: ["owner", "companion"],
        }

        for model, entries in ref_data.items():
            for entry in entries:
                if isinstance(entry, dict):
                    name = entry["name"]
                    if not db.session.query(model).filter_by(name=name).first():
                        db.session.add(model(**entry))
                else:
                    if not db.session.query(model).filter_by(name=entry).first():
                        db.session.add(model(name=entry))

        db.session.commit()
    except ProgrammingError:
        db.session.rollback()
