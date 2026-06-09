"""
Shekel Budget App -- Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, contribution, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.

Contributions are derived from shadow income transactions (transfer_id IS NOT
NULL) in the investment/retirement account.  The caller queries these
transactions and passes them in; this module has no database access.
"""

from collections import namedtuple
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import CalcMethodEnum, EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.utils.balance_predicates import status_contributes_to_balance
from app.utils.deduction_cap import cap_period_amount
from app.utils.money import ZERO, round_money


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance().

    ``ytd_contributions`` and ``ytd_contributions_seed`` are two YTD views
    of the same contribution stream that differ only on the current period
    (deep-quality-hunt #10):

    * ``ytd_contributions`` -- contributions this calendar year *through*
      the current period (``<=``).  This is the displayed limit-card value.
    * ``ytd_contributions_seed`` -- contributions this calendar year
      *strictly before* the current period (``<``).  This is the
      ``ytd_contributions_start`` handed to the growth engine, whose own
      per-period walk then applies and counts the current period's
      contribution against the limit.  Seeding the through-current value
      instead would charge the current period against the annual limit
      twice.  The two views converge at the engine's current-period row.
    """
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    ytd_contributions_seed: Decimal
    gross_biweekly: Decimal


AdaptedDeduction = namedtuple(
    "AdaptedDeduction",
    ["amount", "calc_method_id", "annual_salary", "pay_periods_per_year",
     "annual_cap"],
)


def adapt_deductions(raw_deductions: list) -> list[AdaptedDeduction]:
    """Adapt PaycheckDeduction ORM objects for calculate_investment_inputs().

    Extracts the fields needed from each deduction and its parent salary
    profile into lightweight namedtuples with no ORM dependency.  This
    decouples the projection logic from the database layer and
    consolidates the adaptation pattern previously duplicated across
    year_end_summary_service, savings_dashboard_service, and
    retirement_dashboard_service.

    Args:
        raw_deductions: List of PaycheckDeduction ORM objects.  Each
            must have a loaded ``salary_profile`` relationship with
            ``annual_salary`` and ``pay_periods_per_year`` attributes.

    Returns:
        List of AdaptedDeduction namedtuples ready for
        calculate_investment_inputs() or build_contribution_timeline().
    """
    result = []
    for ded in raw_deductions:
        profile = ded.salary_profile
        result.append(AdaptedDeduction(
            amount=ded.amount,
            calc_method_id=ded.calc_method_id,
            annual_salary=profile.annual_salary,
            pay_periods_per_year=profile.pay_periods_per_year or 26,
            annual_cap=ded.annual_cap,
        ))
    return result


def _compute_deduction_per_period(deduction, pct_id):
    """Compute the per-period contribution amount from a single deduction.

    Handles flat-dollar and percentage-of-salary calculation methods.
    Shared by calculate_investment_inputs() and build_contribution_timeline()
    to keep the deduction amount logic in one place (DRY).

    Args:
        deduction:  Object with .amount, .calc_method_id, .annual_salary,
                    .pay_periods_per_year.
        pct_id:     The ref ID for the PERCENTAGE calculation method.

    Returns:
        Tuple of (contribution_amount: Decimal, gross_biweekly: Decimal).
        contribution_amount is the per-period dollar amount.
        gross_biweekly is the derived gross pay per period (used by
        calculate_investment_inputs for employer params).
    """
    salary = Decimal(str(deduction.annual_salary))
    pay_per_year = deduction.pay_periods_per_year or 26
    gross = round_money(salary / pay_per_year)
    amt = Decimal(str(deduction.amount))
    if deduction.calc_method_id == pct_id:
        amt = round_money(gross * amt)
    return amt, gross


def _annual_cap_averaged(per_period_amount, deduction):
    """Per-period amount evenly throttled to the deduction's annual cap.

    The periodic contribution is the growth engine's fallback for periods with
    no dated ``ContributionRecord`` -- in practice the synthetic long-horizon
    chart, whose generated dates never match a real period.  A capped deduction
    must not contribute more than ``annual_cap`` per calendar year there either,
    so the per-period amount is the cap spread evenly across the year:
    ``min(amount * ppy, annual_cap) / ppy``.  This even-spread is the
    long-horizon analogue of the front-loaded per-period timeline
    (:func:`_deduction_contribution_records`): both hold the annual total at the
    cap and differ only in WITHIN-year timing, which a multi-year projection
    does not surface.  ``annual_cap`` is read via ``getattr`` so a minimal
    deduction-like fake (no cap field) is treated as uncapped.

    Args:
        per_period_amount: Decimal uncapped per-period contribution.
        deduction:         The deduction-like object (.pay_periods_per_year,
                           optionally .annual_cap).

    Returns:
        The capped per-period amount (Decimal); unchanged when uncapped.
    """
    annual_cap = getattr(deduction, "annual_cap", None)
    if annual_cap is None:
        return per_period_amount
    pay_per_year = deduction.pay_periods_per_year or 26
    annual_capped = min(per_period_amount * pay_per_year, Decimal(str(annual_cap)))
    return round_money(annual_capped / pay_per_year)


def _periodic_from_deductions(deductions, salary_gross_biweekly):
    """Sum the per-period contribution from paycheck deductions.

    Each deduction's per-period amount is throttled to its calendar-year
    ``annual_cap`` via :func:`_annual_cap_averaged` (deep-hunt #2) before
    summing, so this fallback average respects the same cap the per-period
    timeline enforces.

    Args:
        deductions:            List of deduction-like objects with
                               .amount, .calc_method_id, .annual_salary,
                               .pay_periods_per_year, and optionally
                               .annual_cap.
        salary_gross_biweekly: Engine gross per pay period (Decimal or
                               None), used as the fallback gross when no
                               deduction supplied one.

    Returns:
        Tuple of (periodic_contribution: Decimal, gross_biweekly: Decimal).
        gross_biweekly is the deduction-derived gross, falling back to
        ``salary_gross_biweekly`` and then ZERO.
    """
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
    periodic_contribution = ZERO
    gross_biweekly = ZERO

    for ded in deductions:
        amt, gross = _compute_deduction_per_period(ded, pct_id)
        gross_biweekly = gross
        periodic_contribution += _annual_cap_averaged(amt, ded)

    # Use salary profile gross as fallback when no deductions provided one.
    if gross_biweekly == ZERO and salary_gross_biweekly is not None:
        gross_biweekly = Decimal(str(salary_gross_biweekly))

    return periodic_contribution, gross_biweekly


def _average_transfer_contribution(all_contributions):
    """Average per-period contribution from shadow income transactions.

    ``all_contributions`` are shadow income transactions already filtered
    to one account by the caller.  Cancelled/credit transactions
    (status.excludes_from_balance=True) are excluded via the centralized
    ``status_contributes_to_balance`` helper (D6-09 / MED-02) so the
    "is this contribution counted" rule shares one definition with the
    SQL filters in ``year_end_summary_service`` /
    ``savings_dashboard_service`` and the Python ``is_balance_contributing``
    predicate.  The status-only variant is required because the caller
    passes in already-deleted-filtered rows whose duck-typed test fakes
    (``FakeContribTransaction``) deliberately omit ``is_deleted``.

    Contributions are summed on ``effective_amount`` -- the realized
    actual when a shadow is settled, else the estimate -- the same
    accessor the per-period timeline uses, so this average and the
    YTD/limit accounting cannot disagree with the engine on a settled
    transfer whose actual differs from its estimate (deep-quality-hunt
    #11).

    Args:
        all_contributions: List of shadow income transactions with
                           .effective_amount, .pay_period_id, .status.

    Returns:
        The per-period average contribution (Decimal), or ZERO when no
        active contributions exist.
    """
    if not all_contributions:
        return ZERO

    active_contributions = [
        t for t in all_contributions
        if status_contributes_to_balance(t)
    ]
    total_contrib = sum(
        Decimal(str(t.effective_amount)) for t in active_contributions
    )
    num_periods_with_contrib = len(
        set(t.pay_period_id for t in active_contributions)
    )
    if num_periods_with_contrib > 0:
        return round_money(total_contrib / num_periods_with_contrib)
    return ZERO


def _employer_params(investment_params, gross_biweekly):
    """Build the employer-contribution params dict, or None.

    Args:
        investment_params: Object with ``employer_contribution_type_id``
                           and the ``employer_*_percentage`` fields.
        gross_biweekly:    Engine gross per pay period (Decimal), embedded
                           so the growth engine can size a
                           percentage-of-gross employer match.

    Returns:
        A dict describing the employer contribution, or None when the
        account has no employer contribution configured.  The dict
        carries the employer-type ref id under ``type_id`` (#38) so the
        growth engine branches on the id, not a string.
    """
    emp_type_id = getattr(investment_params, "employer_contribution_type_id", None)
    none_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.NONE
    )
    if emp_type_id is None or emp_type_id == none_id:
        return None
    return {
        "type_id": emp_type_id,
        "flat_percentage": getattr(
            investment_params, "employer_flat_percentage", None) or ZERO,
        "match_percentage": getattr(
            investment_params, "employer_match_percentage", None) or ZERO,
        "match_cap_percentage": getattr(
            investment_params, "employer_match_cap_percentage", None) or ZERO,
        "gross_biweekly": gross_biweekly,
    }


def _current_year_period_ids(all_periods, current_period, *, inclusive):
    """Current-calendar-year period ids up to the current period.

    ``inclusive`` controls the current period itself: ``True`` keeps it
    (``<=``, the through-current YTD shown on the limit card); ``False``
    drops it (``<``, the strictly-before seed handed to the growth
    engine).  Sharing one builder keeps the two YTD windows from drifting
    (deep-quality-hunt #10).

    Args:
        all_periods:    Period objects with .id and .start_date.
        current_period: The current period object (caller guards None).
        inclusive:      Keyword-only; include the current period or not.

    Returns:
        The set of matching ``period_id`` values.
    """
    year = current_period.start_date.year
    boundary = current_period.start_date
    return {
        p.id for p in all_periods
        if p.start_date.year == year
        and (p.start_date <= boundary if inclusive else p.start_date < boundary)
    }


def _sum_year_contributions(all_contributions, period_ids):
    """Sum the ``effective_amount`` of active contributions in ``period_ids``.

    Active = passes the centralized ``status_contributes_to_balance``
    filter (the same rule ``_average_transfer_contribution`` uses).
    ``effective_amount`` (the realized actual when a shadow is settled,
    else the estimate) is ``Transaction``'s single source of truth for
    what a row contributes to a projection, so this YTD-seed/limit
    accounting agrees with the per-period timeline
    (:func:`build_contribution_timeline`, also ``effective_amount``) once
    a transfer shadow is settled with an actual that differs from its
    estimate (deep-quality-hunt #11).  Summing ``estimated_amount`` here
    previously let the cap/limit math read a different dollar than the
    engine actually applied; the prior "F-027 S18 contract-safe"
    rationale assumed a shadow's ``actual_amount`` is always ``None``,
    which is untrue once ``transfer_service._apply_actual_amount`` sets
    it on settlement (the ``Transfer`` parent has no ``actual_amount``
    column, so a settled actual lives only on the shadows).

    Args:
        all_contributions: Shadow income transactions for one account
                           (.effective_amount, .pay_period_id, .status).
        period_ids:        The period_id set to sum over.

    Returns:
        The contribution total (Decimal).
    """
    total = ZERO
    for t in all_contributions:
        if t.pay_period_id in period_ids and status_contributes_to_balance(t):
            total += Decimal(str(t.effective_amount))
    return total


def _ytd_contributions(all_contributions, all_periods, current_period):
    """Sum year-to-date contributions THROUGH the current period (``<=``).

    The displayed limit-card YTD value.  Sums ``effective_amount`` for
    active contributions whose pay period falls in the current calendar
    year up to and including ``current_period``.

    Args:
        all_contributions: Shadow income transactions for one account
                           (.effective_amount, .pay_period_id, .status).
        all_periods:       Period objects with .id and .start_date.
        current_period:    The current period object, or None.

    Returns:
        The YTD contribution total (Decimal); ZERO when current_period
        is None.
    """
    if current_period is None:
        return ZERO
    period_ids = _current_year_period_ids(
        all_periods, current_period, inclusive=True,
    )
    return _sum_year_contributions(all_contributions, period_ids)


def _ytd_contributions_seed(all_contributions, all_periods, current_period):
    """Sum year-to-date contributions STRICTLY BEFORE the current period (``<``).

    The ``ytd_contributions_start`` seed handed to the growth engine.
    The engine's per-period walk then applies and counts the current
    period's own contribution against the annual limit, so seeding the
    through-current value (:func:`_ytd_contributions`) instead would
    charge the current period twice (deep-quality-hunt #10).

    Args:
        all_contributions: Shadow income transactions for one account
                           (.effective_amount, .pay_period_id, .status).
        all_periods:       Period objects with .id and .start_date.
        current_period:    The current period object, or None.

    Returns:
        The strictly-before-current YTD total (Decimal); ZERO when
        current_period is None.
    """
    if current_period is None:
        return ZERO
    period_ids = _current_year_period_ids(
        all_periods, current_period, inclusive=False,
    )
    return _sum_year_contributions(all_contributions, period_ids)


def calculate_investment_inputs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    investment_params,
    deductions,
    all_contributions,
    all_periods,
    current_period,
    salary_gross_biweekly=None,
):
    """Compute projection inputs for an investment account.

    Args:
        investment_params:     Object with employer fields and
                               ``annual_contribution_limit``.
        deductions:            List of deduction-like objects with
                               .amount, .calc_method_id, .annual_salary,
                               .pay_periods_per_year.
        all_contributions:     List of shadow income transactions
                               (transfer_id IS NOT NULL) in this account.
                               Each has .effective_amount, .pay_period_id,
                               .status.
        all_periods:           List of period objects with .id,
                               .start_date, .period_index.
        current_period:        The current period object, or None.
        salary_gross_biweekly: Engine gross per pay period used as the
                               fallback gross when no deduction supplied
                               one (Decimal or None).

    Returns:
        InvestmentInputs dataclass.

    Pylint: ``too-many-arguments`` (6/5) / ``too-many-positional-arguments``
    (6/5) -- the six inputs are independent, heterogeneous projection inputs
    (account config, two contribution feeds, the period calendar, and a
    salary-gross fallback); each is consumed by a different step, so a
    param object would be stamp coupling.  The scoped disable mirrors the
    immediately-downstream ``growth_engine.project_balance``, which takes
    the same documented disable for the same reason.
    """
    periodic_contribution, gross_biweekly = _periodic_from_deductions(
        deductions, salary_gross_biweekly,
    )
    periodic_contribution += _average_transfer_contribution(all_contributions)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        employer_params=_employer_params(investment_params, gross_biweekly),
        annual_contribution_limit=getattr(
            investment_params, "annual_contribution_limit", None),
        ytd_contributions=_ytd_contributions(
            all_contributions, all_periods, current_period),
        ytd_contributions_seed=_ytd_contributions_seed(
            all_contributions, all_periods, current_period),
        gross_biweekly=gross_biweekly,
    )


def _deduction_contribution_records(deductions, periods, pct_id, today):
    """Per-period deduction ContributionRecords, each clamped to its annual cap.

    Deductions contribute the same raw amount every period; each is clamped to
    its own calendar-year ``annual_cap`` (deep-hunt #2) through the shared
    ``cap_period_amount`` so this timeline agrees with the net-pay path.  Cap
    state is tracked per deduction and resets at each year boundary, mirroring
    the growth engine's own year reset.

    A record is emitted for every covered period -- even a fully-capped $0 --
    so the growth engine applies the capped amount rather than the
    periodic-average fallback a missing record would trigger.  ``annual_cap`` is
    read via ``getattr`` so a minimal deduction-like fake (no cap field) is
    treated as uncapped.

    Args:
        deductions: Deduction-like objects (see build_contribution_timeline).
        periods:    Period objects with .start_date.
        pct_id:     The ref ID for the PERCENTAGE calculation method.
        today:      The date splitting confirmed (past) from projected periods.

    Returns:
        list[ContributionRecord] in period-start-date order; empty when no
        deduction contributes a positive amount.
    """
    deduction_raws = [
        (_compute_deduction_per_period(d, pct_id)[0],
         getattr(d, "annual_cap", None))
        for d in deductions
    ]
    if not any(raw > ZERO for raw, _ in deduction_raws):
        return []

    # (year, raw_cumulative) per deduction; None until its first period.
    cap_state = [None] * len(deduction_raws)
    records = []
    for period in sorted(periods, key=lambda p: p.start_date):
        period_year = period.start_date.year
        period_total = ZERO
        for i, (raw, annual_cap) in enumerate(deduction_raws):
            if raw <= ZERO:
                continue
            prior = cap_state[i]
            cumulative_before = (
                prior[1] if prior is not None and prior[0] == period_year
                else ZERO
            )
            period_total += cap_period_amount(raw, cumulative_before, annual_cap)
            cap_state[i] = (period_year, cumulative_before + raw)
        records.append(ContributionRecord(
            contribution_date=period.start_date,
            amount=period_total,
            # Past periods are confirmed (the deduction was taken from the
            # paycheck); future periods are projected.
            is_confirmed=period.start_date < today,
        ))
    return records


def current_period_transfer_contribution(contribution_transactions, current_period):
    """Sum the effective contribution the current period's transfers add.

    These shadow income transactions are BOTH counted in the entries-aware
    end-of-current-period balance (``balance_calculator._income_amount``
    uses ``effective_amount``) AND re-applied by the growth engine when the
    projection window includes the current period
    (:func:`build_contribution_timeline` Path 2, the same
    ``effective_amount``).  Subtracting this sum from the end-of-current
    seed cancels exactly that double-count (deep-quality-hunt #9 / #14)
    while preserving every OTHER current-period balance movement -- a
    withdrawal, a fee, an entries-aware envelope expense -- which the
    engine never re-creates, so a blunter "re-anchor to the prior period"
    seed would silently drop them.

    Deductions are intentionally NOT summed here: they are not budget
    transactions, so they are absent from the balance and must be applied
    fresh by the engine for the current period (the engine's own walk does
    that via the timeline's Path 1).

    Args:
        contribution_transactions: Shadow income Transaction objects for
            one account (.effective_amount, .pay_period_id, .status).
        current_period: The current period object, or None.

    Returns:
        The effective-amount sum of active
        (``status_contributes_to_balance``) shadow contributions whose
        ``pay_period_id`` is the current period; ZERO when current_period
        is None.
    """
    if current_period is None:
        return ZERO
    total = ZERO
    for txn in contribution_transactions:
        if (txn.pay_period_id == current_period.id
                and status_contributes_to_balance(txn)):
            amount = txn.effective_amount
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            total += amount
    return total


def build_contribution_timeline(
    deductions,
    contribution_transactions,
    periods,
):
    """Build ContributionRecords from deductions and shadow transactions.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: The same raw amount every period, each
    clamped to its own calendar-year ``annual_cap`` (deep-hunt #2) so this
    timeline agrees with the net-pay path.  Confirmation is date-based (past
    period = confirmed) because there is no per-period transaction record for
    deductions.

    Path 2 -- Transfer-based contributions: Per-transaction amounts from
    shadow income transactions.  Confirmation is status-based
    (txn.status.is_settled) -- factual from the transaction workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    Args:
        deductions:                 List of deduction-like objects with
                                    .amount, .calc_method_id,
                                    .annual_salary, .pay_periods_per_year,
                                    and optionally .annual_cap (the
                                    calendar-year ceiling; absent = uncapped).
        contribution_transactions:  List of shadow income Transaction
                                    objects (transfer_id IS NOT NULL)
                                    with .effective_amount, .pay_period_id,
                                    .status (.is_settled, .excludes_from_balance).
                                    Status must be eager-loaded by caller.
        periods:                    List of period objects with .id,
                                    .start_date.

    Returns:
        list[ContributionRecord] sorted by contribution_date.  Empty
        list if no deductions and no qualifying transactions exist.
    """
    # Centralized ``status_contributes_to_balance`` helper
    # (D6-09 / MED-02); see ``_average_transfer_contribution`` above
    # for why the status-only variant is the right primitive here.

    records = []
    today = date.today()
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)

    # Path 1: Paycheck deductions -- same raw amount every period, each
    # clamped to its own calendar-year cap.
    records.extend(
        _deduction_contribution_records(deductions, periods, pct_id, today)
    )

    # Path 2: Transfer-based contributions -- per-transaction amounts.
    period_by_id = {p.id: p for p in periods}
    for txn in contribution_transactions:
        # Skip cancelled/credit transactions that do not represent
        # actual contributions (same filter as loan_payment_service).
        if not status_contributes_to_balance(txn):
            continue
        period = period_by_id.get(txn.pay_period_id)
        if period is None:
            # Transaction in a period outside the projection range.
            continue
        amount = txn.effective_amount
        # Defensive: ensure Decimal even if effective_amount returns
        # a non-Decimal from a DB column.
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        records.append(ContributionRecord(
            contribution_date=period.start_date,
            amount=amount,
            # Transfer-based: determined by the transaction's
            # settlement status (Paid/Settled=True, Projected=False).
            is_confirmed=txn.status.is_settled,
        ))

    records.sort(key=lambda r: r.contribution_date)
    return records
