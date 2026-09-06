"""
Shekel Budget App -- Loan payments PREPARED for the amortization engine.

The corrections a recorded payment needs before an engine that thinks in
contractual installments can replay it: the escrow term subtracted so a PITI
payment is not read as principal, and biweekly payments landing twice in one
calendar month redistributed onto distinct months.

Lowest leaf of the package: it imports no sibling, so the two corrections can
be read without the loading or pricing tiers in scope.
"""

import calendar
from datetime import date
from decimal import Decimal

from app.models.loan_params import LoanParams
from app.services import escrow_calculator, loan_resolver
from app.services.amortization_engine import PaymentRecord, RateChangeRecord

def compute_contractual_pi(
    params: LoanParams,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date | None = None,
) -> Decimal:
    """Return the SSOT monthly P&I number for a loan.

    Routes through :func:`loan_resolver.compute_monthly_payment_baseline`
    so the returned value is byte-identical to
    ``LoanState.monthly_payment`` -- the loan card, the schedule's
    projected rows, and the escrow-subtraction threshold in
    :func:`prepare_payments_for_engine` all converge on one number.

    The monthly P&I is the level payment of the rate period containing
    ``as_of``, derived from the loan's rate-change feed (its origination
    :class:`RateHistory` row plus any ARM adjustments).  DH-#56 retired
    the ``LoanParams.interest_rate`` column, so the rate now comes
    exclusively from ``rate_changes``; the prior legacy pure-LoanParams
    fallback (which read the column) is gone.  The value is independent
    of the running balance, so no anchor or payment feed is taken (the
    read-switch arc's final commit dropped the old unused
    compatibility parameters).

    Args:
        params: LoanParams model instance with ``original_principal``,
            ``term_months``, and the ARM cadence fields.
        rate_changes: The loan's rate-change feed (origination row plus
            any ARM adjustments).  Required -- an empty/``None`` feed
            raises in the resolver, because every loan must carry an
            origination :class:`RateHistory` row.
        as_of: Optional evaluation date.  Defaults to ``date.today()``.

    Returns:
        Decimal monthly P&I payment, or ``Decimal("0")`` when
        ``original_principal`` is NULL (defensive: the column is NOT
        NULL, so this is unreachable in practice).

    Raises:
        ValueError: When ``rate_changes`` is empty/``None`` (the
            origination-rate invariant is violated) -- surfaced by
            :func:`loan_resolver._periods._origination_rate`.
    """
    if params.original_principal is None:
        return Decimal("0")
    # The rate comes from ``rate_changes`` (DH-#56 retired
    # ``LoanParams.interest_rate``), so an empty feed raises in the
    # resolver rather than silently defaulting to a wrong payment.
    return loan_resolver.compute_monthly_payment_baseline(
        params, rate_changes, as_of or date.today(),
    )


def _redistribute_to_distinct_months(
    payments: list[PaymentRecord], payment_day: int
) -> list[PaymentRecord]:
    """Shift payments sharing a monthly DUE month to consecutive months.

    Biweekly pay periods sometimes place two mortgage payments in the same
    calendar month; the monthly engine would sum them, double-counting one
    month and leaving the next empty.  At most one extra payment per month
    (~2x/year) is expected, so cascading collisions are not, but the
    while-loop handles them defensively.  The collision key is each payment's
    DUE month (:attr:`PaymentRecord.due_date`, the installment it satisfies),
    NOT its pay-period-start month: two pay periods that both fall before the
    same ``payment_day`` (e.g. Apr 10 and Apr 24, both due May 1) collide on
    the May schedule row, and the schedule/override key everything by due
    month -- a pay-period-start-month key would leave that collision
    unresolved and sum both into a single double payment.

    Only the DUE date shifts.  ``payment_date`` (the pay period funding the
    payment) and ``settled_on`` (the day its cash moved) are FACTS and are
    carried through untouched: the first is the replay's rate lookup, the second
    is its "has this happened?" cap, and an invented date must reach neither.
    Overwriting ``payment_date`` with the shifted due date (the pre-fix
    behaviour) costs a WRONG RATE PERIOD for that payment today -- the reason
    finding **N-36** keeps the rate on a fact rather than on a redistribution's
    output.  It used to cost more: until plan step **X-an** ``payment_date`` was
    also the replay's as-of cap, so a shifted payment whose invented due date
    sorted after ``as_of`` fell out of the replay entirely.  That half is gone
    with the cap; the rate half is not.
    """
    result: list[PaymentRecord] = []
    allocated_months: set[tuple[int, int]] = set()
    for p in payments:
        ym = (p.due_date.year, p.due_date.month)
        if ym not in allocated_months:
            result.append(p)
            allocated_months.add(ym)
        else:
            y, m = ym
            m += 1
            if m > 12:
                m = 1
                y += 1
            while (y, m) in allocated_months:
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            max_day = calendar.monthrange(y, m)[1]
            new_due = date(y, m, min(payment_day, max_day))
            result.append(PaymentRecord(
                payment_date=p.payment_date,
                due_date=new_due,
                settled_on=p.settled_on,
                amount=p.amount,
            ))
            allocated_months.add((y, m))
    return result


def prepare_payments_for_engine(
    payments: list[PaymentRecord],
    payment_day: int,
    escrow_lines: list,
    contractual_pi: Decimal,
) -> list[PaymentRecord]:
    """Prepare payment records for the amortization engine.

    Corrects two mismatches between biweekly shadow transactions and
    the monthly amortization schedule:

    1. Escrow subtraction: Recurring transfers include escrow in their
       total amount, but the engine handles P&I only.  Without this
       correction, the engine treats escrow as extra principal, inflating
       paydown speed and showing escrow as spurious "Extra" entries.
       Only subtracts escrow from the portion that exceeds the standard
       P&I payment, so payments that do not include escrow are unaffected.
       Each payment subtracts the escrow IN EFFECT FOR ITS INSTALLMENT
       (:func:`~app.services.escrow_calculator.escrow_monthly_as_of` on the
       payment's DUE date), NOT one current figure: once escrow can be
       future-dated, a payment made under the old escrow must have the old
       escrow backed out to recover its P&I, or the resolver's replay / payoff
       projection mis-attributes the escrow delta as extra principal.  This is
       the same date-keyed escrow the genesis split subtracts (ruling D5's
       contract time, finding N-34), so the two agree on every payment's P&I.

    2. Biweekly redistribution: Pay period start dates are biweekly and
       sometimes place two mortgage payments in the same calendar month
       (e.g., the Aug 1 payment falls in a Jul 29 pay period).  The
       engine sums same-month payments, double-counting one month and
       leaving the next empty.  This shifts extra same-month payments
       to subsequent months to restore one-payment-per-month alignment.

    Args:
        payments: List of PaymentRecord from get_payment_history().
        payment_day: Mortgage payment day of month (from LoanParams).
        escrow_lines: The loan's escrow lines with their full version history
            (:func:`~app.services.loan_loaders.load_escrow_lines`); each
            payment resolves its own as-of escrow from them.  Empty for a loan
            with no escrow, which skips the subtraction entirely.
        contractual_pi: Standard monthly P&I payment (no escrow).

    Returns:
        Corrected list of PaymentRecord.
    """
    if not payments:
        return payments

    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    # Step 1: Subtract each payment's installment-dated escrow from the portion
    # that exceeds contractual P&I, so payments equal to or below P&I (no escrow
    # included) are untouched.  The DUE date is the key -- the same one the
    # genesis split and the live-cash derivation use (D5 / N-34) -- and it is
    # read BEFORE step 2, so it is the payment's real installment, never a
    # redistribution's invented one.  Skipped entirely for a loan with no escrow
    # lines; a line that resolves to 0 on a given date (not yet in effect, or
    # a removal tombstone) subtracts nothing for that payment.
    if escrow_lines:
        adjusted = []
        for p in sorted_payments:
            escrow = escrow_calculator.escrow_monthly_as_of(
                escrow_lines, p.due_date,
            )
            if escrow > Decimal("0.00") and p.amount > contractual_pi:
                new_amount = p.amount - min(
                    escrow, p.amount - contractual_pi,
                )
            else:
                new_amount = p.amount
            adjusted.append(PaymentRecord(
                payment_date=p.payment_date,
                due_date=p.due_date,
                settled_on=p.settled_on,
                amount=new_amount,
            ))
        sorted_payments = adjusted

    # Step 2: Redistribute payments that share a monthly DUE month to
    # consecutive months so the monthly engine sees one per due month.
    return _redistribute_to_distinct_months(sorted_payments, payment_day)
