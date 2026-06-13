"""Tests for pay-period CRUD slice (e): regenerate (rebuild the future tail).

Regenerate = truncate the not-yet-started, unlocked tail, then generate a
fresh schedule from a corrected start/cadence and repopulate it.  It
composes truncate (so it inherits the hard-lock and discard gates) with
generate + populate + a cadence upsert.

``today`` is pinned with ``freeze_today`` so the past / current / future
split is deterministic regardless of when the suite runs.  All four
disciplines apply: structural invariants (Discipline 1), hand-computed
as-of balances across the retained and rebuilt windows (Discipline 2),
the production integrity checker (Discipline 3), and adversarial refusal
tests (Discipline 4).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    ValidationError,
)
from app.enums import StatusEnum
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
    add_txn,
    assert_pay_period_invariants,
    freeze_today,
    make_expense_template,
)


# Pinned "today": with the 2026-05-01 biweekly schedule below, indices
# 1..3 have ended (historical), index 4 (06-12..06-25) is in progress, and
# index 5 onward is the not-yet-started, rebuildable tail.
FROZEN_TODAY = date(2026, 6, 15)
_SPAN_START = date(2026, 5, 1)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` to FROZEN_TODAY for every test in this module."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _spanning_periods(db_session, seed_user, count=8):
    """Generate biweekly periods spanning FROZEN_TODAY (indices 1..count).

    Index 4 (06-12..06-25) is the in-progress period; 1..3 are historical;
    5.. are the rebuildable future tail.
    """
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=_SPAN_START,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _count_periods(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _index_set(user_id):
    """The set of period_index values the user currently has."""
    return {
        p.period_index
        for p in pay_period_service.get_all_periods(user_id)
    }


class TestRegenerateHappyPath:
    """Regenerate keeps the locked/current prefix and rebuilds the tail."""

    def test_rebuilds_tail_keeps_current_and_historical(
        self, app, db, seed_user,
    ):
        """Indices 0..4 (anchor, past, current) stay; 5.. are rebuilt."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            current_index = periods[3].period_index  # index 4
            current_start = periods[3].start_date
            new_start = periods[3].end_date + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            # Bootstrap (0) + retained 1..4 + freshly built 5..7.
            assert _index_set(user_id) == {0, 1, 2, 3, 4, 5, 6, 7}
            assert [p.period_index for p in new_periods] == [5, 6, 7]
            assert new_periods[0].start_date == new_start
            # The in-progress period was not touched.
            kept = db.session.query(PayPeriod).filter_by(
                user_id=user_id, period_index=current_index,
            ).one()
            assert kept.start_date == current_start
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_rebuilt_periods_get_recurring_rows(self, app, db, seed_user):
        """The rebuilt tail is repopulated with active templates' rows."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            new_start = periods[3].end_date + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                cadence_days=14,
            )
            db.session.commit()
            for period in new_periods:
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 1
            assert_pay_period_invariants(db.session, user_id)

    def test_persists_new_cadence(self, app, db, seed_user):
        """Regenerate stores the new cadence and builds at it."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            new_start = periods[3].end_date + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=2,
                cadence_days=7,
            )
            db.session.commit()

            schedule = pay_schedule_service.get_schedule(user_id)
            assert schedule.cadence_days == 7
            assert (
                new_periods[0].end_date - new_periods[0].start_date
            ).days + 1 == 7

    def test_balances_correct_after_regenerate(self, app, db, seed_user):
        """Disciplines 1-3: retained balance unchanged, rebuilt window correct.

        Anchor $1000 at the bootstrap (index 0, no expense); a $1200
        every-period expense fills indices 1..8.  Regenerate keeps 1..4
        and rebuilds 5..8 (repopulated with the same expense), so the end
        balance at index 4 stays 1000 - 4*1200 = -3800 and the new index 8
        is 1000 - 8*1200 = -8600.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            make_expense_template(db.session, seed_user, amount="1200.00")
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()
            retained_end = periods[3].end_date  # index 4
            new_start = retained_end + timedelta(days=1)

            before = balance_resolver.balance_as_of_date(
                account, scen, retained_end,
            )
            assert before == Decimal("-3800.00")  # 1000 - 4*1200

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            after_retained = balance_resolver.balance_as_of_date(
                account, scen, retained_end,
            )
            assert after_retained == before  # retained window untouched

            last = pay_period_service.get_all_periods(user_id)[-1]  # index 8
            assert balance_resolver.balance_as_of_date(
                account, scen, last.end_date,
            ) == Decimal("-8600.00")  # 1000 - 8*1200
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestRegenerateRefusals:
    """Regenerate inherits truncate's lock + discard gates (Discipline 4)."""

    def test_settled_period_in_tail_refuses(self, app, db, seed_user):
        """A settled period inside the rebuildable tail blocks the rebuild."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            add_txn(
                db.session, seed_user, periods[5], "Paid", "100.00",  # index 6
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)
            new_start = periods[3].end_date + timedelta(days=1)

            with pytest.raises(PayPeriodLocked):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=new_start, num_periods=3,
                    cadence_days=14,
                )
            db.session.rollback()
            assert _count_periods(db.session, user_id) == before

    def test_adhoc_row_requires_confirm_then_proceeds(self, app, db, seed_user):
        """A hand-entered row in the tail needs confirmation."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            add_txn(db.session, seed_user, periods[5], "Cash", "50.00")  # idx 6
            db.session.commit()
            new_start = periods[3].end_date + timedelta(days=1)

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=new_start, num_periods=3,
                    cadence_days=14,
                )
            db.session.rollback()

            # With confirmation it rebuilds the tail (discarding the row).
            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                cadence_days=14, confirm_discard=True,
            )
            db.session.commit()
            assert [p.period_index for p in new_periods] == [5, 6, 7]
            assert_pay_period_invariants(db.session, user_id)

    def test_overlapping_new_start_rejected_and_rolls_back(
        self, app, db, seed_user,
    ):
        """A new_start that overlaps the retained schedule is rejected.

        The truncate runs before generate validates the start, so the
        route's rollback (simulated here) must restore the deleted tail --
        nothing partial survives.
        """
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)
            # A start strictly inside the retained current period (not on a
            # boundary, so generate cannot skip it as an existing start)
            # overlaps the retained coverage.
            bad_start = periods[3].start_date + timedelta(days=5)

            with pytest.raises(ValidationError):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=bad_start, num_periods=3,
                    cadence_days=14,
                )
            db.session.rollback()
            assert _count_periods(db.session, user_id) == before
            assert_pay_period_invariants(db.session, user_id)
