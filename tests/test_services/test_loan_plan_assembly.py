"""C6a: loan_plan assembles the right records -- PLANNED shadows, ESTIMATED fill.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  Companion to
``test_loan_plan_forward_oracle.py`` (which pins the fold ARITHMETIC by hand): this
pins the ASSEMBLY -- that :func:`app.services.balance_at._plan.loan_plan` turns a
loan's projected transfer shadows into PLANNED records and fills every FUTURE
contractual slot no record covers with an ESTIMATED one, out to payoff, while never
synthesizing a strictly-PAST installment (the B-9 fix: a missed installment with no
record pays nothing).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import loan_loaders, transfer_service
from app.services.balance_at._plan import fold_forward, loan_plan
from app.services.loan_resolution import contractual_schedule_from_origination
from app.services.resolution_context import BalanceContext
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    loan_income_shadow,
    settle_instant_on,
)

# A short amortizing loan so the whole contractual schedule is enumerable:
# $12,000 at 6% over 6 months, originated 2026-01-01, due on the 1st.
_PRINCIPAL = Decimal("12000.00")
_RATE = Decimal("0.06")
_TERM = 6
_ORIGINATION = date(2026, 1, 1)
# Read the loan mid-life: installments due 02-01, 03-01, 04-01 are PAST; 05-01,
# 06-01, 07-01 are FUTURE.  No recurring payment is configured, so every future
# slot is ESTIMATED and every past one -- unpaid, no record -- pays nothing.
_AS_OF = date(2026, 4, 15)


def _configured_loan(seed_user, db):
    """Create the controlled short loan and return (account, ctx)."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    return account, ctx


def test_a_loan_with_no_recurring_payment_is_all_estimated_future_installments(
    seed_user, db,
):
    """No projected records -> every FUTURE contractual installment is ESTIMATED."""
    account, ctx = _configured_loan(seed_user, db)

    plan = loan_plan(account, ctx)

    # The contractual schedule the ESTIMATED tier draws from, for cross-checking.
    contractual = contractual_schedule_from_origination(
        account.loan_params, loan_loaders.load_rate_changes(account.id),
    )
    future_rows = [row for row in contractual if row.payment_date >= _AS_OF]

    # Every plan entry is ESTIMATED (no records exist), one per future installment.
    assert plan, "a configured loan must project a forward plan"
    assert all(payment.is_estimated for payment in plan)
    assert [p.due_date for p in plan] == [r.payment_date for r in future_rows]
    # The future installments are exactly 05-01, 06-01, 07-01 -- the PAST ones
    # (02-01, 03-01, 04-01) are absent: a missed installment is not synthesized.
    assert [p.due_date for p in plan] == [
        date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1),
    ]
    # The ESTIMATED cash is the contractual P&I, escrow-free; the effective date
    # is the due date (all future, so the as_of + 1d clamp is a no-op).
    for payment, row in zip(plan, future_rows):
        assert payment.cash == row.payment
        assert payment.escrow == Decimal("0.00")
        assert payment.effective_date == payment.due_date


def test_missed_installments_with_no_record_do_not_pay_the_loan_down(seed_user, db):
    """The B-9 fix at the plan level: an unpaid past installment reduces nothing."""
    account, ctx = _configured_loan(seed_user, db)
    plan = loan_plan(account, ctx)

    # Nothing has been paid, so the confirmed present is the full opening balance.
    seed = _PRINCIPAL
    folded = fold_forward(
        seed, _ORIGINATION, plan,
        # as_of, then a date past the missed 04-01 installment but before the
        # first FUTURE one (05-01), then the first real paydown.
        [_AS_OF, date(2026, 4, 30), date(2026, 5, 1)],
    )

    # The three missed installments (02-01, 03-01, 04-01) never happened, so the
    # balance is still the full $12,000 -- not paid down three installments as the
    # old schedule walk did (finding B-9).
    assert folded[_AS_OF] == _PRINCIPAL
    assert folded[date(2026, 4, 30)] == _PRINCIPAL
    # Only the first FUTURE installment (05-01) begins to pay it down.
    assert folded[date(2026, 5, 1)] < _PRINCIPAL
    # Concretely: interest = round(12000 * 0.06 / 12) = 60.00; principal =
    # contractual P&I - 60.00.
    first = plan[0]
    expected = _PRINCIPAL - (first.cash - Decimal("60.00"))
    assert folded[date(2026, 5, 1)] == expected


def _project_loan_payment(seed_user, db, loan, period, amount, due_date):
    """Create a PROJECTED loan-payment transfer and pin its due date."""
    transfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name="Loan Payment",
        ),
    )
    shadow = loan_income_shadow(db.session, transfer.id, loan.id)
    shadow.due_date = due_date
    db.session.commit()
    return shadow


def test_a_projected_record_makes_its_slot_planned_not_estimated(
    seed_user, db, seed_periods,
):
    """A projected shadow becomes PLANNED; its contractual slot is not also ESTIMATED."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    # A projected payment for the 2026-06-01 installment (placed in a period, its
    # due date pinned to the installment it satisfies).
    _project_loan_payment(
        seed_user, db, account, seed_periods[9],
        amount=Decimal("2100.00"), due_date=date(2026, 6, 1),
    )
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

    plan = loan_plan(account, ctx)

    by_due = {payment.due_date: payment for payment in plan}
    # The 2026-06 slot is folded exactly once, as a PLANNED record at its cash --
    # never doubled by an ESTIMATED synthesis (the de-dup).
    assert [p.due_date for p in plan].count(date(2026, 6, 1)) == 1
    june = by_due[date(2026, 6, 1)]
    assert june.is_estimated is False
    assert june.cash == Decimal("2100.00")
    # The other future installments have no record, so they are ESTIMATED.
    assert by_due[date(2026, 5, 1)].is_estimated is True
    assert by_due[date(2026, 7, 1)].is_estimated is True


def test_an_early_settled_payment_is_not_re_synthesized_as_estimated(
    seed_user, db, seed_periods,
):
    """A payment settled by as_of but due after it is in the SEED, not the plan.

    The C3c settled-slot overlap, at the balance: a payment settled on/before
    as_of whose contractual installment is due AT OR AFTER as_of is already paid
    down inside the fold's seed, is not a projected record, and has ``due >=
    as_of`` -- so without a settled-slot exclusion the ESTIMATED tier would
    synthesize it and :func:`fold_forward` would subtract its principal a SECOND
    time.  It must be absent from the plan.
    """
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    # A payment for the 2026-06-01 installment, SETTLED early on 2026-05-20.
    transfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], account,
        seed_periods[9], amount=Decimal("2100.00"),
        paid_at=settle_instant_on(date(2026, 5, 20)),
    )
    shadow = loan_income_shadow(db.session, transfer.id, account.id)
    shadow.due_date = date(2026, 6, 1)
    db.session.commit()

    # Read after the settlement but before the installment's due date.
    ctx = BalanceContext.build(seed_user["user"].id, date(2026, 5, 25))
    plan = loan_plan(account, ctx)

    dues = [payment.due_date for payment in plan]
    # The June installment is in the seed already, so it is NOT re-synthesized.
    assert date(2026, 6, 1) not in dues
    # The genuinely-uncovered July installment still is (ESTIMATED).
    assert date(2026, 7, 1) in dues
    assert all(payment.is_estimated for payment in plan)
