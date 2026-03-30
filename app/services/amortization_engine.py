"""
Shekel Budget App -- Amortization Engine

Pure-function service for loan amortization calculations.
Generates amortization schedules, summary metrics, and payoff analysis.
No database access -- operates only on values passed in.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


def calculate_remaining_months(
    origination_date: date, term_months: int, as_of: date | None = None,
) -> int:
    """Return how many payment months remain on a loan.

    Calculates months elapsed from *origination_date* to *as_of* (default
    today) and subtracts from *term_months*.  Never returns below 0.
    """
    if as_of is None:
        as_of = date.today()
    months_elapsed = (
        (as_of.year - origination_date.year) * 12
        + (as_of.month - origination_date.month)
    )
    return max(0, term_months - months_elapsed)


@dataclass
class AmortizationRow:
    """A single month in an amortization schedule."""
    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal


@dataclass
class AmortizationSummary:
    """High-level metrics for a loan."""
    monthly_payment: Decimal
    total_interest: Decimal
    payoff_date: date
    total_interest_with_extra: Decimal
    payoff_date_with_extra: date
    months_saved: int
    interest_saved: Decimal


def calculate_monthly_payment(
    principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
) -> Decimal:
    """Standard amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1].

    Returns $0 if principal <= 0 or remaining_months <= 0.
    For zero-rate loans, returns principal / remaining_months.
    """
    if principal <= 0 or remaining_months <= 0:
        return Decimal("0.00")

    if annual_rate <= 0:
        return (principal / remaining_months).quantize(TWO_PLACES, ROUND_HALF_UP)

    monthly_rate = annual_rate / 12
    factor = (1 + monthly_rate) ** remaining_months
    payment = principal * (monthly_rate * factor) / (factor - 1)
    return payment.quantize(TWO_PLACES, ROUND_HALF_UP)


def _advance_month(year: int, month: int, day: int) -> date:
    """Move forward one month, clamping the day to the month's max days."""
    month += 1
    if month > 12:
        month = 1
        year += 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, max_day))


def generate_schedule(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    extra_monthly: Decimal = Decimal("0.00"),
    origination_date: date | None = None,
    payment_day: int = 1,
    original_principal: Decimal | None = None,
    term_months: int | None = None,
) -> list[AmortizationRow]:
    """Generate month-by-month amortization schedule.

    Args:
        current_principal: Current outstanding balance.
        annual_rate: Annual interest rate as a decimal (e.g. 0.065 for 6.5%).
        remaining_months: Number of months remaining on the loan.
        extra_monthly: Additional principal payment per month.
        origination_date: Loan origination date (used for payment date calc).
        payment_day: Day of month payments are due.
        original_principal: Original loan amount at origination.  When
            provided with *term_months*, the contractual monthly payment
            is computed from the original terms instead of re-amortizing
            from current_principal.
        term_months: Original loan term in months.  Used with
            *original_principal* to derive the contractual payment.

    Returns:
        List of AmortizationRow objects.
    """
    if current_principal <= 0 or remaining_months <= 0:
        return []

    # Compute the monthly payment.  When original loan terms are provided,
    # use them for the contractual payment (what the borrower actually pays).
    # Otherwise re-amortize from current_principal (backward compat).
    using_contractual = (
        original_principal is not None and term_months is not None
    )
    if using_contractual:
        monthly_payment = calculate_monthly_payment(
            original_principal, annual_rate, term_months,
        )
    else:
        monthly_payment = calculate_monthly_payment(
            current_principal, annual_rate, remaining_months,
        )
    monthly_rate = annual_rate / 12 if annual_rate > 0 else Decimal("0")

    # When using the contractual payment, the number of months needed to
    # pay off current_principal may differ from remaining_months (e.g. a
    # partially paid loan where current < original needs fewer months,
    # while remaining_months is based on the calendar).  Use a generous
    # upper bound and let the balance-reaches-zero break handle
    # termination.  For re-amortized schedules, remaining_months is exact.
    if using_contractual:
        max_months = remaining_months + term_months
    else:
        max_months = remaining_months

    # Determine starting payment date.
    if origination_date:
        pay_year = origination_date.year
        pay_month = origination_date.month + 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1
    else:
        today = date.today()
        pay_year = today.year
        pay_month = today.month

    # Defensive: ensure Decimal even if caller passes float from DB column.
    balance = Decimal(str(current_principal))
    rows = []

    for month_num in range(1, max_months + 1):
        if balance <= 0:
            break

        # Calculate payment date.
        max_day = calendar.monthrange(pay_year, pay_month)[1]
        pay_date = date(pay_year, pay_month, min(payment_day, max_day))

        # Calculate interest for this month.
        interest = (balance * monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)

        # Calculate principal portion.
        principal_portion = monthly_payment - interest

        # Cap payment at remaining balance + interest.
        # When principal would exceed balance or we've hit the loop cap,
        # absorb the remaining balance exactly to avoid rounding residue.
        is_final = (principal_portion >= balance) or (month_num == max_months)
        if is_final:
            principal_portion = balance
            actual_payment = principal_portion + interest
            extra = Decimal("0.00")
            balance = Decimal("0.00")
        else:
            actual_payment = monthly_payment

            # Apply extra payment.
            extra = min(extra_monthly, balance - principal_portion)
            extra = max(extra, Decimal("0.00"))

            balance -= principal_portion + extra
            balance = balance.quantize(TWO_PLACES, ROUND_HALF_UP)

            # Guard against negative balance from rounding.
            if balance < 0:
                extra += balance
                balance = Decimal("0.00")

        rows.append(AmortizationRow(
            month=month_num,
            payment_date=pay_date,
            payment=actual_payment.quantize(TWO_PLACES, ROUND_HALF_UP),
            principal=principal_portion.quantize(TWO_PLACES, ROUND_HALF_UP),
            interest=interest,
            extra_payment=extra.quantize(TWO_PLACES, ROUND_HALF_UP),
            remaining_balance=balance,
        ))

        if balance <= 0:
            break

        # Advance to next month.
        pay_month += 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1

    return rows


def calculate_summary(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    origination_date: date,
    payment_day: int,
    term_months: int,
    extra_monthly: Decimal = Decimal("0.00"),
    original_principal: Decimal | None = None,
) -> AmortizationSummary:
    """Compute summary metrics: payoff date, interest saved, etc.

    Args:
        original_principal: Original loan amount at origination.  When
            provided, the contractual monthly payment is computed from
            (original_principal, annual_rate, term_months) instead of
            re-amortizing from current_principal.
    """
    if original_principal is not None:
        monthly_payment = calculate_monthly_payment(
            original_principal, annual_rate, term_months,
        )
    else:
        monthly_payment = calculate_monthly_payment(
            current_principal, annual_rate, remaining_months,
        )

    # Standard schedule (no extra payments).
    standard = generate_schedule(
        current_principal, annual_rate, remaining_months,
        extra_monthly=Decimal("0.00"),
        origination_date=origination_date,
        payment_day=payment_day,
        original_principal=original_principal,
        term_months=term_months,
    )

    total_interest_standard = sum((r.interest for r in standard), Decimal("0.00"))
    payoff_date_standard = standard[-1].payment_date if standard else origination_date

    # Accelerated schedule (with extra payments).
    if extra_monthly > 0:
        accelerated = generate_schedule(
            current_principal, annual_rate, remaining_months,
            extra_monthly=extra_monthly,
            origination_date=origination_date,
            payment_day=payment_day,
            original_principal=original_principal,
            term_months=term_months,
        )
        total_interest_extra = sum((r.interest for r in accelerated), Decimal("0.00"))
        payoff_date_extra = accelerated[-1].payment_date if accelerated else origination_date
    else:
        total_interest_extra = total_interest_standard
        payoff_date_extra = payoff_date_standard
        accelerated = standard

    months_saved = len(standard) - len(accelerated)
    interest_saved = total_interest_standard - total_interest_extra

    return AmortizationSummary(
        monthly_payment=monthly_payment,
        total_interest=total_interest_standard,
        payoff_date=payoff_date_standard,
        total_interest_with_extra=total_interest_extra,
        payoff_date_with_extra=payoff_date_extra,
        months_saved=months_saved,
        interest_saved=interest_saved.quantize(TWO_PLACES, ROUND_HALF_UP),
    )


def calculate_payoff_by_date(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    target_date: date,
    origination_date: date,
    payment_day: int,
    original_principal: Decimal | None = None,
    term_months: int | None = None,
) -> Decimal | None:
    """Calculate required extra monthly payment to pay off by target_date.

    Args:
        original_principal: Original loan amount for contractual payment.
        term_months: Original loan term for contractual payment.

    Returns None if target_date is not achievable (already past or too soon).
    Returns Decimal("0.00") if standard payoff is already before target.
    """
    if current_principal <= 0 or remaining_months <= 0:
        return Decimal("0.00")

    # Determine the standard payoff date.
    standard = generate_schedule(
        current_principal, annual_rate, remaining_months,
        origination_date=origination_date,
        payment_day=payment_day,
        original_principal=original_principal,
        term_months=term_months,
    )
    if not standard:
        return Decimal("0.00")

    standard_payoff = standard[-1].payment_date
    if standard_payoff <= target_date:
        return Decimal("0.00")

    # Calculate how many months until target_date.
    if origination_date:
        start_year = origination_date.year
        start_month = origination_date.month + 1
        if start_month > 12:
            start_month = 1
            start_year += 1
    else:
        today = date.today()
        start_year = today.year
        start_month = today.month

    target_months = (
        (target_date.year - start_year) * 12
        + (target_date.month - start_month)
        + 1  # inclusive
    )

    if target_months <= 0:
        return None  # Target date is in the past.

    if target_months >= remaining_months:
        return Decimal("0.00")

    # Binary search for the required extra payment.
    lo = Decimal("0.01")
    hi = current_principal  # Upper bound: pay it all off immediately.

    for _ in range(100):  # Max iterations for convergence.
        mid = ((lo + hi) / 2).quantize(TWO_PLACES, ROUND_HALF_UP)
        schedule = generate_schedule(
            current_principal, annual_rate, remaining_months,
            extra_monthly=mid,
            origination_date=origination_date,
            payment_day=payment_day,
            original_principal=original_principal,
            term_months=term_months,
        )
        if not schedule:
            return mid

        actual_payoff = schedule[-1].payment_date
        if actual_payoff <= target_date:
            hi = mid
        else:
            lo = mid

        if hi - lo <= Decimal("0.01"):
            break

    return hi


@dataclass
class LoanProjection:
    """Bundled projection output for a loan: summary, schedule, and chart data."""
    remaining_months: int
    summary: AmortizationSummary
    schedule: list  # list[AmortizationRow]


def get_loan_projection(params, schedule_start=None):
    """Compute remaining months, summary, and schedule for a loan in one call.

    The contractual monthly payment is computed from ``original_principal``
    and ``term_months`` (what the borrower actually pays each month), then
    applied against ``current_principal`` over ``remaining_months`` to
    project the payoff trajectory.

    Args:
        params: An object with ``origination_date``, ``term_months``,
                ``original_principal``, ``current_principal``,
                ``interest_rate``, and ``payment_day`` attributes
                (e.g. a LoanParams model instance).
        schedule_start: Date to use as schedule start.  Defaults to the
                        first day of the current month.

    Returns:
        LoanProjection with remaining_months, summary, and schedule.
    """
    if schedule_start is None:
        schedule_start = date.today().replace(day=1)

    remaining = calculate_remaining_months(
        params.origination_date, params.term_months,
    )

    principal = Decimal(str(params.current_principal))
    original = Decimal(str(params.original_principal))
    rate = Decimal(str(params.interest_rate))

    summary = calculate_summary(
        current_principal=principal,
        annual_rate=rate,
        remaining_months=remaining,
        origination_date=schedule_start,
        payment_day=params.payment_day,
        term_months=params.term_months,
        original_principal=original,
    )

    schedule = generate_schedule(
        principal, rate, remaining,
        payment_day=params.payment_day,
        original_principal=original,
        term_months=params.term_months,
    )

    return LoanProjection(
        remaining_months=remaining,
        summary=summary,
        schedule=schedule,
    )
