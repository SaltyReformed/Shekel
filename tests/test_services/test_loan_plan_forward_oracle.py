"""C6a: the forward fold is graded on hand-computed balances, not a second producer.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  The forward
projection stops walking the resolver's contractual schedule (which pays down one
installment per month whether or not a payment was recorded -- finding B-9) and
folds over payment RECORDS instead.  This file pins the FOLD arithmetic
(:func:`app.services.balance_at._plan_fold.fold_forward`) against balances computed BY
HAND -- never against the schedule walk it replaces (that would prove B-9), and
never against a second producer that shares its code (plan Section 7.2).

Each balance below is the fold of ``interest = round_money(balance * rate / 12)``
then ``principal = cash - interest - escrow`` (capped at the balance, the surplus
a refund), applied in due order from the seed.  A payment becomes VISIBLE on its
effective date, so an overdue-but-clamped record pays down only from tomorrow, and
an installment with NO record does not pay the loan down at all -- the B-9 fix,
proven here at the arithmetic.

The integration side -- that :func:`app.services.balance_at._plan.loan_plan`
ASSEMBLES the right records (projected shadows -> PLANNED, gaps -> ESTIMATED, one
slot folded once) -- is pinned in ``test_loan_plan_assembly.py``.
"""

from datetime import date
from decimal import Decimal

from app.services.balance_at._plan import (
    AccrualCharge,
    LoanForwardPlan,
    PlannedPayment,
)
from app.services.balance_at._plan_fold import (
    fold_forward,
    plan_interest_in_year,
)

_RATE = Decimal("0.06")           # 6% annual -> 0.5% monthly
_ORIGINATION = date(2025, 1, 1)


def _payment(
    due: date,
    cash: str,
    *,
    effective: date | None = None,
    estimated: bool = False,
) -> PlannedPayment:
    """Build one :class:`PlannedPayment`; ``effective`` defaults to ``due``."""
    return PlannedPayment(
        due_date=due,
        effective_date=effective if effective is not None else due,
        cash=Decimal(cash),
        is_estimated=estimated,
    )


def _plan(
    payments: list[PlannedPayment],
    *,
    escrow: str = "0.00",
    rate: Decimal = _RATE,
) -> LoanForwardPlan:
    """Bundle *payments* with one CHARGE per accrual period they occupy.

    Stated by hand rather than taken from ``_plan._charges_for``, so the fold is
    graded against arithmetic rather than against the derivation that feeds it.
    Every plan below puts one payment in each month, so each charge lands on that
    payment's own due date and the hand-computed balances are unchanged from
    before plan step R16-a lifted the accrual off the record.
    """
    opens: dict[tuple[int, int], date] = {}
    for payment in payments:
        slot = (payment.due_date.year, payment.due_date.month)
        if payment.due_date < opens.get(slot, date.max):
            opens[slot] = payment.due_date
    return LoanForwardPlan(
        payments=list(payments),
        charges=[
            AccrualCharge(
                on_date=on_date, annual_rate=rate, escrow=Decimal(escrow),
            )
            for on_date in sorted(opens.values())
        ],
    )


def test_a_planned_payment_pays_its_principal_down():
    """One $1,000 payment on a $100,000 balance at 6%: $500 interest, $500 principal."""
    seed = Decimal("100000.00")
    plan = _plan([_payment(date(2026, 2, 1), "1000.00")])

    result = fold_forward(
        seed, _ORIGINATION, plan,
        [date(2026, 1, 31), date(2026, 2, 1), date(2026, 3, 1)],
    )

    # Interest = round(100000 * 0.06 / 12) = 500.00; principal = 1000 - 500 = 500.
    assert result[date(2026, 1, 31)] == Decimal("100000.00")  # before it is visible
    assert result[date(2026, 2, 1)] == Decimal("99500.00")
    assert result[date(2026, 3, 1)] == Decimal("99500.00")    # no further payment


def test_interest_accrues_on_the_running_balance_across_payments():
    """Two payments compound: the second accrues on the paid-down balance."""
    seed = Decimal("100000.00")
    plan = _plan([
        _payment(date(2026, 2, 1), "1000.00"),
        _payment(date(2026, 3, 1), "1000.00"),
    ])

    result = fold_forward(seed, _ORIGINATION, plan, [date(2026, 3, 1)])

    # P1: interest 500.00, principal 500.00 -> 99,500.00.
    # P2: interest round(99500 * 0.005) = 497.50, principal 502.50 -> 98,997.50.
    assert result[date(2026, 3, 1)] == Decimal("98997.50")


def test_an_empty_plan_holds_the_seed_flat():
    """No payment records -> no paydown: the B-9 fix (a missed installment pays nothing)."""
    seed = Decimal("177277.97")

    result = fold_forward(
        seed, _ORIGINATION, _plan([]),
        [date(2026, 3, 1), date(2027, 1, 1), date(2030, 6, 1)],
    )

    assert result[date(2026, 3, 1)] == seed
    assert result[date(2027, 1, 1)] == seed
    assert result[date(2030, 6, 1)] == seed


def test_an_overdue_records_clamp_defers_its_paydown_to_the_effective_date():
    """An overdue but still-projected record pays down from its clamped date, not its due date."""
    seed = Decimal("100000.00")
    # Due 2026-01-15 (already past on an as_of of 2026-01-20), so loan_plan would
    # clamp its effective date to as_of + 1d = 2026-01-21.
    plan = _plan([_payment(date(2026, 1, 15), "1000.00", effective=date(2026, 1, 21))])

    result = fold_forward(
        seed, _ORIGINATION, plan,
        [date(2026, 1, 15), date(2026, 1, 20), date(2026, 1, 21)],
    )

    # The paydown does not show on the due date, nor on as_of -- only from the
    # clamped effective date (a plan cannot have already happened, D1).
    assert result[date(2026, 1, 15)] == Decimal("100000.00")
    assert result[date(2026, 1, 20)] == Decimal("100000.00")
    assert result[date(2026, 1, 21)] == Decimal("99500.00")


def test_a_payoff_overpayment_reaches_zero_and_stays():
    """Cash beyond the balance closes the loan; further payments add nothing."""
    seed = Decimal("1000.00")
    plan = _plan([
        _payment(date(2026, 2, 1), "1200.00"),   # pays off (interest 5.00, principal caps at 1000)
        _payment(date(2026, 3, 1), "1200.00"),   # post-payoff: pure refund, no paydown
    ])

    result = fold_forward(
        seed, _ORIGINATION, plan,
        [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
    )

    assert result[date(2026, 1, 1)] == Decimal("1000.00")
    assert result[date(2026, 2, 1)] == Decimal("0.00")
    assert result[date(2026, 3, 1)] == Decimal("0.00")


def test_extra_cash_pays_the_loan_down_faster():
    """A larger payment (P&I + extra_principal) reduces the balance more (D3)."""
    seed = Decimal("100000.00")
    base = fold_forward(
        seed, _ORIGINATION, _plan([_payment(date(2026, 2, 1), "1000.00")]),
        [date(2026, 2, 1)],
    )
    with_extra = fold_forward(
        seed, _ORIGINATION, _plan([_payment(date(2026, 2, 1), "1500.00")]),
        [date(2026, 2, 1)],
    )

    # Same $500 interest; the extra $500 lands entirely in principal.
    assert base[date(2026, 2, 1)] == Decimal("99500.00")
    assert with_extra[date(2026, 2, 1)] == Decimal("99000.00")


def test_a_date_before_origination_owes_nothing():
    """A loan owes 0.00 before it exists, even seeded with its opening balance."""
    seed = Decimal("200000.00")               # the balance it will OPEN at
    owed_from = date(2026, 4, 1)
    plan = _plan([_payment(date(2026, 5, 1), "1500.00")])

    result = fold_forward(
        seed, owed_from, plan,
        [date(2026, 3, 15), date(2026, 4, 1), date(2026, 5, 1)],
    )

    assert result[date(2026, 3, 15)] == Decimal("0.00")     # before origination
    assert result[date(2026, 4, 1)] == Decimal("200000.00")  # opens at the seed
    # Interest on 200000 at 6% = 1000.00; principal 1500 - 1000 = 500.
    assert result[date(2026, 5, 1)] == Decimal("199500.00")


def test_dates_are_valued_independently_and_duplicates_collapse():
    """The fold answers every requested date off one walk; a duplicate costs nothing."""
    seed = Decimal("100000.00")
    plan = _plan([_payment(date(2026, 2, 1), "1000.00")])

    result = fold_forward(
        seed, _ORIGINATION, plan,
        [date(2026, 3, 1), date(2026, 1, 1), date(2026, 3, 1)],
    )

    assert result == {
        date(2026, 1, 1): Decimal("100000.00"),
        date(2026, 3, 1): Decimal("99500.00"),
    }


# ── plan_interest_in_year: the projected half of the Schedule-A figure ──────
#
# The interest sibling of ``fold_forward``: it folds the SAME plan over the SAME
# running balance (``_split_plan``), summing each payment's accrued interest by the
# year the payment is projected to be PAID -- its EFFECTIVE date (step C6c).  These
# pin that arithmetic BY HAND, so the tax figure's projected term is graded against
# hand math, never the producer as its own oracle (plan N-7).


def test_plan_interest_sums_each_payments_accrued_interest():
    """Two 2026 payments on a $100,000 balance: 500.00 + 497.50 = 997.50."""
    seed = Decimal("100000.00")
    plan = _plan([
        _payment(date(2026, 2, 1), "1000.00"),
        _payment(date(2026, 3, 1), "1000.00"),
    ])

    # P1 interest round(100000 * 0.005) = 500.00 (balance -> 99,500.00);
    # P2 interest round(99500 * 0.005) = 497.50 (on the paid-down balance).
    assert plan_interest_in_year(seed, plan, 2026) == Decimal("997.50")


def test_plan_interest_is_keyed_by_the_effective_year():
    """A payment's interest lands in its EFFECTIVE year, split across a boundary."""
    seed = Decimal("100000.00")
    plan = _plan([
        _payment(date(2026, 12, 1), "1000.00"),
        _payment(date(2027, 1, 1), "1000.00"),
    ])

    # Folded in due order: P1 (Dec 2026) interest 500.00 -> 99,500.00; P2 (Jan
    # 2027) interest round(99500 * 0.005) = 497.50 on the paid-down balance.
    assert plan_interest_in_year(seed, plan, 2026) == Decimal("500.00")
    assert plan_interest_in_year(seed, plan, 2027) == Decimal("497.50")


def test_plan_interest_of_an_overdue_clamp_lands_in_the_effective_year():
    """An overdue record's interest deducts in the year it is projected to CLEAR.

    A payment due 2025-12-15 that has not settled is clamped forward to
    ``as_of + 1d`` (here 2026-01-05, ruling D1), so its interest -- the interest a
    borrower is projected to pay when they finally make the payment -- belongs to
    the year of that EFFECTIVE date (2026), NOT the closed year it was contractually
    due (2025).  This is the fork settled for C6c: attribute by expected-paid date,
    so an unpaid installment never books a deduction into an already-filed year.
    """
    seed = Decimal("100000.00")
    plan = _plan([_payment(
        date(2025, 12, 15), "1000.00", effective=date(2026, 1, 5),
    )])

    # Interest round(100000 * 0.005) = 500.00, attributed to the effective year.
    assert plan_interest_in_year(seed, plan, 2025) == Decimal("0.00")
    assert plan_interest_in_year(seed, plan, 2026) == Decimal("500.00")


def test_an_empty_plan_has_no_projected_interest():
    """No payment records -> no projected interest in any year (the B-9 fix)."""
    seed = Decimal("177277.97")

    assert plan_interest_in_year(seed, _plan([]), 2026) == Decimal("0.00")
    assert plan_interest_in_year(seed, _plan([]), 2030) == Decimal("0.00")
