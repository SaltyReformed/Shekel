"""
Shekel Budget App — Test Fixtures

Provides reusable pytest fixtures for the test suite: a configured
test app, a clean database session, an authenticated client, and
factory helpers for creating test data.

Strategy: each test gets a fully clean database by truncating all
tables between tests.  This is reliable and avoids the complexity
of nested-transaction rollback with SQLAlchemy 2.0.
"""

import pytest
from datetime import date
from decimal import Decimal

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
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus,
    RaiseType, RecurrencePattern, Status, TaxType, TransactionType,
)
from app.services.auth_service import hash_password


# --- App & DB Fixtures ---------------------------------------------------


@pytest.fixture(autouse=True)
def set_totp_key(monkeypatch):
    """Set a test TOTP encryption key for all tests."""
    from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())


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

    yield

    # Teardown: drop all tables after the session.
    with app.app_context():
        _db.drop_all()
        for schema_name in ("ref", "auth", "budget", "salary", "system"):
            _db.session.execute(
                _db.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            )
        _db.session.commit()


@pytest.fixture(autouse=True)
def db(app, setup_database):
    """Provide a clean database for each test.

    Truncates all non-ref tables before each test so tests don't
    interfere with each other.  Reference tables are preserved.
    """
    with app.app_context():
        # Truncate budget and auth tables (order matters for FK constraints).
        _db.session.execute(_db.text(
            "TRUNCATE TABLE "
            "salary.pension_profiles, "
            "salary.paycheck_deductions, "
            "salary.salary_raises, "
            "salary.salary_profiles, "
            "salary.fica_configs, "
            "salary.state_tax_configs, "
            "salary.tax_brackets, "
            "salary.tax_bracket_sets, "
            "budget.escrow_components, "
            "budget.mortgage_rate_history, "
            "budget.mortgage_params, "
            "budget.auto_loan_params, "
            "budget.investment_params, "
            "budget.hysa_params, "
            "budget.savings_goals, "
            "budget.transfers, "
            "budget.transfer_templates, "
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
        # System schema — clean audit log between tests.
        _db.session.execute(_db.text("TRUNCATE system.audit_log"))
        _db.session.commit()

        yield _db

        # Clean up after each test.
        _db.session.rollback()


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
        db.session.query(AccountType).filter_by(name="checking").one()
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
        db.session.query(AccountType).filter_by(name="checking").one()
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


# --- Helpers --------------------------------------------------------------


def _create_audit_infrastructure():
    """Create system.audit_log table, trigger function, and triggers.

    Mirrors the Alembic migration for the audit system.  Called once
    during test-session setup because ``create_all()`` only knows about
    SQLAlchemy models and the audit infrastructure is raw SQL.
    """
    # Table
    _db.session.execute(_db.text("""
        CREATE TABLE IF NOT EXISTS system.audit_log (
            id              BIGSERIAL       PRIMARY KEY,
            table_schema    VARCHAR(50)     NOT NULL,
            table_name      VARCHAR(100)    NOT NULL,
            operation       VARCHAR(10)     NOT NULL,
            row_id          INTEGER,
            old_data        JSONB,
            new_data        JSONB,
            changed_fields  TEXT[],
            user_id         INTEGER,
            db_user         VARCHAR(100)    DEFAULT current_user,
            executed_at     TIMESTAMPTZ     DEFAULT now()
        )
    """))
    _db.session.execute(_db.text("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_table
            ON system.audit_log (table_schema, table_name)
    """))
    _db.session.execute(_db.text("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_executed
            ON system.audit_log (executed_at)
    """))
    _db.session.execute(_db.text("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_row
            ON system.audit_log (table_name, row_id)
    """))

    # Trigger function
    _db.session.execute(_db.text(r"""
        CREATE OR REPLACE FUNCTION system.audit_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            v_old_data  JSONB;
            v_new_data  JSONB;
            v_changed   TEXT[] := '{}';
            v_user_id   INTEGER;
            v_row_id    INTEGER;
            v_key       TEXT;
        BEGIN
            BEGIN
                v_user_id := current_setting('app.current_user_id', true)::INTEGER;
            EXCEPTION WHEN OTHERS THEN
                v_user_id := NULL;
            END;

            IF TG_OP = 'DELETE' THEN
                v_old_data := to_jsonb(OLD);
                v_row_id   := OLD.id;
                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     v_old_data, NULL, NULL, v_user_id);
                RETURN OLD;

            ELSIF TG_OP = 'INSERT' THEN
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;
                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     NULL, v_new_data, NULL, v_user_id);
                RETURN NEW;

            ELSIF TG_OP = 'UPDATE' THEN
                v_old_data := to_jsonb(OLD);
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;
                FOR v_key IN
                    SELECT key FROM jsonb_each(v_new_data)
                    WHERE NOT v_old_data ? key
                       OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
                LOOP
                    v_changed := array_append(v_changed, v_key);
                END LOOP;
                IF array_length(v_changed, 1) IS NULL THEN
                    RETURN NEW;
                END IF;
                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     v_old_data, v_new_data, v_changed, v_user_id);
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """))

    # Attach triggers to all audited tables.
    audited_tables = [
        ("budget", "accounts"),
        ("budget", "transactions"),
        ("budget", "transaction_templates"),
        ("budget", "transfers"),
        ("budget", "transfer_templates"),
        ("budget", "savings_goals"),
        ("budget", "recurrence_rules"),
        ("budget", "pay_periods"),
        ("budget", "account_anchor_history"),
        ("budget", "hysa_params"),
        ("budget", "mortgage_params"),
        ("budget", "mortgage_rate_history"),
        ("budget", "escrow_components"),
        ("budget", "auto_loan_params"),
        ("budget", "investment_params"),
        ("salary", "salary_profiles"),
        ("salary", "salary_raises"),
        ("salary", "paycheck_deductions"),
        ("salary", "pension_profiles"),
        ("auth", "users"),
        ("auth", "user_settings"),
        ("auth", "mfa_configs"),
    ]
    for schema, table in audited_tables:
        trigger_name = f"audit_{table}"
        _db.session.execute(_db.text(f"""
            DROP TRIGGER IF EXISTS {trigger_name} ON {schema}.{table}
        """))
        _db.session.execute(_db.text(f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()
        """))


def _seed_ref_tables():
    """Populate reference tables for the test database."""
    ref_data = [
        (AccountType, [
            "checking", "savings", "hysa", "mortgage", "auto_loan",
            "401k", "roth_401k", "traditional_ira", "roth_ira", "brokerage",
        ]),
        (TransactionType, ["income", "expense"]),
        (Status, ["projected", "done", "received", "credit", "cancelled"]),
        (RecurrencePattern, [
            "every_period", "every_n_periods", "monthly",
            "monthly_first", "quarterly", "semi_annual",
            "annual", "once",
        ]),
        (FilingStatus, [
            "single", "married_jointly", "married_separately",
            "head_of_household",
        ]),
        (DeductionTiming, ["pre_tax", "post_tax"]),
        (CalcMethod, ["flat", "percentage"]),
        (TaxType, ["flat", "none", "bracket"]),
        (RaiseType, ["merit", "cola", "custom"]),
    ]
    for model_class, names in ref_data:
        for name in names:
            existing = (
                _db.session.query(model_class).filter_by(name=name).first()
            )
            if existing is None:
                _db.session.add(model_class(name=name))

    # Backfill category on account types.
    _db.session.flush()
    category_map = {
        "checking": "asset", "savings": "asset", "hysa": "asset",
        "mortgage": "liability", "auto_loan": "liability",
        "401k": "retirement", "roth_401k": "retirement",
        "traditional_ira": "retirement", "roth_ira": "retirement",
        "brokerage": "investment",
    }
    for type_name, category in category_map.items():
        at = _db.session.query(AccountType).filter_by(name=type_name).first()
        if at:
            at.category = category
