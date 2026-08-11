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

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodUnresolved,
)
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    pay_period_admin,
    pay_period_service,
    pay_period_write,
    period_population,
    posting_service,
    transfer_service,
)
from app.services.pay_period_locks import PeriodLockReason
# The APP's civil day, never ``date.today()`` -- these fixtures build periods
# that must CONTAIN the day the account factory stamps on its origination
# assertion, and that day is the display-timezone one (CI runs a zone where
# the two differ, deliberately).
from app.utils.dates import display_today
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
    seam_cash_balance_at,
)


def _future_periods(db_session, seed_user, count=6, start=date(2026, 7, 3)):
    """Generate `count` biweekly FUTURE periods (indices 1..count)."""
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=start,
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
                user_id, keep_through_period_id=periods[2].id,
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
            keep_period_id = periods[1].id
            assert _txns_in(db.session, doomed_id) == 1

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
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
            )
            make_transfer_template(db.session, seed_user, savings)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()
            doomed_id = periods[3].id  # capture before deletion
            keep_period_id = periods[1].id
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 1

            # confirm_discard not needed: template transfers are regenerable.
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
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

    def test_idempotent_noop_through_the_last_period(self, app, db, seed_user):
        """Keeping through the LAST period deletes nothing.

        Truncate's no-op case.  It used to be spelled ``keep_through_index=999``
        -- an ordinal above every real one -- and plan step C3-a removed the
        spelling with the ordinal: an id above every real id names no period,
        which is now a REFUSAL rather than a no-op (see
        ``TestTruncateRefusesAnIdItCannotResolve``).  Naming the actual last
        period is the same question asked of the surviving key.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[-1].id,
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

            before = seam_cash_balance_at(
                account, scen, periods[2].end_date,  # index 3
            )
            assert before == Decimal("-2600.00")  # 1000 - 3*1200

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[2].id,
            )
            db.session.commit()
            assert deleted == 3

            after = seam_cash_balance_at(
                account, scen, periods[2].end_date,
            )
            assert after == before  # retained window untouched
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


# Three ACCOUNT_ANCHOR cases were DELETED at plan step X-f1c3c (ruling R-EO):
# truncate blocking on a period an account anchored to, the same for a period
# holding no correction, and the raw bulk-DELETE raising an IntegrityError on
# the anchor FK.  All three graded a lock and an FK that are gone -- neither an
# account nor a balance assertion references a pay period, so no period delete
# can strand one.  The period's POSTED state is what is still worth refusing,
# and ``LEDGER_POSTINGS`` (which outranked ``ACCOUNT_ANCHOR`` in the precedence
# anyway) covers it; its own cases in this class are untouched.


class TestTruncateRefusesAnIdItCannotResolve:
    """Finding P13: the wire key names a ROW, so an unowned one is refused.

    Until plan step C3-a the truncate form posted ``period_index``, and the
    service compared it with ``>``.  Every value the comparison accepted
    "worked": an ordinal from a stale page named whichever period now sits at
    that position, and one above the end was a silent no-op.  Keyed on ``id``
    there is exactly one correct answer -- the row -- and everything else is
    refused before the classifier, the discard gate or the DELETE runs.
    """

    def test_an_id_that_names_no_period_is_refused(self, app, db, seed_user):
        """A forged or stale id deletes nothing and says so."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)
            absent = max(p.id for p in periods) + 1000

            with pytest.raises(PayPeriodUnresolved) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=absent,
                )

            assert str(absent) in str(excinfo.value)
            assert _count_periods(db.session, user_id) == before

    def test_another_owners_period_is_refused_identically(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real id belonging to someone else raises the SAME message.

        The security response rule: "not yours" and "does not exist" must be
        indistinguishable, or this door reports whether another owner's period
        id exists.  Both are asserted against the same rendered message, and
        the second owner keeps every period they had.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            theirs = seed_second_user["bootstrap_period"]
            second_id = seed_second_user["user"].id
            before = _count_periods(db.session, second_id)

            with pytest.raises(PayPeriodUnresolved) as owned_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id,
                )
            with pytest.raises(PayPeriodUnresolved) as absent_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id + 10_000,
                )

            # Same sentence, only the echoed id differs -- no existence oracle.
            assert str(owned_exc.value).replace(
                str(theirs.id), "X",
            ) == str(absent_exc.value).replace(str(theirs.id + 10_000), "X")
            assert _count_periods(db.session, second_id) == before

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
                    user_id, keep_through_period_id=periods[1].id,
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
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 1, 2), num_periods=14, cadence_days=14,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id,
                    keep_through_period_id=seed_user["bootstrap_period"].id,
                )
            assert PeriodLockReason.HISTORICAL in excinfo.value.blocking.values()
            assert _count_periods(db.session, user_id) == before

    def test_nonzero_anchor_opening_blocks_as_ledger_postings(
        self, app, db, seed_user,
    ):
        """A NON-zero anchor's opening correction hard-locks its period.

        The Step-5 accepted behavior change (plan Section 3.5): a $500.00
        savings whose opening correction lands in a to-delete period makes
        that period's per-ledger nets non-zero, so the double-entry gate
        reports LEDGER_POSTINGS (it precedes ACCOUNT_ANCHOR).  Nothing is
        deleted.

        **The schedule is generated AROUND today deliberately** (plan step
        X-ai-r).  A correction books in the period CONTAINING the day the
        balance was observed (ruling R-DH), and the factory defaults that day
        to today -- so the period that receives the correction is the one
        holding today, and this fixture puts a to-delete period there.  It
        used to force ``anchor_period_id`` onto a FUTURE period and rely on
        the writer copying that stored id, which is the attribution X-ai-r
        removed; the split case moved to the test below.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # Periods straddling today, so the account's observed day (today)
            # falls INSIDE one of them -- production's shape, where a row's
            # period and its observed day are two statements of one fact.
            periods = _future_periods(
                db.session, seed_user, count=6,
                start=display_today() - timedelta(days=28),
            )
            anchored = next(
                period for period in periods
                if period.start_date <= display_today() <= period.end_date
            )
            # Truncate through the period BEFORE the anchored one, so the
            # anchored period is inside the to-delete window.  Named by id
            # since plan step C3-a; it was ``anchored.period_index - 1``.
            previous = max(
                (p for p in periods if p.start_date < anchored.start_date),
                key=lambda p: p.start_date,
            )
            create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=previous.id,
                )
            assert excinfo.value.blocking.get(anchored.id) == (
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
                    user_id, keep_through_period_id=periods[1].id,
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
                user_id, keep_through_period_id=periods[1].id,
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
                    user_id, keep_through_period_id=periods[1].id,
                )
            assert excinfo.value.blocking.get(periods[2].id) == (
                PeriodLockReason.RECURRENCE_ANCHOR
            )


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
                    user_id, keep_through_period_id=periods[1].id,
                )
            assert excinfo.value.count == 1
            assert _count_periods(db.session, user_id) == before

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
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
                    user_id, keep_through_period_id=periods[1].id,
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
                    user_id, keep_through_period_id=periods[1].id,
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
                user_id, keep_through_period_id=periods[1].id,
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
            )
            make_transfer_template(db.session, seed_user, savings)
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
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
            )
            _make_adhoc_transfer(db.session, seed_user, savings, periods[2])
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
