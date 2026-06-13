"""Tests for ``pay_period_admin`` slice (b): the lock classifier, plus the
reusable ``assert_pay_period_invariants`` checker's own self-tests.

The classifier (`classify_period_lock` / `classify_periods_bulk`) is the
single place that decides whether a pay period may be deleted or rebuilt.
Getting it wrong risks either silently wiping real money (a settled
period misread as mutable) or refusing legitimate edits, so every reason
and the precedence between them is asserted here.

The invariant checker is the load-bearing safety net every later
mutation test calls.  A checker that always passes is worse than none,
so its self-tests prove it both PASSES on a healthy schedule and RAISES
on a corrupted one.  See ``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.enums import StatusEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.services import pay_period_admin, pay_period_service
from app.services.pay_period_admin import PeriodLockReason
from tests._test_helpers import add_txn, assert_pay_period_invariants


# Today is well after the seed_user bootstrap period (2024) and before
# these generated periods, so the generated ones are genuinely "future"
# under the default as_of while the bootstrap is historical.
_FUTURE_START = date(2026, 7, 3)
_BOOTSTRAP_AS_OF = date(2024, 1, 1)  # before the bootstrap period ends


def _make_future_periods(db_session, seed_user, count=5):
    """Generate ``count`` future pay periods for ``seed_user``.

    Appended after the fixture's bootstrap period (index 0), so these
    take indices 1..count and all end after today.
    """
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=_FUTURE_START,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _add_rule_anchor(db_session, seed_user, period):
    """Create a recurrence rule whose start period is ``period``."""
    pattern = (
        db_session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=pattern.id,
        start_period_id=period.id,
    )
    db_session.add(rule)
    db_session.flush()
    return rule


class TestClassifyPeriodLock:
    """``classify_period_lock`` returns the correct reason or None."""

    def test_future_empty_period_is_mutable(self, app, db, seed_user):
        """A future period with no settled txn / anchor / rule -> None."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            assert pay_period_admin.classify_period_lock(periods[2]) is None

    def test_historical_period_is_locked(self, app, seed_user):
        """A period that has already ended -> HISTORICAL."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            assert pay_period_admin.classify_period_lock(
                bootstrap, as_of=date(2026, 6, 13),
            ) == PeriodLockReason.HISTORICAL

    def test_settled_transaction_locks(self, app, db, seed_user):
        """A future period holding a Paid (settled) txn -> SETTLED_TXN."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            assert pay_period_admin.classify_period_lock(
                periods[1],
            ) == PeriodLockReason.SETTLED_TXN

    def test_projected_only_period_not_locked(self, app, db, seed_user):
        """A future period holding only a Projected txn -> None (mutable)."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.PROJECTED,
            )
            assert pay_period_admin.classify_period_lock(periods[1]) is None

    def test_soft_deleted_settled_not_locked(self, app, db, seed_user):
        """A soft-deleted settled row does not lock -- the user removed it."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.DONE, is_deleted=True,
            )
            assert pay_period_admin.classify_period_lock(periods[1]) is None

    def test_cancelled_transaction_not_settled_lock(self, app, db, seed_user):
        """A Cancelled txn is not settled, so it does not SETTLED_TXN-lock.

        This is the basis for the discard-gate split: Credit / Cancelled
        are deliberate-intent rows handled by the overridable confirm
        gate, NOT a hard settled lock.
        """
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, periods[1], "Rent", "1200.00",
                status_enum=StatusEnum.CANCELLED,
            )
            assert pay_period_admin.classify_period_lock(periods[1]) is None

    def test_account_anchor_locks(self, app, seed_user):
        """The account's anchor period -> ACCOUNT_ANCHOR (when not historical).

        ``as_of`` is set before the bootstrap period ends so the
        historical check does not pre-empt the anchor reason.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            assert pay_period_admin.classify_period_lock(
                bootstrap, as_of=_BOOTSTRAP_AS_OF,
            ) == PeriodLockReason.ACCOUNT_ANCHOR

    def test_recurrence_anchor_locks(self, app, db, seed_user):
        """A future period that is a rule's start period -> RECURRENCE_ANCHOR."""
        with app.app_context():
            periods = _make_future_periods(db.session, seed_user)
            _add_rule_anchor(db.session, seed_user, periods[3])
            assert pay_period_admin.classify_period_lock(
                periods[3],
            ) == PeriodLockReason.RECURRENCE_ANCHOR

    def test_historical_precedes_settled(self, app, db, seed_user):
        """A historical period with a settled txn still reports HISTORICAL."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            add_txn(
                db.session, seed_user, bootstrap, "Old Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            assert pay_period_admin.classify_period_lock(
                bootstrap, as_of=date(2026, 6, 13),
            ) == PeriodLockReason.HISTORICAL

    def test_settled_precedes_account_anchor(self, app, db, seed_user):
        """A non-historical anchor period with a settled txn reports SETTLED_TXN."""
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            add_txn(
                db.session, seed_user, bootstrap, "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            # Not historical at this as_of, and it IS the account anchor,
            # but the settled txn outranks the anchor reason.
            assert pay_period_admin.classify_period_lock(
                bootstrap, as_of=_BOOTSTRAP_AS_OF,
            ) == PeriodLockReason.SETTLED_TXN


class TestClassifyPeriodsBulk:
    """``classify_periods_bulk`` agrees with N single-period calls."""

    def test_empty_input_returns_empty(self, app):
        """No periods -> empty dict, no queries."""
        with app.app_context():
            assert pay_period_admin.classify_periods_bulk([]) == {}

    def test_bulk_matches_single_across_a_mix(self, app, db, seed_user):
        """Bulk classification equals per-period classification on a mix.

        The user's full set spans HISTORICAL (the 2024 bootstrap),
        SETTLED_TXN, RECURRENCE_ANCHOR, and mutable (None) periods, so a
        single fixed ``as_of`` exercises every branch through both paths.
        """
        as_of = date(2026, 6, 13)
        with app.app_context():
            futures = _make_future_periods(db.session, seed_user)
            add_txn(
                db.session, seed_user, futures[0], "Rent", "1200.00",
                status_enum=StatusEnum.DONE,
            )
            _add_rule_anchor(db.session, seed_user, futures[2])
            db.session.commit()

            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )
            bulk = pay_period_admin.classify_periods_bulk(all_periods, as_of)
            expected = {
                p.id: pay_period_admin.classify_period_lock(p, as_of)
                for p in all_periods
            }
            assert bulk == expected
            # Sanity: the mix actually covers more than one reason.
            assert PeriodLockReason.HISTORICAL in bulk.values()
            assert PeriodLockReason.SETTLED_TXN in bulk.values()
            assert PeriodLockReason.RECURRENCE_ANCHOR in bulk.values()
            assert None in bulk.values()


class TestInvariantChecker:
    """``assert_pay_period_invariants`` passes on healthy, raises on corrupt."""

    def test_passes_on_healthy_schedule(self, app, db, bare_periods):
        """A contiguous, in-order schedule satisfies every invariant."""
        with app.app_context():
            assert_pay_period_invariants(db.session, bare_periods[0].user_id)

    def test_passes_on_full_user_data(self, app, db, seed_full_user_data):
        """A user with accounts, periods, and transactions passes.

        Exercises the anchor-integrity and orphan invariants on real
        account + transaction rows, not just bare periods.
        """
        with app.app_context():
            assert_pay_period_invariants(
                db.session, seed_full_user_data["user"].id,
            )

    def test_raises_when_index_order_differs_from_dates(
        self, app, db, bare_user,
    ):
        """Index order not matching calendar order is caught.

        Two periods are inserted so the lower index has the LATER start
        date -- the exact corruption that makes the balance resolver walk
        periods out of order and silently drop transactions.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 6, 1), end_date=date(2026, 6, 14),
                ),
                PayPeriod(
                    user_id=user_id, period_index=1,
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 14),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="calendar order"):
                assert_pay_period_invariants(db.session, user_id)

    def test_raises_on_index_gap(self, app, db, bare_user):
        """A non-contiguous period_index sequence is caught."""
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 14),
                ),
                PayPeriod(
                    user_id=user_id, period_index=2,
                    start_date=date(2026, 1, 15), end_date=date(2026, 1, 28),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="gap"):
                assert_pay_period_invariants(db.session, user_id)

    def test_raises_on_date_overlap(self, app, db, bare_user):
        """Two periods whose date spans overlap is caught."""
        user_id = bare_user["user"].id
        with app.app_context():
            db.session.add_all([
                PayPeriod(
                    user_id=user_id, period_index=0,
                    start_date=date(2026, 1, 1), end_date=date(2026, 2, 1),
                ),
                PayPeriod(
                    user_id=user_id, period_index=1,
                    start_date=date(2026, 1, 15), end_date=date(2026, 3, 1),
                ),
            ])
            db.session.commit()
            with pytest.raises(AssertionError, match="overlaps"):
                assert_pay_period_invariants(db.session, user_id)
