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


ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance()."""
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    gross_biweekly: Decimal


AdaptedDeduction = namedtuple(
    "AdaptedDeduction",
    ["amount", "calc_method_id", "annual_salary", "pay_periods_per_year"],
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
    gross = (salary / pay_per_year).quantize(TWO_PLACES)
    amt = Decimal(str(deduction.amount))
    if deduction.calc_method_id == pct_id:
        amt = (gross * amt).quantize(TWO_PLACES)
    return amt, gross


def _periodic_from_deductions(deductions, salary_gross_biweekly):
    """Sum the per-period contribution from paycheck deductions.

    Args:
        deductions:            List of deduction-like objects with
                               .amount, .calc_method_id, .annual_salary,
                               .pay_periods_per_year.
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
        periodic_contribution += amt

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

    Args:
        all_contributions: List of shadow income transactions with
                           .estimated_amount, .pay_period_id, .status.

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
        Decimal(str(t.estimated_amount)) for t in active_contributions
    )
    num_periods_with_contrib = len(
        set(t.pay_period_id for t in active_contributions)
    )
    if num_periods_with_contrib > 0:
        return (total_contrib / num_periods_with_contrib).quantize(TWO_PLACES)
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


def _ytd_contributions(all_contributions, all_periods, current_period):
    """Sum year-to-date contributions from shadow income transactions.

    Sums ``estimated_amount`` for active contributions whose pay period
    falls in the current calendar year up to and including
    ``current_period``.  Uses the same status filter as
    ``_average_transfer_contribution``.

    Args:
        all_contributions: Shadow income transactions for one account
                           (.estimated_amount, .pay_period_id, .status).
        all_periods:       Period objects with .id and .start_date.
        current_period:    The current period object, or None.

    Returns:
        The YTD contribution total (Decimal); ZERO when current_period
        is None.
    """
    if current_period is None:
        return ZERO

    current_year = current_period.start_date.year
    ytd_period_ids = {
        p.id for p in all_periods
        if p.start_date.year == current_year
        and p.start_date <= current_period.start_date
    }
    ytd_contributions = ZERO
    for t in all_contributions:
        if (t.pay_period_id in ytd_period_ids
                and status_contributes_to_balance(t)):
            ytd_contributions += Decimal(str(t.estimated_amount))
    return ytd_contributions


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
                               Each has .estimated_amount, .pay_period_id,
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
        gross_biweekly=gross_biweekly,
    )


def build_contribution_timeline(
    deductions,
    contribution_transactions,
    periods,
):
    """Build ContributionRecords from deductions and shadow transactions.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: The same dollar amount every period.
    Confirmation is date-based (past period = confirmed) because there
    is no per-period transaction record for deductions.

    Path 2 -- Transfer-based contributions: Per-transaction amounts from
    shadow income transactions.  Confirmation is status-based
    (txn.status.is_settled) -- factual from the transaction workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    Args:
        deductions:                 List of deduction-like objects with
                                    .amount, .calc_method_id,
                                    .annual_salary, .pay_periods_per_year.
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

    # Path 1: Paycheck deductions -- same amount every period.
    total_deduction_per_period = sum(
        (_compute_deduction_per_period(d, pct_id)[0] for d in deductions),
        ZERO,
    )

    if total_deduction_per_period > ZERO:
        for period in periods:
            records.append(ContributionRecord(
                contribution_date=period.start_date,
                amount=total_deduction_per_period,
                # Deduction-based: past periods are confirmed (the
                # deduction was taken from the paycheck); future
                # periods are projected.
                is_confirmed=period.start_date < today,
            ))

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
