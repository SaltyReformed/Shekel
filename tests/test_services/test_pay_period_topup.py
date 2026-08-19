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

from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
    period_population,
)
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    all_periods,
    assert_pay_period_invariants,
    capture_sql_statements,
    freeze_today,
    make_expense_template,
    seam_cash_balance_at,
    took_advisory_lock,
)


FROZEN_TODAY = date(2026, 6, 15)
_FUTURE_START = date(2026, 7, 3)  # first payday after the frozen today

#: ``seed_user``'s 2024 bootstrap paycheck counts toward the rolling target,
#: and since plan step **C3-b** it always will.  Its stored end was
#: 2024-01-18 -- long past ``FROZEN_TODAY``, so ``_future_period_count``
#: skipped it -- but the writer now materialises the payday derivation, in
#: which a period ends the day before the NEXT payday.  With the next payday
#: at 2026-07-03 the bootstrap covers 2024-01-05..2026-07-02, which CONTAINS
#: the frozen today, so it is the current period rather than a historical one.
#: That is ledger row **P27**'s absorption working as ruled, on the fixture
#: this suite happens to build; the deficit arithmetic below counts it.
_BOOTSTRAP_IN_WINDOW = 1


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` so "current vs future" is deterministic."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _future_periods(db_session, seed_user, count, start=_FUTURE_START):
    """Generate `count` biweekly future periods (indices 1..count)."""
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=start,
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


class TestTopUpFastPaths:
    """The cheap paths: no write work and -- crucially -- no lock taken."""

    def test_no_schedule_row_returns_zero_no_lock(self, app, db, seed_user):
        """A user with no schedule row is a no-op and takes no lock."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _count_periods(db.session, seed_user["user"].id)
            result, statements = capture_sql_statements(
                lambda: pay_period_admin.top_up_rolling_window(
                    seed_user["user"].id,
                )
            )
            assert result == 0
            assert not took_advisory_lock(statements)
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
            result, statements = capture_sql_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            assert result == 0
            assert not took_advisory_lock(statements)
            assert _count_periods(db.session, user_id) == before

    def test_full_window_returns_zero_no_lock(self, app, db, seed_user):
        """future_count >= target returns 0 before the lock; nothing created."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)  # 3 future
            _enable_rolling(db.session, user_id, target=3)
            before = _count_periods(db.session, user_id)
            result, statements = capture_sql_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            assert result == 0
            assert not took_advisory_lock(statements)
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
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 6, 8),
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
            _enable_rolling(db.session, user_id, target=5)
            result, statements = capture_sql_statements(
                lambda: pay_period_admin.top_up_rolling_window(user_id)
            )
            db.session.commit()

            # 5 target - (3 future + the absorbed bootstrap, now current).
            assert result == 5 - (3 + _BOOTSTRAP_IN_WINDOW)
            assert took_advisory_lock(statements)
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
            assert first == 5 - (3 + _BOOTSTRAP_IN_WINDOW)
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
                for p in all_periods(user_id)
            )
            # bootstrap 0 + the 2 seeded + the 3 topped up -> 0..5, no
            # duplicates.  The bootstrap counts toward the target of 6, so the
            # deficit is 3 rather than 4.
            assert indices == list(range(0, 6))

    def test_new_periods_get_recurring_rows(self, app, db, seed_user):
        """Topped-up periods are repopulated with active templates' rows."""
        user_id = seed_user["user"].id
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            _enable_rolling(db.session, user_id, target=5)
            created = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            assert created == 5 - (2 + _BOOTSTRAP_IN_WINDOW)
            new_periods = all_periods(user_id)[-created:]
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

        Anchor $1000 at index 0 (no expense).  A $1200 every-period expense
        fills indices 1..3, so the projected end balance at index N is
        1000 - N*1200.  Rolling target 5 tops up index 4 with the same
        expense, so the projection continues to 1000 - 4*1200 in the new
        window while the retained window is untouched.

        **The deficit is ONE, not two, and plan step C3-b is why**: the
        absorbed bootstrap paycheck now contains ``FROZEN_TODAY`` and counts
        toward the target (see :data:`_BOOTSTRAP_IN_WINDOW`).  The figures
        move with it -- the point of this test is that the projection
        CONTINUES through the top-up, not how many periods it added.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)  # idx 1..3
            make_expense_template(db.session, seed_user, amount="1200.00")
            period_population.populate_periods_from_active_templates(
                user_id, {p.id for p in periods},
            )
            db.session.commit()

            # Retained window before the top-up: 1000 - 2*1200 at index 2.
            retained = seam_cash_balance_at(
                account, scen, periods[1].end_date,
            )
            assert retained == Decimal("-1400.00")

            _enable_rolling(db.session, user_id, target=5)
            created = pay_period_admin.top_up_rolling_window(user_id)
            db.session.commit()
            assert created == 5 - (3 + _BOOTSTRAP_IN_WINDOW)

            # New window: the projection continues to 1000 - 4*1200.
            new_last = all_periods(user_id)[-1]  # idx 4
            assert seam_cash_balance_at(
                account, scen, new_last.end_date,
            ) == Decimal("-3800.00")
            # Retained window untouched.
            assert seam_cash_balance_at(
                account, scen, periods[1].end_date,
            ) == retained

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestTheTopUpCountsOnTheOwnersDay:
    """"How many paychecks are left" is asked of the OWNER's clock.

    Plan step **C2-f3b**, ruled 2026-08-19 with the lock classifier's -- finding
    **balance:N-191** named this door as the second of the two sites deciding
    something against the user's CALENDAR on ``date.today()``.  Both live callers
    (``/grid`` and ``/dashboard``) pass no ``as_of``, so the default IS the
    decision, and an adversarial review of this step found it ungraded.

    Where the clocks differ the owner's day is the EARLIER one, so a period the
    process clock has already retired still counts as future -- the window looks
    one paycheck longer and the top-up appends one fewer.
    """

    def test_the_default_as_of_is_the_display_clock(
        self, app, db, seed_user, monkeypatch,
    ):
        """The clocks are pinned APART and the count follows the display one.

        The schedule is built so exactly one period sits BETWEEN the two clocks:
        it has ended on the process clock (2026-07-31) and has not on the display
        clock (2026-07-30, its own last covered day).  The two counts therefore
        differ by exactly one, and both are asserted -- the process-clock number
        is computed here rather than assumed, so the case cannot pass on a
        schedule where the two happen to agree.
        """
        owner_day = date(2026, 7, 30)
        with app.app_context():
            user_id = seed_user["user"].id
            pay_period_write.record_paydays(
                user_id, date(2026, 7, 3), 4, 14,
            )
            db.session.commit()

            # pylint: disable=protected-access
            on_process = pay_period_admin._future_period_count(
                user_id, date(2026, 7, 31),
            )
            on_owner = pay_period_admin._future_period_count(
                user_id, owner_day,
            )
            assert on_owner == on_process + 1, (on_owner, on_process)

            freeze_today(monkeypatch, date(2026, 7, 31))
            monkeypatch.setattr(
                pay_period_admin, "display_today", lambda: owner_day,
            )
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=on_owner,
            )
            db.session.commit()

            # The window is FULL on the owner's clock and one short on the
            # process clock, so a door reading the process clock appends one.
            assert pay_period_admin.top_up_rolling_window(user_id) == 0
