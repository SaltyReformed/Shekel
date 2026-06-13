"""Tests for pay-period CRUD slice (d): truncate (the first destructive op).

Truncate deletes the schedule tail via a single bulk DELETE (PostgreSQL
cascades transactions, transfers + both shadows, and anchor history).
Two gates run first: the hard lock classifier (historical / settled /
anchor / rule -- never overridable) and the broadened discard gate
(hand-entered / override / Credit-Cancelled rows -- overridable with
``confirm_discard``).

Because this is the highest-stakes operation in the feature, the suite
carries all four disciplines: structural invariants after every
successful delete (Discipline 1), a hand-computed retained-window balance
(Discipline 2), the production integrity checker (Discipline 3), and the
adversarial refusal tests that assert a bad state is BLOCKED and nothing
is deleted (Discipline 4).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import PayPeriodDiscardRequired, PayPeriodLocked
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    balance_resolver,
    pay_period_admin,
    pay_period_service,
    period_population,
    transfer_service,
)
from app.services.pay_period_admin import PeriodLockReason
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    add_txn,
    assert_pay_period_invariants,
    create_savings_account,
    make_every_period_rule,
    make_expense_template,
    make_transfer_template,
)


def _future_periods(db_session, seed_user, count=6, start=date(2026, 7, 3)):
    """Generate `count` biweekly FUTURE periods (indices 1..count)."""
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=start,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _count_periods(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _txns_in(db_session, period_id):
    """Count all transactions physically held in a period (by id).

    Takes an int id, not a PayPeriod object, so callers can query a
    period AFTER truncate has bulk-deleted (and ``expire_all``-ed) it
    without tripping ``ObjectDeletedError`` on a stale instance.
    """
    return db_session.query(Transaction).filter_by(pay_period_id=period_id).count()


def _make_adhoc_transfer(db_session, seed_user, to_account, period):
    """Create an ad-hoc (no template) projected transfer in a period."""
    xfer = transfer_service.create_transfer(transfer_service.TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("150.00"),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=None,
    ))
    db_session.flush()
    return xfer


class TestTruncateHappyPath:
    """Truncate removes the tail and only the tail."""

    def test_deletes_only_indices_above_keep(self, app, db, seed_user):
        """Indices > keep_through go; indices <= keep_through stay."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[2].period_index,
            )
            db.session.commit()

            assert deleted == 3  # indices 4, 5, 6
            remaining = {
                p.period_index
                for p in pay_period_service.get_all_periods(user_id)
            }
            # Bootstrap (0) + kept future indices 1..3.
            assert remaining == {0, 1, 2, 3}
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_cascade_removes_transactions(self, app, db, seed_user):
        """Deleting a period cascades its transactions away."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()
            doomed_id = periods[3].id  # index 4; capture before deletion
            keep_index = periods[1].period_index
            assert _txns_in(db.session, doomed_id) == 1

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=keep_index,
            )
            db.session.commit()
            # The deleted period's row is gone with it.
            assert _txns_in(db.session, doomed_id) == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_cascade_removes_transfers_and_both_shadows(self, app, db, seed_user):
        """A transfer in a deleted period takes both shadows with it."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=periods[0].id,
            )
            make_transfer_template(db.session, seed_user, savings)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()
            doomed_id = periods[3].id  # capture before deletion
            keep_index = periods[1].period_index
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 1

            # confirm_discard not needed: template transfers are regenerable.
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=keep_index,
            )
            db.session.commit()
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 0
            # No orphaned shadow survived in the deleted period.
            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id == doomed_id,
                Transaction.transfer_id.isnot(None),
            ).count() == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_idempotent_noop_past_max_index(self, app, db, seed_user):
        """Keeping through a too-high index deletes nothing."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=999,
            )
            db.session.commit()
            assert deleted == 0
            assert _count_periods(db.session, user_id) == before

    def test_balances_correct_after_truncate(self, app, db, seed_user):
        """Disciplines 1-3: the retained-window balance is unchanged.

        Anchor $1000 at the bootstrap (index 0, no expense); a $1200
        every-period expense fills indices 1..6.  Truncating to keep index
        3 removes 4..6 but leaves the projection for index 3 exactly
        1000 - 3*1200 = -2600, both before and after.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            make_expense_template(db.session, seed_user, amount="1200.00")
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            before = balance_resolver.balance_as_of_date(
                account, scen, periods[2].end_date,  # index 3
            )
            assert before == Decimal("-2600.00")  # 1000 - 3*1200

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[2].period_index,
            )
            db.session.commit()
            assert deleted == 3

            after = balance_resolver.balance_as_of_date(
                account, scen, periods[2].end_date,
            )
            assert after == before  # retained window untouched
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestTruncateHardLocks:
    """Hard locks refuse the delete and change nothing (Discipline 4)."""

    def test_settled_transaction_blocks_and_deletes_nothing(
        self, app, db, seed_user,
    ):
        """A settled txn in the window raises PayPeriodLocked; nothing goes."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(
                db.session, seed_user, periods[2], "Paid Bill", "100.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )

            assert _count_periods(db.session, user_id) == before
            assert db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 1
            assert_pay_period_invariants(db.session, user_id)

    def test_historical_period_blocks(self, app, db, seed_user):
        """A historical period in the window is hard-locked."""
        with app.app_context():
            user_id = seed_user["user"].id
            # Spanning past->future: early indices have already ended.
            pay_period_service.generate_pay_periods(
                user_id=user_id,
                start_date=date(2026, 1, 2), num_periods=14, cadence_days=14,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=0,
                )
            assert PeriodLockReason.HISTORICAL in excinfo.value.blocking.values()
            assert _count_periods(db.session, user_id) == before

    def test_account_anchor_blocks(self, app, db, seed_user):
        """A second account's anchor period in the window is hard-locked."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=periods[2].id,  # index 3
            )
            db.session.commit()

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )
            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.ACCOUNT_ANCHOR
            )

    def test_recurrence_anchor_blocks(self, app, db, seed_user):
        """A rule's start period in the window is hard-locked."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            rule = make_every_period_rule(db.session, user_id)
            rule.start_period_id = periods[2].id  # index 3
            db.session.commit()

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )
            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.RECURRENCE_ANCHOR
            )

    def test_bulk_delete_of_anchor_period_raises_integrity_error(
        self, app, db, seed_user,
    ):
        """The Phase 0 FK refuses a direct delete of an anchor period.

        The application lock is the first guard; this proves the database
        backstop -- a delete that somehow bypassed the lock raises
        IntegrityError immediately, never silently NULLing the anchor.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            with pytest.raises(IntegrityError):
                db.session.query(PayPeriod).filter(
                    PayPeriod.id == bootstrap.id,
                ).delete(synchronize_session=False)
                db.session.flush()
            db.session.rollback()


class TestTruncateDiscardGate:
    """The overridable discard gate (Discipline 4)."""

    def test_adhoc_row_requires_confirm_then_proceeds(self, app, db, seed_user):
        """A hand-entered row blocks without confirm, deletes with it."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(db.session, seed_user, periods[2], "Cash", "50.00")
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )
            assert excinfo.value.count == 1
            assert _count_periods(db.session, user_id) == before

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[1].period_index,
                confirm_discard=True,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_override_row_requires_confirm(self, app, db, seed_user):
        """A template row marked override needs confirmation."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.is_override = True
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )

    def test_cancelled_row_requires_confirm(self, app, db, seed_user):
        """A Cancelled template row needs confirmation (broadened gate).

        Cancelled is not settled, so it is not hard-locked, but the user's
        cancel decision is not reproducible by regeneration -- so the gate
        must warn before discarding it.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )

    def test_projected_template_rows_need_no_confirm(self, app, db, seed_user):
        """Plain projected template rows are regenerable -- no confirm gate."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[1].period_index,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_recurring_transfer_needs_no_confirm(self, app, db, seed_user):
        """A template transfer is regenerable; its shadows do not trip the gate.

        Transfer shadows always carry template_id IS NULL, so a naive
        ``template_id IS NULL`` gate would falsely flag every recurring
        transfer.  The refined predicate counts transfers on their own
        table, so a template transfer needs no confirmation.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=periods[0].id,
            )
            make_transfer_template(db.session, seed_user, savings)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[1].period_index,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_adhoc_transfer_requires_confirm(self, app, db, seed_user):
        """An ad-hoc (no-template) transfer is not regenerable -- confirm needed."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=periods[0].id,
            )
            _make_adhoc_transfer(db.session, seed_user, savings, periods[2])
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )
