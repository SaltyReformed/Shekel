"""
Shekel Budget App -- Paycheck engine: the DEDUCTION lines of a paycheck.

Everything between a paycheck's gross and its taxable income, and everything
taken after tax: the per-paycheck :class:`_DeductionContext` both timing
passes share, the two passes themselves (:func:`_compute_deductions` over
:func:`_calculate_deductions`), the cadence rule that says which paydays a
26 / 24 / 12-per-year line lands on (:func:`_deduction_applies_at`), a line's
raw amount with its inflation escalation (:func:`_raw_deduction_amount`,
:func:`_inflation_years`), and a capped line's own year-to-date
(:func:`_cumulative_deduction_before`) -- the fourth of the package
docstring's calendar questions, kept beside the lines it caps because it
replays exactly what :func:`_raw_deduction_amount` applies to the live one.

The cadence rule is a three-valued MODE wearing a biweekly count (ledger row
**F-21**); plan step **salary:R15** replaces it with a recurrence rule against
the pay calendar (ruling **R-SAL3**) and deletes :func:`_deduction_applies_at`
with it.  It is spelled here, once, until then.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  Imports :mod:`._breakdown` and :mod:`._calendar_questions` and
nothing above them, so :mod:`._pricing` may import it without a cycle.
"""

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum
from app.services.pay_calendar import DerivedPeriod, paydays_in_year_before
from app.services.payroll_basis import PayrollBasis, gross_per_paycheck
from app.utils.deduction_cap import cap_period_amount
from app.utils.money import ZERO, round_money

from ._breakdown import DeductionBreakdown, DeductionLine
from ._calendar_questions import _is_third_paycheck, _month_ordinal


@dataclass(frozen=True)
class _DeductionContext:
    """Immutable inputs shared by the pre- and post-tax deduction passes.

    Carries the whole :class:`~app.services.payroll_basis.PayrollBasis` rather
    than a bare profile: the annual-cap cumulative replays prior paydays'
    grosses through :func:`~app.services.payroll_basis.gross_per_paycheck`,
    which needs the paycheck COUNT, and it walks those prior paydays off the
    calendar the same value carries.

    Attributes:
        basis: The owner's salary contract and pay calendar.
        period: The pay period this paycheck is for.
        gross_biweekly: What this paycheck pays before deductions.
        month_ordinal: This payday's 1-based position among the paydays of its
            calendar month.  ONE number where there were two independent
            predicates until plan step **balance:X-bh-1**: a 24-per-year
            deduction skips ordinal 3 and above, and a 12-per-year one is taken
            only at ordinal 1.  Resolved once per paycheck rather than per
            deduction, because every deduction of a paycheck asks it of the
            same payday.
    """
    basis: PayrollBasis
    period: DerivedPeriod
    gross_biweekly: Decimal
    month_ordinal: int


def _compute_deductions(ctx):
    """Compute the pre- and post-tax deduction lines for a paycheck.

    Runs :func:`_calculate_deductions` once per timing using the shared
    :class:`_DeductionContext`, returning both line lists bundled in a
    :class:`DeductionBreakdown`.

    Args:
        ctx: The per-paycheck :class:`_DeductionContext`.

    Returns:
        DeductionBreakdown with the pre- and post-tax line items.
    """
    return DeductionBreakdown(
        pre_tax=_calculate_deductions(
            ctx, ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
        ),
        post_tax=_calculate_deductions(
            ctx, ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)
        ),
    )


def _calculate_deductions(ctx, timing_id):
    """Calculate the deduction lines for a specific timing.

    Args:
        ctx: The per-paycheck :class:`_DeductionContext` (basis, period,
            gross_biweekly, month_ordinal).
        timing_id: Integer ID of the DeductionTiming to filter on.

    Handles:
    - deductions_per_year (26/24/12) filtering on the payday's month ordinal
    - calc_method (flat vs percentage)
    - inflation adjustment
    - annual cap: once a deduction's calendar-year total reaches its
      ``annual_cap`` the period amount is clamped so the year sums to the
      cap and stops (deep-hunt #2; shares ``cap_period_amount`` with the
      investment-contribution timeline so the two surfaces agree)
    """
    deductions = []
    profile = ctx.basis.profile
    if not profile.deductions:
        return deductions

    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
    for ded in profile.deductions:
        if not ded.is_active:
            continue
        if ded.deduction_timing_id != timing_id:
            continue
        if not _deduction_applies_at(ded, ctx.month_ordinal):
            continue

        amount = _raw_deduction_amount(
            ded, ctx.gross_biweekly, ctx.period.start_date, profile, pct_id
        )

        # Clamp to the user-set calendar-year cap (deep-hunt #2).  Only a
        # capped deduction pays for the prior-period cumulative replay; an
        # uncapped one (the common case) passes through untouched.  Read via
        # getattr to match the sibling ``target_account_id`` line: a deduction-
        # like duck type (test fake) may omit the optional column.
        annual_cap = getattr(ded, "annual_cap", None)
        if annual_cap is not None:
            amount = cap_period_amount(
                amount,
                _cumulative_deduction_before(ded, ctx, pct_id),
                annual_cap,
            )

        deductions.append(DeductionLine(
            name=ded.name, amount=amount,
            target_account_id=getattr(ded, "target_account_id", None),
        ))

    return deductions


def _deduction_applies_at(ded, month_ordinal):
    """Whether a deduction is taken on a payday at this position in its month.

    26-per-year deductions apply on every payday; 24-per-year skip the 3rd of a
    month; 12-per-year apply only on the first.  Shared by the line-building
    pass and the annual-cap cumulative so a payday the deduction skips
    contributes nothing to either.

    **It takes the ORDINAL rather than a period and a period list** (plan step
    **balance:X-bh-1**).  Both arms asked the same question of the same payday
    -- where does it sit in its month -- through two separate scans, and the
    12-per-year arm re-scanned per deduction where the 24-per-year arm had been
    given one answer for the whole paycheck.  One number, resolved once by
    :func:`_month_ordinal`, is what removed both.

    Args:
        ded: The deduction, read for ``deductions_per_year``.
        month_ordinal: The payday's 1-based position in its calendar month.

    Returns:
        ``True`` when this payday takes the deduction.
    """
    if ded.deductions_per_year == 24 and _is_third_paycheck(month_ordinal):
        return False
    if ded.deductions_per_year == 12:
        return month_ordinal == 1
    return True


def _raw_deduction_amount(ded, gross_biweekly, payday, profile, pct_id):
    """Per-paycheck deduction amount before any annual-cap clamp.

    Applies the flat-vs-percentage calc method and the optional inflation
    escalation at FULL Decimal precision, then rounds ONCE at return --
    the E-26(a) rule (intermediates stay full-precision; the line amount
    is the boundary, ratified 2026-06-11).  The pre-ratification shape
    quantized the percentage product BEFORE the inflation multiply and
    again after (a true intermediate quantize, off by up to a cent on
    inflated percentage deductions), and returned flat amounts
    UNQUANTIZED at the column's 4-decimal precision (so the displayed
    2dp line could disagree with the net-pay math by sub-cents).  The
    single boundary rounding fixes both: what the user sees per line is
    exactly what the paycheck subtracts and what the annual-cap
    cumulative sums.

    Pulled out of the line-building loop so the annual-cap cumulative
    reproduces, for prior paydays, the exact amount the loop applies to
    the current one.

    Args:
        ded: The deduction to price.
        gross_biweekly: The gross of the paycheck this is taken from -- the
            base a percentage deduction is a percentage OF.
        payday: The day the paycheck arrives.  A ``date`` rather than a period
            since plan step **balance:X-bh-1**: the inflation escalation reads
            its year and month and nothing else, and the cumulative below
            replays PAYDAYS the calendar counted rather than period values.
        profile: The salary profile, read for ``created_at`` by the inflation
            escalation.
        pct_id: The ref id of the PERCENTAGE calculation method.

    Returns:
        The amount for one paycheck, quantized to the cent.
    """
    amount = Decimal(str(ded.amount))
    if ded.calc_method_id == pct_id:
        amount = gross_biweekly * amount
    if ded.inflation_enabled and ded.inflation_rate:
        inflation_rate = Decimal(str(ded.inflation_rate))
        eff_month = ded.inflation_effective_month or 1
        years = _inflation_years(payday, profile, eff_month)
        if years > 0:
            amount = amount * (1 + inflation_rate) ** years
    return round_money(amount)


def _cumulative_deduction_before(ded, ctx, pct_id):
    """Sum a deduction's raw amounts for this year's earlier paydays.

    Mirrors :func:`~._calendar_questions._get_cumulative_wages` (the FICA
    wage-base precedent): walk the calendar's paydays for this year before
    ``ctx.period``, skip the ones where the deduction is not taken, and sum
    each applicable payday's raw amount -- recomputing that paycheck's gross
    through
    :func:`~app.services.payroll_basis.gross_per_paycheck` so a percentage
    deduction tracks the raise-adjusted gross exactly as the live paycheck
    does.  Summing the raw (pre-cap) amounts is equivalent to summing
    the capped ones (see ``cap_period_amount``), so no capped running state has
    to be threaded across the per-paycheck calls.

    **The paydays come from ``ctx.basis.calendar``** since plan step
    **balance:X-bh-1**, through
    :func:`~app.services.pay_calendar.paydays_in_year_before` -- the same
    producer :func:`~._calendar_questions._get_cumulative_wages` reads, so the
    two cumulatives cannot be summing different years of the same owner.  It
    was ``ctx.all_periods``, where a partial-context caller under-counted the
    cumulative and DEFERRED the cap, so the deduction went on being charged
    after it should have stopped.

    **It also stopped being cubic.**  The old walk asked
    ``_is_third_paycheck(p, all_periods)`` -- itself a scan of every period --
    once per prior period, inside :func:`project_salary`'s loop over every
    period.  Measured 2026-08-30 on the owner's 63 saved paydays with one
    capped deduction, by counting the calls rather than by multiplying the
    loop bounds: **718 scans, 45,234 period comparisons**.  (Reasoning from
    the bounds gives ~250,000, five times too many, because the inner walk
    stops at the current period and at the year boundary -- which is why the
    figure here is counted.)  The ordinal is now a bisect over the payday
    sequence.

    Args:
        ded: The capped deduction whose year-to-date is wanted.
        ctx: The per-paycheck :class:`_DeductionContext`.
        pct_id: The ref id of the PERCENTAGE calculation method.

    Returns:
        The sum of this deduction's raw amounts for the year's earlier
        paydays, ``ZERO`` when there are none.
    """
    basis = ctx.basis
    profile = basis.profile
    cumulative = ZERO
    for payday in paydays_in_year_before(basis.calendar, ctx.period.start_date):
        ordinal = _month_ordinal(basis.calendar, payday)
        if not _deduction_applies_at(ded, ordinal):
            continue
        gross = gross_per_paycheck(basis.annual_salary_on(payday), basis.periods_per_year)
        cumulative += _raw_deduction_amount(
            ded, gross, payday, profile, pct_id,
        )
    return cumulative


def _inflation_years(payday, profile, effective_month):
    """Return the number of full inflation years between profile creation and *payday*.

    Args:
        payday: The day the paycheck arrives.  Its year and month are the whole
            of what is read.
        profile: The salary profile, read for ``created_at``.  A profile with
            no creation stamp escalates nothing.
        effective_month: The month of the year the escalation steps in.

    Returns:
        The whole number of escalations due, never negative.
    """
    created = profile.created_at
    if created is None:
        return 0

    years = payday.year - created.year
    if payday.month < effective_month:
        years -= 1

    return max(0, years)
