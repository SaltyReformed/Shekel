"""
Shekel Budget App -- Paycheck Calculator Service

Core Phase 2 service: calculates net biweekly paycheck amounts from a
salary profile including raises, deductions, and taxes.

All functions are pure (no DB access) -- data is passed in as arguments.

A pay period here is a :class:`~app.services.pay_calendar.DerivedPeriod`, not a
``budget.pay_periods`` ORM row (pay-calendar plan step **C2-f2d-3**, whose
as-built record carries the census).  Only ``start_date`` and the period's
IDENTITY are read; ``end_date`` / ``period_index`` -- the columns plan step
**C4-c** dropped -- never were.  See :class:`PeriodInfo` for why that identity is
never ``None``.

The four calendar questions -- asked of the CALENDAR
----------------------------------------------------

Pricing a paycheck needs four facts that are not about the paycheck itself but
about where its payday SITS among this owner's other paydays:

* whether it is the THIRD payday of its calendar month, which is the one a
  24-per-year deduction skips;
* whether it is the FIRST, which is the only one a 12-per-year deduction is
  taken on;
* the gross this owner has already been paid this calendar year, which drives
  the FICA Social Security wage-base cap; and
* how much of a capped deduction has already been taken this calendar year.

**All four are answered from the owner's** :class:`~app.services.pay_calendar.PayCalendar`,
which :class:`~app.services.payroll_basis.PayrollBasis` carries -- through
exactly two producers,
:func:`~app.services.pay_calendar.paydays_in_month_through` and
:func:`~app.services.pay_calendar.paydays_in_year_before`.  **Until plan step
balance:X-bh-1 they were answered from an ``all_periods`` SEQUENCE the caller
supplied**, and the four had four separate scans of it.  The type was
``Sequence[DerivedPeriod]``, so a window, a year slice and a one-to-three
period sample all satisfied it while under-counting every one of the four:
measured at ``$502.45`` on one stored salary row when a schedule extend handed
the engine only its newly created periods (ledger row **D25**).  A calendar can
be built only from a COMPLETE payday set, so that argument is now
unrepresentable rather than refused in prose -- finding **N-390**'s first half.

**The SECOND half of N-390 closed at plan step balance:X-bh-2** (ruling
**balance:R-IA**, amended 2026-08-31): both producers project the rhythm
BACKWARD below the schedule's opening payday as well as forward past its
horizon, bounded by ``budget.pay_schedule.history_opens_on`` -- a stored fact
the registration form and the pay-periods settings section ask for, because the
app knows the CADENCE and cannot derive when a job began.  Until then a month
or a calendar year the record opened INSIDE was counted from the first RECORDED
payday: the owner's 2026 year-to-date gross for 2026-05-21 read ``$14,103.84``
-- four recorded paydays at ``$3,525.96`` -- against the ``$31,733.64`` of the
NINE he was really paid, which is what it reads once he states his opening.

**An owner who has stated nothing keeps the old reading, and that is the
amendment.**  ``NULL`` means NOT STATED, so the backward half answers nothing
for every owner nobody has asked.  Why that is the right default rather than
"back to ``CALENDAR_DATE_MIN``" is argued where the fact lives --
``budget.pay_schedule.history_opens_on``'s own column comment -- and turns on
DIRECTION: over-counting a year-to-date retires the FICA wage base and exhausts
an ``annual_cap`` early, understating the deduction and the tax and so
OVERSTATING net.  An application that budgets should guess poor.

*Stated further back than his own opening it reads TEN, not nine*, because the
rhythm steps from 2026-03-26 onto 2026-01-01 and that paycheck was really paid
**2025-12-31**, New Year's Day being a holiday (developer, 2026-08-30).  All 63
of his saved gaps are exactly 14 days, so the app models no shift at all: a
cadence projection can be wrong at exactly the year boundary a calendar-year
cumulative turns on.  That is ledger row **N-398** rather than a silence.

It moves ``$0.00`` on this owner's data either way -- measured 2026-08-30 by
pricing all 63 of his paychecks on both trees -- because he has no 12-per-year
deduction, no ``annual_cap``, and ``$91,675`` against a ``$184,500`` wage base.
The counts underneath DO move once he states his opening: 2026-03-26 goes from
his month's first paycheck to its second, and the 2026 year-to-date at
2026-12-31 from 20 paydays to 26.  With one deduction set to 12-per-year and
one capped at ``$1,200``, the same harness moves net **UP by ``$1,190.54``**
across four paychecks -- up, because both mechanisms REMOVE a deduction.  That
is what the ``$0.00`` is a property of, and what it is not.

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
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendarError,
    paydays_in_month_through,
    paydays_in_year_before,
)
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
    annual-cap cumulative replays prior paydays' grosses through
    :func:`~app.services.payroll_basis.gross_per_paycheck`, which needs the
    paycheck COUNT, and it walks those prior paydays off the calendar the same
    value carries.

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
    basis: "PayrollBasis"
    period: DerivedPeriod
    gross_biweekly: Decimal
    month_ordinal: int


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


def calculate_paycheck(basis: PayrollBasis, period: DerivedPeriod, tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

    The gross is the (post-raise) annual salary divided by
    ``basis.periods_per_year`` and rounded once, at the cent
    (:func:`~app.services.payroll_basis.gross_per_paycheck`).  It is a RATE:
    the same figure for every paycheck in one salary segment, and a function of
    the salary and the cadence alone -- the payday SET does not reach it.  See
    the module docstring section "The per-paycheck gross -- a RATE, not a share
    of a year" for what that replaced (plan step **balance:X-aw**, ruling
    **balance:R-HW**, superseding audit MED-05 / PA-07).

    Args:
        basis:        The :class:`PayrollBasis` -- this owner's salary profile
                      (with loaded raises and deductions) bound to the pay
                      CALENDAR their paychecks arrive on.  The calendar is
                      REQUIRED and carries both facts the engine needs beyond
                      the profile: the cadence it divides the salary by
                      (assuming biweekly would model a weekly-paid owner's
                      income at half its true value) and the payday set the
                      four calendar questions above are counted over.  It was
                      a bare cadence beside an ``all_periods`` sequence until
                      plan step **balance:X-bh-1**; see the module docstring's
                      "The four calendar questions" for what a partial
                      sequence cost.
        period:       The ``DerivedPeriod`` this paycheck is for.
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
    # rounded once.  Deliberately NOT a function of the payday SET: that is
    # what plan step balance:X-aw removed (finding N-239).
    gross_biweekly = gross_per_paycheck(annual_salary, basis.periods_per_year)

    # Steps 3-4 & 8: this payday's position in its month plus the pre- and
    # post-tax deduction passes (all three share one per-paycheck context).
    ded_ctx = _DeductionContext(
        basis, period, gross_biweekly,
        _month_ordinal(basis.calendar, period.start_date),
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
        _get_cumulative_wages(basis, period),
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
            period.period_id, _is_third_paycheck(ded_ctx.month_ordinal),
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
                          to its owner's pay CALENDAR (plan steps R-F16 and
                          balance:X-bh-1).
        periods:          The paychecks to price, and ONLY that.  It was also
                          handed to each :func:`calculate_paycheck` as its
                          ``all_periods`` until plan step **balance:X-bh-1**,
                          so a caller passing a year slice was choosing the
                          engine's month and year context as well as its
                          output -- two questions on one argument, and the one
                          that could be got wrong was silent.  The context now
                          comes off ``basis.calendar``, so this list may be any
                          subset in any order and every breakdown is the same
                          as it would be alone.
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
            basis, period,
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


def _month_ordinal(calendar, payday):
    """Return this payday's 1-based position among the paydays of its month.

    The ONE calendar read behind both month-position judgements.  With biweekly
    pay most months hold two paydays and twice a year one holds three, so the
    ordinal is 1, 2 or 3 -- but it is derived rather than assumed, because
    ``budget.pay_schedule.cadence_days`` is user-selectable 1..365 and a
    daily-paid owner's month holds about thirty.

    It reads the CALENDAR, so the answer is a property of the owner's whole
    schedule rather than of whichever periods a caller was holding: see the
    module docstring's "The four calendar questions" for what the second
    shape cost.

    **It takes a payday rather than a period**, which is what lets the
    annual-cap cumulative call it too: that walk holds bare ``date`` values
    off :func:`~app.services.pay_calendar.paydays_in_year_before`, so a
    period-keyed signature left it spelling the count itself -- two spellings
    of one rule in one file, under a docstring claiming to be the only one.
    An adversarial review of this step measured that; the signature is the
    fix.

    **It REFUSES a period this calendar cannot place**, and an adversarial
    review of this step is why: the count is over the CALENDAR's paydays, not
    over the argument, so a period paired with the wrong owner's calendar was
    answered silently -- measured at 0, 1 and 2 for three foreign paydays in
    one month, each a different wrong answer to the deduction cadence, and 0
    is the reading that skips a 12-per-year deduction and takes a 24-per-year
    one on a third paycheck.  :class:`PayrollBasis` makes a narrow payday SET
    unrepresentable; it cannot make a period/calendar MISMATCH unrepresentable,
    because the period arrives separately.  So the mismatch is refused where it
    is detectable rather than described in a docstring, which is the standing
    :func:`~app.services.pay_calendar._views.axis_window`'s own refusal has --
    no caller in ``app/`` reaches it, and it guards the value against one
    assembled by hand.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        payday: The day the paycheck arrives.  Must be one this owner is paid
            on -- saved, or projected forward at their cadence.

    Returns:
        The 1-based ordinal, never 0.

    Raises:
        PayCalendarError: This owner is not paid on *payday*, so there is no
            position in the month to answer with.  Plan step **balance:X-bh-2**
            NARROWED what reaches here and did not remove it: the rhythm runs
            below the opening payday for an owner who has STATED one, so a
            day on their own phase and at or above their stated opening is
            placed -- the day 2026-03-12 that used to raise for the developer
            prices as March's first paycheck once he states it.  What is left
            is a day OFF that phase, a day below the stated opening, and every
            day below the record for an owner who has stated nothing.
            **For a stated owner this is a WEAKER cross-owner guard and the
            cost is countable**: at a shared cadence one payday in
            ``cadence_days`` lands on their phase, so a mispairing that was
            refused unconditionally is refused about thirteen times in
            fourteen.  For an unstated owner -- which is every owner until
            they answer -- the old strictness is unchanged.
    """
    paydays = paydays_in_month_through(calendar, payday)
    if not paydays or paydays[-1] != payday:
        raise PayCalendarError(
            f"user {calendar.user_id} is not paid on {payday.isoformat()}, "
            f"so that paycheck has no position among the paydays of its "
            f"month and the deduction cadence cannot be answered.  Their "
            f"calendar holds {len(calendar.periods)} payday(s) and "
            f"{len(paydays)} in that month at or before it.  A paycheck is "
            f"priced against the calendar it belongs to; pairing one owner's "
            f"period with another's schedule, or naming a day off their "
            f"cadence or below the day their paychecks began, reaches here."
        )
    return len(paydays)


def _is_third_paycheck(month_ordinal):
    """Whether a payday at this month position is its month's THIRD or later.

    **The rule written once**, for the two consumers that ask it: the deduction
    cadence below (a 24-per-year deduction is not taken on it) and
    :attr:`PeriodInfo.is_third_paycheck`, which the salary cockpit, the
    projection ledger and the paycheck-anatomy fragment all render.  Two
    spellings of ``>= 3`` would be two places for the boundary to move.

    ``>=`` rather than ``==`` because a cadence shorter than fourteen days puts
    a fourth, fifth or thirtieth payday in a month, and a 24-per-year deduction
    is taken on the first two of them and no more.

    Args:
        month_ordinal: The payday's 1-based position in its calendar month, as
            :func:`_month_ordinal` returns it.

    Returns:
        ``True`` when the payday is the month's third or later.
    """
    return month_ordinal >= 3


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

    Mirrors :func:`_get_cumulative_wages` (the FICA wage-base precedent): walk
    the calendar's paydays for this year before ``ctx.period``, skip the ones
    where the deduction is not taken, and sum each applicable payday's raw
    amount -- recomputing that paycheck's gross through
    :func:`~app.services.payroll_basis.gross_per_paycheck` so a percentage
    deduction tracks the raise-adjusted gross exactly as the live paycheck
    does.  Summing the raw (pre-cap) amounts is equivalent to summing
    the capped ones (see ``cap_period_amount``), so no capped running state has
    to be threaded across the per-paycheck calls.

    **The paydays come from ``ctx.basis.calendar``** since plan step
    **balance:X-bh-1**, through
    :func:`~app.services.pay_calendar.paydays_in_year_before` -- the same
    producer :func:`_get_cumulative_wages` reads, so the two cumulatives cannot
    be summing different years of the same owner.  It was ``ctx.all_periods``,
    where a partial-context caller under-counted the cumulative and DEFERRED
    the cap, so the deduction went on being charged after it should have
    stopped.

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
        salary = apply_raises(profile.annual_salary, profile.raises, payday)
        gross = gross_per_paycheck(salary, basis.periods_per_year)
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


def _get_cumulative_wages(basis, period):
    """Return the gross this owner has been paid this year before *period*.

    What the FICA Social Security wage-base cap and the Medicare surtax
    threshold are measured against, on BOTH tax paths (CRIT-03 / F-037).

    **The paydays come from ``basis.calendar``** since plan step
    **balance:X-bh-1**, through
    :func:`~app.services.pay_calendar.paydays_in_year_before`.  It was an
    ``all_periods`` sequence a caller supplied, and the year-scoping and the
    ordering were both done here -- a filter on the year, a sort, and a break
    -- where the producer now guarantees both.

    **It reads BELOW the schedule's opening payday since plan step
    balance:X-bh-2**, which closed ledger row **N-390** -- for an owner who has
    STATED when their paychecks began.  Such an owner is no longer summed from
    the record's boundary, so the wage-base cap is reached when their wages
    reach it rather than late: the 2026 total for 2026-05-21 goes from
    ``$14,103.84`` -- four recorded paydays -- to the nine the developer was
    really paid, once he states his own opening.  An owner who has stated
    nothing is summed from the record exactly as before, which is the ruling's
    2026-08-31 amendment and the conservative direction.

    Args:
        basis: The :class:`PayrollBasis` -- its profile prices each paycheck
            and its calendar supplies the paydays.
        period: The period being priced.  Its payday bounds the sum, which is
            STRICTLY before it, and its year is the window.

    Returns:
        The summed gross, ``ZERO`` for the year's first paycheck.
    """
    profile = basis.profile
    cumulative = ZERO

    for payday in paydays_in_year_before(basis.calendar, period.start_date):
        salary = apply_raises(profile.annual_salary, profile.raises, payday)
        # The SAME producer ``calculate_paycheck`` prices a paycheck with, so
        # the earlier grosses summed here match the per-period
        # ``gross_biweekly`` by construction rather than by two expressions
        # happening to agree.
        cumulative += gross_per_paycheck(salary, basis.periods_per_year)

    return cumulative
