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
from app.enums import (
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.exceptions import PayPeriodDiscardRequired, PayPeriodLocked
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    balance_resolver,
    pay_period_admin,
    pay_period_service,
    period_population,
    posting_service,
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


def _emit_untethered_entry(db_session, seed_user, savings, period, amount):
    """Post one balanced entry whose per-account nets are NON-zero in *period*.

    The shape a loan opening / true-up correction has -- balanced across two
    ledger accounts (Checking ``-amount`` / Savings ``+amount``), each with a
    non-zero net, linked to NO transaction or transfer -- so the LEDGER_POSTINGS
    gate is exercised without a settled source row (which would trip the
    higher-precedence SETTLED_TXN lock instead).  Built directly on the models
    (tests are unfenced); the deferred balanced-journal trigger accepts it.
    """
    ledger_ids = {
        account_id: ledger_id
        for ledger_id, account_id in db_session.query(
            LedgerAccount.id, LedgerAccount.account_id,
        ).filter(LedgerAccount.account_id.isnot(None)).all()
    }
    entry = JournalEntry(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period.id,
        entry_date=period.start_date,
        source_kind_id=ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
        description="Untethered balanced correction",
    )
    db_session.add(entry)
    transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
    entry.postings.append(Posting(
        ledger_account_id=ledger_ids[seed_user["account"].id],
        amount=-amount, posting_kind_id=transfer_kind,
    ))
    entry.postings.append(Posting(
        ledger_account_id=ledger_ids[savings.id],
        amount=amount, posting_kind_id=transfer_kind,
    ))
    db_session.flush()
    return entry


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
        """A ZERO-anchor account's anchor period in the window is hard-locked.

        The $0.00 opening books nothing (the zero-delta rule), so the block
        is the ACCOUNT_ANCHOR reason itself, not the LEDGER_POSTINGS gate a
        non-zero opening would trip first (see the companion below).
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
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

    def test_nonzero_anchor_opening_blocks_as_ledger_postings(
        self, app, db, seed_user,
    ):
        """A NON-zero anchor's opening correction hard-locks its period.

        The Step-5 accepted behavior change (plan Section 3.5): a $500.00
        savings anchored to a to-delete period posts its opening correction
        there, so the period's per-ledger nets are non-zero and the
        double-entry gate reports LEDGER_POSTINGS (it precedes
        ACCOUNT_ANCHOR).  Nothing is deleted.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=periods[2].id,  # index 3
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )
            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.LEDGER_POSTINGS
            )
            assert _count_periods(db.session, user_id) == before

    def test_unbalanced_ledger_postings_block_and_delete_nothing(
        self, app, db, seed_user,
    ):
        """A period whose postings do not net to zero per ledger is hard-locked.

        The R2 defense-in-depth gate (the 2026-07-02 adversarial review):
        ``journal_entries.pay_period_id`` is ON DELETE CASCADE and the
        balanced-journal trigger never fires on DELETE, so truncating a period
        holding a NON-self-cancelling entry (the shape a loan opening /
        true-up correction has -- balanced across two ledger accounts, each
        with a non-zero net) would silently mis-state both accounts.  The
        entry here is built directly in that shape (no settled transaction,
        so the pre-existing SETTLED_TXN lock cannot mask the new gate):
        Checking -100 / Savings +100 in a to-delete period.  Truncate must
        refuse with LEDGER_POSTINGS and delete nothing.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=seed_user["bootstrap_period"].id,
            )
            db.session.flush()
            _emit_untethered_entry(
                db.session, seed_user, savings, periods[2],
                Decimal("100.00"),
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_index=periods[1].period_index,
                )

            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.LEDGER_POSTINGS
            )
            assert _count_periods(db.session, user_id) == before
            assert db.session.query(JournalEntry).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 1

    def test_zero_netting_reversal_pair_does_not_block(
        self, app, db, seed_user,
    ):
        """A period whose entries fully cancel per ledger account may truncate.

        The other half of the R2 gate: after a settle-in-F,
        revert-and-move-to-P flow, F holds the original entry AND its reversal
        (the R2 attribution rule keeps the reversal in the period it
        reverses), netting every ledger account to zero.  Cascading that pair
        moves no account's sum, so the gate must NOT block -- truncating F
        succeeds, the moved (now Projected) transaction survives in P, and
        the whole-ledger trial balance stays zero.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            keep_period = seed_user["bootstrap_period"]
            txn = add_txn(
                db.session, seed_user, periods[2], "Early Bill", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            db.session.commit()
            posting_service.sync_transaction_postings(txn, settled=True)
            db.session.commit()

            # The H1 mirror flow: revert AND move back to the kept period.
            txn.pay_period_id = keep_period.id
            txn.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.flush()
            posting_service.sync_transaction_postings(txn, settled=False)
            db.session.commit()
            # F holds the self-cancelling pair; the source row left it.
            assert db.session.query(JournalEntry).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 2

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_index=periods[1].period_index,
                confirm_discard=True,
            )

            assert deleted == 2
            # The moved transaction survives in the kept period; the cascade
            # removed the netted pair without moving any account's sum.
            assert db.session.get(Transaction, txn.id) is not None
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), Decimal("0")),
            ).scalar() == Decimal("0")
            assert_pay_period_invariants(db.session, user_id)

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
