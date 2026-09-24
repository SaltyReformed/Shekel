"""
Shekel Budget App -- Paycheck engine: PRICING one paycheck, and a list of them.

The two public entries: :func:`calculate_paycheck`, which prices ONE
paycheck by composing the other leaves in the order its own numbered steps
name -- the payday's base pay off the basis (the post-raise annual, the
paychecks a year its rhythm pays, and the per-paycheck rate they make), the
taxable earning lines and the gross they make, the deduction
passes, the wage figures, the withholding path, the after-tax earning lines,
the net -- and :func:`project_salary`, the batch over a period list that is
nothing but a loop over the first with the tax configs resolved per period
year.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  This is the leaf that imports every other one; nothing else in
the package imports it.  The public names are re-exported by the package,
which is the door every caller uses.
"""

from collections.abc import Sequence

from app.services.pay_calendar import DerivedPeriod
from app.services.payroll_basis import PayrollBasis
from app.services.salary_raises import get_raise_event
from app.utils.money import ZERO

from ._breakdown import Earnings, PaycheckBreakdown, PeriodInfo, waterfall_net
from ._calendar_questions import (
    _get_cumulative_wages,
    _is_third_paycheck,
    _month_ordinal,
)
from ._lines import (
    _compute_deductions,
    _LineContext,
    priced_after_tax,
    priced_gross,
)
from ._withholding import _tax_lines, _WageBasis


def calculate_paycheck(basis: PayrollBasis, period: DerivedPeriod, tax_configs,
                       *, calibration=None):
    """Calculate a single paycheck for a given period.

    The BASE pay is the (post-raise) annual salary divided by the paychecks a
    year of the rhythm in force on the payday, and rounded once, at the cent
    (:func:`~app.services.payroll_basis.gross_per_paycheck`, read through
    :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on` since plan
    step **salary:X-av-2**).  It is a RATE:
    the same figure for every paycheck in one salary segment, and a function of
    the salary and the cadence alone -- the payday SET does not reach it.  See
    the package docstring section "The per-paycheck gross -- a RATE, not a share
    of a year" for what that replaced (plan step **balance:X-aw**, ruling
    **balance:R-HW**, superseding audit MED-05 / PA-07).  **The gross is the
    base plus the taxable earning lines admitted on the payday, since plan
    step salary:R18-b** (ruling **R-SAL38**), and the net adds the after-tax
    earning lines after the withholding; both come from the one pass that
    prices the deductions, through :mod:`._lines`.

    Args:
        basis:        The :class:`~app.services.payroll_basis.PayrollBasis` --
                      this owner's salary profile (with its deductions) bound
                      to the pay CALENDAR their paychecks arrive on, naming
                      the RAISE SET the paycheck
                      is priced under (plan step salary:S3-f-1; read through
                      ``basis.raises``, never off the profile).  The calendar is
                      REQUIRED and carries both facts the engine needs beyond
                      the profile: the cadence it divides the salary by --
                      the era's in force on the payday, since plan step
                      salary:X-av-2 (assuming biweekly would model a
                      weekly-paid owner's income at half its true value) --
                      and the payday set the
                      calendar questions are counted over -- and, since plan
                      step salary:R15-b, the calendar each deduction's
                      cadence rule is resolved against.  It was
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
    # Steps 1-2: the payday's base pay, read ONCE -- the post-raise annual
    # (off the basis's raise set), the paychecks a year of the rhythm in force
    # on the payday (plan step salary:X-av-2), and the rate they make, rounded
    # once.  Deliberately NOT a function of the payday SET: that is what plan
    # step balance:X-aw removed (finding N-239).  The rate is the base every
    # percentage line is a percentage of (ruling R-SAL38); the count is what
    # the withholding below annualises by.
    base_pay = basis.base_pay_on(period.start_date)
    base_biweekly = base_pay.per_paycheck

    # Step 3: this payday's position in its month -- read BEFORE any line is
    # priced, because the read is where a payday this calendar cannot place
    # is REFUSED (``_month_ordinal`` raises), and a refusal that came after
    # the deduction passes would let them price a foreign payday first (an
    # adversarial review of plan step salary:R15-b, which moved each line's
    # cadence off the ordinal and onto its rule).  What survives of the
    # ordinal's own readers is the cockpit's third-paycheck badge below.
    month_ordinal = _month_ordinal(basis.calendar, period.start_date)

    # Step 3b: the gross -- base pay plus the taxable earning lines admitted
    # on this payday (plan step salary:R18-b), from the ONE producer the
    # year-to-date wage cumulative replays for the earlier paydays.  Every
    # kind's pass shares this per-paycheck context; each line's cadence is
    # its rule's answer through the basis (plan step salary:R15-b).
    line_ctx = _LineContext(basis, period.start_date, base_biweekly)
    taxable_lines, gross_biweekly = priced_gross(line_ctx)

    # Steps 4 & 8: the pre- and post-tax deduction passes.
    deductions = _compute_deductions(line_ctx)

    # Step 5: Taxable income (for display -- taxes computed via Pub 15-T).
    taxable_biweekly = max(gross_biweekly - deductions.total_pre_tax, ZERO)

    # Steps 6-7: Tax calculation -- calibrated or bracket-based.  Both
    # paths read the same wage figures; the cumulative YTD gross is
    # computed once here and feeds the FICA SS wage-base cap on both paths
    # (CRIT-03 / F-037: the calibration path used to skip this and
    # over-charged SS after the cap on high earners).
    taxes = _tax_lines(
        basis,
        _WageBasis(
            gross_biweekly,
            taxable_biweekly,
            _get_cumulative_wages(basis, period),
            base_pay.periods_per_year,
        ),
        deductions.total_pre_tax, tax_configs, calibration,
    )

    # Step 8b: the after-tax earning lines -- untaxed, joining the deposit
    # after every deduction and withholding (plan step salary:R18-b).
    after_tax_lines = priced_after_tax(line_ctx)

    # Step 9: Net pay, through the waterfall a transcribed pay stub shares.
    net_pay = waterfall_net(
        gross_biweekly, deductions.total_pre_tax, taxes.total,
        deductions.total_post_tax,
        sum((line.amount for line in after_tax_lines), ZERO),
    )

    return PaycheckBreakdown(
        period=PeriodInfo(
            period.start_date, period.period_id,
            _is_third_paycheck(month_ordinal),
            get_raise_event(basis.raises, period),
        ),
        earnings=Earnings(
            base_pay.annual_salary, base_biweekly, gross_biweekly,
            taxable_biweekly, net_pay,
            taxable=taxable_lines, after_tax=after_tax_lines,
        ),
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
