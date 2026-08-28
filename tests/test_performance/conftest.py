"""Fixtures for performance tests.

Provides a larger dataset (52 pay periods = 2-year horizon) to
produce meaningful timing measurements.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType
from app.services.auth_service import hash_password
from app.utils.dates import display_today
from app.services import account_service, pay_period_write

# A 2-year horizon of biweekly paychecks.
PERIOD_COUNT = 52
CADENCE_DAYS = 14


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

    # The paydays are recorded BEFORE the account, and the order is a
    # precondition of the door rather than a preference:
    # ``account_service.create_account`` refuses an owner with no pay
    # periods (``_require_pay_period_schedule``), because the opening
    # balance it posts derives its pay period from the day it asserts and
    # an empty calendar has no such period.
    #
    # The span is anchored to the APP's civil day, not to a literal.  The
    # property these benchmarks need is that today falls INSIDE the
    # recorded calendar -- ``create_account`` defaults its observation day
    # to today and must find a period holding it -- and a hard-coded
    # ``date(2026, 1, 2)`` holds that property only until the horizon runs
    # out, at which point the fixture would start refusing again for a
    # reason that has nothing to do with the code under test.  Half the
    # periods sit behind today and half ahead, on whatever day this runs.
    periods = pay_period_write.record_paydays(
        user_id=user.id,
        first_payday=display_today() - timedelta(days=CADENCE_DAYS * (PERIOD_COUNT // 2)),
        num_periods=PERIOD_COUNT,
        cadence_days=CADENCE_DAYS,
    )
    _db.session.flush()

    checking_type = (
        _db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Perf Checking",
            anchor_balance=Decimal("5000.00"),
        ),
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
        "periods": periods,
    }


@pytest.fixture()
def perf_periods(app, db, perf_user):
    """The 52 pay periods (2-year horizon) ``perf_user`` recorded.

    The calendar is written by ``perf_user`` because the account door
    requires it; this fixture hands back the rows that write produced so
    the two never disagree about the owner's calendar.
    """
    return perf_user["periods"]
