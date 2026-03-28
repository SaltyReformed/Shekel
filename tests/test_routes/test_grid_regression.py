"""
Shekel Budget App -- Payday Workflow Regression Tests

Regression test suite for the payday workflow (project_requirements_v2.md
Section 3).  Created as Commit #0 of Section 4 (UX/Grid Overhaul) to
protect against breakage during schema changes, enum refactoring, and DOM
restructuring in Commits #1 through #16.

This file tests the application as it exists BEFORE any Section 4 changes.
All assertions use current status names, current route paths, current
template output, and current fixture patterns.  When Section 4 refactors
land, these tests will be updated alongside them.
"""

# ------------------------------------------------------------------
# Implementation Plan Verification Notes (Commit #0)
# ------------------------------------------------------------------
# This test suite was written against the actual codebase, not the
# implementation plan.  The following discrepancies were noted:
#
# - mark_done and mark_credit routes return HX-Trigger: "gridRefresh"
#   (consistent with the code; confirmed in app/routes/transactions.py
#   lines 246 and 267).
# - true_up route returns HX-Trigger: "balanceChanged" (confirmed in
#   app/routes/accounts.py line 503).
# - _balance_row.html tfoot contains exactly 1 <tr> element (confirmed
#   in app/templates/grid/_balance_row.html).
# - Status model does not yet have is_settled / is_immutable boolean
#   columns (those are Commit #1 of Section 4).
# - None of the planned renames (e.g., "done" -> "Paid") are in effect
#   yet.  Tests use the current status names: projected, done, received,
#   credit, cancelled, settled.
# ------------------------------------------------------------------

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import Status, TransactionType
from app.services import pay_period_service


class TestPaydayWorkflowRegression:
    """Regression tests covering the complete payday workflow.

    Each test maps to a step in the payday workflow documented in
    project_requirements_v2.md Section 3:

        C-0-1: Anchor balance true-up (Step 2)
        C-0-2: Mark paycheck as received (Step 3)
        C-0-3: Carry forward unpaid items (Step 4)
        C-0-4: Mark expense as done/paid (Step 5)
        C-0-5: Mark expense as credit card (Step 6)
        C-0-6: Balance row HTMX refresh
        C-0-7: Full payday sequence end-to-end

    All tests use the existing conftest fixtures (seed_user, seed_periods,
    auth_client) and are fully independent of each other.
    """

    # -- C-0-1 -------------------------------------------------------

    def test_trueup_anchor_balance(self, app, auth_client, seed_user,
                                   seed_periods):
        """Anchor balance true-up updates the balance, sets the anchor
        period to the current period, and returns display HTML with the
        balanceChanged HX-Trigger.

        Covers Step 2 of the payday workflow: the user logs their real
        checking balance so projections are anchored to reality.
        """
        with app.app_context():
            account = seed_user["account"]

            response = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "3500.00"},
            )
            assert response.status_code == 200

            # HX-Trigger drives balance row recalculation.
            # Verified: accounts.py true_up returns "balanceChanged".
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Response HTML: _anchor_edit.html display mode contains the
            # formatted balance inside an id="anchor-display" div.
            assert b"anchor-display" in response.data
            assert b"$3,500" in response.data

            # Database: anchor balance updated.
            # Re-fetch from DB because fixture-created objects may be
            # detached after request processing (scoped session recycling).
            account = db.session.get(Account, account.id)
            assert account.current_anchor_balance == Decimal("3500.00")

            # Database: anchor period set to the current period.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert account.current_anchor_period_id == current_period.id

    # -- C-0-2 -------------------------------------------------------

    def test_mark_paycheck_received(self, app, auth_client, seed_user,
                                    seed_periods):
        """Marking an income transaction as done sets its status to
        'received' (not 'done'), returns the badge-done indicator, and
        triggers gridRefresh.

        Covers Step 3: the user confirms their paycheck was deposited.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done"
            )
            assert response.status_code == 200

            # Income uses 'received', not 'done'.
            # Verified: transactions.py mark_done checks is_income.
            db.session.refresh(txn)
            assert txn.status.name == "Received"

            # HX-Trigger verified in transactions.py mark_done.
            assert response.headers.get("HX-Trigger") == "gridRefresh"

            # Response HTML: _transaction_cell.html renders badge-done
            # for both 'done' and 'received' statuses.
            assert b"badge-done" in response.data

    # -- C-0-3 -------------------------------------------------------

    def test_carry_forward_unpaid(self, app, auth_client, seed_user,
                                  seed_periods):
        """Carry forward moves projected transactions to the current
        period, flags template-linked items as overrides, and leaves
        done items in the source period.

        Covers Step 4: unpaid items from a past period are carried
        forward so they remain visible in the current view.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="Paid").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            past_period = seed_periods[0]

            # Create a template so one transaction is template-linked.
            # Carry forward sets is_override=True on template-linked items
            # because they moved from their rule-assigned period.
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent Payment",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()

            # Projected expense 1: template-linked.
            txn_template = Transaction(
                template_id=template.id,
                pay_period_id=past_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent Payment",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            # Projected expense 2: ad-hoc (no template).
            txn_adhoc = Transaction(
                pay_period_id=past_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            # Done expense: should NOT be carried forward.
            txn_done = Transaction(
                pay_period_id=past_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=done_status.id,
                name="Car Payment",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add_all([txn_template, txn_adhoc, txn_done])
            db.session.commit()

            response = auth_client.post(
                f"/pay-periods/{past_period.id}/carry-forward"
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "gridRefresh"

            # Resolve the target (the route uses get_current_period).
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )

            db.session.refresh(txn_template)
            db.session.refresh(txn_adhoc)
            db.session.refresh(txn_done)

            # Both projected transactions moved to current period.
            assert txn_template.pay_period_id == current_period.id
            assert txn_adhoc.pay_period_id == current_period.id

            # Done transaction stays in the past period.
            assert txn_done.pay_period_id == past_period.id

            # Template-linked transaction is flagged as override.
            # Verified: carry_forward_service sets is_override when
            # txn.template_id is not None.
            assert txn_template.is_override is True

    # -- C-0-4 -------------------------------------------------------

    def test_mark_expense_done(self, app, auth_client, seed_user,
                               seed_periods):
        """Marking an expense as done sets its status to 'done', returns
        the badge-done indicator, and triggers gridRefresh.

        Covers Step 5: the user confirms a bill was paid from checking.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done"
            )
            assert response.status_code == 200

            # Expenses get 'done' (not 'received').
            # Verified: transactions.py mark_done else branch.
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            assert response.headers.get("HX-Trigger") == "gridRefresh"
            assert b"badge-done" in response.data

    # -- C-0-5 -------------------------------------------------------

    def test_mark_credit_creates_payback(self, app, auth_client,
                                         seed_user, seed_periods):
        """Marking an expense as credit sets status to 'credit' and
        auto-generates a payback transaction in the next pay period
        with matching amount and 'Credit Card: Payback' category.

        Covers Step 6: the user indicates an expense was charged to a
        credit card rather than paid from checking.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            # Create expense in seed_periods[0]; the payback will go to
            # seed_periods[1] (the next period by index).
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Restaurant",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-credit"
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "gridRefresh"

            # Original transaction now has status 'credit'.
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Payback transaction created by credit_workflow.mark_as_credit.
            # Verified: credit_workflow.py creates payback with:
            #   credit_payback_for_id = original txn id
            #   pay_period_id = get_next_period(original's period)
            #   status = projected
            #   estimated_amount = original's estimated (no actual set)
            #   category = "Credit Card: Payback"
            #   transaction_type = expense
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .one()
            )
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.status.name == "Projected"
            assert payback.estimated_amount == Decimal("75.00")
            assert payback.category.group_name == "Credit Card"
            assert payback.category.item_name == "Payback"
            assert payback.transaction_type.name == "expense"

    # -- C-0-6 -------------------------------------------------------

    def test_balance_row_refresh(self, app, auth_client, seed_user,
                                 seed_periods):
        """Balance row endpoint returns a tfoot partial with correct HTMX
        attributes, calculated balances, and a single <tr> element.

        Verifies the HTMX refresh mechanism that keeps projected balances
        current after any grid edit.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            # Create income and expense in the current period so the
            # calculated balance is deterministic.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            income_txn = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            expense_txn = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("800.00"),
            )
            db.session.add_all([income_txn, expense_txn])
            db.session.commit()

            response = auth_client.get(
                "/grid/balance-row?periods=6&offset=0"
            )
            assert response.status_code == 200

            # Structural assertions from _balance_row.html:
            # - tfoot id="grid-summary"
            # - HTMX self-refresh trigger
            # - Projected End Balance label
            assert b'id="grid-summary"' in response.data
            assert (
                b'hx-trigger="balanceChanged from:body"' in response.data
            )
            assert b"Projected End Balance" in response.data

            # Balance calculation:
            # Anchor = $1,000 at period 0 (set by seed_periods fixture).
            # Periods 1-5: no transactions, balance stays $1,000.
            # Period 6 (current): +$2,000 income -$800 expense = $2,200.
            assert b"$2,200" in response.data

            # The tfoot contains exactly 1 <tr> element.
            # Verified: _balance_row.html has one tr.balance-row-summary.
            html = response.data.decode("utf-8")
            tfoot_start = html.find('id="grid-summary"')
            tfoot_end = html.find("</tfoot>")
            tfoot_section = html[tfoot_start:tfoot_end]
            assert tfoot_section.count("<tr") == 1

    # -- C-0-7 -------------------------------------------------------

    def test_full_payday_sequence(self, app, auth_client, seed_user,
                                  seed_periods):
        """Full payday workflow executed as a sequence, verifying that all
        operations interact correctly and the final balance is correct.

        Setup:
          - Past period:    1 projected expense ($150)
          - Current period: 1 income ($2,000) + 2 expenses ($500, $300)
          - Anchor:         $1,000 at period 0 (from seed_periods)

        Steps:
          1. True-up anchor to $5,000 at current period
          2. Mark income as received
          3. Carry forward from past period (moves $150 to current)
          4. Mark $500 expense as done
          5. Mark $300 expense as credit (payback in future period)

        Hand-calculated balances after all steps:

          Current period (anchor = $5,000):
            _sum_remaining counts only projected items:
              received income ($2,000) -- excluded (settled)
              done expense ($500) -- excluded (settled)
              credit expense ($300) -- excluded (credit)
              carried-forward expense ($150) -- counted
            balance = $5,000 + $0 - $150 = $4,850

          Future period:
            _sum_all counts only projected items:
              CC payback ($300) -- counted
            balance = $4,850 + $0 - $300 = $4,550
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            account = seed_user["account"]

            # Resolve key periods.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            # Past period: immediately before current.
            past_period = next(
                p for p in seed_periods
                if p.period_index == current_period.period_index - 1
            )
            # Future period: immediately after current (payback target).
            future_period = next(
                p for p in seed_periods
                if p.period_index == current_period.period_index + 1
            )

            # -- Create test transactions --

            # Past period: projected expense ($150).
            past_expense = Transaction(
                pay_period_id=past_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Past Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            # Current period: income ($2,000).
            income_txn = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            # Current period: expense to mark done ($500).
            expense_done = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            # Current period: expense to mark credit ($300).
            expense_credit = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add_all([
                past_expense, income_txn, expense_done, expense_credit,
            ])
            db.session.commit()

            # -- Step 1: True-up anchor to $5,000 --
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert resp.status_code == 200

            # -- Step 2: Mark income as received --
            resp = auth_client.post(
                f"/transactions/{income_txn.id}/mark-done"
            )
            assert resp.status_code == 200

            # -- Step 3: Carry forward from past period --
            resp = auth_client.post(
                f"/pay-periods/{past_period.id}/carry-forward"
            )
            assert resp.status_code == 200

            # -- Step 4: Mark expense as done --
            resp = auth_client.post(
                f"/transactions/{expense_done.id}/mark-done"
            )
            assert resp.status_code == 200

            # -- Step 5: Mark expense as credit --
            resp = auth_client.post(
                f"/transactions/{expense_credit.id}/mark-credit"
            )
            assert resp.status_code == 200

            # -- Verify final state --

            # Re-fetch all objects from the database.  After 5 sequential
            # HTTP requests, the scoped session may have been recycled and
            # the original Python objects detached.
            account = db.session.get(Account, account.id)
            income_txn = db.session.get(Transaction, income_txn.id)
            past_expense = db.session.get(Transaction, past_expense.id)
            expense_done = db.session.get(Transaction, expense_done.id)
            expense_credit = db.session.get(Transaction, expense_credit.id)

            assert account.current_anchor_balance == Decimal("5000.00")
            assert account.current_anchor_period_id == current_period.id
            assert income_txn.status.name == "Received"
            assert past_expense.pay_period_id == current_period.id
            assert expense_done.status.name == "Paid"
            assert expense_credit.status.name == "Credit"

            # Verify CC payback exists in the future period.
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=expense_credit.id)
                .one()
            )
            assert payback.pay_period_id == future_period.id
            assert payback.estimated_amount == Decimal("300.00")

            # -- Verify balance via balance-row endpoint --
            resp = auth_client.get(
                f"/grid/balance-row?periods=2&offset=0"
                f"&account_id={account.id}"
            )
            assert resp.status_code == 200

            # See hand calculation in docstring above.
            assert b"$4,850" in resp.data
            assert b"$4,550" in resp.data
