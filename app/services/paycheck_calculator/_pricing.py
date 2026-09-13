"""
Shekel Budget App -- Paycheck engine: PRICING one paycheck, and a list of them.

The two public entries: :func:`calculate_paycheck`, which prices ONE
paycheck by composing the other leaves in the order its own nine numbered
steps name -- the post-raise annual salary off the basis, the per-paycheck
gross rate, the deduction passes, the wage figures, the withholding path,
the net -- and :func:`project_salary`, the batch over a
period list that is nothing but a loop over the first with the tax configs
resolved per period year.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  This is the leaf that imports every other one; nothing else in
the package imports it.  The public names are re-exported by the package,
which is the door every caller uses.
"""

from collections.abc import Sequence

from app.services.pay_calendar import DerivedPeriod
from app.services.payroll_basis import PayrollBasis, gross_per_paycheck
from app.services.salary_raises import get_raise_event
from app.utils.money import ZERO, round_money

from ._breakdown import Earnings, PaycheckBreakdown, PeriodInfo
from ._calendar_questions import (
    _get_cumulative_wages,
    _is_third_paycheck,
    _month_ordinal,
)
from ._deductions import _compute_deductions, _DeductionContext
from ._withholding import _bracket_tax_lines, _calibrated_tax_lines, _WageBasis


def calculate_paycheck(basis: PayrollBasis, period: DerivedPeriod, tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

    The gross is the (post-raise) annual salary divided by
    ``basis.periods_per_year`` and rounded once, at the cent
    (:func:`~app.services.payroll_basis.gross_per_paycheck`).  It is a RATE:
    the same figure for every paycheck in one salary segment, and a function of
    the salary and the cadence alone -- the payday SET does not reach it.  See
    the package docstring section "The per-paycheck gross -- a RATE, not a share
    of a year" for what that replaced (plan step **balance:X-aw**, ruling
    **balance:R-HW**, superseding audit MED-05 / PA-07).

    Args:
        basis:        The :class:`~app.services.payroll_basis.PayrollBasis` --
                      this owner's salary profile (with its deductions) bound
                      to the pay CALENDAR their paychecks arrive on, naming
                      the RAISE SET the paycheck
                      is priced under (plan step salary:S3-f-1; read through
                      ``basis.raises``, never off the profile).  The calendar is
                      REQUIRED and carries both facts the engine needs beyond
                      the profile: the cadence it divides the salary by
                      (assuming biweekly would model a weekly-paid owner's
                      income at half its true value) and the payday set the
                      four calendar questions are counted over.  It was
                      a bare cadence beside an ``all_periods`` sequence until
                      plan step **balance:X-bh-1**; see the package docstring's
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
    # Step 1: Determine annual salary after raises (off the basis's raise set).
    annual_salary = basis.annual_salary_on(period.start_date)

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
            period.start_date, period.period_id,
            _is_third_paycheck(ded_ctx.month_ordinal),
            get_raise_event(basis.raises, period),
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
      :func:`app.services.tax_config_service.configs_by_year`
      and pass it in -- this module performs no DB access (purity contract).

    Args:
        basis:            The :class:`~app.services.payroll_basis.PayrollBasis`
                          -- the salary profile bound to its owner's pay
                          CALENDAR (plan steps R-F16 and balance:X-bh-1).
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
