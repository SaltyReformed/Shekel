"""
Shekel Budget App -- Paycheck Calculator Service

Core Phase 2 service: calculates net biweekly paycheck amounts from a
salary profile including raises, deductions, and taxes.

All functions are pure (no DB access) -- data is passed in as arguments.

Biweekly rounding residue -- reconciled to the annual aggregate
---------------------------------------------------------------

``gross_biweekly`` is computed by dividing the (post-raise) annual
salary by ``pay_periods_per_year`` and reconciling the per-cycle
rounding residue back into the annual aggregate so the sum of all
periods sharing the same effective annual salary in the same calendar
year equals their share of that annual salary exactly (audit MED-05 /
PA-07).

Algorithm.  Within a reconciliation group -- the set of periods in one
calendar year that share one post-raise annual salary -- the per-period
floor is ``(annual / pay_periods_per_year)`` rounded *down* to the
cent.  The cumulative residue is ``annual * group_size /
pay_periods_per_year - floor * group_size``, expressed in whole cents.
The earliest ``residue_cents`` periods of the group (sorted by start
date) each receive ``floor + $0.01``; the remaining periods receive
``floor``.  The distribution is deterministic and reproducible across
invocations, and the per-period values differ from each other by at
most one cent.

Examples (assuming all 26 periods in one year, one salary):

- $50,000 / 26 -> floor $1,923.07, residue 18 cents -> 18 periods at
  $1,923.08 + 8 periods at $1,923.07 = $50,000.00 exact.
- $75,000 / 26 -> floor $2,884.61, residue 14 cents -> 14 periods at
  $2,884.62 + 12 periods at $2,884.61 = $75,000.00 exact.
- $100,000 / 26 -> floor $3,846.15, residue 10 cents -> 10 periods at
  $3,846.16 + 16 periods at $3,846.15 = $100,000.00 exact.

Partial-context fallback.  When the supplied ``all_periods`` does not
cover the full pay-period year for the period's salary group (e.g. a
single-period call in a route preview, or a test fixture that supplies
one period at a time), the reconciliation cannot anchor against a
complete annual figure and the helper falls back to
``ROUND_HALF_UP`` quantisation -- the historical per-period semantics.
The fallback prevents a partial sample from being mis-reconciled as
though it were a full year.

This reconciliation matches the canonical W-2 box 1 expectation -- the
sum of pay stubs equals the contract annual salary exactly -- and
removes the silent ~$0.10/year drift documented in audit prior PA-07.
The previous fallback contract (each stub is the half-up quantised
value, with the residue carried into the year-end aggregate) was
classified in 2026-04-15's audit as F-127 "accepted simplification";
MED-05 / PA-07 supersedes F-127 with this exact-reconciliation
contract.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum
from app.services import tax_calculator
from app.services.calibration_service import apply_calibration

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
ONE_CENT = Decimal("0.01")


@dataclass
class DeductionLine:
    """A single deduction line item in a paycheck breakdown."""
    name: str
    amount: Decimal
    target_account_id: int = None


@dataclass
class TaxLines:
    """The four withholding lines computed for a single paycheck."""
    federal: Decimal = ZERO
    state: Decimal = ZERO
    social_security: Decimal = ZERO
    medicare: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        """Return the sum of the four withholding lines."""
        return self.federal + self.state + self.social_security + self.medicare


@dataclass
class DeductionBreakdown:
    """Pre- and post-tax deduction line items for a single paycheck."""
    pre_tax: list[DeductionLine] = field(default_factory=list)
    post_tax: list[DeductionLine] = field(default_factory=list)

    @property
    def total_pre_tax(self) -> Decimal:
        """Return the sum of the pre-tax deduction amounts."""
        return sum((d.amount for d in self.pre_tax), ZERO)

    @property
    def total_post_tax(self) -> Decimal:
        """Return the sum of the post-tax deduction amounts."""
        return sum((d.amount for d in self.post_tax), ZERO)


@dataclass
class Earnings:
    """Gross-to-net dollar figures for a single paycheck."""
    annual_salary: Decimal
    gross_biweekly: Decimal
    taxable_income: Decimal = ZERO
    net_pay: Decimal = ZERO

    @property
    def take_home_rate_pct(self) -> Decimal | None:
        """Return ``net / gross`` expressed as a percent (MED-04 / E-16).

        Pre-computed here so the salary breakdown template renders the
        take-home rate without Jinja-side division.  Returns ``None``
        when ``gross_biweekly`` is non-positive so the template can
        render a placeholder ``--`` without dividing by zero.
        """
        if self.gross_biweekly <= ZERO:
            return None
        return (self.net_pay / self.gross_biweekly) * Decimal("100")


@dataclass
class PeriodInfo:
    """Pay-period identity and per-paycheck event flags."""
    period_id: int
    is_third_paycheck: bool = False
    raise_event: str = ""


@dataclass
class PaycheckBreakdown:
    """Complete paycheck breakdown for a single pay period.

    The breakdown is organised into four cohesive sections rather than a
    flat field list: :class:`PeriodInfo` (``period``), :class:`Earnings`
    (``earnings``), :class:`TaxLines` (``taxes``), and
    :class:`DeductionBreakdown` (``deductions``).  Section totals live on
    the section that owns the data (``taxes.total``,
    ``deductions.total_pre_tax``, ``earnings.take_home_rate_pct``).
    """
    period: PeriodInfo
    earnings: Earnings
    taxes: TaxLines = field(default_factory=TaxLines)
    deductions: DeductionBreakdown = field(default_factory=DeductionBreakdown)


@dataclass(frozen=True)
class _DeductionContext:
    """Immutable inputs shared by the pre- and post-tax deduction passes."""
    profile: object
    period: object
    all_periods: list
    gross_biweekly: Decimal
    is_third_paycheck: bool


@dataclass(frozen=True)
class _WageBasis:
    """The per-paycheck wage figures withholding is computed from.

    The three figures travel together through both tax paths (calibrated
    and bracket-based): the period gross, the period taxable amount (gross
    less pre-tax deductions, floored at zero), and the year-to-date
    cumulative gross that drives the FICA Social Security wage-base cap.
    """
    gross_biweekly: Decimal
    taxable_biweekly: Decimal
    cumulative_wages: Decimal


def calculate_paycheck(profile, period, all_periods, tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

    The gross biweekly amount is computed by dividing the (post-raise)
    annual salary by ``pay_periods_per_year``; the per-cycle
    quantisation residue is reconciled back into the annual aggregate
    so the sum of the year's grosses for a single salary segment
    equals that salary exactly (audit MED-05 / PA-07, supersedes
    F-127).  See the module docstring section "Biweekly rounding
    residue -- reconciled to the annual aggregate" for the algorithm,
    including the partial-context fallback for callers that supply
    fewer than ``pay_periods_per_year`` periods (route previews,
    isolated test fixtures).

    Args:
        profile:      SalaryProfile with loaded raises and deductions.
        period:       The PayPeriod for this paycheck.
        all_periods:  All pay periods for the year (for 3rd paycheck detection
                      and cumulative wage tracking).
        tax_configs:  dict with keys:
                      - bracket_set: TaxBracketSet
                      - state_config: StateTaxConfig
                      - fica_config: FicaConfig
        calibration:  Optional CalibrationOverride with effective rates.
                      When provided and is_active is True, overrides
                      bracket-based tax calculations with calibrated rates.

    Returns:
        PaycheckBreakdown dataclass.
    """
    # Step 1: Determine annual salary after raises.
    annual_salary = apply_raises(profile.annual_salary, profile.raises, period.start_date)

    # Step 2: Gross biweekly.  Residue from the per-cycle quantisation
    # is reconciled back into the annual aggregate (MED-05 / PA-07);
    # see the module docstring "Biweekly rounding residue -- reconciled
    # to the annual aggregate" for the algorithm and the partial-context
    # fallback.
    pay_periods_per_year = profile.pay_periods_per_year or 26
    gross_biweekly = _gross_biweekly_for_period(
        annual_salary, period, all_periods, profile, pay_periods_per_year,
    )

    # Steps 3-4 & 8: 3rd-paycheck detection plus the pre- and post-tax
    # deduction passes (both share the same per-paycheck context).
    ded_ctx = _DeductionContext(
        profile, period, all_periods, gross_biweekly,
        _is_third_paycheck(period, all_periods),
    )
    deductions = _compute_deductions(ded_ctx)

    # Step 5: Taxable income (for display -- taxes computed via Pub 15-T).
    taxable_biweekly = max(gross_biweekly - deductions.total_pre_tax, ZERO)

    # Steps 6-7: Tax calculation -- calibrated or bracket-based.  Both
    # paths read the same wage figures; the cumulative YTD gross is
    # computed once here and feeds the FICA SS wage-base cap on both paths
    # (CRIT-03 / F-037: the calibration path used to skip this and
    # over-charged SS after the cap on high earners).
    wages = _WageBasis(
        gross_biweekly,
        taxable_biweekly,
        _get_cumulative_wages(profile, period, all_periods),
    )
    if calibration is not None and getattr(calibration, "is_active", False):
        taxes = _calibrated_tax_lines(
            wages, calibration, tax_configs.get("fica_config"),
        )
    else:
        taxes = _bracket_tax_lines(
            profile, wages, pay_periods_per_year,
            deductions.total_pre_tax, tax_configs,
        )

    # Step 9: Net pay.
    net_pay = (
        gross_biweekly
        - deductions.total_pre_tax
        - taxes.total
        - deductions.total_post_tax
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return PaycheckBreakdown(
        period=PeriodInfo(
            period.id, ded_ctx.is_third_paycheck, _get_raise_event(profile, period),
        ),
        earnings=Earnings(annual_salary, gross_biweekly, taxable_biweekly, net_pay),
        taxes=taxes,
        deductions=deductions,
    )


def project_salary(profile, periods, tax_configs, *, calibration=None):
    """Generate paycheck breakdowns for all given periods.

    Args:
        profile:      SalaryProfile with loaded raises and deductions.
        periods:      List of PayPeriod objects.
        tax_configs:  dict with bracket_set, state_config, fica_config.
        calibration:  Optional CalibrationOverride for rate-based taxes.

    Returns:
        List of PaycheckBreakdown, one per period.
    """
    return [
        calculate_paycheck(
            profile, period, periods, tax_configs,
            calibration=calibration,
        )
        for period in periods
    ]


# ── Private Helpers ────────────────────────────────────────────────


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


def _calibrated_tax_lines(wages, calibration, fica_config):
    """Compute the four withholding lines from effective calibrated rates.

    The Social Security line inside :func:`apply_calibration` delegates to
    ``capped_social_security`` so the wage-base cap is enforced identically
    to the bracket path (CRIT-03 / F-037).

    Args:
        wages: The per-paycheck :class:`_WageBasis` (gross, taxable, and the
            cumulative YTD gross that drives the SS wage-base cap).
        calibration: An active CalibrationOverride with effective rates.
        fica_config: The FicaConfig (or None) for the SS wage-base cap.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts.
    """
    cal_taxes = apply_calibration(
        wages.gross_biweekly,
        wages.taxable_biweekly,
        calibration,
        cumulative_wages=wages.cumulative_wages,
        fica_config=fica_config,
    )
    return TaxLines(
        federal=cal_taxes["federal"],
        state=cal_taxes["state"],
        social_security=cal_taxes["ss"],
        medicare=cal_taxes["medicare"],
    )


def _bracket_tax_lines(profile, wages, pay_periods_per_year, total_pre_tax, tax_configs):
    """Compute the four withholding lines from IRS Pub 15-T brackets plus FICA.

    The cumulative YTD gross on ``wages`` feeds the FICA SS wage-base cap so
    it is enforced identically to the calibration path (CRIT-03 / F-037).

    Args:
        profile: The SalaryProfile (read for the W-4 federal inputs).
        wages: The per-paycheck :class:`_WageBasis` (gross, taxable, and the
            cumulative YTD gross that drives the SS wage-base cap).
        pay_periods_per_year: The full-year denominator (typically 26).
        total_pre_tax: Per-period pre-tax deduction total (annualised for
            the bracket federal calculation).
        tax_configs: dict with bracket_set, state_config, fica_config.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts.
    """
    bracket_set = tax_configs.get("bracket_set")
    federal = (
        _bracket_federal(
            profile, wages.gross_biweekly, pay_periods_per_year,
            bracket_set, total_pre_tax * pay_periods_per_year,
        )
        if bracket_set
        else ZERO
    )
    state = _bracket_state(
        wages.taxable_biweekly, pay_periods_per_year, tax_configs.get("state_config")
    )
    fica = tax_calculator.calculate_fica(
        wages.gross_biweekly, tax_configs.get("fica_config"), wages.cumulative_wages
    )
    return TaxLines(
        federal=federal,
        state=state,
        social_security=fica["ss"],
        medicare=fica["medicare"],
    )


def _bracket_federal(profile, gross_biweekly, pay_periods_per_year, bracket_set,
                     annual_pre_tax):
    """Return the bracket-based biweekly federal withholding (IRS Pub 15-T).

    Reads the W-4 inputs off ``profile`` and delegates to
    :func:`tax_calculator.calculate_federal_withholding`.

    Args:
        profile: The SalaryProfile (read for the W-4 inputs).
        gross_biweekly: The period gross to withhold against.
        pay_periods_per_year: The full-year denominator (typically 26).
        bracket_set: The TaxBracketSet to withhold against.
        annual_pre_tax: Annualised pre-tax deduction total.

    Returns:
        Decimal biweekly federal withholding.
    """
    w4 = tax_calculator.W4Inputs(
        additional_income=getattr(profile, "additional_income", 0) or 0,
        pre_tax_deductions=annual_pre_tax,
        additional_deductions=getattr(profile, "additional_deductions", 0) or 0,
        qualifying_children=getattr(profile, "qualifying_children", 0) or 0,
        other_dependents=getattr(profile, "other_dependents", 0) or 0,
        extra_withholding=getattr(profile, "extra_withholding", 0) or 0,
    )
    return tax_calculator.calculate_federal_withholding(
        gross_biweekly, pay_periods_per_year, bracket_set, w4,
    )


def _bracket_state(taxable_biweekly, pay_periods_per_year, state_config):
    """Return the biweekly state withholding from annualised taxable income.

    Args:
        taxable_biweekly: Gross less pre-tax deductions, floored at zero.
        pay_periods_per_year: The full-year denominator (typically 26).
        state_config: The StateTaxConfig (or None).

    Returns:
        Decimal biweekly state withholding.
    """
    state_annual = tax_calculator.calculate_state_tax(
        taxable_biweekly * pay_periods_per_year, state_config
    )
    return (state_annual / pay_periods_per_year).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _gross_biweekly_for_period(
    annual_salary, period, all_periods, profile, pay_periods_per_year,
):
    """Return the per-period gross with the biweekly residue reconciled.

    Within a "reconciliation group" -- the set of periods in
    ``all_periods`` that share both ``period``'s calendar year and the
    same post-raise annual salary -- the helper distributes the
    quantisation residue so the group's grosses sum to its exact share
    of the annual salary (audit MED-05 / PA-07).  See the module
    docstring "Biweekly rounding residue -- reconciled to the annual
    aggregate" for the algorithm.

    When ``all_periods`` does not cover a full pay-period year for the
    group (e.g. fewer than ``pay_periods_per_year`` periods total in
    that year), the helper falls back to ``ROUND_HALF_UP`` so a
    partial-sample call does not mis-distribute a residue computed
    against an incomplete denominator.  Single-period callers (route
    previews, isolated test fixtures) therefore retain the historical
    per-period semantics.

    Args:
        annual_salary: The post-raise annual salary for ``period``,
            as returned by :func:`apply_raises`.  Constructed from a
            Decimal upstream; the helper does not re-coerce.
        period: The :class:`PayPeriod` whose gross is being computed.
        all_periods: Every :class:`PayPeriod` known to the calling
            ``calculate_paycheck`` invocation.  Periods outside
            ``period.start_date.year`` are ignored.
        profile: The :class:`SalaryProfile`; consulted only for
            ``apply_raises`` so the group boundary respects mid-year
            raise events.
        pay_periods_per_year: The full-year denominator (typically 26).

    Returns:
        Decimal -- the period's gross, equal to either ``floor`` or
        ``floor + $0.01`` where ``floor = (annual / pay_periods_per_year)``
        rounded down to the cent.  Earlier periods in the group (by
        ``start_date``) receive the ``+$0.01`` adjustment when the
        group's residue is positive.
    """
    pay_periods_dec = Decimal(str(pay_periods_per_year))
    period_year = period.start_date.year

    # Restrict to the same calendar year, then to the same effective
    # annual salary (i.e. the same post-raise segment).  Sort by
    # start_date so the distribution is deterministic and reproducible
    # across invocations.
    same_year = [
        p for p in all_periods
        if p.start_date.year == period_year
    ]
    group = sorted(
        (
            p for p in same_year
            if apply_raises(profile.annual_salary, profile.raises, p.start_date)
            == annual_salary
        ),
        key=lambda p: p.start_date,
    )

    # Partial-context fallback: when the supplied year does not cover
    # the full pay-period year, the residue would be computed against
    # an incomplete denominator.  Retain the historical per-period
    # half-up semantics so single-period callers (route previews,
    # isolated tests) are unaffected by the reconciliation contract.
    if len(same_year) < pay_periods_per_year:
        return (annual_salary / pay_periods_dec).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

    floor_value = (annual_salary / pay_periods_dec).quantize(
        TWO_PLACES, rounding=ROUND_DOWN
    )
    residue_cents = _residue_cents(
        annual_salary, len(group), pay_periods_dec, floor_value
    )

    # ``residue_cents`` is non-negative by construction (floor rounded
    # the share down; quantising the share never decreases it below
    # ``floor * group_size``).  Distribute to the earliest periods in
    # group order.
    if residue_cents <= 0:
        return floor_value

    # ``period`` is guaranteed to be in ``group``: it shares its own
    # calendar year and (by construction, since ``annual_salary`` was
    # computed from it) the group's effective annual salary, so it
    # survives both filters above.  The earliest ``residue_cents`` periods
    # in group order receive the +$0.01 adjustment.
    if group.index(period) < residue_cents:
        return floor_value + ONE_CENT
    return floor_value


def _residue_cents(annual_salary, group_size, pay_periods_dec, floor_value):
    """Return the whole-cent residue to distribute across a reconciliation group.

    The group's exact share of the annual salary at full precision is
    ``annual_salary * group_size / pay_periods_per_year``.  The residue is
    the cents that must be added on top of ``floor_value * group_size`` to
    reach that share.  Quantising the share to the cent here is safe:
    ``floor_value`` is already at cent precision, so any sub-cent fraction
    in the exact share is below the rounding boundary the residue
    distribution targets.  The result is non-negative by construction (the
    floor rounded the share down; quantising the share never decreases it
    below ``floor * group_size``).

    Args:
        annual_salary: The post-raise annual salary for the group.
        group_size: Number of periods sharing the salary in the year.
        pay_periods_dec: ``pay_periods_per_year`` as a Decimal.
        floor_value: The per-period floor (annual / periods, rounded down).

    Returns:
        int -- the count of cents to distribute (one cent each to the
        earliest ``residue_cents`` periods in group order).
    """
    group_size_dec = Decimal(group_size)
    exact_share = (
        annual_salary * group_size_dec / pay_periods_dec
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    residue = exact_share - floor_value * group_size_dec
    return int((residue / ONE_CENT).to_integral_value(rounding=ROUND_HALF_UP))


def apply_raises(base_salary, raises, as_of):
    """Return the effective annual salary as of a date, after applying raises.

    The shared raise-application rule used by both the paycheck pipeline
    (:func:`calculate_paycheck` / :func:`project_salary`) and the pension
    salary projection
    (:func:`app.services.pension_calculator.project_salaries_by_year`).
    Promoted from the former private ``_apply_raises(profile, period)`` to
    plain inputs so the pension projector no longer reaches into a private
    symbol with fabricated duck-typed objects (deep-hunt #83).

    Raises are sorted by (effective_year, effective_month) before
    application so that flat raises apply before percentage raises
    within the same effective date.  This ensures deterministic
    results regardless of database query order (M-01).

    A raise applies if:
    - Its effective_year matches ``as_of``'s year (or is None for recurring)
    - Its effective_month is on or before ``as_of``'s month (for that year)

    Args:
        base_salary: The pre-raise annual salary -- a Decimal, or any
            value ``Decimal(str(...))`` accepts.
        raises: An iterable of raise objects, each exposing
            ``effective_year``, ``effective_month``, ``is_recurring``,
            ``percentage``, and ``flat_amount``.  A falsy/empty value
            returns ``base_salary`` unchanged (unquantized, matching the
            prior behavior).
        as_of: The :class:`datetime.date` the salary is evaluated at;
            only its ``year`` and ``month`` are consulted (day ignored).

    Returns:
        Decimal -- the post-raise annual salary, quantized to cents
        (ROUND_HALF_UP) when any raise applied.
    """
    salary = Decimal(str(base_salary))

    if not raises:
        return salary

    period_year = as_of.year
    period_month = as_of.month

    sorted_raises = sorted(
        raises,
        key=lambda r: (r.effective_year or 0, r.effective_month or 0),
    )

    for raise_obj in sorted_raises:
        eff_year = raise_obj.effective_year
        eff_month = raise_obj.effective_month

        if raise_obj.is_recurring:
            # Recurring raises compound each year at the specified month.
            # Count total applications: one per year from eff_year onward
            # where the effective month has been reached.
            if not eff_year:
                # No start year -- apply once if month reached this year.
                if period_month >= eff_month:
                    salary = _apply_single_raise(salary, raise_obj)
            elif period_year >= eff_year:
                total_applications = period_year - eff_year
                if period_month >= eff_month:
                    total_applications += 1
                for _ in range(total_applications):
                    salary = _apply_single_raise(salary, raise_obj)
        else:
            # One-time raise: apply if we're at or past the effective date.
            if eff_year is None:
                continue
            if (period_year > eff_year) or (
                period_year == eff_year and period_month >= eff_month
            ):
                salary = _apply_single_raise(salary, raise_obj)

    return salary.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _apply_single_raise(salary, raise_obj):
    """Apply a single raise (percentage or flat) to the salary."""
    if raise_obj.percentage:
        pct = Decimal(str(raise_obj.percentage))
        return salary * (1 + pct)
    if raise_obj.flat_amount:
        return salary + Decimal(str(raise_obj.flat_amount))
    return salary


def _get_raise_event(profile, period):
    """Return a description of any raise event occurring in this period."""
    if not profile.raises:
        return ""

    period_year = period.start_date.year
    period_month = period.start_date.month
    events = []

    for raise_obj in profile.raises:
        eff_month = raise_obj.effective_month
        eff_year = raise_obj.effective_year

        is_match = False
        if raise_obj.is_recurring and period_month == eff_month:
            is_match = True
        elif eff_year == period_year and eff_month == period_month:
            is_match = True

        if is_match:
            raise_type = raise_obj.raise_type.name if raise_obj.raise_type else "raise"
            if raise_obj.percentage:
                pct = Decimal(str(raise_obj.percentage)) * 100
                events.append(f"{raise_type.upper()} +{pct}%")
            else:
                events.append(f"{raise_type.upper()} +${raise_obj.flat_amount}")

    return ", ".join(events)


def _is_third_paycheck(period, all_periods):
    """Detect if this period is the 3rd paycheck in its calendar month.

    With biweekly pay (26 per year), most months have 2 paychecks.
    Twice a year, a month has 3 paycheck start dates.
    """
    target_month = period.start_date.month
    target_year = period.start_date.year

    # Count how many periods start in the same month, up to and including this one.
    count = 0
    for p in all_periods:
        if (p.start_date.year == target_year and
                p.start_date.month == target_month and
                p.start_date <= period.start_date):
            count += 1

    return count >= 3


def _is_first_paycheck_of_month(period, all_periods):
    """Detect if this is the first paycheck starting in this calendar month."""
    target_month = period.start_date.month
    target_year = period.start_date.year

    for p in all_periods:
        if (p.start_date.year == target_year and
                p.start_date.month == target_month and
                p.start_date < period.start_date):
            return False

    return True


def _calculate_deductions(ctx, timing_id):
    """Calculate the deduction lines for a specific timing.

    Args:
        ctx: The per-paycheck :class:`_DeductionContext` (profile, period,
            all_periods, gross_biweekly, is_third_paycheck).
        timing_id: Integer ID of the DeductionTiming to filter on.

    Handles:
    - deductions_per_year (26/24/12) filtering based on 3rd paycheck
    - calc_method (flat vs percentage)
    - inflation adjustment
    - annual cap tracking
    """
    deductions = []
    if not ctx.profile.deductions:
        return deductions

    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
    for ded in ctx.profile.deductions:
        if not ded.is_active:
            continue
        if ded.deduction_timing_id != timing_id:
            continue

        # Skip 24-per-year deductions on 3rd paychecks
        if ded.deductions_per_year == 24 and ctx.is_third_paycheck:
            continue

        # Skip 12-per-year deductions unless first paycheck of month
        if ded.deductions_per_year == 12:
            if not _is_first_paycheck_of_month(ctx.period, ctx.all_periods):
                continue

        # Calculate amount
        amount = Decimal(str(ded.amount))
        if ded.calc_method_id == pct_id:
            amount = (ctx.gross_biweekly * amount).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )

        # Apply inflation if enabled
        if ded.inflation_enabled and ded.inflation_rate:
            inflation_rate = Decimal(str(ded.inflation_rate))
            eff_month = ded.inflation_effective_month or 1
            # Calculate years of inflation based on period date
            years = _inflation_years(ctx.period, ctx.profile, eff_month)
            if years > 0:
                amount = (amount * (1 + inflation_rate) ** years).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )

        target_id = getattr(ded, "target_account_id", None)
        deductions.append(DeductionLine(
            name=ded.name, amount=amount, target_account_id=target_id
        ))

    return deductions


def _inflation_years(period, profile, effective_month):
    """Calculate the number of full inflation years since profile creation."""
    created = profile.created_at
    if created is None:
        return 0

    period_year = period.start_date.year
    period_month = period.start_date.month
    created_year = created.year

    years = period_year - created_year
    if period_month < effective_month:
        years -= 1

    return max(0, years)


def _get_cumulative_wages(profile, period, all_periods):
    """Calculate cumulative gross wages for the year up to (but not including) this period.

    Periods are sorted by start_date before iteration so the break
    condition works correctly regardless of input order (M-02).

    Used for FICA SS wage base cap tracking.
    """
    period_year = period.start_date.year
    pay_periods_per_year = profile.pay_periods_per_year or 26
    cumulative = ZERO

    for p in sorted(all_periods, key=lambda p: p.start_date):
        if p.start_date.year != period_year:
            continue
        if p.start_date >= period.start_date:
            break

        salary = apply_raises(profile.annual_salary, profile.raises, p.start_date)
        # Reuse the same reconciliation contract as ``calculate_paycheck``
        # so prior-period grosses summed here match the per-period
        # ``gross_biweekly`` exactly.  Without this, the FICA cap path
        # would compare a half-up cumulative to reconciled per-period
        # grosses and shift the cap-crossing period by one cent in edge
        # cases (MED-05 / PA-07).
        gross = _gross_biweekly_for_period(
            salary, p, all_periods, profile, pay_periods_per_year,
        )
        cumulative += gross

    return cumulative
