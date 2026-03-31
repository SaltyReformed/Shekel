"""
Shekel Budget App - Access Control Tests (IDOR Prevention)

Verifies that users cannot access other users' resources by guessing
database IDs. Every route that accepts an ID parameter is tested.
User B (second_auth_client) attempts to access User A's resources
(seed_full_user_data). Every attempt must return 302 (redirect) or
404 (not found), never 200 (success).

These tests are the safety net against Insecure Direct Object Reference
vulnerabilities. A 200 response on any test means User B can access
User A's financial data.
"""
# pylint: disable=too-many-lines
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.account import Account
from app.models.category import Category
from app.models.pension_profile import PensionProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate


def _assert_blocked(response, msg=""):
    """Assert that a response indicates the request was blocked.

    Ownership checks return either 302 (redirect with flash) or
    404 (direct not-found). A 200 means the attacker got access.

    Args:
        response: The Flask test client response.
        msg: Optional context message for the assertion.
    """
    assert response.status_code in (302, 404), (
        f"Expected 302 or 404 but got {response.status_code}. "
        f"User B may have accessed User A's resource. {msg}"
    )


# ---- Section 5.1: Account Routes ------------------------------------------


class TestAccountAccessControl:
    """IDOR tests for account routes."""

    def test_edit_account_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's account edit form."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/edit"
            )
            _assert_blocked(response, "GET /accounts/<id>/edit")

    def test_update_account_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's account."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(response, "POST /accounts/<id>")

    def test_deactivate_account_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot deactivate User A's account."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/delete"
            )
            _assert_blocked(response, "POST /accounts/<id>/delete")

    def test_reactivate_account_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot reactivate User A's account."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/reactivate"
            )
            _assert_blocked(response, "POST /accounts/<id>/reactivate")

    def test_inline_anchor_update_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's anchor balance inline."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.patch(
                f"/accounts/{target_id}/inline-anchor",
                data={"anchor_balance": "99999.99"},
            )
            _assert_blocked(
                response, "PATCH /accounts/<id>/inline-anchor",
            )

    def test_inline_anchor_form_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's inline anchor edit form."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/inline-anchor-form"
            )
            _assert_blocked(
                response, "GET /accounts/<id>/inline-anchor-form",
            )

    def test_inline_anchor_display_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's inline anchor display."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/inline-anchor-display"
            )
            _assert_blocked(
                response,
                "GET /accounts/<id>/inline-anchor-display",
            )

    def test_true_up_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot true-up User A's anchor balance."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.patch(
                f"/accounts/{target_id}/true-up",
                data={"anchor_balance": "99999.99"},
            )
            _assert_blocked(response, "PATCH /accounts/<id>/true-up")

    def test_anchor_form_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's grid anchor edit form."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/anchor-form"
            )
            _assert_blocked(
                response, "GET /accounts/<id>/anchor-form",
            )

    def test_anchor_display_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's grid anchor display."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/anchor-display"
            )
            _assert_blocked(
                response, "GET /accounts/<id>/anchor-display",
            )

    def test_hysa_detail_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's HYSA detail page."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/interest"
            )
            _assert_blocked(response, "GET /accounts/<id>/interest")

    def test_update_hysa_params_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's HYSA parameters."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/interest/params",
                data={
                    "apy": "5.00",
                    "compounding_frequency": "daily",
                },
            )
            _assert_blocked(
                response, "POST /accounts/<id>/interest/params",
            )


# ---- Section 5.2: Template Routes -----------------------------------------


class TestTemplateAccessControl:
    """IDOR tests for transaction template routes."""

    def test_edit_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's template edit form."""
        with app.app_context():
            target_id = seed_full_user_data["template"].id
            response = second_auth_client.get(
                f"/templates/{target_id}/edit"
            )
            _assert_blocked(response, "GET /templates/<id>/edit")

    def test_update_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's template."""
        with app.app_context():
            target_id = seed_full_user_data["template"].id
            response = second_auth_client.post(
                f"/templates/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(response, "POST /templates/<id>")

    def test_delete_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot deactivate User A's template."""
        with app.app_context():
            target_id = seed_full_user_data["template"].id
            response = second_auth_client.post(
                f"/templates/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /templates/<id>/delete",
            )

    def test_reactivate_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot reactivate User A's template."""
        with app.app_context():
            target_id = seed_full_user_data["template"].id
            response = second_auth_client.post(
                f"/templates/{target_id}/reactivate"
            )
            _assert_blocked(
                response, "POST /templates/<id>/reactivate",
            )


# ---- Section 5.4: Transaction Routes --------------------------------------


class TestTransactionAccessControl:
    """IDOR tests for transaction routes."""

    def test_get_cell_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's transaction cell."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.get(
                f"/transactions/{target_id}/cell"
            )
            _assert_blocked(
                response, "GET /transactions/<id>/cell",
            )

    def test_get_quick_edit_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's quick-edit form."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.get(
                f"/transactions/{target_id}/quick-edit"
            )
            _assert_blocked(
                response, "GET /transactions/<id>/quick-edit",
            )

    def test_get_full_edit_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's full-edit form."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.get(
                f"/transactions/{target_id}/full-edit"
            )
            _assert_blocked(
                response, "GET /transactions/<id>/full-edit",
            )

    def test_update_transaction_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's transaction."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.patch(
                f"/transactions/{target_id}",
                data={"estimated_amount": "999.99"},
            )
            _assert_blocked(
                response, "PATCH /transactions/<id>",
            )

    def test_mark_done_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot mark User A's transaction as done."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.post(
                f"/transactions/{target_id}/mark-done"
            )
            _assert_blocked(
                response, "POST /transactions/<id>/mark-done",
            )

    def test_mark_credit_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot mark User A's transaction as credit."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.post(
                f"/transactions/{target_id}/mark-credit"
            )
            _assert_blocked(
                response, "POST /transactions/<id>/mark-credit",
            )

    def test_unmark_credit_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot unmark credit on User A's transaction."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.delete(
                f"/transactions/{target_id}/unmark-credit"
            )
            _assert_blocked(
                response,
                "DELETE /transactions/<id>/unmark-credit",
            )

    def test_cancel_transaction_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot cancel User A's transaction."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.post(
                f"/transactions/{target_id}/cancel"
            )
            _assert_blocked(
                response, "POST /transactions/<id>/cancel",
            )

    def test_delete_transaction_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot delete User A's transaction."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            response = second_auth_client.delete(
                f"/transactions/{target_id}"
            )
            _assert_blocked(
                response, "DELETE /transactions/<id>",
            )

    def test_carry_forward_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot carry forward User A's pay period."""
        with app.app_context():
            target_id = seed_full_user_data["periods"][0].id
            response = second_auth_client.post(
                f"/pay-periods/{target_id}/carry-forward"
            )
            _assert_blocked(
                response,
                "POST /pay-periods/<id>/carry-forward",
            )


# ---- Section 5.3: Transfer Template Routes --------------------------------


class TestTransferTemplateAccessControl:
    """IDOR tests for transfer template routes."""

    def test_edit_transfer_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's transfer template edit form."""
        with app.app_context():
            target_id = seed_full_user_data["transfer_template"].id
            response = second_auth_client.get(
                f"/transfers/{target_id}/edit"
            )
            _assert_blocked(
                response, "GET /transfers/<id>/edit",
            )

    def test_update_transfer_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's transfer template."""
        with app.app_context():
            target_id = seed_full_user_data["transfer_template"].id
            response = second_auth_client.post(
                f"/transfers/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(response, "POST /transfers/<id>")

    def test_delete_transfer_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot deactivate User A's transfer template."""
        with app.app_context():
            target_id = seed_full_user_data["transfer_template"].id
            response = second_auth_client.post(
                f"/transfers/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /transfers/<id>/delete",
            )

    def test_reactivate_transfer_template_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot reactivate User A's transfer template."""
        with app.app_context():
            target_id = seed_full_user_data["transfer_template"].id
            response = second_auth_client.post(
                f"/transfers/{target_id}/reactivate"
            )
            _assert_blocked(
                response, "POST /transfers/<id>/reactivate",
            )


# ---- Section 5.5: Salary Routes -------------------------------------------


class TestSalaryAccessControl:
    """IDOR tests for salary profile routes."""

    def test_edit_profile_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's salary profile edit form."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.get(
                f"/salary/{target_id}/edit"
            )
            _assert_blocked(response, "GET /salary/<id>/edit")

    def test_update_profile_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's salary profile."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.post(
                f"/salary/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(response, "POST /salary/<id>")

    def test_delete_profile_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot delete User A's salary profile."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.post(
                f"/salary/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /salary/<id>/delete",
            )

    def test_add_raise_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot add a raise to User A's salary profile."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.post(
                f"/salary/{target_id}/raises",
                data={
                    "name": "BLOCKED_TEST",
                    "amount": "1000",
                    "raise_type_id": "1",
                },
            )
            _assert_blocked(
                response, "POST /salary/<id>/raises",
            )

    def test_add_deduction_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot add a deduction to User A's salary profile."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.post(
                f"/salary/{target_id}/deductions",
                data={
                    "name": "BLOCKED_TEST",
                    "amount": "100",
                    "deduction_timing_id": "1",
                    "calc_method_id": "1",
                },
            )
            _assert_blocked(
                response, "POST /salary/<id>/deductions",
            )

    def test_breakdown_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's paycheck breakdown (current)."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.get(
                f"/salary/{target_id}/breakdown"
            )
            _assert_blocked(
                response, "GET /salary/<id>/breakdown",
            )

    def test_breakdown_with_period_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's paycheck breakdown for a period."""
        with app.app_context():
            profile_id = seed_full_user_data["salary_profile"].id
            period_id = seed_full_user_data["periods"][0].id
            response = second_auth_client.get(
                f"/salary/{profile_id}/breakdown/{period_id}"
            )
            _assert_blocked(
                response,
                "GET /salary/<id>/breakdown/<period_id>",
            )

    def test_projection_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's salary projection."""
        with app.app_context():
            target_id = seed_full_user_data["salary_profile"].id
            response = second_auth_client.get(
                f"/salary/{target_id}/projection"
            )
            _assert_blocked(
                response, "GET /salary/<id>/projection",
            )


# ---- Section 5.7: Savings Routes ------------------------------------------


class TestSavingsAccessControl:
    """IDOR tests for savings goal routes."""

    def test_edit_goal_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's savings goal edit form."""
        with app.app_context():
            target_id = seed_full_user_data["savings_goal"].id
            response = second_auth_client.get(
                f"/savings/goals/{target_id}/edit"
            )
            _assert_blocked(
                response, "GET /savings/goals/<id>/edit",
            )

    def test_update_goal_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's savings goal."""
        with app.app_context():
            target_id = seed_full_user_data["savings_goal"].id
            response = second_auth_client.post(
                f"/savings/goals/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(
                response, "POST /savings/goals/<id>",
            )

    def test_delete_goal_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot deactivate User A's savings goal."""
        with app.app_context():
            target_id = seed_full_user_data["savings_goal"].id
            response = second_auth_client.post(
                f"/savings/goals/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /savings/goals/<id>/delete",
            )


# ---- Section 5.8: Category Routes -----------------------------------------


class TestCategoryAccessControl:
    """IDOR tests for category routes."""

    def test_delete_category_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot delete User A's category."""
        with app.app_context():
            target_id = seed_full_user_data["categories"]["Rent"].id
            response = second_auth_client.post(
                f"/categories/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /categories/<id>/delete",
            )


# ---- Section 5.6: Retirement/Pension Routes -------------------------------


class TestRetirementAccessControl:
    """IDOR tests for retirement/pension routes."""

    @pytest.fixture()
    def user_a_pension(self, db, seed_full_user_data):
        """Create a pension profile for User A."""
        pension = PensionProfile(
            user_id=seed_full_user_data["user"].id,
            name="Test Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()
        return pension

    def test_edit_pension_blocked(
        self, app, second_auth_client, user_a_pension,
    ):
        """User B cannot view User A's pension edit form."""
        with app.app_context():
            target_id = user_a_pension.id
            response = second_auth_client.get(
                f"/retirement/pension/{target_id}/edit"
            )
            _assert_blocked(
                response, "GET /retirement/pension/<id>/edit",
            )

    def test_update_pension_blocked(
        self, app, second_auth_client, user_a_pension,
    ):
        """User B cannot update User A's pension profile."""
        with app.app_context():
            target_id = user_a_pension.id
            response = second_auth_client.post(
                f"/retirement/pension/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(
                response, "POST /retirement/pension/<id>",
            )

    def test_delete_pension_blocked(
        self, app, second_auth_client, user_a_pension,
    ):
        """User B cannot deactivate User A's pension profile."""
        with app.app_context():
            target_id = user_a_pension.id
            response = second_auth_client.post(
                f"/retirement/pension/{target_id}/delete"
            )
            _assert_blocked(
                response,
                "POST /retirement/pension/<id>/delete",
            )


# ---- Section 5.9: Loan Routes ---------------------------------------------


class TestLoanAccessControl:
    """IDOR tests for unified loan routes."""

    def test_loan_dashboard_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's loan dashboard."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/loan"
            )
            _assert_blocked(
                response, "GET /accounts/<id>/loan",
            )

    def test_loan_setup_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot set up loan params on User A's account."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/loan/setup",
                data={
                    "current_principal": "200000",
                    "interest_rate": "6.5",
                    "term_months": "360",
                    "origination_date": "2024-01-01",
                    "payment_day": "1",
                },
            )
            _assert_blocked(
                response, "POST /accounts/<id>/loan/setup",
            )

    def test_loan_update_params_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's loan parameters."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/loan/params",
                data={"current_principal": "999999"},
            )
            _assert_blocked(
                response,
                "POST /accounts/<id>/loan/params",
            )

    def test_loan_add_rate_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot add a rate change to User A's loan."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/loan/rate",
                data={
                    "effective_date": "2026-04-01",
                    "interest_rate": "7.0",
                },
            )
            _assert_blocked(
                response, "POST /accounts/<id>/loan/rate",
            )

    def test_loan_add_escrow_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot add an escrow component to User A's loan."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/loan/escrow",
                data={
                    "name": "BLOCKED_TEST",
                    "annual_amount": "3600",
                },
            )
            _assert_blocked(
                response,
                "POST /accounts/<id>/loan/escrow",
            )

    def test_loan_payoff_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot calculate payoff on User A's loan."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/loan/payoff",
                data={
                    "mode": "extra_payment",
                    "extra_monthly": "500",
                },
            )
            _assert_blocked(
                response, "POST /accounts/<id>/loan/payoff",
            )


# ---- Section 5.11: Investment Routes --------------------------------------


class TestInvestmentAccessControl:
    """IDOR tests for investment/retirement account routes."""

    def test_investment_dashboard_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's investment dashboard."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/investment"
            )
            _assert_blocked(
                response, "GET /accounts/<id>/investment",
            )

    def test_investment_growth_chart_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot view User A's investment growth chart."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.get(
                f"/accounts/{target_id}/investment/growth-chart",
                headers={"HX-Request": "true"},
            )
            _assert_blocked(
                response,
                "GET /accounts/<id>/investment/growth-chart",
            )

    def test_investment_update_params_blocked(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User B cannot update User A's investment parameters."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            response = second_auth_client.post(
                f"/accounts/{target_id}/investment/params",
                data={"assumed_annual_return": "0.07"},
            )
            _assert_blocked(
                response,
                "POST /accounts/<id>/investment/params",
            )


# ---- Nonexistent Resource Access -------------------------------------------


class TestNonexistentResourceAccess:
    """Verify that nonexistent IDs return 404, not 500."""

    def test_nonexistent_account(self, app, second_auth_client):
        """Nonexistent account ID returns 302 or 404."""
        with app.app_context():
            response = second_auth_client.get("/accounts/999999/edit")
            _assert_blocked(response, "nonexistent account")

    def test_nonexistent_transaction(self, app, second_auth_client):
        """Nonexistent transaction ID returns 404."""
        with app.app_context():
            response = second_auth_client.get(
                "/transactions/999999/cell"
            )
            assert response.status_code == 404, (
                f"Expected 404 but got {response.status_code}"
            )

    def test_nonexistent_template(self, app, second_auth_client):
        """Nonexistent template ID returns 302 or 404."""
        with app.app_context():
            response = second_auth_client.get("/templates/999999/edit")
            _assert_blocked(response, "nonexistent template")

    def test_nonexistent_salary_profile(
        self, app, second_auth_client,
    ):
        """Nonexistent salary profile ID returns 302 or 404."""
        with app.app_context():
            response = second_auth_client.get("/salary/999999/edit")
            _assert_blocked(response, "nonexistent salary profile")

    def test_nonexistent_savings_goal(
        self, app, second_auth_client,
    ):
        """Nonexistent savings goal ID returns 302 or 404."""
        with app.app_context():
            response = second_auth_client.get(
                "/savings/goals/999999/edit"
            )
            _assert_blocked(response, "nonexistent savings goal")


# ---- Data Integrity After Blocked Access -----------------------------------


class TestDataIntegrityAfterBlockedAccess:
    """Verify that blocked attempts do not partially modify resources.

    The most critical class of bug is a partial mutation: the route
    starts modifying the resource, then hits the ownership check and
    returns 404, but the modification is partially committed.
    """

    def test_account_unchanged_after_blocked_update(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User A's account is unchanged after User B's blocked update."""
        with app.app_context():
            target_id = seed_full_user_data["account"].id
            original_name = seed_full_user_data["account"].name
            original_anchor = (
                seed_full_user_data["account"].current_anchor_balance
            )

            response = second_auth_client.post(
                f"/accounts/{target_id}",
                data={"name": "BLOCKED_TEST"},
            )
            _assert_blocked(response, "POST /accounts/<id>")

            _db.session.expire_all()
            account = _db.session.get(Account, target_id)
            assert account.name == original_name, (
                f"Account name changed from '{original_name}' "
                f"to '{account.name}' after blocked update"
            )
            assert account.current_anchor_balance == original_anchor, (
                "Anchor balance changed after blocked update"
            )

    def test_transaction_unchanged_after_blocked_mark_done(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User A's transaction status is unchanged after blocked mark-done."""
        with app.app_context():
            target_id = seed_full_user_data["transaction"].id
            original_status = (
                seed_full_user_data["transaction"].status_id
            )

            response = second_auth_client.post(
                f"/transactions/{target_id}/mark-done"
            )
            _assert_blocked(
                response, "POST /transactions/<id>/mark-done",
            )

            _db.session.expire_all()
            txn = _db.session.get(Transaction, target_id)
            assert txn.status_id == original_status, (
                "Transaction status changed after blocked mark-done"
            )

    def test_template_unchanged_after_blocked_delete(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User A's template is still active after blocked delete."""
        with app.app_context():
            target_id = seed_full_user_data["template"].id
            original_active = seed_full_user_data["template"].is_active

            response = second_auth_client.post(
                f"/templates/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /templates/<id>/delete",
            )

            _db.session.expire_all()
            template = _db.session.get(
                TransactionTemplate, target_id,
            )
            assert template.is_active == original_active, (
                "Template is_active changed after blocked delete"
            )

    def test_category_unchanged_after_blocked_delete(
        self, app, second_auth_client, seed_full_user_data,
    ):
        """User A's category count is unchanged after blocked delete."""
        with app.app_context():
            user_a_id = seed_full_user_data["user"].id
            target_id = (
                seed_full_user_data["categories"]["Groceries"].id
            )

            count_before = (
                _db.session.query(Category)
                .filter_by(user_id=user_a_id)
                .count()
            )

            response = second_auth_client.post(
                f"/categories/{target_id}/delete"
            )
            _assert_blocked(
                response, "POST /categories/<id>/delete",
            )

            _db.session.expire_all()
            count_after = (
                _db.session.query(Category)
                .filter_by(user_id=user_a_id)
                .count()
            )
            assert count_after == count_before, (
                f"Category count changed from {count_before} "
                f"to {count_after} after blocked delete"
            )
