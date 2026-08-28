"""Lifecycle tests for the transfer -> posting-ledger wiring (Step 2, Commit 5).

Commit 5 wires :func:`~app.services.posting_service.sync_transfer_postings`
into the transfer service's mutation chokepoints (``update_transfer`` /
``delete_transfer`` / ``restore_transfer``), so settling, reverting,
cancelling, deleting, and restoring a transfer keep the append-only
double-entry ledger in step WITHOUT any caller touching ``posting_service``
directly.  These tests drive transfers END TO END through ``transfer_service``
only (the way the mark-done / cancel / delete routes do) and assert the
resulting ledger state, covering the full ``is_settled`` truth table:

  * ``projected -> done``       posts one balanced entry (+effect);
  * ``done -> settled``         is an idempotent no-op (already at target);
  * ``done -> projected``       reverses it to net zero (append-only);
  * ``projected -> cancelled``  posts nothing (never settled);
  * soft-delete of a settled transfer reverses, and restore re-posts;
  * hard-delete of a settled transfer reverses, then the immutable pair
    survives with ``transfer_id`` SET NULL;
  * a double mark-done never double-posts;
  * settling and setting ``actual_amount`` in ONE call posts the ACTUAL
    effective amount (the reconcile runs after every kwarg is applied, NOT
    inside the status-change helper -- the placement that makes the grid
    shadow-edit path correct).

After each mutation the per-account reconciliation invariant is asserted in
its Build-Order Step 5 ABSOLUTE form: ``account_posting_total == opening
anchor + settled_transfer_effect``.  ``create_account`` posts each fixture
account's opening correction at create time (Checking $1000.00, the Savings
$100.00 sentinel), and every settle here is stamped at server-now -- AFTER the
origination assertion -- so the settled-shadow effect is exactly the
post-assertion effect riding on top of the opening.  All money is ``Decimal``
from strings, with the arithmetic shown per the testing standard.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; test bodies bind fixtures by name.
from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transfer import Transfer
from app.services import posting_service, transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    linked_ledger_account,
)
from app.services import cash_ledger


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


def _ledger_id(account):
    """Return the LINKED ledger account id for *account* (never its twin)."""
    return linked_ledger_account(_db.session, account.id).id


def _entries_for_transfer(transfer_id):
    """Return every journal entry still linked to *transfer_id*, oldest first."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transfer_id=transfer_id)
        .order_by(JournalEntry.id)
        .all()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _create_projected_transfer(seed_user, from_account, to_account, amount):
    """Create a Projected ad-hoc transfer via the service (posts nothing yet).

    Routed through ``transfer_service.create_transfer`` -- the sole transfer
    writer -- so the two shadows obey every transfer invariant.  A Projected
    transfer never goes through the status seam and so posts no ledger
    entry until it is settled.
    """
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
        ),
    )


def _settle(transfer, user_id, **extra):
    """Settle a transfer (Projected -> Done) through the service chokepoint.

    Mirrors the ``transfers.mark_done`` route: status -> Done with a concrete
    the settle day.  Extra kwargs (e.g. ``actual_amount``) are forwarded so a test
    can settle and set an actual amount in one call.
    """
    transfer_service.update_transfer(
        transfer.id, user_id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        settle_day=an_entered_day(display_today()),
        **extra,
    )


def _assert_reconciles(scenario_id, *accounts):
    """Assert ledger == settled-shadow effect for each account (the oracle).

    The per-account reconciliation invariant in its Step-5 ABSOLUTE form: the
    net of an account's posting legs equals its opening anchor correction
    (posted at create time; the origination row is each fixture account's
    only assertion) plus the net effect of its settled, non-deleted transfer
    shadows -- every settle in this suite is stamped at server-now, after the
    assertion instant, so the effect rides on top of the opening.  Asserted
    from independent producers so a divergence fails loudly.
    """
    for account in accounts:
        posted = posting_service.account_posting_total(account.id, scenario_id)
        effect = posting_service.settled_transfer_effect(account.id, scenario_id)
        opening = cash_ledger.resolve_anchor(account).balance
        assert posted == opening + effect, (
            f"account {account.id}: ledger {posted} != opening {opening} "
            f"+ settled effect {effect}"
        )


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the pattern ``seed_user``
    uses.  ``create_account_of_type`` fires the Step-2 ledger-account sync
    hook, so the account already carries its paired ledger account.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Lifecycle Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Settle: projected -> done posts one balanced entry
# ---------------------------------------------------------------------------


class TestSettlePostsEntry:
    """Settling a transfer through the service auto-posts one balanced entry."""

    def test_mark_done_posts_one_balanced_entry(
        self, app, db, seed_user, savings,
    ):
        """projected -> done posts one -100 / +100 entry; the ledger reconciles.

        Arithmetic: settling a $100 Checking -> Savings transfer posts -100.00
        on Checking's ledger (money out, a credit) and +100.00 on Savings'
        (money in, a debit), summing to zero.  The reconcile invariant holds:
        account_posting_total(Savings) == 100.00 (opening) + 100.00 (effect)
        = 200.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            # No entry before settling.
            assert _entries_for_transfer(transfer.id) == []

            _settle(transfer, user_id)
            _db.session.commit()

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_ledger_id(checking)] == Decimal("-100.00")
            assert legs[_ledger_id(savings)] == Decimal("100.00")
            assert sum(legs.values()) == Decimal("0.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_done_to_settled_archive_is_noop(
        self, app, db, seed_user, savings,
    ):
        """done -> settled posts no second entry (already at target).

        Arithmetic: the settle posted +100 to Savings (total 200.00 on the
        $100.00 opening); archiving Done -> Settled keeps is_settled True, so
        target == current == +100, delta 0, no entry.  The ledger stays at one
        transfer entry and still reconciles.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            transfer = _create_projected_transfer(
                seed_user, seed_user["account"], savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            _db.session.commit()

            assert len(_entries_for_transfer(transfer.id)) == 1
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, seed_user["account"], savings)


# ---------------------------------------------------------------------------
# Revert: done -> projected reverses to zero (append-only)
# ---------------------------------------------------------------------------


class TestRevertReverses:
    """Reverting a settled transfer appends a balanced reversal."""

    def test_revert_to_projected_reverses_to_zero(
        self, app, db, seed_user, savings,
    ):
        """done -> projected appends a -100 reversal; Savings nets to zero.

        Arithmetic: the settle posted +100; reverting posts the delta to reach
        the new target 0: 0 - 100 = -100 on Savings, +100 on Checking.  Two
        entries survive (append-only -- the original is never edited).  The
        reverted income shadow is no longer is_settled, so it drops from
        settled_transfer_effect, and the Savings total lands back on its
        $100.00 opening.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 2
            reversal_legs = _legs_by_ledger(entries[1].id)
            assert reversal_legs[_ledger_id(savings)] == Decimal("-100.00")
            assert reversal_legs[_ledger_id(checking)] == Decimal("100.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Cancel: projected -> cancelled posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a never-settled transfer writes no ledger entry."""

    def test_cancel_projected_posts_nothing(
        self, app, db, seed_user, savings,
    ):
        """projected -> cancelled posts no entry (never settled).

        Arithmetic: a Projected transfer has no posted effect; cancelling keeps
        the target at 0 (is_settled False), delta 0, nothing written.  Both
        accounts stay on their openings (Savings 100.00) with a zero settled
        effect.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            _db.session.commit()

            assert _entries_for_transfer(transfer.id) == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Delete + restore
# ---------------------------------------------------------------------------


class TestDeleteAndRestore:
    """Deleting a settled transfer reverses it; restoring re-posts it."""

    def test_soft_delete_settled_reverses_then_restore_reposts(
        self, app, db, seed_user, savings,
    ):
        """Soft-delete reverses a settled transfer; restore re-posts it.

        Arithmetic: settle +100 (1 entry); soft-delete reverses -100 (2
        entries, Savings back on its 100.00 opening); restore re-posts +100
        (3 entries, Savings 200.00).  Append-only throughout -- every
        correction is a new entry, none edited.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            # Soft-delete reverses the posted effect.
            transfer_service.delete_transfer(transfer.id, user_id, soft=True)
            _db.session.commit()
            assert len(_entries_for_transfer(transfer.id)) == 2
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            # The reverted shadows are soft-deleted, so the effect is 0 too.
            _assert_reconciles(scenario_id, checking, savings)

            # Restore re-posts the confirmed effect.
            transfer_service.restore_transfer(transfer.id, user_id)
            _db.session.commit()
            assert len(_entries_for_transfer(transfer.id)) == 3
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_hard_delete_settled_reverses_and_pair_survives_null_transfer_id(
        self, app, db, seed_user, savings,
    ):
        """Hard-delete reverses, then the immutable pair survives, link nulled.

        Arithmetic: settle +100 (1 entry); hard-delete first reverses -100 (2nd
        entry), then removes the transfer row, SET-NULLing ``transfer_id`` on
        both entries.  The immutable legs survive and the transfer row is gone.
        This is the append-only correction proven through a hard delete.

        **The Savings LINKED ledger holds FIVE legs, and it held three until
        plan step X-f3c-2b.**  Its opening is now posted, reversed and
        re-posted, because ``create_account_of_type`` restates the account's
        books so a fixture can date a row before the account's own creation day
        -- which is production's own shape after the same act.  Those three net
        to the ``$100.00`` opening exactly as the single leg did, so the ledger
        still reads ``+$100.00`` in total.  Both counts are asserted: the whole
        ledger, which is what catches an amount-NEUTRAL pair of extra legs
        landing during the delete, and the TRANSFER-sourced pair alone, which
        is the append-only correction this case is named for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()
            transfer_id = transfer.id
            savings_ledger = _ledger_id(savings)

            transfer_service.delete_transfer(transfer_id, user_id, soft=False)
            _db.session.commit()

            # The transfer row is gone.
            assert _db.session.get(Transfer, transfer_id) is None
            # Both entries survive with transfer_id nulled (immutable
            # history).  Filtered by the TRANSFER source kind: the openings
            # the fixtures posted are also concrete-FK-less entries, but
            # carry the account_opening source.
            assert _entries_for_transfer(transfer_id) == []
            surviving = (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == user_id,
                    JournalEntry.transfer_id.is_(None),
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER,
                    ),
                )
                .all()
            )
            assert len(surviving) == 2
            # THE WHOLE LEDGER first, which is the reading a source filter
            # cannot give: five legs -- the opening posted, reversed and
            # re-posted by the factory's restatement, plus the settle and its
            # reversal -- netting to the $100.00 opening.  An amount-NEUTRAL
            # extra pair landing here during the delete moves this count and
            # nothing else, so dropping it for the filtered count below would
            # have been the cheaper repair rather than the stronger one.
            all_savings_legs = (
                _db.session.query(Posting)
                .filter_by(ledger_account_id=savings_ledger)
                .all()
            )
            assert len(all_savings_legs) == 5
            assert sum(
                leg.amount for leg in all_savings_legs
            ) == Decimal("100.00")

            # THEN the TRANSFER-sourced pair alone (+100 settle, -100
            # reversal), which is the append-only correction this case is named
            # for: it survives the parent's disposal and nets to zero.
            savings_legs = (
                _db.session.query(Posting)
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == savings_ledger,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER,
                    ),
                )
                .all()
            )
            assert len(savings_legs) == 2
            assert sum(leg.amount for leg in savings_legs) == Decimal("0.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Idempotency: a double mark-done never double-posts
# ---------------------------------------------------------------------------


class TestDoubleMarkDoneIdempotent:
    """Marking a transfer done twice posts exactly one entry."""

    def test_double_mark_done_does_not_double_post(
        self, app, db, seed_user, savings,
    ):
        """A second mark-done (done -> done) posts no second entry.

        Arithmetic: the first mark-done posts +100; the identity re-submit
        (done -> done is a legal idempotent transition) sees current == target
        == +100, delta 0, and writes nothing.  The service-level
        double-mark-done guard -- one entry, ledger reconciles.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            transfer = _create_projected_transfer(
                seed_user, seed_user["account"], savings, Decimal("100.00"),
            )
            _db.session.commit()

            _settle(transfer, user_id)
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            assert len(_entries_for_transfer(transfer.id)) == 1
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, seed_user["account"], savings)


# ---------------------------------------------------------------------------
# The trap: settle + actual_amount in one call posts the ACTUAL
# ---------------------------------------------------------------------------


class TestSettleWithActualSameCall:
    """Settling and setting actual_amount in one call posts the actual amount."""

    def test_settle_with_actual_amount_in_one_call_posts_actual(
        self, app, db, seed_user, savings,
    ):
        """One call settling AND setting actual_amount posts the ACTUAL effect.

        The grid shadow-edit path can send ``status_id=done`` AND
        ``actual_amount`` in a single ``update_transfer`` call, and the service
        applies ``actual_amount`` AFTER ``status_id``.  The reconcile runs at
        the END of ``update_transfer`` (NOT inside ``apply_status_to_all_three``), so
        it reads the FINAL income-shadow effective amount.

        Arithmetic: nominal $100, settled actual $88.00 -> the income shadow's
        effective_amount is $88.00, so the posting is -88.00 / +88.00, NOT
        -100 / +100 (Savings total 100.00 opening + 88.00 = 188.00).  A
        reconcile placed before ``actual_amount`` was applied would wrongly
        post the $100 estimate -- the regression this guards.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()

            # Settle and record the actual in ONE call (the trap).
            _settle(transfer, user_id, settled_amount=Decimal("88.00"))
            _db.session.commit()

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_ledger_id(checking)] == Decimal("-88.00")
            assert legs[_ledger_id(savings)] == Decimal("88.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("188.00")
            _assert_reconciles(scenario_id, checking, savings)
