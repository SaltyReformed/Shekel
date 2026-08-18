"""
Shekel Budget App -- Retirement Income Gap Calculator Service

Orchestrates pension calculator, growth engine, and paycheck data to
produce a retirement income gap analysis.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.utils.money import MONTHS_PER_YEAR, round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Four-decimal quantum for the funded ratio (after-tax projected /
# required).  Preserves the tenth-of-a-percent the direction-D hero shows
# ("56.5% funded") while keeping the encoded value bounded.
_RATIO_QUANTUM = Decimal("0.0001")


@dataclass
class RetirementGapAnalysis:  # pylint: disable=too-many-instance-attributes
    """Result of a retirement income gap calculation.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a cohesive single-return result aggregate -- every
    field is one figure of the retirement net-frame analysis the
    readiness producer (:mod:`app.services.retirement_readiness`) reads
    -- mirroring ``amortization_engine.AmortizationRow`` /
    ``growth_engine.ProjectedBalance``. The pre-tax fields and their
    after-tax counterparts (``after_tax_monthly_pension``,
    ``after_tax_projected_savings``, ``after_tax_surplus_or_shortfall``)
    are read side-by-side by that consumer; nesting them would fragment
    one domain concept for no design gain.
    """
    pre_retirement_net_monthly: Decimal
    monthly_pension_income: Decimal
    after_tax_monthly_pension: Decimal  # None when no tax rate
    monthly_income_gap: Decimal
    required_retirement_savings: Decimal
    projected_total_savings: Decimal
    savings_surplus_or_shortfall: Decimal
    safe_withdrawal_rate: Decimal
    after_tax_projected_savings: Decimal = None
    after_tax_surplus_or_shortfall: Decimal = None


def funded_ratio_for(projected: Decimal, required: Decimal):
    """The after-tax funded ratio for one projected / required pair.

    **The ONE definition of the ratio and of its zero-requirement guard.**  It
    was written twice until plan step C2-f2d-2: once in the readiness
    producer's ``funded_ratio_state`` and once in the contribution lever's
    ``_contribution_outcome``, which asks the same question of a projection
    with extra contributions added to it.  Two copies of a division and a
    quantum is two places a rounding rule can be changed in one of them, and
    the lever's copy is what the "contribute $X per period" caption is checked
    against -- so a divergence would show a solved amount that the hero above
    it does not agree closes the gap.

    Args:
        projected: The after-tax projected savings at the horizon.
        required: The net-frame required retirement savings.

    Returns:
        ``(funded_ratio, no_savings_needed)``: the quantized ratio with
        ``no_savings_needed`` False; or ``(None, True)`` when the requirement
        is zero -- reported as a distinct state, never as a division.
    """
    if required == ZERO:
        return None, True
    return (projected / required).quantize(_RATIO_QUANTUM), False


def funded_ratio_state(net: "RetirementGapAnalysis"):
    """Compute an analysis's after-tax funded ratio.

    **It lives here, beside the type it reads, since plan step C2-f2d-2.**  It
    was a public name in :mod:`app.services.retirement_readiness`, which is a
    CONSUMER of the analysis rather than its owner, and both the readiness
    producer and the lever solver called it across that boundary.  Moving it
    beside :class:`RetirementGapAnalysis` is what lets the one producer of the
    retirement picture (:mod:`app.services.retirement_plan`) compute the ratio
    without importing the readiness module -- an import the readiness module
    would then have to make back, which is a cycle.  A pure function of one
    record belongs with that record.

    Args:
        net: The net-frame :class:`RetirementGapAnalysis`.

    Returns:
        ``(funded_ratio, no_savings_needed)`` for the analysis's own projected
        and required figures -- see :func:`funded_ratio_for`.
    """
    return funded_ratio_for(
        net.after_tax_projected_savings, net.required_retirement_savings,
    )


def _sum_projected_balances(projections: list[dict]) -> Decimal:
    """Sum the projected balances across all retirement-account projections.

    Args:
        projections: list of projection dicts, each carrying a Decimal under
            the ``projected_balance`` key.

    Returns:
        The total projected balance (ZERO when the list is empty).
    """
    total = ZERO
    for proj in projections:
        total += Decimal(str(proj.get("projected_balance", 0)))
    return total


def _after_tax_projected_savings(
    projections: list[dict], estimated_tax_rate: Decimal
) -> Decimal:
    """Compute the after-tax projected savings total.

    Traditional balances (401k, Trad IRA) are taxed on withdrawal, so the
    estimated tax rate is applied to their sum; Roth / brokerage balances
    are assumed already-taxed and pass through untouched.

    Args:
        projections: list of projection dicts, each with ``projected_balance``
            (Decimal) and ``is_traditional`` (bool) keys.
        estimated_tax_rate: Decimal fractional tax rate applied to the
            traditional balances.

    Returns:
        The after-tax projected total, quantized to cents.
    """
    traditional_total = ZERO
    roth_total = ZERO
    for proj in projections:
        bal = Decimal(str(proj.get("projected_balance", 0)))
        if proj.get("is_traditional", False):
            traditional_total += bal
        else:
            roth_total += bal
    return round_money(traditional_total * (1 - estimated_tax_rate) + roth_total)


def calculate_gap(  # pylint: disable=too-many-arguments
    *,
    net_biweekly_pay,
    pay_cadence,
    monthly_pension_income=ZERO,
    retirement_account_projections=None,
    safe_withdrawal_rate=Decimal("0.04"),
    estimated_tax_rate=None,
):
    """Calculate the retirement income gap analysis.

    **Every argument is KEYWORD-ONLY**, which plan step R7a-2a made structural
    rather than conventional.  All three production call sites and all 30-odd
    tests already passed them by name, and the reason is worth enforcing: six
    of these are money or rates, several are ``Decimal``, and a transposed pair
    -- a pension benefit read as a withdrawal rate -- would produce a plausible
    wrong retirement plan rather than an error.  Adding the sixth parameter is
    what forced the question; the ``*`` is the answer that removes the hazard
    instead of counting it.

    Pylint: ``too-many-arguments`` (6/5) -- the six are independent
    assumptions of one analysis, not a cohesive entity: the income basis and
    the cadence it is measured in, the pension benefit, the projected balances,
    and the two rates.  Each call site supplies a different subset of the
    defaults, so a parameter object would be a bag assembled to satisfy a count
    rather than a concept -- and it would have to be constructed at each of
    them from values they hold individually.  Grouping the two rates was
    weighed and rejected: they are resolved by two different resolvers from two
    different settings columns (``resolve_swr_fraction`` /
    ``resolve_estimated_tax_rate``), and pairing them would imply a
    relationship the settings do not have.

    Args:
        net_biweekly_pay:              Decimal net pay for one paycheck.
        pay_cadence:                   The owner's
            :class:`~app.services.pay_calendar.PayCadence` -- how often that
            paycheck arrives, which is what turns it into monthly income.
            A hardcoded 26/year until plan step R7a-2a, which made a
            weekly-paid owner's pre-retirement income read at half its true
            monthly value and so understated the retirement gap they face.
        monthly_pension_income:        Decimal monthly pension benefit.
        retirement_account_projections: list of dicts with keys:
            - projected_balance: Decimal
            - is_traditional: bool (True for 401k, Trad IRA; False for Roth, brokerage)
        safe_withdrawal_rate:          Decimal (default 0.04 = 4% rule).
        estimated_tax_rate:            Decimal or None. If set, applied to traditional balances.

    Returns:
        RetirementGapAnalysis dataclass.
    """
    net_biweekly_pay = Decimal(str(net_biweekly_pay))
    monthly_pension_income = Decimal(str(monthly_pension_income))
    safe_withdrawal_rate = Decimal(str(safe_withdrawal_rate))

    if retirement_account_projections is None:
        retirement_account_projections = []

    # Step 1: Pre-retirement net monthly income.  The paycheck-to-monthly
    # conversion is the OWNER's, through the one value that owns it, so this
    # site cannot drift from /obligations and /savings (E-24, HIGH-05, and
    # plan step R7a-2a, which made the rate per-owner).
    pre_retirement_net_monthly = round_money(
        pay_cadence.per_paycheck_to_monthly(net_biweekly_pay),
    )

    # Step 2: Monthly pension income (passed in directly).

    # Step 2b: After-tax pension income (when tax rate provided).
    after_tax_monthly_pension = None
    if estimated_tax_rate is not None:
        estimated_tax_rate = Decimal(str(estimated_tax_rate))
        after_tax_monthly_pension = round_money(
            monthly_pension_income * (1 - estimated_tax_rate)
        )

    # Step 3: Monthly income gap.
    # Use after-tax pension when available for apples-to-apples comparison
    # with net (post-tax) current income.
    effective_pension = (
        after_tax_monthly_pension
        if after_tax_monthly_pension is not None
        else monthly_pension_income
    )
    monthly_income_gap = max(
        pre_retirement_net_monthly - effective_pension,
        ZERO,
    )

    # Step 4: Required retirement savings (4% rule or custom SWR).
    # ``MONTHS_PER_YEAR`` annualizes the monthly gap so the SWR (an
    # annual rate) divides into an apples-to-apples figure.
    if safe_withdrawal_rate > 0:
        required_retirement_savings = round_money(
            monthly_income_gap * MONTHS_PER_YEAR / safe_withdrawal_rate
        )
    else:
        required_retirement_savings = ZERO

    # Step 5: Projected total savings at retirement.
    projected_total_savings = _sum_projected_balances(retirement_account_projections)

    # Step 6: Surplus or shortfall.
    savings_surplus_or_shortfall = projected_total_savings - required_retirement_savings

    # After-tax view.
    after_tax_projected = None
    after_tax_surplus = None
    if estimated_tax_rate is not None:
        after_tax_projected = _after_tax_projected_savings(
            retirement_account_projections, estimated_tax_rate
        )
        after_tax_surplus = after_tax_projected - required_retirement_savings

    return RetirementGapAnalysis(
        pre_retirement_net_monthly=pre_retirement_net_monthly,
        monthly_pension_income=monthly_pension_income,
        after_tax_monthly_pension=after_tax_monthly_pension,
        monthly_income_gap=monthly_income_gap,
        required_retirement_savings=required_retirement_savings,
        projected_total_savings=projected_total_savings,
        savings_surplus_or_shortfall=savings_surplus_or_shortfall,
        safe_withdrawal_rate=safe_withdrawal_rate,
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_surplus,
    )
