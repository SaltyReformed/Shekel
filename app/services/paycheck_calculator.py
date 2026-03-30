"""
Shekel Budget App -- Paycheck Calculator Service

Core Phase 2 service: calculates net biweekly paycheck amounts from a
salary profile including raises, deductions, and taxes.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from app.services import tax_calculator
from app.services.calibration_service import apply_calibration

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


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


def calculate_paycheck(profile, period, all_periods, tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

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

    # Step 2: Gross biweekly
    pay_periods_per_year = profile.pay_periods_per_year or 26
    gross_biweekly = (annual_salary / pay_periods_per_year).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )

    # Step 3: Detect 3rd paycheck
    is_third = _is_third_paycheck(period, all_periods)

    # Resolve deduction timing and calc method IDs from the startup cache.
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import CalcMethodEnum, DeductionTimingEnum  # pylint: disable=import-outside-toplevel

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

    if use_calibration:
        # Use effective rates from the pay stub calibration.
        cal_taxes = apply_calibration(
            gross_biweekly, taxable_biweekly, calibration
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
        cumulative_wages = _get_cumulative_wages(
            profile, period, all_periods
        )
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
        gross = (salary / pay_periods_per_year).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        cumulative += gross

    return cumulative
