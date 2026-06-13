"""
Shekel Budget App -- Savings Dashboard: emergency-fund and debt metrics.

Average monthly expenses (the higher of recent settled expenses and the
committed-template floor), the aggregate debt summary and its DTI band,
the canonical current-period paycheck breakdown producer, and the liquid
balance sum that feeds the emergency fund.  No Flask imports.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    escrow_calculator,
    obligations_aggregator,
    paycheck_calculator,
)
from app.services.tax_config_service import load_tax_configs
from app.utils.money import MONTHS_PER_YEAR, PAY_PERIODS_PER_YEAR, round_money

_RATE_PLACES = Decimal("0.00001")
_DTI_HEALTHY_THRESHOLD = Decimal("36")
_DTI_HIGH_THRESHOLD = Decimal("43")


def _sum_liquid_balances(account_data):
    """Sum the current balances of liquid accounts for the emergency fund.

    Args:
        account_data: List of per-account dicts from
            ``_compute_account_projections``.

    Returns:
        The total liquid balance as a Decimal.
    """
    total_savings = Decimal("0.00")
    for ad in account_data:
        if ad["account"].account_type and ad["account"].account_type.is_liquid:
            total_savings += ad["current_balance"] or Decimal("0.00")
    return total_savings


def _get_current_paycheck_breakdown(user_id, all_periods, current_period):
    """Compute the canonical paycheck breakdown for the current period.

    The single income producer this module uses for any engine-derived
    income figure (MED-06 / F-032).  Both consumers -- the savings-goal
    trajectory's net biweekly pay and the DTI denominator's gross
    monthly income -- route through this helper so the page cannot
    silently disagree with the paycheck engine on the same period.
    Pre-Commit-26 the DTI denominator read the off-engine
    ``annual_salary / pay_periods`` recompute, which dropped applicable
    ``SalaryRaise`` rows; the engine applies raises period-by-period
    via ``apply_raises`` and is therefore the only correct source for
    a raise-aware monthly gross.

    Args:
        user_id: Integer ID of the current user.
        all_periods: All pay periods for the user (passed through to
            the paycheck engine for 3rd-paycheck detection and the
            FICA SS wage-base cap's cumulative-wage tracking).
        current_period: The current :class:`PayPeriod`, or ``None``.

    Returns:
        :class:`PaycheckBreakdown` for the current period under the
        user's active salary profile, or ``None`` if ``current_period``
        is ``None`` or no active profile exists.  Callers treat
        ``None`` as "no income data on the page" rather than as a zero
        amount, since absence of an income source is structurally
        different from a real zero (E-12).
    """
    if current_period is None:
        return None

    # Pylint: ``duplicate-code`` -- resolve-active-profile ->
    # load-tax-configs -> calculate_paycheck.  ``dashboard_service`` runs
    # the same three steps, but the two return different contracts (that
    # one keeps only ``net_pay``; this one returns the full
    # PaycheckBreakdown for the DTI / trajectory math), so they are
    # deliberately separate surfaces over the same calculator rather than a
    # shared helper (coding-standards rule 13).  One-sided
    # ``duplicate-code`` disable (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if profile is None:
        return None

    tax_configs = load_tax_configs(user_id, profile)
    return paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )
    # pylint: enable=duplicate-code


def _checking_account_ids(accounts):
    """IDs of the user's checking accounts.

    The single source for the checking-account scope shared by the two
    operands of :func:`_compute_avg_monthly_expenses` (DH-#29): both the
    committed-template floor and the recent-settled-expenses average
    measure outflow from these accounts, so the set is derived once here
    and threaded into both.  Resolved by the CHECKING ref-type id (IDs
    for logic), not a name string.

    Args:
        accounts: List of Account model instances.

    Returns:
        List of integer account IDs whose type is the CHECKING ref type.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return [
        acct.id for acct in accounts
        if acct.account_type_id == checking_type_id
    ]


def _recent_settled_expenses_monthly(
    checking_ids, all_periods, current_period, scenario,
):
    """Average monthly settled checking expenses over the last 6 periods.

    Sums settled expense transactions on the user's checking accounts
    across the most recent 6 periods (at or before the current period)
    and converts the per-period average to a monthly figure via the
    biweekly-to-monthly factor.  Scoped to the same checking-account set
    as :func:`_committed_expense_floor` (DH-#29) so the two operands of
    :func:`_compute_avg_monthly_expenses`'s ``max()`` measure the same
    "outflow from checking" universe -- a settled expense on a
    non-checking account (e.g. a transfer's expense shadow on a
    savings/HSA source) is excluded here just as it is from the floor,
    rather than inflating only the historical operand.

    Args:
        checking_ids: IDs of the user's checking accounts (the
            :func:`_checking_account_ids` set the floor also uses).
        all_periods: All pay periods for the user.
        current_period: The current :class:`PayPeriod`, or ``None``.
        scenario: The baseline scenario, or ``None``.

    Returns:
        The monthly average as a Decimal.  ``Decimal("0.00")`` when
        there is no current period / scenario, no checking account, or
        no recent periods.
    """
    if not (current_period and scenario) or not checking_ids:
        return Decimal("0.00")

    recent_periods = [
        p for p in all_periods
        if p.period_index <= current_period.period_index
    ][-6:]
    if not recent_periods:
        return Decimal("0.00")

    recent_period_ids = [p.id for p in recent_periods]
    recent_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(recent_period_ids),
            Transaction.account_id.in_(checking_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    total_expenses = Decimal("0.00")
    for txn in recent_txns:
        if txn.is_expense and txn.status and txn.status.is_settled:
            total_expenses += Decimal(str(txn.effective_amount))

    per_period = total_expenses / len(recent_periods)
    return per_period * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR


def _committed_expense_floor(user_id, checking_ids):
    """Committed monthly expense floor from active checking templates.

    Sums the monthly-normalized commitment of active expense templates
    and active outgoing transfer templates on the user's checking
    accounts, via the canonical obligations aggregator (E-24 / HIGH-05)
    -- so the same skip-ONCE / skip-expired filter the /obligations
    page applies governs the emergency-fund baseline.

    Args:
        user_id: Integer ID of the current user.
        checking_ids: IDs of the user's checking accounts (the
            :func:`_checking_account_ids` set the historical operand
            also uses).

    Returns:
        The committed monthly floor as a Decimal.  ``Decimal("0.00")``
        when the user has no checking account.
    """
    if not checking_ids:
        return Decimal("0.00")

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    active_expense_templates = (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.account_id.in_(checking_ids),
            TransactionTemplate.transaction_type_id == expense_type_id,
            TransactionTemplate.is_active.is_(True),
        )
        .all()
    )
    active_transfer_templates = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.from_account_id.in_(checking_ids),
            TransferTemplate.is_active.is_(True),
        )
        .all()
    )
    return obligations_aggregator.committed_monthly(
        list(active_expense_templates) + list(active_transfer_templates),
        date.today(),
    )


def _compute_avg_monthly_expenses(
    user_id, accounts, all_periods, current_period, scenario,
):
    """Compute average monthly expenses for emergency fund coverage.

    Uses the higher of: historical settled expenses from the last 6
    periods, or the committed monthly baseline from active templates.
    Both operands are scoped to the user's checking accounts (DH-#29)
    so the ``max()`` compares like with like -- the "outflow from
    checking" universe the committed floor (E-24) defines -- rather than
    pairing an all-accounts historical figure against a checking-only
    floor.
    """
    checking_ids = _checking_account_ids(accounts)
    historical = _recent_settled_expenses_monthly(
        checking_ids, all_periods, current_period, scenario,
    )
    floor = _committed_expense_floor(user_id, checking_ids)
    return max(historical, floor)


def _loan_ad_current_principal(ad: dict) -> Decimal | None:
    """Return a loan account dict's contributing current balance, or None.

    The single definition of "which loan accounts contribute to the debt
    summary's active-loans-only aggregates" (its ``total_debt``,
    ``total_monthly_payments``, and weighted-average rate).  A loan
    contributes its resolver-derived current balance when it is neither
    paid off nor at a zero (or negative) balance; otherwise it contributes
    nothing and the caller skips it.  The principal-paid progress fraction
    does NOT use this predicate -- it sums over ALL loans ever originated
    (see :func:`_compute_principal_paid_fraction`), keeping paid-off loans
    in both of its sums so the marker stays monotonic.  The displayed debt
    balance, by contrast, is active-loans-only, which is exactly what this
    predicate scopes.

    Args:
        ad: A per-account dict carrying ``is_paid_off`` and
            ``current_balance`` (a loan entry from
            ``_compute_account_projections``).

    Returns:
        The loan's resolver-derived current balance as a positive
        ``Decimal`` when it contributes, or ``None`` when it is paid off
        or its balance is zero / negative.
    """
    if ad["is_paid_off"]:
        return None
    # Resolver-derived current_balance (E-18 / Commit 15).  Same dollar
    # figure as the loan card; replaces the previous read of the
    # non-authoritative ``LoanParams.current_principal`` column that
    # produced F-008's stored-vs-engine divergence.
    principal = ad["current_balance"] or Decimal("0.00")
    if principal <= Decimal("0.00"):
        return None
    return principal


def _compute_principal_paid_fraction(
    account_data: list[dict],
) -> Decimal | None:
    """Aggregate fraction of original principal paid across ALL loans ever.

    Computes ``(sum(original_principal) - sum(current_balance)) /
    sum(original_principal)`` over EVERY loan the pipeline surfaces, not
    just the loans still carrying a balance.  A paid-off loan stays in
    BOTH the numerator and the denominator, contributing
    ``Decimal("0.00")`` to the current-balance sum -- so its full
    ``original_principal`` lands in the "paid" portion of the numerator.

    This "all loans ever originated" basis (locked 2026-06-12 in
    ``docs/design/dashboard_card_audit.md``, Rebuild decisions item 4) is
    what makes the debt-track marker MONOTONIC: paying a single loan all
    the way off only adds its principal to the paid portion and never
    removes anything from the denominator, so the fraction can only rise,
    reaches exactly ``1`` at full payoff of every loan, and stays there --
    it never jumps backward the way the prior active-loans-only basis did
    when one loan dropped out of both sums at payoff.  The displayed
    balance label remains active-loans-only; that is
    :func:`_compute_debt_summary`'s concern, not this marker's.

    "All loans the pipeline surfaces" is, reachably, all of the user's
    NON-ARCHIVED (``is_active=True``) loan accounts that have a
    ``LoanParams`` row.  Archived accounts are filtered out upstream by
    ``_load_dashboard_core_data`` (``is_active=True``) and never reach
    ``account_data``, so they cannot be included; a loan with no
    ``LoanParams`` row carries no ``original_principal`` and is likewise
    not a loan-ad here.  Paid-off loans, by contrast, remain active
    accounts and DO appear in ``account_data`` carrying
    ``is_paid_off=True``, so the all-loans-ever set is fully reachable.

    ``original_principal`` is a NOT NULL, ``> 0`` column on
    :class:`~app.models.loan_params.LoanParams`, so any real loan-ad
    supplies a positive denominator.  ``None`` is returned ONLY when the
    user has no loan accounts at all (the denominator would be zero); a
    fully paid-off loan set returns ``Decimal("1")``, not ``None``.

    Args:
        account_data: Per-account dicts from
            ``_compute_account_projections`` (any mix -- only entries
            carrying ``loan_params`` are read).

    Returns:
        The principal-paid fraction as a ``Decimal`` in ``[0, 1]`` (a
        loan whose current balance somehow exceeds its original principal
        is clamped to ``0`` so the marker never renders to the left of the
        rail), or ``None`` when the user has no loans at all.
    """
    loan_ads = [ad for ad in account_data if ad.get("loan_params")]

    total_original = Decimal("0.00")
    total_current = Decimal("0.00")
    for ad in loan_ads:
        # ALL loans ever: every loan-ad contributes its original
        # principal to the denominator.  A paid-off loan contributes
        # Decimal("0.00") to the current-balance sum (regardless of the
        # resolver's as-of-today figure) so its full principal counts as
        # paid; an active loan contributes its resolver-derived current
        # balance, never below zero.
        total_original += ad["loan_params"].original_principal
        if ad["is_paid_off"]:
            continue
        current = ad["current_balance"] or Decimal("0.00")
        total_current += max(current, Decimal("0.00"))

    if total_original <= Decimal("0.00"):
        return None

    fraction = (total_original - total_current) / total_original
    # A current balance above the original principal (negative paid
    # fraction) is meaningless for a payoff marker; clamp to 0.
    if fraction < Decimal("0"):
        return Decimal("0")
    return fraction


def _accumulate_loan_debt(
    loan_ads: list, escrow_map: dict,
) -> tuple[Decimal, Decimal, Decimal, list]:
    """Sum debt metrics across active (non-paid-off) loan accounts.

    Walks the per-account loan dicts, skipping paid-off loans and loans
    whose resolver-derived current balance is zero, and accumulates the
    running totals the debt summary reports.

    Args:
        loan_ads: Per-account dicts that carry a ``loan_params`` key
            (the loan subset of ``_compute_account_projections`` output).
        escrow_map: Dict mapping account_id to list of EscrowComponent.

    Returns:
        ``(total_debt, total_monthly, weighted_rate_sum, payoff_dates)``
        -- the running sums (Decimals) and the list of per-loan payoff
        dates.
    """
    total_debt = Decimal("0.00")
    total_monthly = Decimal("0.00")
    weighted_rate_sum = Decimal("0.00")
    payoff_dates = []

    for ad in loan_ads:
        # The same contribute-or-skip rule the principal-paid fraction
        # uses, so the two aggregates sum over one loan set (DRY).
        principal = _loan_ad_current_principal(ad)
        if principal is None:
            continue

        # DH-#56: the loan's CURRENT rate (resolver-derived,
        # ``state.current_rate``), replacing the retired
        # ``LoanParams.interest_rate`` column.  weighted_avg_rate now
        # reflects the rate the loan is actually accruing at today --
        # for a changed ARM the in-effect rate, not the stale origination
        # value the dropped column had drifted from.
        rate = ad["current_rate"]
        monthly_pi = ad["monthly_payment"]

        # Include escrow (property tax, insurance) for PITI total.
        components = escrow_map.get(ad["account"].id, [])
        monthly_escrow = escrow_calculator.calculate_monthly_escrow(components)
        monthly_total = round_money(monthly_pi + monthly_escrow)

        total_debt += principal
        total_monthly += monthly_total
        weighted_rate_sum += rate * principal

        if ad.get("payoff_date"):
            payoff_dates.append(ad["payoff_date"])

    return total_debt, total_monthly, weighted_rate_sum, payoff_dates


def _compute_debt_summary(
    account_data: list,
    escrow_map: dict,
) -> dict | None:
    """Compute aggregate debt metrics across active loan accounts.

    Uses per-account data already computed by _compute_account_projections
    (monthly_payment, payoff_date, loan_params, is_paid_off).  Escrow
    components are loaded separately and included in the monthly total
    so DTI reflects PITI (principal, interest, taxes, insurance).

    Paid-off loans are excluded from all aggregate metrics.  Loans with
    missing LoanParams are skipped with a warning.

    Args:
        account_data: List of per-account dicts from
            _compute_account_projections.
        escrow_map: Dict mapping account_id to list of EscrowComponent.

    Returns:
        Dict with keys: total_debt, total_monthly_payments,
        weighted_avg_rate, projected_debt_free_date.
        Returns None if no loan accounts with params exist.
    """
    loan_ads = [ad for ad in account_data if ad.get("loan_params")]
    if not loan_ads:
        return None

    total_debt, total_monthly, weighted_rate_sum, payoff_dates = (
        _accumulate_loan_debt(loan_ads, escrow_map)
    )

    if total_debt > Decimal("0.00"):
        weighted_avg_rate = (weighted_rate_sum / total_debt).quantize(
            _RATE_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        weighted_avg_rate = Decimal("0.00000")

    debt_free_date = max(payoff_dates) if payoff_dates else None

    return {
        "total_debt": round_money(total_debt),
        "total_monthly_payments": round_money(total_monthly),
        "weighted_avg_rate": weighted_avg_rate,
        "projected_debt_free_date": debt_free_date,
    }


def _get_dti_label(dti_pct: Decimal) -> str:
    """Return the DTI health label based on conventional thresholds.

    Boundaries: < 36% is healthy, 36%--43% is moderate, > 43% is high.
    36.0% is moderate (not healthy).  43.0% is moderate (not high).

    Args:
        dti_pct: DTI as a percentage (e.g. Decimal("34.2")).

    Returns:
        'healthy', 'moderate', or 'high'.
    """
    if dti_pct < _DTI_HEALTHY_THRESHOLD:
        return "healthy"
    if dti_pct > _DTI_HIGH_THRESHOLD:
        return "high"
    return "moderate"


def _apply_dti_metrics(debt_summary, gross_biweekly):
    """Populate the debt summary's DTI fields from gross biweekly pay.

    Mutates ``debt_summary`` in place, adding ``dti_ratio``,
    ``dti_label``, and ``gross_monthly_income``.  The biweekly -> monthly
    conversion factor (``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``) is a
    structural property of the 26-period pay schedule (Shekel is a
    biweekly app), applied to the engine-derived gross (MED-06 / F-032);
    it is a "genuine flat conversion" in the sense Commit 26 calls out,
    not a raise-dropping shortcut.  When ``gross_biweekly`` is zero (no
    income data), all three fields are set to ``None`` so the template
    distinguishes "no income source" from a real zero (E-12).

    Args:
        debt_summary: The debt-summary dict to populate, in place.
        gross_biweekly: Engine-derived gross biweekly pay (Decimal).
    """
    if gross_biweekly > Decimal("0.00"):
        gross_monthly = round_money(
            gross_biweekly * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
        )
        dti_ratio = (
            debt_summary["total_monthly_payments"]
            / gross_monthly * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        debt_summary["dti_ratio"] = dti_ratio
        debt_summary["dti_label"] = _get_dti_label(dti_ratio)
        debt_summary["gross_monthly_income"] = gross_monthly
    else:
        debt_summary["dti_ratio"] = None
        debt_summary["dti_label"] = None
        debt_summary["gross_monthly_income"] = None
