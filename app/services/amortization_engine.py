"""
Shekel Budget App -- Amortization Engine

Pure-function service for loan amortization calculations.
Generates amortization schedules, summary metrics, and payoff analysis.
No database access -- operates only on values passed in.

Supports payment-aware projections: when a list of PaymentRecord
instances is provided, the schedule replays actual/committed payments
month-by-month instead of assuming the contractual amount.  This
enables three projection scenarios from the same engine:

  1. Original schedule -- payments=None, extra_monthly=0
  2. Committed schedule -- payments=confirmed+projected transfers
  3. What-if schedule -- payments=confirmed, extra_monthly=user input
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class PaymentRecord:
    """A single payment applied to a loan.

    Used to replay actual or committed payments through the amortization
    schedule so projections reflect real payment history rather than
    assuming the contractual amount every month.

    Attributes:
        payment_date: The date the payment was made or is projected.
            Matched to the schedule by year-month, not exact day, so
            biweekly payment dates (e.g. 2026-03-06) correctly map to
            the monthly schedule period (2026-03).
        amount: The total payment amount (principal + interest).  Must
            be >= 0.  A zero amount represents a missed payment where
            only interest accrues (negative amortization).
        is_confirmed: True if the payment is Paid/Settled (historical
            fact).  False if Projected (future commitment).
    """

    payment_date: date
    amount: Decimal
    is_confirmed: bool

    def __post_init__(self):
        """Validate payment record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the schedule loop.

        Raises:
            TypeError: If payment_date is not a date, amount is not a
                Decimal, or is_confirmed is not a bool.
            ValueError: If amount is negative.
        """
        if not isinstance(self.payment_date, date):
            raise TypeError(
                f"payment_date must be a date, got {type(self.payment_date).__name__}"
            )
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"amount must be a Decimal, got {type(self.amount).__name__}"
            )
        if self.amount < 0:
            raise ValueError(
                f"amount must be >= 0, got {self.amount}"
            )
        if not isinstance(self.is_confirmed, bool):
            raise TypeError(
                f"is_confirmed must be a bool, got {type(self.is_confirmed).__name__}"
            )


@dataclass(frozen=True)
class RateChangeRecord:
    """A historical or scheduled rate change applied to an ARM loan.

    Used to replay known rate adjustments through the amortization
    schedule so projections reflect the loan's actual rate history
    rather than assuming a fixed rate for the entire term.

    Attributes:
        effective_date: The date the new rate takes effect.  Matched
            to schedule months by finding the most recent entry with
            effective_date <= payment_date.
        interest_rate: The new annual interest rate as a Decimal
            (e.g., Decimal("0.065") for 6.5%).  Must be >= 0.
    """

    effective_date: date
    interest_rate: Decimal

    def __post_init__(self):
        """Validate rate change record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the schedule loop.

        Raises:
            TypeError: If effective_date is not a date or interest_rate
                is not a Decimal.
            ValueError: If interest_rate is negative.
        """
        if not isinstance(self.effective_date, date):
            raise TypeError(
                f"effective_date must be a date, "
                f"got {type(self.effective_date).__name__}"
            )
        if not isinstance(self.interest_rate, Decimal):
            raise TypeError(
                f"interest_rate must be a Decimal, "
                f"got {type(self.interest_rate).__name__}"
            )
        if self.interest_rate < 0:
            raise ValueError(
                f"interest_rate must be >= 0, got {self.interest_rate}"
            )


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
    """A single month in an amortization schedule.

    The is_confirmed flag distinguishes historical fact from projection:
    True when the row's payment came from a confirmed PaymentRecord
    (Paid/Settled status), False when projected or computed from the
    contractual payment formula.
    """

    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal
    is_confirmed: bool = False
    interest_rate: Decimal | None = None


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


def _build_payment_lookups(
    payments: list[PaymentRecord],
    origination_date: date | None,
) -> tuple[dict[tuple[int, int], Decimal], dict[tuple[int, int], bool]]:
    """Build year-month lookup dicts from a list of PaymentRecord instances.

    Sums multiple payments in the same month.  For the is_confirmed flag,
    a month is considered confirmed only when ALL payments in that month
    are confirmed -- a mix of confirmed and projected means the month's
    total is not fully confirmed.

    Payments dated before origination_date are silently filtered (they
    may exist as data artifacts from before the loan started).

    Args:
        payments: Non-empty list of PaymentRecord instances.
        origination_date: Loan origination date.  Payments before this
            date are excluded.  If None, no filtering is applied.

    Returns:
        (amount_by_month, confirmed_by_month) where:
            amount_by_month: dict mapping (year, month) -> total Decimal
            confirmed_by_month: dict mapping (year, month) -> bool
    """
    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    amount_by_month: dict[tuple[int, int], Decimal] = {}
    confirmed_by_month: dict[tuple[int, int], bool] = {}

    for payment in sorted_payments:
        # Filter pre-origination payments.
        if origination_date is not None and payment.payment_date < origination_date:
            continue

        key = (payment.payment_date.year, payment.payment_date.month)
        amount_by_month[key] = amount_by_month.get(key, Decimal("0")) + payment.amount
        # A month is confirmed only if ALL its payments are confirmed.
        if key in confirmed_by_month:
            confirmed_by_month[key] = confirmed_by_month[key] and payment.is_confirmed
        else:
            confirmed_by_month[key] = payment.is_confirmed

    return amount_by_month, confirmed_by_month


def _build_rate_change_list(
    rate_changes: list[RateChangeRecord],
    origination_date: date | None,
) -> list[tuple[date, Decimal]]:
    """Build a sorted, deduplicated list of (effective_date, rate) tuples.

    Pre-origination entries are filtered.  When multiple entries share
    the same effective_date, the last one (after stable sort) wins --
    this is deterministic but likely a data entry error, so a warning
    is logged.

    Args:
        rate_changes: Non-empty list of RateChangeRecord instances.
        origination_date: Loan origination date.  Entries before this
            date are excluded.  If None, no filtering is applied.

    Returns:
        List of (effective_date, interest_rate) sorted by effective_date.
    """
    sorted_changes = sorted(rate_changes, key=lambda r: r.effective_date)

    # Filter pre-origination entries.
    if origination_date is not None:
        sorted_changes = [
            r for r in sorted_changes
            if r.effective_date >= origination_date
        ]

    # Deduplicate by effective_date (last entry wins).
    seen_dates: dict[date, Decimal] = {}
    for record in sorted_changes:
        if record.effective_date in seen_dates:
            logger.warning(
                "Multiple rate changes on %s: overriding %.5f with %.5f",
                record.effective_date,
                seen_dates[record.effective_date],
                record.interest_rate,
            )
        seen_dates[record.effective_date] = record.interest_rate

    return sorted(seen_dates.items(), key=lambda x: x[0])


def _find_applicable_rate(
    payment_date: date,
    rate_schedule: list[tuple[date, Decimal]],
    base_rate: Decimal,
) -> Decimal:
    """Find the applicable annual rate for a given payment date.

    Scans the rate_schedule for the most recent entry with
    effective_date <= payment_date.  Falls back to base_rate if
    no entry qualifies.

    Args:
        payment_date: The payment date for the current schedule month.
        rate_schedule: Sorted list of (effective_date, rate) tuples.
        base_rate: The loan's original annual rate (fallback).

    Returns:
        The applicable annual interest rate as a Decimal.
    """
    applicable_rate = base_rate
    for effective_date, rate in rate_schedule:
        if effective_date <= payment_date:
            applicable_rate = rate
        else:
            break
    return applicable_rate


def generate_schedule(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    extra_monthly: Decimal = Decimal("0.00"),
    origination_date: date | None = None,
    payment_day: int = 1,
    original_principal: Decimal | None = None,
    term_months: int | None = None,
    payments: list[PaymentRecord] | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
    anchor_balance: Decimal | None = None,
    anchor_date: date | None = None,
) -> list[AmortizationRow]:
    """Generate month-by-month amortization schedule.

    When *payments* is provided, the schedule replays those payments
    month-by-month instead of assuming the contractual amount.  Months
    without a matching payment use the standard contractual payment
    plus *extra_monthly*.  This enables three projection scenarios:

      1. Original schedule -- payments=None, extra_monthly=0
      2. Committed schedule -- payments=confirmed+projected transfers
      3. What-if schedule -- payments=confirmed only, extra_monthly=X

    Payment matching is by year-month of ``payment_date``, not exact
    day, so biweekly payment dates correctly map to monthly periods.

    When *rate_changes* is provided, the schedule re-amortizes the
    remaining balance at each rate adjustment.  The applicable rate for
    each month is the most recent entry with effective_date <= the
    month's payment_date.  The monthly payment is recalculated using
    the standard amortization formula: M = P * [r(1+r)^n] / [(1+r)^n-1]
    where P = remaining balance, r = new monthly rate, n = remaining
    months.  Both payments and rate_changes can be used together.

    When *anchor_balance* and *anchor_date* are provided, the schedule
    resets the running balance to anchor_balance at the first month
    whose payment date is after anchor_date, then re-amortizes from
    that point forward.  This is used for ARM loans where the
    origination-forward calculation drifts from reality due to missing
    historical rate data.  Pre-anchor rows have approximate P&I splits
    (payment amounts from confirmed records are real, but the
    interest/principal breakdown uses the current rate rather than the
    historical rate).  Post-anchor rows are mathematically exact.

    Args:
        current_principal: Current outstanding balance.
        annual_rate: Annual interest rate as a decimal (e.g. 0.065 for 6.5%).
        remaining_months: Number of months remaining on the loan.
        extra_monthly: Additional principal payment per month.  Applied
            only to months where no PaymentRecord exists.
        origination_date: Loan origination date (used for payment date calc).
        payment_day: Day of month payments are due.
        original_principal: Original loan amount at origination.  When
            provided with *term_months*, the contractual monthly payment
            is computed from the original terms instead of re-amortizing
            from current_principal.
        term_months: Original loan term in months.  Used with
            *original_principal* to derive the contractual payment.
        payments: Optional list of PaymentRecord instances.  When
            provided, each month checks for a matching payment (by
            year-month) and uses that amount instead of the contractual
            payment.
        rate_changes: Optional list of RateChangeRecord instances for
            ARM loans.  When provided, the schedule applies rate changes
            at their effective dates and re-amortizes the remaining
            balance at the new rate.  Pre-origination entries are
            filtered.  None or [] leaves behavior unchanged.
        anchor_balance: Optional verified balance to snap to at
            anchor_date.  Used with anchor_date for ARM loans where
            historical rate data is unavailable.
        anchor_date: The date at which anchor_balance is known.  The
            schedule resets balance at the first month after this date.

    Returns:
        List of AmortizationRow objects.
    """
    if current_principal <= 0 or remaining_months <= 0:
        return []

    # Build payment lookup dicts if payments are provided.
    has_payments = payments is not None and len(payments) > 0
    if has_payments:
        amount_by_month, confirmed_by_month = _build_payment_lookups(
            payments, origination_date,
        )
    else:
        amount_by_month = {}
        confirmed_by_month = {}

    # Build rate change schedule if rate_changes are provided.
    has_rate_changes = rate_changes is not None and len(rate_changes) > 0
    if has_rate_changes:
        rate_schedule = _build_rate_change_list(rate_changes, origination_date)
    else:
        rate_schedule = []

    # Compute the monthly payment.  For fixed-rate loans with original
    # terms provided, use the contractual payment (what the borrower
    # actually pays -- same for the life of the loan).  For ARM loans
    # (indicated by non-empty rate_changes), the "contractual" payment
    # changes at each rate adjustment, so re-amortize from
    # current_principal and remaining_months at the current rate.
    using_contractual = (
        original_principal is not None
        and term_months is not None
        and not has_rate_changes
    )
    if using_contractual:
        monthly_payment = calculate_monthly_payment(
            original_principal, annual_rate, term_months,
        )
    else:
        monthly_payment = calculate_monthly_payment(
            current_principal, annual_rate, remaining_months,
        )
    current_annual_rate = annual_rate
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
    anchor_applied = False

    for month_num in range(1, max_months + 1):
        if balance <= 0:
            break

        # Calculate payment date.
        max_day = calendar.monthrange(pay_year, pay_month)[1]
        pay_date = date(pay_year, pay_month, min(payment_day, max_day))

        # Anchor reset: when a verified balance is provided, snap the
        # schedule to the anchor at the first month after anchor_date.
        # Pre-anchor rows have approximate P&I splits; post-anchor rows
        # project from the user-verified balance and are exact.
        if (anchor_balance is not None and anchor_date is not None
                and not anchor_applied and pay_date > anchor_date):
            balance = anchor_balance
            anchor_applied = True
            months_left = max_months - month_num + 1
            monthly_payment = calculate_monthly_payment(
                balance, current_annual_rate, months_left,
            )

        # ARM rate adjustment: check if the rate changes this month.
        # When the rate differs from the previous period, re-amortize
        # the remaining balance over the remaining months at the new rate.
        if rate_schedule:
            period_rate = _find_applicable_rate(
                pay_date, rate_schedule, annual_rate,
            )
            if period_rate != current_annual_rate:
                current_annual_rate = period_rate
                monthly_rate = (
                    current_annual_rate / 12
                    if current_annual_rate > 0
                    else Decimal("0")
                )
                # Re-amortize: new payment for remaining balance over
                # remaining months at the new rate.
                months_left = max_months - month_num + 1
                monthly_payment = calculate_monthly_payment(
                    balance, current_annual_rate, months_left,
                )

        # Calculate interest for this month.
        interest = (balance * monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)

        # Check for a payment record matching this month.
        month_key = (pay_year, pay_month)
        has_payment_record = month_key in amount_by_month

        if has_payment_record:
            # Payment record exists: use the recorded amount as the
            # total payment for this month.  extra_monthly is NOT added
            # on top -- the payment record IS the total payment.
            total_payment = amount_by_month[month_key]
            row_confirmed = confirmed_by_month[month_key]

            # Split into interest and principal.
            principal_portion = total_payment - interest
            # Negative principal_portion is valid: it represents a
            # partial payment below the interest due (negative
            # amortization -- the principal increases).  This correctly
            # models missed or underpaid months.

            # The "extra" portion is any amount beyond the standard
            # contractual payment (P&I).
            extra = max(total_payment - monthly_payment, Decimal("0.00"))

            # Overpayment cap: if principal exceeds remaining balance,
            # cap it to avoid negative balance.
            if principal_portion >= balance:
                principal_portion = balance
                actual_payment = principal_portion + interest
                extra = max(actual_payment - monthly_payment, Decimal("0.00"))
                balance = Decimal("0.00")
            else:
                actual_payment = principal_portion + interest
                balance -= principal_portion
                balance = balance.quantize(TWO_PLACES, ROUND_HALF_UP)

                # Guard against negative balance from rounding.
                # Mirrors the standard-path guard at the end of the
                # else branch below.  Trigger conditions are extremely
                # narrow (sub-penny rounding after quantize), but a
                # negative balance would propagate downstream as a
                # nonsensical financial value.
                if balance < 0:
                    balance = Decimal("0.00")
        else:
            # No payment record: use standard contractual logic.
            row_confirmed = False

            # Calculate principal portion.
            principal_portion = monthly_payment - interest

            # Cap payment at remaining balance + interest.
            # When principal would exceed balance or we've hit the loop
            # cap, absorb the remaining balance exactly to avoid
            # rounding residue.
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

        # Record the rate used for this period.  When rate_changes are
        # provided, always populate; otherwise leave as None for backward
        # compatibility with consumers that do not expect the field.
        row_rate = current_annual_rate if rate_schedule else None

        rows.append(AmortizationRow(
            month=month_num,
            payment_date=pay_date,
            payment=actual_payment.quantize(TWO_PLACES, ROUND_HALF_UP),
            principal=principal_portion.quantize(TWO_PLACES, ROUND_HALF_UP),
            interest=interest,
            extra_payment=extra.quantize(TWO_PLACES, ROUND_HALF_UP),
            remaining_balance=balance,
            is_confirmed=row_confirmed,
            interest_rate=row_rate,
        ))

        if balance <= 0:
            break

        # Advance to next month.
        pay_month += 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1

    return rows


def _derive_summary_metrics(
    schedule: list[AmortizationRow],
    fallback_date: date,
) -> tuple[Decimal, date]:
    """Extract total interest and payoff date from a generated schedule.

    Used by get_loan_projection to build summary metrics from a single
    schedule generation, avoiding the redundant second pass that
    calculate_summary would perform.

    Args:
        schedule: List of AmortizationRow from generate_schedule().
        fallback_date: Date to return as payoff_date when the schedule
            is empty (e.g. origination_date or today).

    Returns:
        (total_interest, payoff_date) tuple.
    """
    if not schedule:
        return Decimal("0.00"), fallback_date
    total_interest = sum(
        (row.interest for row in schedule), Decimal("0.00"),
    )
    payoff_date = schedule[-1].payment_date
    return total_interest, payoff_date


def calculate_summary(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    origination_date: date,
    payment_day: int,
    term_months: int,
    extra_monthly: Decimal = Decimal("0.00"),
    original_principal: Decimal | None = None,
    payments: list[PaymentRecord] | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
    anchor_balance: Decimal | None = None,
    anchor_date: date | None = None,
) -> AmortizationSummary:
    """Compute summary metrics: payoff date, interest saved, etc.

    When *payments* is provided, both the standard and accelerated
    schedules incorporate the payment data.  The standard schedule uses
    payments with no extra_monthly; the accelerated schedule adds
    extra_monthly on top.

    Args:
        original_principal: Original loan amount at origination.  For
            fixed-rate loans, the contractual payment is computed from
            (original_principal, annual_rate, term_months).  Ignored
            when rate_changes is non-empty (ARM loans re-amortize from
            current_principal at the current rate).
        payments: Optional list of PaymentRecord instances passed
            through to generate_schedule().
        rate_changes: Optional list of RateChangeRecord instances
            passed through to generate_schedule() for ARM loans.
            When non-empty, forces re-amortization from
            current_principal instead of using original terms.
        anchor_balance: Optional verified balance passed through to
            generate_schedule() for ARM anchor support.
        anchor_date: Date at which anchor_balance is known.
    """
    # For fixed-rate loans, the contractual payment from original terms
    # is correct for the life of the loan.  For ARM loans (non-empty
    # rate_changes), the payment is re-amortized at each rate adjustment,
    # so the current payment must be derived from current_principal and
    # remaining_months at the current rate.
    has_rate_changes = rate_changes is not None and len(rate_changes) > 0
    if original_principal is not None and not has_rate_changes:
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
        payments=payments,
        rate_changes=rate_changes,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
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
            payments=payments,
            rate_changes=rate_changes,
            anchor_balance=anchor_balance,
            anchor_date=anchor_date,
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
    rate_changes: list[RateChangeRecord] | None = None,
) -> Decimal | None:
    """Calculate required extra monthly payment to pay off by target_date.

    Args:
        original_principal: Original loan amount for contractual payment.
        term_months: Original loan term for contractual payment.
        rate_changes: Optional list of RateChangeRecord instances
            passed through to generate_schedule() for ARM loans.

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
        rate_changes=rate_changes,
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
            rate_changes=rate_changes,
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
    """Bundled projection output for a loan: summary, schedule, and balance.

    current_balance is the loan balance as of today.  For ARM loans
    this is the user-verified current_principal from LoanParams (set
    via the anchor mechanism).  For fixed-rate loans it is derived
    from the schedule by walking to today's date.
    """

    remaining_months: int
    summary: AmortizationSummary
    schedule: list  # list[AmortizationRow]
    current_balance: Decimal = Decimal("0.00")


def get_loan_projection(
    params,
    schedule_start=None,  # pylint: disable=unused-argument  # kept for callers
    payments=None,
    rate_changes=None,
):
    """Compute remaining months, summary, and schedule for a loan in one call.

    Generates the full life-of-loan schedule from ``origination_date``
    using ``original_principal`` as the starting balance.  This allows
    all payment records -- past confirmed and future projected -- to be
    matched by the engine's year-month lookup.

    For ARM loans, the schedule is anchored to ``current_principal``
    at today.  Pre-anchor rows use approximate P&I splits (the current
    rate applied to all historical months); post-anchor rows project
    from the user-verified balance and are mathematically exact.  This
    handles the common case where historical rate data is unavailable.

    For fixed-rate loans, the contractual monthly payment is computed
    from ``original_principal`` and ``term_months``.  No anchor is
    needed because the rate never changes and the origination-forward
    calculation is deterministic.

    The schedule is generated once (not twice as in the previous
    implementation that called both calculate_summary and
    generate_schedule).  Summary metrics are derived from the single
    schedule via _derive_summary_metrics.

    Args:
        params: An object with ``origination_date``, ``term_months``,
                ``original_principal``, ``current_principal``,
                ``interest_rate``, ``payment_day``, and optionally
                ``is_arm`` attributes (e.g. a LoanParams model instance).
        schedule_start: Unused.  Retained for backward compatibility.
        payments: Optional list of PaymentRecord instances passed
                  through to generate_schedule().
        rate_changes: Optional list of RateChangeRecord instances
                      passed through for ARM rate adjustment support.

    Returns:
        LoanProjection with remaining_months, summary, schedule, and
        current_balance.
    """
    remaining = calculate_remaining_months(
        params.origination_date, params.term_months,
    )

    orig_principal = Decimal(str(params.original_principal))
    current_principal = Decimal(str(params.current_principal))
    rate = Decimal(str(params.interest_rate))

    is_arm = getattr(params, "is_arm", False)
    # For fixed-rate loans, pass original_principal so the engine uses
    # the contractual payment.  For ARM, pass None to force
    # re-amortization at the current rate.
    original = None if is_arm else orig_principal

    # ARM anchor: snap the schedule to the user-verified balance at
    # today so forward projections are exact even without historical
    # rate data.  Fixed-rate loans do not need an anchor because the
    # origination-forward calculation is deterministic.
    anchor_bal = current_principal if is_arm else None
    anchor_dt = date.today() if is_arm else None

    # Generate the schedule once.  For ARM loans the anchor resets the
    # running balance at the first month after today; for fixed-rate
    # loans anchor_balance/anchor_date are None (no-op).
    schedule = generate_schedule(
        orig_principal, rate, params.term_months,
        origination_date=params.origination_date,
        payment_day=params.payment_day,
        original_principal=original,
        term_months=params.term_months,
        payments=payments,
        rate_changes=rate_changes,
        anchor_balance=anchor_bal,
        anchor_date=anchor_dt,
    )

    # Derive summary metrics from the single schedule.
    total_interest, payoff_date = _derive_summary_metrics(
        schedule, params.origination_date,
    )

    # Monthly payment: the amount the borrower pays each month.
    if is_arm and remaining > 0:
        # ARM: re-amortize from current_principal at current rate.
        monthly_payment = calculate_monthly_payment(
            current_principal, rate, remaining,
        )
    else:
        # Fixed-rate: contractual payment from original terms.
        monthly_payment = calculate_monthly_payment(
            orig_principal, rate, params.term_months,
        )

    summary = AmortizationSummary(
        monthly_payment=monthly_payment,
        total_interest=total_interest,
        payoff_date=payoff_date,
        total_interest_with_extra=total_interest,
        payoff_date_with_extra=payoff_date,
        months_saved=0,
        interest_saved=Decimal("0.00"),
    )

    # Current balance: for ARM, the anchor value (user-verified).
    # For fixed-rate, use the last confirmed row's remaining_balance
    # (reflecting actual payments), falling back to current_principal
    # when no confirmed payments exist.  Walking the schedule to
    # today's date would pick up theoretical contractual rows that
    # may not match reality when the user hasn't recorded payments.
    if is_arm:
        cur_balance = current_principal
    else:
        cur_balance = current_principal
        for row in reversed(schedule):
            if row.is_confirmed:
                cur_balance = row.remaining_balance
                break

    return LoanProjection(
        remaining_months=remaining,
        summary=summary,
        schedule=schedule,
        current_balance=cur_balance,
    )
