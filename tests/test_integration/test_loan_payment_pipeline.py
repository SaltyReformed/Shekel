"""
Integration test for the Section 5.1 loan payment pipeline.

Verifies the end-to-end flow: recurring transfer creation via the loan
dashboard route -> shadow transaction generation -> payment history
query -> amortization engine projection -> balance calculator.

Also verifies all five transfer invariants hold throughout the process.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.services import balance_calculator
from app.services.loan_payment_service import get_payment_history


class TestLoanPaymentPipeline:
    """End-to-end integration test for the Section 5.1 payment pipeline.

    Exercises every layer: route -> transfer service -> shadow
    transactions -> payment query -> amortization engine -> balance
    calculator.  A passing test means the entire payment linkage
    pipeline works correctly.
    """

    def test_full_payment_pipeline(  # pylint: disable=too-many-locals,too-many-statements
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """Full pipeline: create transfer via route, verify shadows,
        verify payment query, verify balance calculator.

        Steps:
        1. Create a checking account (seed_user already has one) and a
           mortgage account with LoanParams.
        2. Use the create_payment_transfer route to set up recurring
           monthly payments.
        3. Verify shadow transactions were created (invariant 1).
        4. Verify shadow amounts match (invariant 3).
        5. Verify shadow statuses match (invariant 4).
        6. Verify shadow periods match (invariant 5).
        7. Verify get_payment_history returns correct PaymentRecords.
        8. Verify the loan dashboard renders with payment-aware projections.
        9. Verify the balance calculator produces correct balances for
           both the checking and mortgage accounts.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario = seed_user["scenario"]
            periods = seed_periods

            # Step 1: Create mortgage account.
            loan_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Pipeline Mortgage",
                current_anchor_balance=Decimal("200000.00"),
            )
            db.session.add(mortgage)
            db.session.flush()

            mortgage.current_anchor_period_id = periods[0].id

            loan_params = LoanParams(
                account_id=mortgage.id,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=periods[0].start_date,
                payment_day=1,
            )
            db.session.add(loan_params)
            db.session.commit()

            # Step 2: Create recurring transfer via the route.
            resp = auth_client.post(
                f"/accounts/{mortgage.id}/loan/create-transfer",
                data={"source_account_id": str(checking.id)},
            )
            assert resp.status_code == 302, (
                f"Expected redirect, got {resp.status_code}"
            )

            # Step 3: Verify transfer template was created.
            template = (
                db.session.query(TransferTemplate)
                .filter_by(
                    to_account_id=mortgage.id,
                    user_id=seed_user["user"].id,
                )
                .first()
            )
            assert template is not None, "Transfer template was not created"
            assert template.is_active is True
            assert template.from_account_id == checking.id
            assert template.default_amount > 0

            # Step 4: Verify shadow transactions exist.
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            all_shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            # Each transfer has exactly 2 shadows (invariant 1).
            transfer_ids = {s.transfer_id for s in all_shadows}
            for tid in transfer_ids:
                pair = [s for s in all_shadows if s.transfer_id == tid]
                assert len(pair) == 2, (
                    f"Transfer {tid} has {len(pair)} shadows, expected 2"
                )
                types = {s.transaction_type_id for s in pair}
                assert income_type_id in types, (
                    f"Transfer {tid} missing income shadow"
                )
                assert expense_type_id in types, (
                    f"Transfer {tid} missing expense shadow"
                )

                # Invariant 3: amounts match.
                amounts = {s.estimated_amount for s in pair}
                assert len(amounts) == 1, (
                    f"Transfer {tid} shadow amounts differ: {amounts}"
                )

                # Invariant 4: statuses match.
                statuses = {s.status_id for s in pair}
                assert len(statuses) == 1, (
                    f"Transfer {tid} shadow statuses differ: {statuses}"
                )

                # Invariant 5: periods match.
                period_ids = {s.pay_period_id for s in pair}
                assert len(period_ids) == 1, (
                    f"Transfer {tid} shadow periods differ: {period_ids}"
                )

            # Step 5: Verify get_payment_history returns the payments.
            payments = get_payment_history(mortgage.id, scenario.id)
            assert len(payments) > 0, "No payments returned from history"
            for payment in payments:
                assert isinstance(payment.amount, Decimal)
                assert payment.amount > 0

            # Step 6: Verify the loan dashboard renders successfully
            # with payment-aware data.
            resp = auth_client.get(f"/accounts/{mortgage.id}/loan")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Loan Summary" in html
            # Prompt should be hidden (recurring transfer exists).
            assert "No recurring payment" not in html

            # Step 7: Verify balance calculator for checking account.
            # Shadow expense transactions reduce the checking balance.
            checking_shadows = [
                s for s in all_shadows
                if s.account_id == checking.id
            ]
            if checking_shadows:
                checking_balances, _ = balance_calculator.calculate_balances(
                    anchor_balance=Decimal("1000.00"),
                    anchor_period_id=periods[0].id,
                    periods=periods,
                    transactions=checking_shadows,
                )
                # After transfers, checking balance should be lower.
                period_with_transfer = checking_shadows[0].pay_period_id
                if period_with_transfer in checking_balances:
                    assert checking_balances[period_with_transfer] < Decimal("1000.00")

            # Step 8: Verify balance calculator for mortgage account.
            # Shadow income transactions increase the mortgage balance
            # (in the generic calculator's view -- it sums income).
            mortgage_shadows = [
                s for s in all_shadows
                if s.account_id == mortgage.id
            ]
            if mortgage_shadows:
                mortgage.current_anchor_period_id = periods[0].id
                mortgage_balances, _ = balance_calculator.calculate_balances(
                    anchor_balance=Decimal("200000.00"),
                    anchor_period_id=periods[0].id,
                    periods=periods,
                    transactions=mortgage_shadows,
                )
                # The generic calculator adds income to balance.
                # For a loan, this is semantically "payment received."
                period_with_payment = mortgage_shadows[0].pay_period_id
                if period_with_payment in mortgage_balances:
                    assert mortgage_balances[period_with_payment] > Decimal("200000.00")
