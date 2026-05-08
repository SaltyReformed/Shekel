"""
Shekel Budget App -- Test Fixtures

Provides reusable pytest fixtures for the test suite: a configured
test app, a clean database session, an authenticated client, and
factory helpers for creating test data.

Strategy: each test gets a fully clean database by truncating all
tables between tests.  This is reliable and avoids the complexity
of nested-transaction rollback with SQLAlchemy 2.0.
"""

# pylint: disable=wrong-import-position,wrong-import-order
# Imports below are intentionally ordered so SECRET_KEY is set in the
# environment BEFORE any ``app`` module is imported.

import os
from datetime import date, timedelta
from decimal import Decimal

# IMPORTANT: SECRET_KEY must be set in the environment BEFORE the
# ``app`` package is imported, because ``app/config.py`` reads it at
# class-definition time via ``os.getenv("SECRET_KEY")``.  Production
# config has no fallback default (audit finding F-016), so without
# this setdefault Flask sessions in the test suite would fail to
# sign or verify.  ``setdefault`` so that a developer running pytest
# with their own real key in the environment is not overridden.
# The value is intentionally distinct from any placeholder rejected
# by ProdConfig and is at least 32 characters.
os.environ.setdefault(
    "SECRET_KEY",
    "test-suite-fixed-key-not-used-in-production-do-not-deploy",
)

import pytest

from app import create_app
from app.extensions import db as _db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.models.ref import (
    AccountType, AccountTypeCategory, CalcMethod, DeductionTiming,
    FilingStatus, GoalMode, IncomeUnit, RaiseType, RecurrencePattern,
    Status, TaxType, TransactionType, UserRole,
)
from app.services.auth_service import hash_password


# --- App & DB Fixtures ---------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fast_bcrypt():
    """Use minimum bcrypt rounds (4) for all tests.

    Bcrypt's default work factor (12) makes each hash take ~250ms.
    Rounds=4 reduces this to ~2ms, saving 10+ seconds across the
    full suite without affecting test correctness.
    """
    import bcrypt as _bcrypt  # pylint: disable=import-outside-toplevel
    _original_gensalt = _bcrypt.gensalt

    def _fast_gensalt(rounds=4, prefix=b"2b"):
        """Generate a bcrypt salt with minimum work factor."""
        return _original_gensalt(rounds=rounds, prefix=prefix)

    _bcrypt.gensalt = _fast_gensalt
    yield
    _bcrypt.gensalt = _original_gensalt


@pytest.fixture(autouse=True)
def set_totp_key(monkeypatch):
    """Set a test TOTP encryption key for all tests."""
    from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def disable_hibp_check(monkeypatch):
    """Disable the HIBP breached-password check by default.

    ``hash_password`` is invoked from dozens of fixtures (every
    ``seed_user`` variant, plus per-test registration helpers) and
    making each one perform an outbound HTTP call would (a) break the
    suite's hermeticity, (b) slow it by an order of magnitude, and
    (c) silently mask test results during HIBP outages.

    Tests that exercise HIBP behaviour explicitly flip this back on
    via ``monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")`` after
    mocking ``requests.get``.  ``monkeypatch`` is function-scoped so
    the override is local to a single test even when the autouse
    fixture has already run.

    See audit finding F-086 / commit C-11 for the production posture
    (default-on) and ``app/services/auth_service.py:_check_pwned_password``
    for the runtime read.
    """
    monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")


@pytest.fixture(scope="session")
def app():
    """Create the Flask application configured for testing."""
    application = create_app("testing")
    yield application


@pytest.fixture(scope="session", autouse=True)
def setup_database(app):
    """One-time database setup: create schemas and tables.

    Runs once at the start of the test session.  Tables are truncated
    between individual tests by the 'db' fixture.
    """
    with app.app_context():
        # Create schemas.
        # DDL identifiers cannot use bind parameters.  Schema names
        # are from a hardcoded tuple -- not user input.
        for schema_name in ("ref", "auth", "budget", "salary", "system"):
            _db.session.execute(
                _db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            )
        _db.session.commit()

        # Create all tables from model metadata.
        _db.create_all()

        # Create audit log infrastructure (not managed by SQLAlchemy models).
        _create_audit_infrastructure()
        _db.session.commit()

        # Seed reference data (these persist across tests since they're
        # read-only lookup tables).
        _seed_ref_tables()
        _db.session.commit()

        # Re-initialize ref_cache now that ref tables are seeded.
        # create_app() tries to init the cache, but the ref tables may
        # not exist yet at that point (the broad except in create_app
        # silently swallows the failure).  Re-init here so that
        # services using ref_cache work correctly in tests.
        _refresh_ref_cache_and_jinja_globals(app)

    yield

    # Teardown: drop all tables after the session.
    with app.app_context():
        _db.drop_all()
        # DDL identifiers cannot use bind parameters.  Schema names
        # are from a hardcoded tuple -- not user input.
        for schema_name in ("ref", "auth", "budget", "salary", "system"):
            _db.session.execute(
                _db.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            )
        _db.session.commit()


@pytest.fixture(autouse=True)
def db(app, setup_database):
    """Provide a clean database for each test.

    Truncates all non-ref tables before each test so tests don't
    interfere with each other.  Reference tables are preserved
    EXCEPT for ``ref.account_types``, which carries an
    ``ON DELETE RESTRICT`` foreign key to ``auth.users`` after
    commit C-28 / F-044 (the column scopes per-user custom types).
    PostgreSQL ``TRUNCATE ... CASCADE`` follows every FK reference
    regardless of ondelete, so truncating ``auth.users`` wipes
    ``ref.account_types`` too -- including the seeded built-ins
    every test relies on.  After the truncate the helper
    ``_seed_ref_tables`` runs again to restore the built-ins
    (idempotent: existing rows are updated in place, missing rows
    inserted; no duplicates because the seed contains 18 unique
    names).  The same problem will affect any future ref-schema
    table that gains a per-user FK; see
    ``app/audit_infrastructure.py`` for the registry side.
    """
    with app.app_context():
        # Clear any stale transaction state from a prior test that
        # raised an exception without committing or rolling back.
        _db.session.rollback()

        # Truncate budget and auth tables (order matters for FK constraints).
        # CASCADE through ``ref.account_types`` is intentional and
        # is repaired below by the ref re-seed.
        _db.session.execute(_db.text(
            "TRUNCATE TABLE "
            "salary.calibration_deduction_overrides, "
            "salary.calibration_overrides, "
            "salary.pension_profiles, "
            "salary.paycheck_deductions, "
            "salary.salary_raises, "
            "salary.salary_profiles, "
            "salary.fica_configs, "
            "salary.state_tax_configs, "
            "salary.tax_brackets, "
            "salary.tax_bracket_sets, "
            "budget.escrow_components, "
            "budget.rate_history, "
            "budget.loan_params, "
            "budget.investment_params, "
            "budget.interest_params, "
            "budget.savings_goals, "
            "budget.transfers, "
            "budget.transfer_templates, "
            "budget.transaction_entries, "
            "budget.transactions, "
            "budget.transaction_templates, "
            "budget.recurrence_rules, "
            "budget.scenarios, "
            "budget.categories, "
            "budget.account_anchor_history, "
            "budget.accounts, "
            "budget.pay_periods, "
            "auth.mfa_configs, "
            "auth.user_settings, "
            "auth.users "
            "CASCADE"
        ))
        # Restore the seeded built-ins that the CASCADE wiped from
        # ``ref.account_types``.  ``_seed_ref_tables`` is idempotent
        # so the call is also a no-op for the other ref tables that
        # the truncate did not touch (the helper short-circuits on
        # rows that already exist).
        _seed_ref_tables()
        _db.session.commit()

        # System schema -- clean audit log AFTER the ref re-seed so
        # the 18 INSERTs the seed fires through the new
        # ``ref.account_types`` audit trigger (commit C-28 / F-044)
        # do not bleed into per-test audit-log assertions.  Order
        # matters: truncating before the seed would leave the seed
        # rows in audit_log, which broke
        # ``tests/test_scripts/test_audit_cleanup.py`` until this
        # ordering was fixed.
        _db.session.execute(_db.text("TRUNCATE system.audit_log"))
        _db.session.commit()

        # The ref_cache is keyed by name -> id but the IDs are stable
        # only for the rows that survived; after a CASCADE-truncate
        # of account_types the seed re-inserts assign fresh IDs.
        # Re-init so cached enum-to-id resolution matches the new
        # row IDs and refresh the Jinja globals that mirror them
        # (the templates read these at render time -- a stale value
        # would point at a deleted ID and break every page that
        # references one).
        _refresh_ref_cache_and_jinja_globals(app)

        yield _db

        # Clean up after each test: rollback any uncommitted work,
        # then close the session and return the connection.  Using
        # remove() instead of just rollback() ensures cleanup even
        # when nested app_context() blocks already called remove().
        _db.session.remove()


@pytest.fixture()
def client(app, db):
    """Provide a Flask test client."""
    return app.test_client()


# --- Data Fixtures --------------------------------------------------------


@pytest.fixture()
def seed_user(app, db):
    """Create and return a test user with settings, account, and scenario.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="test@shekel.local",
        password_hash=hash_password("testpass"),
        display_name="Test User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=Decimal("1000.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    # Create default categories.
    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
    }


@pytest.fixture()
def seed_periods(app, db, seed_user):
    """Generate 10 pay periods starting from 2026-01-02.

    Also sets the anchor period to the first period.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    # Set the anchor period.
    account = seed_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()

    return periods


def _today_relative_start_date():
    """Return start_date that places today in period 4 of a 10-period biweekly run.

    Period 4 is the middle of a 10-period window, leaving 4 historical
    periods and 5 future periods.  The start is aligned to the most
    recent Monday so period boundaries fall on weekdays consistently.
    Used by ``seed_periods_today``-style fixtures so that
    ``pay_period_service.get_current_period`` always returns a real
    period regardless of the wall-clock date.
    """
    today = date.today()
    return today - timedelta(days=today.weekday() + 4 * 14)


@pytest.fixture()
def seed_periods_today(app, db, seed_user):
    """Generate 10 biweekly pay periods so today falls in period 4.

    Use this fixture when the test exercises a code path that calls
    ``pay_period_service.get_current_period()`` (directly or via a
    route handler).  Use the regular ``seed_periods`` fixture when the
    test asserts on specific calendar dates (due_date filters,
    year-end summaries for tax_year=2026, loan origination alignment).

    A test must use one or the other, never both -- they would write
    overlapping pay_periods rows for the same user.

    Returns:
        List of PayPeriod objects, ordered by period_index.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=_today_relative_start_date(),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    # Set the anchor period to the first period so account-level
    # projections start from a valid period reference.
    account = seed_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()

    return periods


@pytest.fixture()
def auth_client(app, db, client, seed_user):
    """Provide an authenticated test client.

    Logs in via the login form to get a proper session.
    """
    resp = client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    })
    assert resp.status_code == 302, (
        f"auth_client login failed with status {resp.status_code}"
    )
    return client


@pytest.fixture()
def second_user(app, db):
    """Create a second user for IDOR and cross-user isolation testing.

    Mirrors the shape of seed_user so the two can be used interchangeably.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
    }


@pytest.fixture()
def seed_periods_52(app, db, seed_user):
    """Generate 52 pay periods (2-year projection) starting from 2026-01-02.

    Sets anchor to the first period.  Use for FIN tests that require
    production-scale data volumes.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=52,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()

    return periods


# --- Two-User Isolation Fixtures ------------------------------------------


@pytest.fixture()
def seed_second_user(app, db):
    """Create an independent second user for multi-user isolation testing.

    Mirrors seed_user in structure but creates entirely separate objects
    with distinguishable names and amounts.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="second@shekel.local",
        password_hash=hash_password("secondpass12"),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=Decimal("2000.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
    }


@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
    """Generate 10 pay periods for the second user starting 2026-01-02.

    Sets the anchor period to the first period.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_second_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_second_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()

    return periods


@pytest.fixture()
def second_auth_client(app, db, seed_second_user):
    """Provide an authenticated test client for the second user.

    Creates a NEW test client instance to avoid session conflicts
    with the primary auth_client.
    """
    second_client = app.test_client()
    resp = second_client.post("/login", data={
        "email": "second@shekel.local",
        "password": "secondpass12",
    })
    assert resp.status_code == 302, (
        f"second_auth_client login failed with status {resp.status_code}"
    )
    return second_client


def _build_full_user_data(db, seed_user, periods):
    """Build the rich-dataset payload shared by seed_full_user_data variants.

    Extracted so both ``seed_full_user_data`` (calendar-anchored) and
    ``seed_full_user_data_today`` (today-relative) can share a single
    body and only differ in which ``periods`` fixture they consume.

    Args:
        db:        SQLAlchemy db extension (the test ``db`` fixture).
        seed_user: dict from the ``seed_user`` fixture.
        periods:   List of PayPeriod objects from a periods fixture.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_user["user"]
    account = seed_user["account"]
    scenario = seed_user["scenario"]

    # Look up reference data.
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Rent Payment",
        default_amount=Decimal("1200.00"),
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_status.id,
        name="Rent Payment",
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("1200.00"),
    )
    db.session.add(txn)

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template.
    savings_account = Account(
        user_id=user.id,
        account_type_id=savings_acct_type.id,
        name="Savings",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(savings_account)
    db.session.flush()

    savings_account.current_anchor_period_id = periods[0].id

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        state_code="NC",
    )
    db.session.add(salary_profile)

    db.session.commit()

    return {
        **seed_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
    """Create a rich dataset for User A (the primary test user).

    Includes transaction template, transaction, savings goal, savings
    account, transfer template, and salary profile. All objects have
    distinguishable names and amounts for use in isolation testing.

    Uses the calendar-anchored ``seed_periods`` fixture, so transactions
    fall in calendar 2026.  Use ``seed_full_user_data_today`` instead
    when the test exercises a route that calls ``get_current_period``.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods)


@pytest.fixture()
def seed_full_user_data_today(app, db, seed_user, seed_periods_today):
    """Today-relative variant of seed_full_user_data.

    Identical payload to ``seed_full_user_data`` except the periods
    are anchored so today falls in period 4.  Use when the test
    exercises a route that internally calls
    ``pay_period_service.get_current_period`` (e.g. /dashboard).

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods_today)


@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
    """Create a rich dataset for User B (the second test user).

    Mirrors seed_full_user_data but with distinguishable names and
    amounts so isolation tests can verify data separation.

    Returns:
        dict merging seed_second_user keys plus: periods, template,
        transaction, savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_second_user["user"]
    account = seed_second_user["account"]
    scenario = seed_second_user["scenario"]
    periods = seed_second_periods

    # Look up reference data.
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=seed_second_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Second User Rent",
        default_amount=Decimal("900.00"),
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_status.id,
        name="Second User Rent",
        category_id=seed_second_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("900.00"),
    )
    db.session.add(txn)

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Vacation Fund",
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template.
    savings_account = Account(
        user_id=user.id,
        account_type_id=savings_acct_type.id,
        name="Savings",
        current_anchor_balance=Decimal("300.00"),
    )
    db.session.add(savings_account)
    db.session.flush()

    savings_account.current_anchor_period_id = periods[0].id

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Bi-Weekly Savings",
        default_amount=Decimal("150.00"),
    )
    db.session.add(transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Second Job",
        annual_salary=Decimal("60000.00"),
        state_code="NC",
    )
    db.session.add(salary_profile)

    db.session.commit()

    return {
        **seed_second_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


# --- Entry and Companion Fixtures -----------------------------------------


@pytest.fixture()
def seed_entry_template(app, db, seed_user, seed_periods):
    """Create a template with is_envelope=True and a transaction.

    The template is an expense-type template tied to the seed_user's checking
    account with a default amount of $500.  A single projected transaction is
    created in the first pay period.

    Returns:
        dict with keys: template, transaction, category, recurrence_rule.
    """
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    category = seed_user["categories"]["Groceries"]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Weekly Groceries",
        default_amount=Decimal("500.00"),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected_status.id,
        name="Weekly Groceries",
        category_id=category.id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.commit()

    return {
        "template": template,
        "transaction": txn,
        "category": category,
        "recurrence_rule": rule,
    }


@pytest.fixture()
def seed_companion(app, db, seed_user):
    """Create a companion user linked to the seed_user owner.

    The companion has role_id set to the companion role and
    linked_owner_id pointing to the primary seed_user.

    Returns:
        dict with keys: user, settings.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

    companion = User(
        email="companion@shekel.local",
        password_hash=hash_password("companionpass"),
        display_name="Companion User",
        role_id=ref_cache.role_id(RoleEnum.COMPANION),
        linked_owner_id=seed_user["user"].id,
    )
    db.session.add(companion)
    db.session.flush()

    settings = UserSettings(user_id=companion.id)
    db.session.add(settings)
    db.session.commit()

    return {
        "user": companion,
        "settings": settings,
    }


@pytest.fixture()
def companion_client(app, db, seed_companion):
    """Provide an authenticated test client for the companion user.

    Creates a new test client instance and logs in as the companion
    user, following the same pattern as second_auth_client.
    """
    comp_client = app.test_client()
    resp = comp_client.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"companion_client login failed with status {resp.status_code}"
    )
    return comp_client


# --- Helpers --------------------------------------------------------------


def _refresh_ref_cache_and_jinja_globals(app):
    """Re-init ``ref_cache`` and rewrite all ID-derived Jinja globals.

    Called from two places:

      1. ``setup_database`` at session start, once the ref tables
         have been seeded for the first time.
      2. The ``db`` fixture, after the per-test TRUNCATE has wiped
         ``ref.account_types`` (via the new C-28 / F-044 FK to
         ``auth.users``) and the seed has been re-run.  The new
         seed assigns fresh IDs from the sequence; the
         pre-existing Jinja globals would otherwise point at IDs
         that no longer exist and every template that references
         one would break.

    Mirrors the ID exposure list in ``app/__init__.py``; missing
    a member here would render a Jinja Undefined at request time
    and fail tests in confusing ways.  The list is duplicated
    (rather than imported) on purpose -- ``app/__init__.py`` runs
    inside ``create_app()`` which is called once per test session,
    while this helper runs once per test, so a single source of
    truth would require restructuring the registration into a
    standalone function the factory calls.  That refactor is
    out of scope for C-28.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import (
        AcctCategoryEnum, AcctTypeEnum, RecurrencePatternEnum,
        StatusEnum, TxnTypeEnum,
    )

    ref_cache.init(_db.session)

    app.jinja_env.globals["STATUS_PROJECTED"] = ref_cache.status_id(StatusEnum.PROJECTED)
    app.jinja_env.globals["STATUS_DONE"] = ref_cache.status_id(StatusEnum.DONE)
    app.jinja_env.globals["STATUS_RECEIVED"] = ref_cache.status_id(StatusEnum.RECEIVED)
    app.jinja_env.globals["STATUS_CREDIT"] = ref_cache.status_id(StatusEnum.CREDIT)
    app.jinja_env.globals["STATUS_CANCELLED"] = ref_cache.status_id(StatusEnum.CANCELLED)
    app.jinja_env.globals["STATUS_SETTLED"] = ref_cache.status_id(StatusEnum.SETTLED)
    app.jinja_env.globals["TXN_TYPE_INCOME"] = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    app.jinja_env.globals["TXN_TYPE_EXPENSE"] = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
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
    app.jinja_env.globals["REC_EVERY_N_PERIODS"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS)
    app.jinja_env.globals["REC_MONTHLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
    app.jinja_env.globals["REC_MONTHLY_FIRST"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY_FIRST)
    app.jinja_env.globals["REC_QUARTERLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.QUARTERLY)
    app.jinja_env.globals["REC_SEMI_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.SEMI_ANNUAL)
    app.jinja_env.globals["REC_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ANNUAL)
    app.jinja_env.globals["REC_ONCE"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE)
    app.jinja_env.globals["ACCT_CAT_ASSET"] = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
    app.jinja_env.globals["ACCT_CAT_LIABILITY"] = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    app.jinja_env.globals["ACCT_CAT_RETIREMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
    app.jinja_env.globals["ACCT_CAT_INVESTMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)


def _create_audit_infrastructure():
    """Create system.audit_log table, trigger function, and triggers.

    Delegates to ``app.audit_infrastructure.apply_audit_infrastructure``
    so the Alembic migration, ``scripts/init_database.py`` (fresh-DB
    bootstrap), and the test-session setup all materialise the same
    schema.  The previous in-line copy of the SQL drifted six tables
    behind the production migration before this consolidation; the
    shared module is the only way to keep the three call sites in
    lock-step.

    Called once during test-session setup because ``create_all()``
    only knows about SQLAlchemy models and the audit infrastructure
    is raw SQL (PL/pgSQL function, row-level triggers, JSONB
    columns) outside the model registry.
    """
    # pylint: disable=import-outside-toplevel  -- avoid pulling app
    # imports at conftest module-load time so SECRET_KEY can be set
    # via os.environ.setdefault() before the first ``app`` import.
    from app.audit_infrastructure import apply_audit_infrastructure

    apply_audit_infrastructure(
        lambda sql: _db.session.execute(_db.text(sql))
    )


def _seed_ref_tables():
    """Populate reference tables for the test database.

    IMPORTANT: These values must match the production seed data in
    app/__init__.py ``_seed_reference_data()``.  Any value that exists
    in production but not here will cause tests to miss behavior that
    depends on that value.  See audit finding H-002.
    """
    # ── Seed AccountTypeCategory (must precede AccountType) ──────
    category_seeds = ["Asset", "Liability", "Retirement", "Investment"]
    for cat_name in category_seeds:
        if not _db.session.query(AccountTypeCategory).filter_by(name=cat_name).first():
            _db.session.add(AccountTypeCategory(name=cat_name))
    _db.session.flush()

    # Build category name->id lookup for AccountType seeding.
    cat_lookup = {
        c.name: c.id
        for c in _db.session.query(AccountTypeCategory).all()
    }

    # ── Seed AccountType with FK, booleans, metadata ──────────────
    from app.ref_seeds import ACCT_TYPE_SEEDS  # pylint: disable=import-outside-toplevel
    for (name, cat_name, has_params, has_amort,
         has_int, is_pre, is_liq, icon, max_term) in ACCT_TYPE_SEEDS:
        existing = _db.session.query(AccountType).filter_by(name=name).first()
        if existing:
            existing.has_parameters = has_params
            existing.has_amortization = has_amort
            existing.has_interest = has_int
            existing.is_pretax = is_pre
            existing.is_liquid = is_liq
            existing.icon_class = icon
            existing.max_term_months = max_term
        else:
            _db.session.add(AccountType(
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
    ref_data = [
        (TransactionType, ["Income", "Expense"]),
        (Status, [
            {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
            {"name": "Paid", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
            {"name": "Received", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
            {"name": "Credit", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
            {"name": "Cancelled", "is_settled": False, "is_immutable": True, "excludes_from_balance": True},
            {"name": "Settled", "is_settled": True, "is_immutable": True, "excludes_from_balance": False},
        ]),
        (RecurrencePattern, [
            "Every Period", "Every N Periods", "Monthly", "Monthly First",
            "Quarterly", "Semi-Annual", "Annual", "Once",
        ]),
        (FilingStatus, [
            "single", "married_jointly", "married_separately",
            "head_of_household",
        ]),
        (DeductionTiming, ["pre_tax", "post_tax"]),
        (CalcMethod, ["flat", "percentage"]),
        (TaxType, ["flat", "none", "bracket"]),
        (RaiseType, ["merit", "cola", "custom"]),
        (GoalMode, ["Fixed", "Income-Relative"]),
        (IncomeUnit, ["Paychecks", "Months"]),
        (UserRole, ["owner", "companion"]),
    ]
    for model_class, entries in ref_data:
        for entry in entries:
            # Entries are either plain strings (name only) or dicts
            # with name + additional columns (e.g. Status booleans).
            if isinstance(entry, dict):
                name = entry["name"]
                existing = (
                    _db.session.query(model_class).filter_by(name=name).first()
                )
                if existing is None:
                    _db.session.add(model_class(**entry))
            else:
                existing = (
                    _db.session.query(model_class).filter_by(name=entry).first()
                )
                if existing is None:
                    _db.session.add(model_class(name=entry))
