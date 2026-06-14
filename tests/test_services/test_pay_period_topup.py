"""Tests for pay-period CRUD Phase 2b: the continuous rolling top-up.

``top_up_rolling_window`` keeps a target number of current-and-future
pay periods generated ahead of today.  It is called on every grid /
dashboard load, so the common (disabled / already-full) paths must be
cheap and side-effect-free, and the deficit path must create EXACTLY the
shortfall, idempotently and without ever landing a duplicate
``period_index``.

Because a pay period is the spine of every financial number, the
deficit-path tests assert all four disciplines: structural invariants
(Discipline 1, ``assert_pay_period_invariants``), hand-computed as-of
balances continuing into the new window (Discipline 2), and the
production integrity checker passing (Discipline 3).  The advisory-lock
behaviour (taken only on a real deficit, never on the disabled / full
fast paths) is asserted by capturing the emitted SQL.  Concurrency /
idempotency under true parallel requests lives in
``tests/test_concurrent/test_race_conditions.py``.  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import (
    balance_resolver,
    pay_period_admin,
    pay_period_service,
    pay_schedule_service,
    period_population,
)
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    assert_pay_period_invariants,
    freeze_today,
    make_expense_template,
)


FROZEN_TODAY = date(2026, 6, 15)
_FUTURE_START = date(2026, 7, 3)  # first payday after the frozen today


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` so "current vs future" is deterministic."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _future_periods(db_session, seed_user, count, start=_FUTURE_START):
    """Generate `count` biweekly future periods (indices 1..count)."""
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=start,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _enable_rolling(db_session, user_id, target):
    """Give the user a schedule row with rolling on at ``target``."""
    pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
    pay_schedule_service.set_rolling(user_id, enabled=True, target_periods=target)
    db_session.commit()


def _count_periods(db_session, user_id):
    """Total pay periods owned by the user."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _future_count(db_session, user_id):
    """Current-and-future periods (``end_date >= FROZEN_TODAY``)."""
    return (
        db_session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.end_date >= FROZEN_TODAY,
        )
        .count()
    )


def _capture_statements(fn):
    """Run ``fn`` while recording every SQL statement the engine emits.

    Returns ``(result, statements)``.  Mirrors the statement-capture
    idiom used by the C-19 lock tests to assert -- at the unit level --
    that a lock IS or is NOT acquired on a given path.
    """
    statements: list[str] = []

    def _cap(_conn, _cursor, statement, *_args, **_kwargs):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _cap)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _cap)
    return result, statements


def _took_advisory_lock(statements):
    """True if any captured statement acquired the advisory lock."""
    return any("pg_advisory_xact_lock" in s for s in statements)


class TestTopUpFastPaths:
    """The cheap paths: no write work and -- crucially -- no lock taken."""

    def test_no_schedule_row_returns_zero_no_lock(self, app, db, seed_user):
        """A user with no schedule row is a no-op and takes no lock."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _count_periods(db.session, seed_user["user"].id)
            result, statements = _capture_statements(
                lambda: pay_period_admin.top_up_rolling_window(
                    seed_user["user"].id,
                )
            )
            assert result == 0
            assert not _took_advisory_lock(statements)
            assert _count_periods(db.session, seed_user["user"].id) == before

    def test_disabled_returns_zero_no_lock(self, app, db, seed_user):
        """Rolling disabled -> 0, no write, no advisory lock taken."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            # Row exists but rolling is off (the column default).
            pay_schedule_service.upsert_schedule(user_id, cadence_days=14)
            db.session.commit()
            before = _count_periods(db.session, user_id)
            result, statements = _capture_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            assert result == 0
            assert not _took_advisory_lock(statements)
            assert _count_periods(db.session, user_id) == before

    def test_full_window_returns_zero_no_lock(self, app, db, seed_user):
        """future_count >= target returns 0 before the lock; nothing created."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)  # 3 future
            _enable_rolling(db.session, user_id, target=3)
            before = _count_periods(db.session, user_id)
            result, statements = _capture_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            assert result == 0
            assert not _took_advisory_lock(statements)
            assert _count_periods(db.session, user_id) == before

    def test_current_period_counts_toward_target(self, app, db, seed_user):
        """The period containing today counts as one of the N kept ahead.

        With exactly one period spanning the frozen today (so
        ``end_date >= today``) and a target of 1, the window is already
        satisfied and the top-up creates nothing -- proof that "keep N
        ahead" counts the current period, not only strictly-future ones.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            # 06-08..06-21 contains the frozen today (06-15).
            pay_period_service.generate_pay_periods(
                user_id=user_id, start_date=date(2026, 6, 8),
                num_periods=1, cadence_days=14,
            )
            db.session.commit()
            _enable_rolling(db.session, user_id, target=1)
            before = _count_periods(db.session, user_id)
            assert pay_period_admin.top_up_rolling_window(user_id) == 0
            assert _count_periods(db.session, user_id) == before


class TestTopUpDeficitPath:
    """The deficit path creates exactly the shortfall, idempotently."""

    def test_deficit_creates_exactly_deficit_and_locks(
        self, app, db, seed_user,
    ):
        """A deficit of D creates exactly D periods and takes the lock."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)  # idx 1..3 future
            _enable_rolling(db.session, user_id, target=5)  # deficit 2
            result, statements = _capture_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            db.session.commit()

            assert result == 2  # 5 target - 3 future
            assert _took_advisory_lock(statements)
            # The window now holds exactly the target.
            assert _future_count(db.session, user_id) == 5
            # Disciplines 1 + 3.
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_second_call_is_idempotent_noop(self, app, db, seed_user):
        """Once the window is full, a second top-up creates nothing."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            _enable_rolling(db.session, user_id, target=5)
            first = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            second = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            assert first == 2
            assert second == 0
            assert _future_count(db.session, user_id) == 5
            assert_pay_period_invariants(db.session, user_id)

    def test_no_duplicate_index_after_topup(self, app, db, seed_user):
        """Topped-up periods keep a unique, contiguous index sequence."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)  # idx 1..2
            _enable_rolling(db.session, user_id, target=6)
            pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            indices = sorted(
                p.period_index
                for p in pay_period_service.get_all_periods(user_id)
            )
            # bootstrap 0 + idx 1..6 -> 0..6, no duplicates.
            assert indices == list(range(0, 7))

    def test_new_periods_get_recurring_rows(self, app, db, seed_user):
        """Topped-up periods are repopulated with active templates' rows."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            _enable_rolling(db.session, user_id, target=5)  # deficit 3
            created = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            assert created == 3
            new_periods = pay_period_service.get_all_periods(user_id)[-3:]
            for period in new_periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert txns[0].estimated_amount == Decimal("1200.00")

    def test_balances_correct_after_topup(self, app, db, seed_user):
        """Discipline 2: as-of balances continue correctly into the new window.

        Anchor $1000 at index 0 (no expense).  A $1200 every-period
        expense fills indices 1..3, so the projected end balance at index
        N is 1000 - N*1200.  Rolling target 5 tops up indices 4..5 with
        the same expense, so the projection continues to 1000 - 5*1200 in
        the new window while the retained window is untouched.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)  # idx 1..3
            make_expense_template(db.session, seed_user, amount="1200.00")
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            # Retained window before the top-up: 1000 - 2*1200 at index 2.
            retained = balance_resolver.balance_as_of_date(
                account, scen, periods[1].end_date,
            )
            assert retained == Decimal("-1400.00")

            _enable_rolling(db.session, user_id, target=5)  # deficit 2 -> idx 4,5
            created = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            assert created == 2

            # New window: the projection continues to 1000 - 5*1200.
            new_last = pay_period_service.get_all_periods(user_id)[-1]  # idx 5
            assert balance_resolver.balance_as_of_date(
                account, scen, new_last.end_date,
            ) == Decimal("-5000.00")
            # Retained window untouched.
            assert balance_resolver.balance_as_of_date(
                account, scen, periods[1].end_date,
            ) == retained

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))
