"""
Shekel Budget App -- Mark-Paid Entry Integration and Status Guard Tests

Tests for Commit 5: auto-populating actual_amount from entries on
mark-paid, Credit status guard on entry-capable transactions, and
post-paid entry mutation actual_amount updates.

Each test verifies exact Decimal values.  Arithmetic is documented
inline so a reviewer can verify by hand.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum
from app.exceptions import ValidationError

from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    freeze_today,
    make_every_period_rule,
    settlement_basis_id,
)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Mark-paid entry tests use hardcoded purchase dates like
    date(2026, 1, 5) and date(2026, 1, 10) that must fall inside the
    calendar-anchored seed_periods range.  Freezing today inside the
    seeded range keeps "which paycheck contains today" deterministic without
    disturbing those calendar values.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
from app.services import entry_service


# ── Helpers ──────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Uses IDs rather than ORM objects to avoid session detachment
    issues when combined with auth_client HTTP requests.
    """
    entry = TransactionEntry(
        transaction_id=txn_id,
        # The parent's account, resolved here rather than taken as an argument:
        # this helper's whole point is that a caller passes IDS, and an entry's
        # account IS its parent's (``fk_transaction_entries_parent_account``).
        account_id=db.session.get(Transaction, txn_id).account_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _create_tracked_txn(seed_user, seed_periods):
    """Create a tracked expense transaction with template.

    Creates a minimal template with is_envelope=True
    and a projected expense transaction linked to it.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected = db.session.query(Status).filter_by(name="Projected").one()

    rule = make_every_period_rule(db.session, seed_user["user"].id)

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Tracked Groceries",
        default_amount=Decimal("500.00"),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Tracked Groceries",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _create_non_tracked_txn(seed_user, seed_periods):
    """Create a regular expense transaction without entry tracking."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )

    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Non-Tracked Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("200.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


# ── Mark-Paid with Entries ───────────────────────────────────────


class TestMarkPaidRecordsThePurchases:
    """Mark-paid on a tracked row records the ``purchases`` basis.

    Its figure is not stored: the row's own entries state it, and
    ``row_valuation.settled_figure`` is the accessor (plan step X-au-c3).  The
    expected NUMBERS are unchanged from when this class read the column --
    what moved is where the answer comes from.
    """

    def test_mark_done_auto_populates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Mark-paid on tracked transaction with entries sets actual to entry sum.

        Setup: Two debit entries of $150 and $250.
        Expected actual: 150 + 250 = $400.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="150.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="250.00", description="Target")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_mark_done_actual_includes_credit_entries(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Actual includes both debit and credit entries.

        Setup: $300 debit + $100 credit.
        Expected actual: 300 + 100 = $400 (credit is included for analytics).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="300.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="100.00",
                        description="Amazon", is_credit=True)
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # 300 + 100 = 400 total spending.
            assert settled_figure(txn) == Decimal("400.00")

    def test_mark_done_no_entries_manual_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked transaction with no entries accepts manual actual from form.

        Setup: Entry-capable template, no entries created.
        Expected: manual settled_amount=350 is accepted (fall-through).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "350.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.settled_amount == Decimal("350.00")

    def test_mark_done_no_entries_records_the_derived_figure(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A tracked row with no entries records what it BOOKED, not nothing.

        Setup: Entry-capable template, no entries, no form data.

        **This asserted ``actual_amount is None`` until plan step X-au-c3.**  An
        envelope with no entries takes the MANUAL branch (ruling **R-FJ**: a
        door has relocated nothing, so pressing Paid books the budget rather
        than ``$0.00``), and before this step that branch recorded no figure at
        all -- so the row read back its PLAN.  It now records the figure it
        booked, on the ``derived`` basis because the app resolved it and nobody
        typed a correction.  The figure is the same ``$500.00`` the old
        fall-back answered.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            planned = txn.estimated_amount

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == planned

    def test_mark_done_entries_override_form_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry sum overrides manual actual_amount from the form.

        Setup: Entries sum to $400, form submits settled_amount=999.
        Expected: actual_amount = $400 (entries win).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="200.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="200.00", description="Target")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "999.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # Entry sum (200 + 200 = 400) overrides form value (999).
            assert settled_figure(txn) == Decimal("400.00")

    def test_non_tracked_mark_done_unchanged(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Non-tracked transaction mark-done uses manual actual.

        Setup: Non-tracked expense, no template tracking flag.
        Expected: manual actual_amount from form is accepted.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "175.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.settled_amount == Decimal("175.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)


# ── Credit Status Guard ──────────────────────────────────────────


class TestCreditStatusGuard:
    """Tests for blocking Credit status on entry-capable transactions."""

    def test_update_rejects_credit_status_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on tracked transaction returns 400.

        The Credit status conflicts with entry-level credit handling.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 400
            assert b"Credit status" in resp.data
            assert b"entry-level credit" in resp.data

            # Transaction status must be unchanged.
            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id != credit_id

    def test_update_allows_credit_status_non_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on non-tracked transaction succeeds.

        Legacy credit workflow is unaffected for non-entry-capable
        transactions.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id == credit_id


# ── Post-Paid Entry Mutations ────────────────────────────────────


class TestPostPaidEntryMutation:
    """A Paid row's purchases are CLOSED: all three doors refuse (X-au-c3).

    **This class asserted the opposite until plan step X-au-c3** -- adding,
    deleting or re-pricing a purchase on a Paid envelope re-derived its
    ``actual_amount`` from the new entry sum.  That is withdrawn (developer
    ruling, 2026-08-17) and the reason is carry-forward: it rolls the
    envelope's unspent remainder into the NEXT period's row and settles the
    source at what was spent, so a purchase recorded against the closed source
    afterwards raises its cost while the later row still holds the
    rolled-forward money -- the same dollars counted twice.  Re-deriving also
    moves money in the optimistic direction with no human act: one back-filled
    purchase against a close at the row's estimate crashes the recorded cost to
    that purchase and hands the difference back to the projection.

    The service-level negative controls live in
    ``test_entry_service.TestASettledRowsPurchasesAreClosed``; these grade the
    same rule on a row closed through the real ``mark-done`` route, and assert
    the second half of it -- the RECORDED figure does not move either.
    """

    @staticmethod
    def _paid_envelope_at_300(auth_client, seed_user, seed_periods):
        """Return (txn_id, entry_ids) for a tracked row closed at $300."""
        txn = _create_tracked_txn(seed_user, seed_periods)
        txn_id = txn.id
        user_id = seed_user["user"].id
        first = _make_entry(
            txn_id, user_id, amount="150.00", description="Kroger",
        )
        second = _make_entry(
            txn_id, user_id, amount="150.00", description="Target",
        )
        entry_ids = (first.id, second.id)
        db.session.commit()

        auth_client.post(f"/transactions/{txn_id}/mark-done")
        txn = db.session.get(Transaction, txn_id)
        assert settled_figure(txn) == Decimal("300.00")
        return txn_id, entry_ids

    def test_entry_added_after_paid_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A late purchase against a Paid row is refused and changes nothing."""
        with app.app_context():
            txn_id, _ = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Late purchase",
                        purchased_on=date(2026, 1, 10),
                    ),
                )

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("300.00")
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id,
            ).count() == 2

    def test_entry_deleted_after_paid_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Removing a purchase from a Paid row is refused and changes nothing."""
        with app.app_context():
            txn_id, entry_ids = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.delete_entry(
                    entry_id=entry_ids[0], user_id=seed_user["user"].id,
                )

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("300.00")
            assert db.session.get(TransactionEntry, entry_ids[0]) is not None

    def test_entry_updated_after_paid_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Re-pricing a purchase on a Paid row is refused and changes nothing."""
        with app.app_context():
            txn_id, entry_ids = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry_id=entry_ids[0],
                    user_id=seed_user["user"].id,
                    amount=Decimal("250.00"),
                )

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("300.00")
            assert db.session.get(
                TransactionEntry, entry_ids[0],
            ).amount == Decimal("150.00")

    def test_projected_entry_mutation_does_not_set_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry mutations on projected transactions do not touch actual_amount.

        The entry-mutation hook (``_resync_settled_envelope`` since plan
        step X-ap) fires only for a row in the settled band.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            # Transaction is in PROJECTED status.
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_amount is None

            entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Projected period purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # actual_amount must remain None for projected transactions.
            assert txn.settled_amount is None
