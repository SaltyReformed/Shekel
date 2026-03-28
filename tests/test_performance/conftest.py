"""Fixtures for performance tests.

Provides a larger dataset (52 pay periods = 2-year horizon) to
produce meaningful timing measurements.
"""
import pytest
from datetime import date
from decimal import Decimal

from app.extensions import db as _db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services.auth_service import hash_password


@pytest.fixture()
def perf_user(app, db):
    """Create a test user for performance benchmarks."""
    user = User(
        email="perf@shekel.local",
        password_hash=hash_password("perfpass"),
        display_name="Perf User",
    )
    _db.session.add(user)
    _db.session.flush()

    settings = UserSettings(user_id=user.id)
    _db.session.add(settings)

    checking_type = (
        _db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Perf Checking",
        current_anchor_balance=Decimal("5000.00"),
    )
    _db.session.add(account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    _db.session.add(scenario)
    _db.session.flush()

    category = Category(
        user_id=user.id,
        group_name="Home",
        item_name="Perf Expense",
    )
    _db.session.add(category)
    _db.session.flush()

    _db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "category": category,
    }


@pytest.fixture()
def perf_periods(app, db, perf_user):
    """Create 52 pay periods (2-year horizon) for performance testing."""
    from app.services import pay_period_service

    periods = pay_period_service.generate_pay_periods(
        user_id=perf_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=52,
        cadence_days=14,
    )
    _db.session.flush()

    account = perf_user["account"]
    account.current_anchor_period_id = periods[0].id
    _db.session.commit()

    return periods
