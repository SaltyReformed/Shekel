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
    AccountType, TransactionType, Status, RecurrencePattern,
)
from app.services.auth_service import hash_password


# --- App & DB Fixtures ---------------------------------------------------


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
            "budget.savings_goals, "
            "budget.transfers, "
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
    client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    })
    return client


# --- Helpers --------------------------------------------------------------


def _seed_ref_tables():
    """Populate reference tables for the test database."""
    ref_data = [
        (AccountType, ["checking", "savings"]),
        (TransactionType, ["income", "expense"]),
        (Status, ["projected", "done", "received", "credit", "cancelled"]),
        (RecurrencePattern, [
            "every_period", "every_n_periods", "monthly",
            "monthly_first", "quarterly", "semi_annual",
            "annual", "once",
        ]),
    ]
    for model_class, names in ref_data:
        for name in names:
            existing = (
                _db.session.query(model_class).filter_by(name=name).first()
            )
            if existing is None:
                _db.session.add(model_class(name=name))
