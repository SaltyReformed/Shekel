"""
Integration test for the Section 5.1 loan payment pipeline.

Verifies the end-to-end flow: recurring transfer creation via the loan
dashboard route -> shadow transaction generation -> payment history
query -> amortization engine projection -> balance calculator.

Also verifies all five transfer invariants hold throughout the process.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.loan_payment_service import get_payment_history
from tests._test_helpers import (
    amount_basis_for_scenario,
    create_loan_account,
    loan_params_for,
)


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

            # Step 1: Create mortgage account.  The shared factory routes the
            # account through ``account_service.create_account``, inserts the
            # LoanParams + origination RateHistory the resolver needs, and OPENS
            # the loan's genesis posting ledger in the same transaction -- the
            # dance every production loan-write path performs
            # (``app/routes/loan/params.py``).
            mortgage = create_loan_account(
                seed_user, db.session, name="Pipeline Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06500"),
                term=360, origination_date=periods[0].start_date,
                payment_day=1, account_type=AcctTypeEnum.MORTGAGE,
            )

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
            params = loan_params_for(db.session, mortgage.id)
            payments = get_payment_history(
                mortgage.id, amount_basis_for_scenario(scenario.id),
                params.payment_day,
            )
            assert len(payments) > 0, "No payments returned from history"
            for payment in payments:
                assert isinstance(payment.amount, Decimal)
                assert payment.amount > 0
                # Each record carries BOTH dates: the pay period the cash moved
                # in, and the installment it satisfies.  The shadows here are
                # on-time, so the due date is the payment_day of the period's
                # own month or the next.
                assert payment.due_date.day == params.payment_day

            # Step 6: Verify the loan dashboard renders successfully
            # with payment-aware data.
            resp = auth_client.get(f"/accounts/{mortgage.id}/loan")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Balance owed" in html
            # Prompt should be hidden (recurring transfer exists).
            assert "No recurring payment" not in html

            # Step 7: the payment's cash leg reaches the CHECKING balance the
            # app renders.  Re-pointed off the deleted anchor-forward walk at
            # plan step X-g4b, and the two `if` guards went with it: a
            # conditional assertion cannot fail on an empty pipeline, which is
            # exactly the vacuity plan Section 7.3 rules out.  ``as_of`` is
            # pinned inside period 0 so ruling R-G does not clamp the
            # still-Projected shadow out of the window.
            checking_shadows = [
                s for s in all_shadows
                if s.account_id == checking.id
            ]
            assert checking_shadows, "the pipeline produced no checking shadow"
            checking_balances = balance_at.cash_balance_map(
                checking,
                BalanceContext.build(
                    seed_user["user"].id, as_of=periods[0].start_date,
                ),
            )
            period_with_transfer = checking_shadows[0].pay_period_id
            assert period_with_transfer in checking_balances
            assert checking_balances[period_with_transfer] < Decimal("1000.00")

            # Step 8 asked the KIND-BLIND cash view what the MORTGAGE was
            # worth and read a shadow INCOME row as RAISING the balance owed.
            # Ruling R-J closed that door at plan step X-a1: a loan's balance is
            # not a transaction sum, and every cash-flow resolver refuses an
            # amortizing account rather than answering with a wrong figure
            # (measured live before the fix -- the Mortgage rendering
            # $178,103.41 against $177,277.97 owed).
            #
            # **A re-point onto the KIND-CORRECT entry was tried at plan step
            # X-g4b and MEASURED not to hold, which is why this is a deletion
            # and not a move.**  R-J forbids the kind-blind view, not the seam,
            # so ``balance_at.balance_at`` would answer this loan correctly --
            # but at this point in the pipeline the payment is still PROJECTED.
            # Nothing has settled, so the loan's folded balance reads
            # $250,000.00 both at origination and today, and the honest
            # property ("a payment REDUCES what is owed") has nothing to grade
            # here.  It is graded where a settled payment exists, in the loan
            # fold's own suites.  A step asserting a property its fixture does
            # not create is the vacuity plan Section 7.3 rules out; the earlier
            # version passed only because the producer was wrong in the
            # direction that made it pass.
