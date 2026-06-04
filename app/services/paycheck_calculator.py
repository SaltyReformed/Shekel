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
class PaycheckBreakdown:
    """Complete paycheck breakdown for a single pay period."""
    period_id: int
    annual_salary: Decimal
    gross_biweekly: Decimal
    pre_tax_deductions: list = field(default_factory=list)
    taxable_income: Decimal = ZERO
    federal_tax: Decimal = ZERO
    state_tax: Decimal = ZERO
    social_security: Decimal = ZERO
    medicare: Decimal = ZERO
    post_tax_deductions: list = field(default_factory=list)
    net_pay: Decimal = ZERO
    is_third_paycheck: bool = False
    raise_event: str = ""

    @property
    def total_pre_tax(self):
        return sum((d.amount for d in self.pre_tax_deductions), ZERO)

    @property
    def total_post_tax(self):
        return sum((d.amount for d in self.post_tax_deductions), ZERO)

    @property
    def total_taxes(self):
        return self.federal_tax + self.state_tax + self.social_security + self.medicare

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
    bracket_set = tax_configs.get("bracket_set")
    state_config = tax_configs.get("state_config")
    fica_config = tax_configs.get("fica_config")

    # Step 1: Determine annual salary after raises
    annual_salary = _apply_raises(profile, period)
    raise_event = _get_raise_event(profile, period)

    # Step 2: Gross biweekly.  Residue from the per-cycle quantisation
    # is reconciled back into the annual aggregate (MED-05 / PA-07);
    # see the module docstring "Biweekly rounding residue -- reconciled
    # to the annual aggregate" for the algorithm and the partial-context
    # fallback.
    pay_periods_per_year = profile.pay_periods_per_year or 26
    gross_biweekly = _gross_biweekly_for_period(
        annual_salary, period, all_periods, profile,
        pay_periods_per_year,
    )

    # Step 3: Detect 3rd paycheck
    is_third = _is_third_paycheck(period, all_periods)

    # Resolve deduction timing and calc method IDs from the startup cache.

    pre_tax_id = ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
    post_tax_id = ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)

    # Step 4: Calculate pre-tax deductions
    pre_tax_deductions = _calculate_deductions(
        profile, period, all_periods, gross_biweekly, pre_tax_id, pct_id, is_third
    )
    total_pre_tax = sum((d.amount for d in pre_tax_deductions), ZERO)

    # Step 5: Taxable income (for display -- taxes computed via Pub 15-T)
    taxable_biweekly = gross_biweekly - total_pre_tax
    if taxable_biweekly < ZERO:
        taxable_biweekly = ZERO

    # Step 6 & 7: Tax calculation -- calibrated or bracket-based
    use_calibration = (
        calibration is not None
        and getattr(calibration, "is_active", False)
    )

    # Cumulative YTD wages are needed by both branches for the SS wage-base
    # cap (CRIT-03 / F-037: the calibration path used to skip this and
    # over-charged SS after the cap on high earners).
    cumulative_wages = _get_cumulative_wages(
        profile, period, all_periods
    )

    if use_calibration:
        # Use effective rates from the pay stub calibration.  The SS line
        # inside apply_calibration delegates to capped_social_security so
        # the wage-base cap is enforced identically to the bracket path.
        cal_taxes = apply_calibration(
            gross_biweekly,
            taxable_biweekly,
            calibration,
            cumulative_wages=cumulative_wages,
            fica_config=fica_config,
        )
        federal_biweekly = cal_taxes["federal"]
        state_biweekly = cal_taxes["state"]
        ss_biweekly = cal_taxes["ss"]
        medicare_biweekly = cal_taxes["medicare"]
    else:
        # Bracket-based federal withholding (IRS Pub 15-T).
        annual_pre_tax = total_pre_tax * pay_periods_per_year

        additional_income = Decimal(str(getattr(profile, "additional_income", 0) or 0))
        additional_deductions = Decimal(str(getattr(profile, "additional_deductions", 0) or 0))
        extra_withholding = Decimal(str(getattr(profile, "extra_withholding", 0) or 0))
        qualifying_children = int(getattr(profile, "qualifying_children", 0) or 0)
        other_dependents = int(getattr(profile, "other_dependents", 0) or 0)

        if bracket_set:
            federal_biweekly = tax_calculator.calculate_federal_withholding(
                gross_pay=gross_biweekly,
                pay_periods=pay_periods_per_year,
                bracket_set=bracket_set,
                additional_income=additional_income,
                pre_tax_deductions=annual_pre_tax,
                additional_deductions=additional_deductions,
                qualifying_children=qualifying_children,
                other_dependents=other_dependents,
                extra_withholding=extra_withholding,
            )
        else:
            federal_biweekly = ZERO

        state_annual = tax_calculator.calculate_state_tax(
            taxable_biweekly * pay_periods_per_year, state_config
        )
        state_biweekly = (state_annual / pay_periods_per_year).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

        # FICA -- use cumulative wages for SS cap tracking.
        fica = tax_calculator.calculate_fica(
            gross_biweekly, fica_config, cumulative_wages
        )
        ss_biweekly = fica["ss"]
        medicare_biweekly = fica["medicare"]

    # Step 8: Post-tax deductions
    post_tax_deductions = _calculate_deductions(
        profile, period, all_periods, gross_biweekly, post_tax_id, pct_id, is_third
    )
    total_post_tax = sum((d.amount for d in post_tax_deductions), ZERO)

    # Step 9: Net pay
    net_pay = (
        gross_biweekly
        - total_pre_tax
        - federal_biweekly
        - state_biweekly
        - ss_biweekly
        - medicare_biweekly
        - total_post_tax
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return PaycheckBreakdown(
        period_id=period.id,
        annual_salary=annual_salary,
        gross_biweekly=gross_biweekly,
        pre_tax_deductions=pre_tax_deductions,
        taxable_income=taxable_biweekly,
        federal_tax=federal_biweekly,
        state_tax=state_biweekly,
        social_security=ss_biweekly,
        medicare=medicare_biweekly,
        post_tax_deductions=post_tax_deductions,
        net_pay=net_pay,
        is_third_paycheck=is_third,
        raise_event=raise_event,
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
            as returned by :func:`_apply_raises`.  Constructed from a
            Decimal upstream; the helper does not re-coerce.
        period: The :class:`PayPeriod` whose gross is being computed.
        all_periods: Every :class:`PayPeriod` known to the calling
            ``calculate_paycheck`` invocation.  Periods outside
            ``period.start_date.year`` are ignored.
        profile: The :class:`SalaryProfile`; consulted only for
            ``_apply_raises`` so the group boundary respects mid-year
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
            if _apply_raises(profile, p) == annual_salary
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

    # The group's exact share of the annual salary at full precision is
    # ``annual_salary * group_size / pay_periods_per_year``.  The
    # residue is the cents that need to be added on top of
    # ``floor_value * group_size`` to reach that share.  Quantising
    # the share to the cent here is safe: ``floor_value`` is already at
    # cent precision, so any sub-cent fraction in the exact share is
    # below the rounding boundary the residue distribution targets.
    group_size = len(group)
    group_size_dec = Decimal(group_size)
    exact_share = (
        annual_salary * group_size_dec / pay_periods_dec
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    residue = exact_share - floor_value * group_size_dec
    residue_cents = int((residue / ONE_CENT).to_integral_value(
        rounding=ROUND_HALF_UP
    ))

    # ``residue_cents`` is non-negative by construction (floor rounded
    # the share down; quantising the share never decreases it below
    # ``floor * group_size``).  Distribute to the earliest periods in
    # group order.
    if residue_cents <= 0:
        return floor_value

    try:
        idx = group.index(period)
    except ValueError:
        # ``period`` is not in ``all_periods`` (defensive: real callers
        # always include it).  Fall back to the floor value so the
        # caller still receives a deterministic Decimal.
        return floor_value

    if idx < residue_cents:
        return floor_value + ONE_CENT
    return floor_value


def _apply_raises(profile, period):
    """Return the effective annual salary for the given period, after raises.

    Raises are sorted by (effective_year, effective_month) before
    application so that flat raises apply before percentage raises
    within the same effective date.  This ensures deterministic
    results regardless of database query order (M-01).

    A raise applies if:
    - Its effective_year matches the period's year (or is None for recurring)
    - Its effective_month is on or before the period's month (for that year)
    """
    salary = Decimal(str(profile.annual_salary))

    if not profile.raises:
        return salary

    period_year = period.start_date.year
    period_month = period.start_date.month

    sorted_raises = sorted(
        profile.raises,
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


def _calculate_deductions(profile, period, all_periods, gross_biweekly,
                          timing_id, calc_method_pct_id, is_third_paycheck):
    """Calculate deductions for a specific timing.

    Args:
        timing_id:          Integer ID of the DeductionTiming to filter on.
        calc_method_pct_id: Integer ID of the CalcMethod "percentage" row,
                            used to detect percentage-based deductions.

    Handles:
    - deductions_per_year (26/24/12) filtering based on 3rd paycheck
    - calc_method (flat vs percentage)
    - inflation adjustment
    - annual cap tracking
    """
    deductions = []
    if not profile.deductions:
        return deductions

    for ded in profile.deductions:
        if not ded.is_active:
            continue
        if ded.deduction_timing_id != timing_id:
            continue

        # Skip 24-per-year deductions on 3rd paychecks
        if ded.deductions_per_year == 24 and is_third_paycheck:
            continue

        # Skip 12-per-year deductions unless first paycheck of month
        if ded.deductions_per_year == 12:
            if not _is_first_paycheck_of_month(period, all_periods):
                continue

        # Calculate amount
        amount = Decimal(str(ded.amount))
        if ded.calc_method_id == calc_method_pct_id:
            amount = (gross_biweekly * amount).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )

        # Apply inflation if enabled
        if ded.inflation_enabled and ded.inflation_rate:
            inflation_rate = Decimal(str(ded.inflation_rate))
            eff_month = ded.inflation_effective_month or 1
            # Calculate years of inflation based on period date
            years = _inflation_years(period, profile, eff_month)
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

        salary = _apply_raises(profile, p)
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
