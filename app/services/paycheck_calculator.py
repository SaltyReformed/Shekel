"""
Shekel Budget App -- Paycheck Calculator Service

Core Phase 2 service: calculates net biweekly paycheck amounts from a
salary profile including raises, deductions, and taxes.

All functions are pure (no DB access) -- data is passed in as arguments.

A pay period here is a :class:`~app.services.pay_calendar.DerivedPeriod`, not a
``budget.pay_periods`` ORM row (pay-calendar plan step **C2-f2d-3**, whose
as-built record carries the census).  Only ``start_date`` and the period's
IDENTITY are read; ``end_date`` / ``period_index`` -- the columns plan step
**C4** drops -- never were.  See :class:`PeriodInfo` for why that identity is
never ``None``.

The per-paycheck gross -- a RATE, not a share of a year
-------------------------------------------------------

``gross_biweekly`` is the (post-raise) annual salary divided by the owner's
PAYCHECK COUNT and rounded once, at the cent.  The division lives in ONE place
for the whole application, :func:`app.services.payroll_basis.gross_per_paycheck`,
which carries the argument for the rule and the measurements behind it.

The paycheck count is :attr:`PayrollBasis.periods_per_year`, derived from the
owner's pay cadence and from nothing else since plan step **R-F16**; that class
carries what the second stored count cost (finding **F-16**).

Two properties follow, and they are the point:

* Every paycheck in one salary segment pays the SAME figure.
* The figure is a function of the salary and the cadence ALONE.  No period and
  no period LIST reach it, so nothing a schedule extend can do will move it.

**The second property is the whole argument, and the first is NOT evidence for
this fork** -- an adversarial review of the design corrected a first draft that
made it one.  A flat per-paycheck figure is what a real stub shows, and the
owner's does show one; but the superseded rule is ALSO flat whenever the annual
salary divides evenly, so "real stubs are flat" argues for correcting the
salary input (plan step **X-av**, finding **N-391**) and not for deleting the
residue distribution.  What decides THIS fork is that the residue had to be
apportioned, apportioning it needed an ordinal, and the only ordinal available
was a count of rows that happen to exist.

**This replaced a residue-distribution contract at plan step balance:X-aw**
(ruling **balance:R-HW**, 2026-08-29), superseding audit MED-05 / PA-07 --
which had itself superseded F-127's "accepted simplification" -- and closing
finding **N-239**.  MED-05 spread the annual quantisation residue over the
periods of a calendar year so the year summed to the annual salary exactly.
Deciding WHICH paychecks got the extra cent required knowing where a period sat
among its year's paychecks, and the only thing the engine had to count was the
``budget.pay_periods`` rows that happened to exist -- so filling 2028 from 16
rows to 26 moved six already-settled paychecks by a cent each.  N-239 is now
unrepresentable rather than guarded: there is no group, no ordinal, no
partial-context fallback and no list.

**What that gives up, stated because it is a real cost**: a calendar year's
grosses no longer sum to the annual salary exactly.  The bound is half a cent
per paycheck -- ``0.005 x periods_per_year``, so ``$0.13`` at a biweekly
cadence and ``$1.83`` at the daily one ``budget.pay_schedule`` legally admits.
On the owner's own salary, ``26 x $3,525.96 = $91,674.96``, four cents under.

*Two figures that belong to this paragraph are deliberately NOT here, because
an adversarial review found the first draft conflating them.*  ``-$0.03``
(2026), ``-$0.05`` (2027) and ``+$0.10`` (2028) are what the owner's schedule
moves BY, measured 2026-08-30 as this rule's year total minus the superseded
rule's -- they are not the distance from the annual salary, which for 2026 is
``+$5,006.84`` because a July raise splits the year and it holds 27 paydays.

Giving the identity up is the honest answer rather than a regression, because
the identity MED-05 enforced is not one payroll honours.  The employer's flat ``$3,526.00`` sums to
``$91,676.00`` over 26 paychecks against the ``$91,675.00`` the profile holds
-- and that stub is dated inside a 27-payday year while reading ``annual / 26``
rather than ``annual / 27``, so this employer demonstrably does NOT re-divide in
such a year.  Roughly one calendar year in eleven holds 27 biweekly paydays and
simply pays 27 of them.  **2026 is one on this owner's payday phase** -- 2026-01-01
through 2026-12-31 -- and driving MED-05's rule over it at a FLAT
``$91,675.00`` (no mid-year raise, so the whole year is one reconciliation
group) pays ``$95,200.96``: a full extra paycheck above the salary its own
docstring claimed the year would equal.

**The STORED input is still the annual salary, and plan step balance:X-av flips
it** to a dated per-paycheck gross with the annual derived (ruling R-HW).  The
contract stated here -- a constant rate per paycheck, independent of the
schedule -- is what survives that flip unchanged; only the input improves.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, DeductionTimingEnum
from app.services import tax_calculator
from app.services.calibration_service import apply_calibration
from app.services.pay_calendar import DerivedPeriod
from app.services.payroll_basis import PayrollBasis, gross_per_paycheck
from app.services.salary_raises import apply_raises, get_raise_event
from app.utils.deduction_cap import cap_period_amount
from app.utils.money import round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


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
    """Pay-period identity and per-paycheck event flags.

    Attributes:
        period_id: ``budget.pay_periods.id``.  **Never ``None`` structurally**
            -- three consumers KEY on it.  Its three producers are safe two
            ways: ``saved`` / ``period_containing`` FILTER to materialised
            periods; ``period_by_id`` keys on a non-``None`` int.
        is_third_paycheck: Whether this is the third paycheck starting in its
            calendar month, which is what a 24-per-year deduction skips.
        raise_event: The raise taking effect in this period, as the label
            :func:`get_raise_event` composes, or ``""``.
    """
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
    """Immutable inputs shared by the pre- and post-tax deduction passes.

    Carries the whole :class:`PayrollBasis` rather than a bare profile: the
    annual-cap cumulative replays prior periods' grosses through
    :func:`~app.services.payroll_basis.gross_per_paycheck`, which needs the
    paycheck COUNT too.
    """
    basis: "PayrollBasis"
    period: DerivedPeriod
    all_periods: Sequence[DerivedPeriod]
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


def calculate_paycheck(basis: PayrollBasis, period: DerivedPeriod,
                       all_periods: Sequence[DerivedPeriod], tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

    The gross is the (post-raise) annual salary divided by
    ``basis.periods_per_year`` and rounded once, at the cent
    (:func:`~app.services.payroll_basis.gross_per_paycheck`).  It is a RATE:
    the same figure for every paycheck in one salary segment, and a function of
    the salary and the cadence alone -- ``all_periods`` does not reach it.  See
    the module docstring section "The per-paycheck gross -- a RATE, not a share
    of a year" for what that replaced (plan step **balance:X-aw**, ruling
    **balance:R-HW**, superseding audit MED-05 / PA-07).

    Args:
        basis:        The :class:`PayrollBasis` -- this owner's salary profile
                      (with loaded raises and deductions) bound to the cadence
                      their paychecks arrive on.  The cadence is REQUIRED: it
                      is the divisor, and assuming biweekly would model a
                      weekly-paid owner's income at half its true value.
        period:       The ``DerivedPeriod`` this paycheck is for.
        all_periods:  The owner's WHOLE saved schedule (``calendar.saved()``
                      for every direct caller): 3rd-paycheck detection, the
                      first-paycheck-of-month cadence, the FICA cumulative and
                      a deduction's annual cap all read it, and a partial set
                      under-counts every one -- ``$502.45`` on one stored row
                      when the recurrence arc measured it (ledger row **D25**).
                      **Those four are all that read it, and each is still
                      horizon-dependent** -- ledger row **N-390**; the gross
                      stopped reading it at plan step **balance:X-aw**.  A
                      SEQUENCE, not the window type: :func:`project_salary`
                      passes a year slice by design.
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
    profile = basis.profile
    annual_salary = apply_raises(profile.annual_salary, profile.raises, period.start_date)

    # Step 2: Gross biweekly -- the salary over the owner's paycheck count,
    # rounded once.  Deliberately NOT a function of ``all_periods``: that is
    # what plan step balance:X-aw removed (finding N-239).
    gross_biweekly = gross_per_paycheck(annual_salary, basis.periods_per_year)

    # Steps 3-4 & 8: 3rd-paycheck detection plus the pre- and post-tax
    # deduction passes (both share the same per-paycheck context).
    ded_ctx = _DeductionContext(
        basis, period, all_periods, gross_biweekly,
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
        _get_cumulative_wages(basis, period, all_periods),
    )
    if calibration is not None and getattr(calibration, "is_active", False):
        taxes = _calibrated_tax_lines(
            wages, calibration, tax_configs.get("fica_config"),
        )
    else:
        taxes = _bracket_tax_lines(
            basis, wages, deductions.total_pre_tax, tax_configs,
        )

    # Step 9: Net pay.
    net_pay = round_money(
        gross_biweekly
        - deductions.total_pre_tax
        - taxes.total
        - deductions.total_post_tax
    )

    return PaycheckBreakdown(
        period=PeriodInfo(
            period.period_id, ded_ctx.is_third_paycheck,
            get_raise_event(profile, period),
        ),
        earnings=Earnings(annual_salary, gross_biweekly, taxable_biweekly, net_pay),
        taxes=taxes,
        deductions=deductions,
    )


def project_salary(basis: PayrollBasis, periods: Sequence[DerivedPeriod],
                   tax_configs=None, *,
                   configs_by_year=None, calibration=None):
    """Generate paycheck breakdowns for all given periods.

    Exactly one tax-config source must be supplied:

    * ``tax_configs`` -- ONE config set applied to every period.  Correct
      when every period is in the same tax year (the year-end summary, the
      route previews, and the unit tests that hand-build a config dict).
    * ``configs_by_year`` -- a ``{tax_year: config set}`` mapping; each
      period is calculated with ``configs_by_year[period.start_date.year]``.
      This is the multi-year projection path: a ~2-year horizon spans more
      than one tax year, so each period must use its own year's brackets
      and FICA wage base/cap, matching the recurrence engine that generates
      the stored grid amounts (DH-#30).  Callers resolve the mapping via
      :func:`app.services.tax_config_service.load_tax_configs_for_periods`
      and pass it in -- this module performs no DB access (purity contract).

    Args:
        basis:            The :class:`PayrollBasis` -- the salary profile bound
                          to its owner's pay cadence (plan step R-F16).
        periods:          The periods to project, ALSO handed to each
                          :func:`calculate_paycheck` as its ``all_periods``,
                          so a year-scoped caller gets that year as the
                          cumulative context.
        tax_configs:      dict with bracket_set, state_config, fica_config,
                          or ``None`` when ``configs_by_year`` is given.
        configs_by_year:  ``{tax_year: configs dict}`` mapping covering
                          every year present in ``periods``, or ``None``
                          when ``tax_configs`` is given.
        calibration:      Optional CalibrationOverride for rate-based taxes.

    Returns:
        List of PaycheckBreakdown, one per period.

    Raises:
        ValueError: if not exactly one of ``tax_configs`` /
            ``configs_by_year`` is supplied.
    """
    if (tax_configs is None) == (configs_by_year is None):
        raise ValueError(
            "project_salary requires exactly one of tax_configs or "
            "configs_by_year"
        )
    return [
        calculate_paycheck(
            basis, period, periods,
            tax_configs if tax_configs is not None
            else configs_by_year[period.start_date.year],
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


def _bracket_tax_lines(basis, wages, total_pre_tax, tax_configs):
    """Compute the four withholding lines from IRS Pub 15-T brackets plus FICA.

    The cumulative YTD gross on ``wages`` feeds the FICA SS wage-base cap so
    it is enforced identically to the calibration path (CRIT-03 / F-037).

    Args:
        basis: The :class:`PayrollBasis` -- read for the W-4 federal inputs and
            for the denominator Pub 15-T annualises a period's wages by.
        wages: The per-paycheck :class:`_WageBasis` (gross, taxable, and the
            cumulative YTD gross that drives the SS wage-base cap).
        total_pre_tax: Per-period pre-tax deduction total (annualised for
            the bracket federal calculation).
        tax_configs: dict with bracket_set, state_config, fica_config.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts.
    """
    pay_periods_per_year = basis.periods_per_year
    bracket_set = tax_configs.get("bracket_set")
    federal = (
        _bracket_federal(
            basis.profile, wages.gross_biweekly, pay_periods_per_year,
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
        pay_periods_per_year: The full-year denominator IRS Pub 15-T
            annualises against, off :attr:`PayrollBasis.periods_per_year`.
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
        pay_periods_per_year: The full-year denominator the annual state tax
            is computed over and divided back by, off
            :attr:`PayrollBasis.periods_per_year`.
        state_config: The StateTaxConfig (or None).

    Returns:
        Decimal biweekly state withholding.
    """
    state_annual = tax_calculator.calculate_state_tax(
        taxable_biweekly * pay_periods_per_year, state_config
    )
    return round_money(state_annual / pay_periods_per_year)


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
        ctx: The per-paycheck :class:`_DeductionContext` (basis, period,
            all_periods, gross_biweekly, is_third_paycheck).
        timing_id: Integer ID of the DeductionTiming to filter on.

    Handles:
    - deductions_per_year (26/24/12) filtering based on 3rd paycheck
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
        if not _deduction_applies_in_period(
            ded, ctx.period, ctx.all_periods, ctx.is_third_paycheck
        ):
            continue

        amount = _raw_deduction_amount(
            ded, ctx.gross_biweekly, ctx.period, profile, pct_id
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


def _deduction_applies_in_period(ded, period, all_periods, is_third_paycheck):
    """Whether a deduction is taken in a given period by its per-year cadence.

    26-per-year deductions apply every period; 24-per-year skip the 3rd
    paycheck of a month; 12-per-year apply only on the first paycheck of the
    month.  Shared by the line-building pass and the annual-cap cumulative so a
    period the deduction skips contributes nothing to either.
    """
    if ded.deductions_per_year == 24 and is_third_paycheck:
        return False
    if ded.deductions_per_year == 12:
        return _is_first_paycheck_of_month(period, all_periods)
    return True


def _raw_deduction_amount(ded, gross_biweekly, period, profile, pct_id):
    """Per-period deduction amount before any annual-cap clamp.

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
    reproduces, for prior periods, the exact amount the loop applies to
    the current one.
    """
    amount = Decimal(str(ded.amount))
    if ded.calc_method_id == pct_id:
        amount = gross_biweekly * amount
    if ded.inflation_enabled and ded.inflation_rate:
        inflation_rate = Decimal(str(ded.inflation_rate))
        eff_month = ded.inflation_effective_month or 1
        years = _inflation_years(period, profile, eff_month)
        if years > 0:
            amount = amount * (1 + inflation_rate) ** years
    return round_money(amount)


def _cumulative_deduction_before(ded, ctx, pct_id):
    """Sum a deduction's raw amounts for the prior periods in the same year.

    Mirrors :func:`_get_cumulative_wages` (the FICA wage-base precedent): walk
    the same-year periods that start before ``ctx.period``, skip the ones where
    the deduction is not taken, and sum each applicable period's raw amount --
    recomputing that period's gross through
    :func:`~app.services.payroll_basis.gross_per_paycheck` so a percentage
    deduction tracks the raise-adjusted gross exactly as the live paycheck
    does.  Summing the raw (pre-cap) amounts is equivalent to summing
    the capped ones (see ``cap_period_amount``), so no capped running state has
    to be threaded across the per-period calls.

    Like the FICA cap, the cumulative is read from ``ctx.all_periods``; a
    partial-context caller (route preview, isolated fixture) that omits earlier
    periods under-counts it and defers the cap -- the same documented limitation
    :func:`_get_cumulative_wages` carries, and the same one ledger row
    **N-390** owns.  It is a genuine horizon dependence and NOT the one plan
    step **balance:X-aw** removed: the per-period GROSS no longer reads a
    period set at all, but WHICH prior paychecks exist to sum still comes from
    this list.
    """
    period_year = ctx.period.start_date.year
    profile = ctx.basis.profile
    cumulative = ZERO
    for p in sorted(ctx.all_periods, key=lambda p: p.start_date):
        if p.start_date.year != period_year:
            continue
        if p.start_date >= ctx.period.start_date:
            break
        if not _deduction_applies_in_period(
            ded, p, ctx.all_periods, _is_third_paycheck(p, ctx.all_periods),
        ):
            continue
        salary = apply_raises(
            profile.annual_salary, profile.raises, p.start_date,
        )
        gross = gross_per_paycheck(salary, ctx.basis.periods_per_year)
        cumulative += _raw_deduction_amount(ded, gross, p, profile, pct_id)
    return cumulative


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


def _get_cumulative_wages(basis, period, all_periods):
    """Calculate cumulative gross wages for the year up to (but not including) this period.

    Periods are sorted by start_date before iteration so the break
    condition works correctly regardless of input order (M-02).

    Used for FICA SS wage base cap tracking.

    **It reads *all_periods* and is therefore horizon-dependent** -- ledger row
    **N-390**.  An owner whose schedule opens mid-year has no rows for the
    paychecks before it, so the year-to-date total under-counts and the SS
    wage-base cap is reached late: measured 2026-08-29 at ``$14,103.84`` for
    2026-05-21 against ``$35,259.62`` from a complete 2026.  Plan step
    **balance:X-aw** removed that dependence from the per-period GROSS, which
    is a different question; this one is about which paychecks EXIST to sum.
    """
    profile = basis.profile
    period_year = period.start_date.year
    cumulative = ZERO

    for p in sorted(all_periods, key=lambda p: p.start_date):
        if p.start_date.year != period_year:
            continue
        if p.start_date >= period.start_date:
            break

        salary = apply_raises(profile.annual_salary, profile.raises, p.start_date)
        # The SAME producer ``calculate_paycheck`` prices a paycheck with, so
        # the prior-period grosses summed here match the per-period
        # ``gross_biweekly`` by construction rather than by two expressions
        # happening to agree.
        gross = gross_per_paycheck(salary, basis.periods_per_year)
        cumulative += gross

    return cumulative
