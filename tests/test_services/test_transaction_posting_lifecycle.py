"""Lifecycle tests for the transaction -> posting-ledger wiring (Step 3, Commit 6).

Commit 6 wires :func:`~app.services.posting_service.sync_transaction_postings`
into every ordinary-transaction effect boundary -- the mark-done / PATCH /
cancel / delete routes, the envelope entry-mutation service, the credit
payback-delete paths, and carry-forward -- so settling, editing, reverting,
deleting, and carrying forward a transaction keep the append-only double-entry
ledger in step.  These tests drive transactions END TO END through those
handlers (NOT by calling ``posting_service`` directly -- that is Commit 4's unit
suite) and assert the resulting ledger state.

The load-bearing wiring properties, each pinned by a test below:

  * the reconcile runs LAST, after every effect field is applied -- so a manual
    ``actual_amount`` posts the ACTUAL not the estimate, and a
    settle-and-recategorize PATCH posts the NEW category (the 2.8b HIGH, both
    directions);
  * a revert-and-recategorize PATCH reverses the OLD category read from the
    ledger, not the new ``category_id`` (the 2.8 CRITICAL), at the route level;
  * the PATCH reconcile is gated, so a metadata-only edit posts nothing;
  * an envelope posts its debit-only effect (``effective - Sigma(credit)``), and
    an entry mutation on a SETTLED envelope re-syncs while ``toggle_cleared``
    does not;
  * every delete path (hard / soft, the settled-payback unmark, and a recursive
    payback chain) reverses before the row leaves the table;
  * carry-forward posts the confirmed effect of each envelope source it settles.

After each mutation the per-account reconciliation invariant
(``account_posting_total == settled_transfer_effect + settled_transaction_effect``)
is asserted from the two independent producers -- the same equality the Commit-8
oracle locks suite-wide.  All money is ``Decimal`` from strings, with the
arithmetic shown per the testing standard.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern;
# test bodies bind fixtures (``auth_client``, ``seed_user``, ...) by name.
from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, StatusEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    carry_forward_service,
    credit_workflow,
    entry_service,
    ledger_account_service,
    posting_service,
)
from app.services.entry_service import EntryDetails
from tests._test_helpers import (
    add_txn,
    create_envelope_txn,
    ledger_accounts_for_account,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _linked_ledger_id(account):
    """Return the linked ledger account id for a real *account*."""
    return ledger_accounts_for_account(db.session, account.id)[0].id


def _category_ledger_id(seed_user, category_key, ledger_class):
    """Return the category / fallback ledger account id for a seed category.

    Idempotent: returns the row the go-forward reconcile created during the
    test (the resolver respects the partial unique), so a leg can be asserted
    against it.  ``category_key=None`` resolves the per-(owner, class)
    Uncategorized fallback.
    """
    category_id = (
        None if category_key is None
        else seed_user["categories"][category_key].id
    )
    return ledger_account_service.get_or_create_category_ledger_account(
        seed_user["user"].id, category_id, ledger_class,
    ).id


def _cc_payback_ledger_id(seed_user):
    """Return the Expense ledger account id for the user's CC Payback category."""
    cc_category = credit_workflow.get_or_create_cc_category(seed_user["user"].id)
    return ledger_account_service.get_or_create_category_ledger_account(
        seed_user["user"].id, cc_category.id, LedgerAccountClassEnum.EXPENSE,
    ).id


def _ledger_total(ledger_account_id):
    """Return the net of every posting leg on one ledger account.

    The counter-account (category / fallback) analog of
    ``posting_service.account_posting_total``, which keys off a REAL account's
    linked ledger and so cannot be pointed at a category ledger account.
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .filter(Posting.ledger_account_id == ledger_account_id)
        .scalar()
    )


def _entries_for_transaction(transaction_id):
    """Return every journal entry still linked to *transaction_id*, oldest first."""
    return (
        db.session.query(JournalEntry)
        .filter_by(transaction_id=transaction_id)
        .order_by(JournalEntry.id)
        .all()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one journal entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _add_purchase(seed_user, txn, amount, *, is_credit=False):
    """Attach one purchase entry (debit or credit) to *txn* directly and flush.

    Bypasses ``entry_service`` so the envelope's pre-settle state can be built
    without triggering the entry-level payback workflow; the tests that exercise
    the entry-mutation hook call ``entry_service`` explicitly instead.
    """
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        entry_date=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _assert_reconciles(scenario_id, *accounts):
    """Assert ledger == settled-source effect for each real account (the oracle).

    The per-account reconciliation invariant: the net of an account's posting
    legs equals the combined net effect of its settled, non-deleted transfer
    shadows AND ordinary transactions -- asserted from the two independent
    producers so any divergence between the ledger and the source rows fails
    loudly.
    """
    for account in accounts:
        posted = posting_service.account_posting_total(account.id, scenario_id)
        effect = (
            posting_service.settled_transfer_effect(account.id, scenario_id)
            + posting_service.settled_transaction_effect(account.id, scenario_id)
        )
        assert posted == effect, (
            f"account {account.id}: ledger {posted} != source effect {effect}"
        )


# ---------------------------------------------------------------------------
# mark-done route: settling posts the confirmed effect (last, after actuals)
# ---------------------------------------------------------------------------


class TestMarkDoneRoutePostsTransaction:
    """The mark-done route auto-posts a settled transaction's cash split."""

    def test_plain_expense_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $50 Groceries expense, marked Paid, posts -50 / +50.

        Arithmetic (plan Section 1): a plain expense has no entries, so the
        effect is ``effective_amount`` (50) with the expense sign -- cash leg
        -50.00 (money leaving Checking, a credit) and Groceries-Expense leg
        +50.00 (the expense lands, a debit).  -50 + 50 = 0.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            assert _entries_for_transaction(txn_id) == []

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            entries = _entries_for_transaction(txn_id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_linked_ledger_id(checking)] == Decimal("-50.00")
            assert legs[_category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )] == Decimal("50.00")
            assert sum(legs.values()) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_income_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $2000 Salary income, marked Received, posts +2000 / -2000.

        Arithmetic (plan Section 1): income has no entries, so the effect is
        ``effective_amount`` (2000) with the INCOME sign -- cash leg +2000.00
        (money entering Checking, a debit) and Salary-Income leg -2000.00 (a
        credit).  The sign follows the transaction TYPE, never the account class.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Salary", "2000.00",
                is_income=True, category_key="Salary",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            entries = _entries_for_transaction(txn_id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_linked_ledger_id(checking)] == Decimal("2000.00")
            assert legs[_category_ledger_id(
                seed_user, "Salary", LedgerAccountClassEnum.INCOME,
            )] == Decimal("-2000.00")
            assert sum(legs.values()) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_mark_done_manual_actual_posts_actual_not_estimate(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A manual ``actual_amount`` posts the ACTUAL, not the estimate (2.8b HIGH).

        Arithmetic: the row is estimated at 50 but settled with a manual actual
        of 75.  The reconcile runs AFTER ``actual_amount`` is applied (the
        transfer pattern -- post last), so it reads ``effective_amount`` = actual
        = 75: cash leg -75.00, not -50.00.  Proves the reconcile is not fired at
        the status flip (which would book the stale 50).
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "75.00"},
            )
            assert resp.status_code == 200

            entries = _entries_for_transaction(txn_id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_linked_ledger_id(checking)] == Decimal("-75.00")
            assert legs[_category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )] == Decimal("75.00")
            _assert_reconciles(seed_user["scenario"].id, checking)


# ---------------------------------------------------------------------------
# PATCH route: settle / recategorize / revert / metadata-only
# ---------------------------------------------------------------------------


class TestPatchPostingLifecycle:
    """The inline-edit PATCH route reconciles after all fields, gated."""

    def test_settle_and_recategorize_patch_posts_new_category(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A settle+recategorize PATCH books the NEW category, not the old one.

        A Projected Groceries expense is settled and recategorized to Rent in
        ONE PATCH.  ``category_id`` is applied in the setattr loop and
        ``status_id`` through the seam; the reconcile runs LAST and reads the
        current ``category_id`` = Rent: cash -50.00 / Rent-Expense +50.00, with
        the Groceries ledger never touched.  (The strict reconcile-AFTER-the-flip
        ordering proof for a field applied *after* the seam is the
        manual-``actual_amount`` test above, where ``actual`` is set post-seam;
        ``category_id`` here is set pre-seam, so this pins the recategorize-on-
        settle OUTCOME rather than isolating that ordering.)
        """
        with app.app_context():
            checking = seed_user["account"]
            done_id = ref_cache.status_id(StatusEnum.DONE)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id), "category_id": str(rent_id)},
            )
            assert resp.status_code == 200

            assert _ledger_total(_category_ledger_id(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("50.00")
            assert _ledger_total(_category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("-50.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_revert_and_recategorize_reconciles(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Revert+recategorize reverses the OLD category, not the new one (2.8 CRITICAL).

        A Paid Groceries expense (posted -50 / +50 Groceries) is reverted to
        Projected AND recategorized to Rent in ONE PATCH (the lock lifts on the
        revert).  The reconcile reads the posted legs back from the ledger by
        ``transaction_id``, so it reverses GROCERIES (+50 -> 0), never Rent --
        Groceries nets to zero, Rent stays untouched, Checking nets to zero.  A
        later re-settle then posts cleanly to Rent.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("50.00")

            # Revert + recategorize in one PATCH (the lock lifts on the revert).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "category_id": str(rent_id),
                },
            )
            assert resp.status_code == 200
            rent_ledger = _category_ledger_id(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )
            # OLD category reversed to zero; NEW category never touched yet.
            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert _ledger_total(rent_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

            # Re-settle: now it posts to Rent (the current category).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            )
            assert resp.status_code == 200
            assert _ledger_total(rent_ledger) == Decimal("50.00")
            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_notes_only_edit_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A metadata-only PATCH on a Paid row leaves the ledger untouched.

        ``notes`` is not in ``_POSTING_RELEVANT_FIELDS``, so the reconcile is
        skipped.  This asserts the observable RESULT (still exactly one entry,
        same legs), not that the gate fired: because ``sync_transaction_postings``
        is idempotent, an ungated call here would also be a no-op (delta 0).
        The gate is a pure performance pre-filter that avoids the ledger
        round-trip; its only effect is on cost, not on the stored ledger.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert len(_entries_for_transaction(txn_id)) == 1

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"notes": "reconciled at the bank"},
            )
            assert resp.status_code == 200
            # Still exactly one entry -- no reconcile fired.
            assert len(_entries_for_transaction(txn_id)) == 1
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("-50.00")


# ---------------------------------------------------------------------------
# cancel route: never crosses the settled boundary -> posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a Projected transaction is an idempotent ledger no-op."""

    def test_cancel_projected_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancelling a never-posted Projected expense writes no ledger entry.

        Cancelled is non-settled and reachable only from Projected, so the
        reconcile reverses a zero posted balance to zero: no entry is written.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/cancel")
            assert resp.status_code == 200
            assert _entries_for_transaction(txn_id) == []
            assert posting_service.account_posting_total(
                seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Envelopes: debit-only effect + entry-mutation re-sync; toggle_cleared no-op
# ---------------------------------------------------------------------------


class TestEnvelopePostingLifecycle:
    """An envelope posts ``effective - Sigma(credit)`` and re-syncs on entry edits."""

    def test_envelope_mark_done_posts_debit_only(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 60 debit + 40 credit envelope posts -60 / +60 (credit excluded).

        Arithmetic (plan Section 1 / Decision D2): at settle ``actual_amount`` =
        sum(all entries) = 60 + 40 = 100, and credit_sum = 40, so the effect is
        100 - 40 = 60 -- the debit-only checking outflow.  cash -60.00 /
        Groceries-Expense +60.00.  The $40 credit posts nothing here.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            _add_purchase(seed_user, txn, "60.00", is_credit=False)
            _add_purchase(seed_user, txn, "40.00", is_credit=True)
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            entries = _entries_for_transaction(txn_id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_linked_ledger_id(checking)] == Decimal("-60.00")
            assert legs[_category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )] == Decimal("60.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_entry_create_on_settled_envelope_resyncs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Adding a debit entry to a Paid envelope grows its posted outflow.

        Arithmetic: the envelope settles with one $40 debit (posted -40 / +40).
        A late $30 debit purchase recomputes ``actual_amount`` to 70, and the
        entry hook reconciles the +30 delta -> net cash -70.00 / Groceries +70.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("40.00")

            entry_service.create_entry(
                txn_id, user_id,
                EntryDetails(
                    amount=Decimal("30.00"), description="late",
                    entry_date=seed_periods[0].start_date, is_credit=False,
                ),
            )
            db.session.commit()

            assert _ledger_total(groceries_ledger) == Decimal("70.00")
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("-70.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_entry_credit_flip_on_settled_envelope_resyncs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Flipping a Paid envelope's only entry to credit reverses its outflow.

        Arithmetic: settles with one $40 debit (posted -40 / +40).  Flipping
        that entry to credit leaves ``actual_amount`` = sum(all) = 40 but makes
        credit_sum = 40, so the effect drops to 40 - 40 = 0.  The update hook
        reconciles the ledger back to zero (the $40 now rides its CC Payback).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id = entry.id
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("40.00")

            entry_service.update_entry(entry_id, user_id, is_credit=True)
            db.session.commit()

            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("0.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_entry_delete_on_settled_envelope_resyncs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Deleting one of a Paid envelope's entries shrinks its posted outflow.

        Arithmetic: settles with two debits 40 + 30 (posted -70 / +70).
        Deleting the $30 entry leaves ``actual_amount`` = 40, and the delete
        hook reconciles the -30 delta -> net cash -40.00 / Groceries +40.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            _add_purchase(seed_user, txn, "40.00", is_credit=False)
            doomed = _add_purchase(seed_user, txn, "30.00", is_credit=False)
            db.session.commit()
            doomed_id = doomed.id
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("70.00")

            entry_service.delete_entry(doomed_id, user_id)
            db.session.commit()

            assert _ledger_total(groceries_ledger) == Decimal("40.00")
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("-40.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_toggle_cleared_does_not_change_ledger(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Toggling an entry's cleared flag leaves the ledger untouched.

        The cleared/uncleared split does not change ``effective - credit_sum``,
        so ``toggle_cleared`` is deliberately NOT a posting boundary: the single
        posted entry (-40 / +40) is unchanged, no second entry appears.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            entry = _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            entry_id = entry.id
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert len(_entries_for_transaction(txn_id)) == 1

            entry_service.toggle_cleared(entry_id, user_id)
            db.session.commit()

            # No new entry; the ledger is unchanged.
            assert len(_entries_for_transaction(txn_id)) == 1
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("-40.00")
            _assert_reconciles(seed_user["scenario"].id, checking)


# ---------------------------------------------------------------------------
# Delete: every path reverses the posted effect before the row leaves
# ---------------------------------------------------------------------------


class TestDeleteReversesPostings:
    """Deleting a posted transaction (or a posted payback) reverses it first."""

    def test_hard_delete_adhoc_settled_reverses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Hard-deleting a Paid ad-hoc expense reverses its postings first.

        The $50 expense posts -50 / +50; the hard delete reverses to zero
        BEFORE the row goes, so after the delete every ledger account nets to
        zero (the original + reversal survive as a net-zero pair with their
        ``transaction_id`` SET NULL).
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("50.00")

            resp = auth_client.delete(f"/transactions/{txn_id}")
            assert resp.status_code == 200

            # Row is gone; the transaction_id link SET-NULLed on both legs.
            assert db.session.get(Transaction, txn_id) is None
            assert _entries_for_transaction(txn_id) == []
            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_soft_delete_template_settled_reverses(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Soft-deleting a Paid template row reverses its postings first.

        A template-linked envelope settles at $40 (posted -40 / +40).  The
        delete soft-deletes it (template_id set), reversing first: the row
        survives with ``is_deleted=True`` and its two ledger entries net to
        zero, dropping it out of ``settled_transaction_effect`` too.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            _add_purchase(seed_user, txn, "40.00", is_credit=False)
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(groceries_ledger) == Decimal("40.00")

            resp = auth_client.delete(f"/transactions/{txn_id}")
            assert resp.status_code == 200

            # Soft delete: the row survives, the link stays, the pair nets zero.
            survivor = db.session.get(Transaction, txn_id)
            assert survivor is not None
            assert survivor.is_deleted is True
            assert len(_entries_for_transaction(txn_id)) == 2
            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_settled_payback_reversed_on_unmark_credit(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Un-marking credit reverses a payback that was already settled.

        Mark an $80 expense Credit (creating a Projected payback), settle that
        payback (posting -80 / +80 on the CC Payback account), then un-mark the
        source.  ``delete_payback_on_credit_revert`` reverses the payback's
        postings BEFORE deleting it, so the CC Payback ledger nets to zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            source = add_txn(
                db.session, seed_user, seed_periods[0], "Dinner", "80.00",
                category_key="Groceries",
            )
            db.session.commit()
            source_id = source.id

            payback = credit_workflow.mark_as_credit(source_id, user_id)
            db.session.commit()
            payback_id = payback.id
            auth_client.post(f"/transactions/{payback_id}/mark-done")
            cc_ledger = _cc_payback_ledger_id(seed_user)
            assert _ledger_total(cc_ledger) == Decimal("80.00")

            credit_workflow.unmark_credit(source_id, user_id)
            db.session.commit()

            # Payback gone, its postings reversed to zero before deletion.
            assert db.session.get(Transaction, payback_id) is None
            assert _ledger_total(cc_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_recursive_payback_chain_reverses_on_source_delete(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Deleting a source reverses a settled payback DEEP in the credit chain.

        source -> payback1 -> payback2, with payback2 settled (posted -100 /
        +100 on CC Payback).  Deleting the source recurses through the chain,
        reversing payback2's postings at its level before each row is removed,
        so the CC Payback ledger nets to zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            source = add_txn(
                db.session, seed_user, seed_periods[0], "Chain", "100.00",
                category_key="Groceries",
            )
            db.session.commit()
            source_id = source.id

            payback1 = credit_workflow.mark_as_credit(source_id, user_id)
            db.session.commit()
            payback2 = credit_workflow.mark_as_credit(payback1.id, user_id)
            db.session.commit()
            payback2_id = payback2.id
            auth_client.post(f"/transactions/{payback2_id}/mark-done")
            cc_ledger = _cc_payback_ledger_id(seed_user)
            assert _ledger_total(cc_ledger) == Decimal("100.00")

            resp = auth_client.delete(f"/transactions/{source_id}")
            assert resp.status_code == 200

            # Whole chain gone; payback2's deep postings reversed to zero.
            assert db.session.get(Transaction, payback2_id) is None
            assert _ledger_total(cc_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)


# ---------------------------------------------------------------------------
# Carry-forward: each settled envelope source posts its confirmed effect
# ---------------------------------------------------------------------------


class TestCarryForwardPostsSettledSources:
    """Carry-forward posts the confirmed effect of each envelope it settles."""

    def test_carry_forward_posts_partially_spent_envelope(
        self, app, db, seed_user, seed_periods,
    ):
        """A partially-spent carried envelope posts its debit outflow.

        Arithmetic: a $100 envelope in the source period holds one $30 debit.
        Carry-forward settles it at sum(entries) = 30 and rolls the $70
        leftover into the target.  The settled source posts -30 / +30; the
        target row (Projected) posts nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            source = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            _add_purchase(seed_user, source, "30.00", is_credit=False)
            db.session.commit()
            source_id = source.id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, user_id, scenario_id,
            )
            db.session.commit()

            entries = _entries_for_transaction(source_id)
            assert len(entries) == 1
            legs = _legs_by_ledger(entries[0].id)
            assert legs[_linked_ledger_id(checking)] == Decimal("-30.00")
            assert legs[_category_ledger_id(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )] == Decimal("30.00")
            _assert_reconciles(scenario_id, checking)

    def test_carry_forward_empty_envelope_posts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """An empty carried envelope settles at zero and posts no entry.

        With no entries the source settles at ``actual_amount`` = 0, so its
        effect is zero and the reconcile writes nothing -- the full estimate
        rolls forward instead.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            source = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Food", Decimal("100.00"),
            )
            db.session.commit()
            source_id = source.id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, user_id, scenario_id,
            )
            db.session.commit()

            assert _entries_for_transaction(source_id) == []
            assert posting_service.account_posting_total(
                seed_user["account"].id, scenario_id,
            ) == Decimal("0.00")
