"""
Shekel Budget App -- Transaction Route Guard Tests

Tests for the guards on every transaction mutation route: a transfer SHADOW
row is "not found" at every door and moves nothing (plan step
balance:X-bi-6-1, the interval fence until the rows are deleted), a
soft-deleted row cannot be settled, a loan account admits no transaction,
and regular transactions are unaffected.
"""

from datetime import date
from decimal import Decimal

import pytest


from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.ref import AccountType, Status, TransactionType
from app.services import transfer_service
from app.services import account_service
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    one_off_row_of,
    open_books_before_the_first_assertion,
    resolved_amount,
    shadow_amount,
)
from app.models.amount_ownership import AmountOwnership
from app.services import status_seam


def _create_savings(seed_user):
    """Create a savings account for the test user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.flush()
    # Its BOOKS open before anything this fixture dates (plan step
    # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
    # asserts -- the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db.session, acct)
    return acct


def _create_test_transfer(seed_user, seed_periods_today):
    """Create a transfer with shadows via the service.  Returns (transfer, expense_shadow, income_shadow)."""
    savings = _create_savings(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("300.00")),
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="Test Transfer",
        ),
    )
    db.session.commit()

    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
    expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
    income = [s for s in shadows if s.transaction_type_id == income_type.id][0]
    return xfer, expense, income


def _create_regular_txn(seed_user, seed_periods_today):
    """Create a regular transaction (no transfer_id)."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Regular Expense",
        amount=Decimal("50.00"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.commit()
    return txn


# ── Update Guards ──────────────────────────────────────────────────


class TestATransactionDoorRefusesAShadowRow:
    """Every transaction door answers 404 for a transfer SHADOW row and moves nothing.

    **The interval fence of plan step balance:X-bi-6-1 (ruling R-BAL87).**
    Until that leaf these eight classes graded the transfer-detection GUARDS:
    a request landing on a shadow row was re-expressed as a transfer update
    (``_shadow_mutations``), or refused with a 400 where the act had no
    transfer meaning (credit, delete).  The grid draws a transfer as a LEG
    read off its parent now, and the leg's cell calls the transfer's own
    routes -- so no surface asks a transaction door about a shadow, the
    re-expressing module is deleted, and a request that still names a shadow
    (a stale page, a bookmark, a probe) is "not found" at the ownership
    helper (``_get_owned_transaction`` / ``get_accessible_transaction``),
    the answer every row a door does not serve gets.

    **Why the fence, and why it is graded by state, not status.**  The shadow
    rows still exist until ``X-bi-6``'s last leaf deletes them, and the
    doors' regular paths would write PAST the transfer's invariants if they
    admitted one: a PATCH would re-price one leg alone, a Mark Paid would
    settle one leg without its pair, a DELETE would orphan the sibling.  So
    each case asserts the 404 AND that the parent and both shadows are
    exactly as created -- remove the fence and the PATCH, mark-done and
    DELETE cases fire on the second assertion, which is what makes the
    first one a claim rather than a reading.

    The leg's own doors are graded in ``test_transfers.py``
    (``TestLegContextResponse`` and the ``_LEG_`` cases).
    """

    @staticmethod
    def _state(xfer_id):
        """The pair's facts a door could move, read fresh."""
        db.session.expire_all()
        xfer = db.session.get(Transfer, xfer_id)
        shadows = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id)
            .order_by(Transaction.id)
            .all()
        )
        return (
            xfer.amount, xfer.status_id, xfer.is_deleted, xfer.due_date,
            [(s.status_id, s.is_deleted, s.due_date, shadow_amount(s)) for s in shadows],
        )

    @pytest.mark.parametrize(
        ("method", "url", "data"),
        [
            ("get", "/transactions/{id}/cell", None),
            ("get", "/transactions/{id}/quick-edit", None),
            ("get", "/transactions/{id}/full-edit", None),
            (
                "patch", "/transactions/{id}",
                {"estimated_amount": "500.00", "estimated_amount_as_rendered": "300.00"},
            ),
            ("patch", "/transactions/{id}", {"due_date": "2026-01-20"}),
            ("delete", "/transactions/{id}", None),
            ("post", "/transactions/{id}/mark-done", None),
            ("post", "/transactions/{id}/mark-credit", None),
            ("delete", "/transactions/{id}/unmark-credit", None),
            ("post", "/transactions/{id}/cancel", None),
        ],
        ids=[
            "cell", "quick-edit", "full-edit", "patch-amount", "patch-due-date",
            "delete", "mark-done", "mark-credit", "unmark-credit", "cancel",
        ],
    )
    def test_the_door_is_not_found_and_the_pair_is_untouched(
        self, app, auth_client, seed_user, seed_periods_today, method, url, data,
    ):
        """404 for the shadow, and the parent and both legs exactly as created."""
        with app.app_context():
            xfer, expense, _income = _create_test_transfer(
                seed_user, seed_periods_today,
            )
            xfer_id, shadow_id = xfer.id, expense.id
            before = self._state(xfer_id)

            request = getattr(auth_client, method)
            resp = request(url.format(id=shadow_id), data=data)

            assert resp.status_code == 404, resp.get_data(as_text=True)[:200]
            assert self._state(xfer_id) == before, (
                "a transaction door moved a transfer's pair through a shadow row"
            )

    def test_the_companion_aware_door_refuses_a_shadow_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark Paid reaches rows through ``get_accessible_transaction``: same fence.

        The mark-done route reads the companion-aware door rather than the
        owner one, so the fence is stated in both; this pins the second.
        """
        with app.app_context():
            xfer, _expense, income = _create_test_transfer(
                seed_user, seed_periods_today,
            )
            xfer_id, shadow_id = xfer.id, income.id
            before = self._state(xfer_id)

            resp = auth_client.post(
                f"/transactions/{shadow_id}/mark-done",
                data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "1"},
            )

            assert resp.status_code == 404
            assert self._state(xfer_id) == before


class TestASoftDeletedRowCannotBeSettled:
    """Finding **N-233** at the door that made it reachable.

    ``get_accessible_transaction`` does not filter ``is_deleted``, so the
    mark-done route accepted a soft-deleted row: the verb's MANUAL branch had
    no deleted-row refusal (its envelope branch always had one), so the row
    flipped into the settled band and was stamped with a settle day while
    ``effective_amount`` valued it at ``Decimal("0")``.  Production carries 102
    soft-deleted rows.
    """

    def test_mark_done_on_a_soft_deleted_row_is_a_designed_400(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The route refuses it, and the row is left exactly as it was.

        Shown to FIRE: without the refusal the response is a 200 and the row
        comes back Paid with today's settle day.
        """
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)
            txn.is_deleted = True
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")

            assert resp.status_code == 400
            assert b"soft-deleted" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status.name == "Projected"
            assert reloaded.settled_on is None


class TestRegularTransactionUnaffected:
    """Verify guards do not interfere with regular (non-shadow) transactions."""

    def test_update_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """PATCH on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "75.00", "estimated_amount_as_rendered": "50.00"},
            )

            assert resp.status_code == 200
            db.session.refresh(txn)
            # Read through the resolver: the row is a ONE-OFF (plan step
            # balance:X-bi-7c), priced by its definition, so the raw column is None
            # and the app's resolver is the reader (ruling R-BAL60).
            assert resolved_amount(txn) == Decimal("75.00")

    def test_mark_done_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-done on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_mark_credit_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-credit on regular expense transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_cancel_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Cancel on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_delete_regular_one_off_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Delete on a regular one-off works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.delete(f"/transactions/{txn_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, txn_id) is None

    def test_full_edit_regular_returns_transaction_form(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Full edit for regular transaction returns transaction form."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Transaction form has estimated_amount field.
            assert "estimated_amount" in html
            # Does not contain transfer-specific endpoint.
            assert "/transfers/instance/" not in html


# ── Due Date PATCH Tests ────────────────────────────────────────────


class TestDueDatePatch:
    """Tests for PATCH due_date on transactions."""

    def test_patch_due_date_override(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH due_date updates the transaction's due_date."""
        with app.app_context():
            from datetime import date
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods_today[0],
                name="Test Bill",
                amount=Decimal("500.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense.id,
                due_date=date(2026, 1, 15),
            )
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"due_date": "2026-01-20"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.due_date == date(2026, 1, 20)

    def test_patch_due_date_from_a_leg_propagates(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH due_date on a transfer from its grid LEG updates both shadows.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        PATCHed the SHADOW row through the transaction door; the grid's door
        is the transfer PATCH carrying ``leg_account_id`` now.
        """
        with app.app_context():
            from datetime import date

            transfer, exp_shadow, inc_shadow = _create_test_transfer(
                seed_user, seed_periods_today,
            )

            resp = auth_client.patch(
                f"/transfers/instance/{transfer.id}",
                data={
                    "due_date": "2026-01-20",
                    "leg_account_id": str(exp_shadow.account_id),
                },
            )
            assert resp.status_code == 200

            db.session.refresh(exp_shadow)
            db.session.refresh(inc_shadow)
            assert exp_shadow.due_date == date(2026, 1, 20)
            assert inc_shadow.due_date == date(2026, 1, 20)

    def test_patch_due_date_does_not_affect_other_fields(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH only due_date leaves amount, status, notes unchanged."""
        with app.app_context():
            from datetime import date
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods_today[0],
                name="Stable",
                amount=Decimal("750.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense.id,
                due_date=date(2026, 1, 5),
            )
            txn.notes = "original note"
            db.session.commit()

            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"due_date": "2026-01-25"},
            )
            db.session.refresh(txn)
            assert resolved_amount(txn) == Decimal("750.00")
            assert txn.notes == "original note"
            assert txn.status_id == projected.id

    def test_full_edit_shows_due_date(self, app, auth_client, seed_user, seed_periods_today):
        """GET full-edit popover contains due_date input for txn with due_date."""
        with app.app_context():
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods_today[0],
                name="With Due",
                amount=Decimal("100.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense.id,
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"due_date" in resp.data
            assert b"2026-01-10" in resp.data


# ── Loan-Account Create Guard (N-11 / ruling D4) ──────────────────


class TestLoanAccountTransactionGuard:
    """A raw transaction cannot be typed onto an amortizing loan account.

    A loan's balance is ledger-derived, not a transaction sum (ruling D4).
    A raw transaction posted onto a loan account books a bare cash leg onto
    the loan's linked ledger that the sum-of-postings reader counts as a
    real paydown while the loan fold cannot see it -- finding N-11, the one
    balance shape where the two producers diverge with nothing to reconcile
    them.  Both create endpoints refuse it with 422 and write nothing,
    mirroring the transfer-out-of-loan guard (review R6) and closing the
    ad-hoc / inline doors the grid picker (step A1) does not gate.
    """

    def _loan_account(self, seed_user):
        """Build an amortizing loan owned by the seeded user."""
        return create_loan_account(
            seed_user, db.session,
            principal=Decimal("200000.00"), rate=Decimal("0.06"),
            origination_date=date(2026, 1, 1), name="Guard Loan",
        )

    def _expense_form(self, seed_user, seed_periods_today, account_id):
        """The minimal valid ad-hoc create form, targeting *account_id*."""
        expense = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        category = list(seed_user["categories"].values())[0]
        return {
            "name": "Typed On Loan",
            "estimated_amount": "300.00",
            "estimated_amount_as_rendered": "50.00",
            "account_id": account_id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "transaction_type_id": expense.id,
            "scenario_id": seed_user["scenario"].id,
        }

    def test_ad_hoc_create_on_loan_is_refused(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transactions onto a loan returns 422 and writes nothing.

        The loan account carries no transactions of its own (its balance
        lives in the posting ledger), so a zero count after the refused
        POST proves the write never happened.
        """
        with app.app_context():
            loan = self._loan_account(seed_user)
            db.session.commit()
            form = self._expense_form(seed_user, seed_periods_today, loan.id)
            resp = auth_client.post("/transactions", data=form)
            assert resp.status_code == 422
            assert b"not a transaction sum" in resp.data
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=loan.id).count() == 0
            )

    def test_inline_create_on_loan_is_refused(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transactions/inline onto a loan returns 422 and writes nothing."""
        with app.app_context():
            loan = self._loan_account(seed_user)
            db.session.commit()
            form = self._expense_form(seed_user, seed_periods_today, loan.id)
            del form["name"]  # the inline form's name field is optional
            resp = auth_client.post("/transactions/inline", data=form)
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=loan.id).count() == 0
            )

    def test_inline_create_on_plain_account_still_succeeds(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Negative control: the SAME request on a non-loan account is accepted.

        Proves the 422 is attributable to the loan KIND, not the form.  The
        seeded checking account is non-amortizing, so the identical inline
        create succeeds (201) and adds exactly one transaction -- if the
        guard rejected on anything but the loan kind, this control would
        fail.
        """
        with app.app_context():
            plain = seed_user["account"]  # seeded checking (non-amortizing)
            before = (
                db.session.query(Transaction)
                .filter_by(account_id=plain.id).count()
            )
            form = self._expense_form(seed_user, seed_periods_today, plain.id)
            del form["name"]
            resp = auth_client.post("/transactions/inline", data=form)
            assert resp.status_code == 201
            after = (
                db.session.query(Transaction)
                .filter_by(account_id=plain.id).count()
            )
            assert after == before + 1

    def test_direct_income_on_a_credit_card_is_accepted_at_both_doors(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Second negative control: direct income on a CARD passes this guard.

        Plan step credit_card:CC-10 refuses a transfer OUT of a card at the
        transfer doors and leaves direct income on the card -- a refund, a
        redemption -- allowed (design 3.8).  A card classifies PLAIN, not
        AMORTIZING, and the guard never reads the transaction TYPE, so an
        income POST onto a card lands with 201 at BOTH create doors, one new
        transaction each, where the loan cases above get a 422.  Pinned so a
        widening of this guard to "any liability" cannot pass unnoticed.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            income = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            form = self._expense_form(seed_user, seed_periods_today, card.id)
            form["name"] = "Refund On Card"
            form["transaction_type_id"] = income.id
            resp = auth_client.post("/transactions", data=form)
            assert resp.status_code == 201

            del form["name"]  # the inline form's name field is optional
            resp = auth_client.post("/transactions/inline", data=form)
            assert resp.status_code == 201

            rows = (
                db.session.query(Transaction)
                .filter_by(account_id=card.id)
                .order_by(Transaction.id).all()
            )
            assert [r.transaction_type_id for r in rows] == [income.id] * 2
            assert rows[0].name == "Refund On Card"
