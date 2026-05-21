"""
Shekel Budget App -- Investment Dashboard Service (MED-01 / S6-01)

Pure-data orchestration for the investment / retirement dashboard
template (``investment/dashboard.html``) and the HTMX growth-chart
fragment (``investment/_growth_chart.html``).  Pre-Commit-28 these
two surfaces lived as 295/241-line route bodies in
``app/routes/investment.py`` mixing HTTP, 8 inline ORM queries, and
business logic in one function (S6-01 in
``docs/audits/financial_calculations/06_dry_solid.md``).  The extraction
collapses the duplicated salary-profile / deduction / contribution /
projection-inputs loading that previously appeared verbatim in both
route bodies into one shared helper, and reduces ``investment.py`` to
a thin delegator mirroring the long-standing ``savings.py`` shape
(``app/routes/savings.py:107-113``).

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  The route handles ``current_user``,
``request``, ``url_for``, and the 404 / 302 HTTP responses; this service
owns only ``Decimal``-money math, ORM queries, and the projection-engine
calls.  The dashboard service returns plain dicts the route renders;
``salary_profile_url`` resolution lives in the route because it depends
on :func:`flask.url_for`.

Outputs are byte-identical to the pre-Commit-28 route bodies: this is a
pure structural refactor (S6-01 facet of the MED-01 finding); no
financial value changes.  The route-level
``tests/test_routes/test_investment.py`` regression suite is the
load-bearing assert-unchanged gate.
"""

import logging
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.services import (
    balance_resolver,
    growth_engine,
    income_service,
    pay_period_service,
)
from app.services.account_projection import is_payroll_deduction_funded
from app.services.investment_projection import (
    build_contribution_timeline,
    calculate_investment_inputs,
)
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")
_DEFAULT_PAY_PERIODS_PER_YEAR = 26
_FALLBACK_HORIZON_YEARS = 10


# Lightweight adapter struct used by :func:`calculate_investment_inputs`
# and :func:`build_contribution_timeline`.  The pre-Commit-28 route
# bodies built equivalent objects via ``type("D", (), {...})()`` ad-hoc
# class construction (S6-07's duck-typing pattern); a declared
# :func:`namedtuple` is the same plain-data DTO discipline applied to
# the contribution-side feed, with the additional benefit of carrying
# field names that pylint and IDEs can introspect.
_AdaptedDeduction = namedtuple(
    "_AdaptedDeduction",
    ("amount", "calc_method_id", "annual_salary", "pay_periods_per_year"),
)


# ── Shared loaders ─────────────────────────────────────────────────


def _adapt_deductions(deductions: list) -> list[_AdaptedDeduction]:
    """Flatten ORM PaycheckDeduction rows to the shape the engines accept.

    :func:`investment_projection.calculate_investment_inputs` and
    :func:`investment_projection.build_contribution_timeline` only
    require five attributes per deduction (amount, calc_method_id,
    annual_salary, pay_periods_per_year).  Pre-Commit-28 the dashboard
    and growth-chart routes each built a list of anonymous
    ``type("D", ...)`` objects with the identical five fields;
    centralising the conversion here removes that duplication and
    swaps the duck-typed construction for a typed namedtuple.

    Args:
        deductions: A list of :class:`PaycheckDeduction` rows whose
            ``salary_profile`` relationship is loaded.

    Returns:
        A list of :class:`_AdaptedDeduction` namedtuples ready for
        ``calculate_investment_inputs`` / ``build_contribution_timeline``.
    """
    adapted = []
    for ded in deductions:
        profile = ded.salary_profile
        adapted.append(_AdaptedDeduction(
            amount=ded.amount,
            calc_method_id=ded.calc_method_id,
            annual_salary=profile.annual_salary,
            pay_periods_per_year=(
                profile.pay_periods_per_year
                or _DEFAULT_PAY_PERIODS_PER_YEAR
            ),
        ))
    return adapted


def _load_active_salary_profile(user_id: int) -> SalaryProfile | None:
    """Return the user's active salary profile, or ``None`` if none exists."""
    return (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )


def _salary_gross_biweekly(user_id: int) -> Decimal:
    """Return the raise-aware gross biweekly pay for the active profile.

    Delegates to :func:`income_service.get_current_gross_biweekly`
    (F-20 / MED-06 / F-032) so the value is the paycheck engine's
    per-period gross, not the off-engine
    ``annual_salary / pay_periods_per_year`` recompute that silently
    dropped any applicable :class:`SalaryRaise` row pre-Commit-17.
    Falls back to ``Decimal("0")`` when no active profile exists or
    no pay period covers today -- the engines treat that as "no
    salary context", matching the pre-Commit-28 route default.
    """
    return income_service.get_current_gross_biweekly(user_id)


def _load_deductions_for_account(user_id: int, account_id: int) -> list[PaycheckDeduction]:
    """Return active paycheck deductions targeting *account_id*."""
    return (
        db.session.query(PaycheckDeduction)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
            PaycheckDeduction.target_account_id == account_id,
            PaycheckDeduction.is_active.is_(True),
        )
        .all()
    )


def _load_shadow_income_contributions(
    account_id: int, period_ids: list[int],
) -> list[Transaction]:
    """Return shadow-income contribution transactions into *account_id*.

    Filters to transfer-shadow income rows in the supplied period
    window so :func:`calculate_investment_inputs` can derive the
    correct YTD contribution total and the contribution timeline
    can layer historical receipts.  Returns an empty list when
    ``period_ids`` is empty so callers do not issue an ``IN ()``
    query against PostgreSQL.
    """
    if not period_ids:
        return []
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )


def _load_investment_params(account_id: int) -> InvestmentParams | None:
    """Return :class:`InvestmentParams` for *account_id* or ``None``."""
    return (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )


def _resolve_current_balance(
    account: Account,
    scenario,
    current_period,
    all_periods: list,
) -> Decimal:
    """Return the canonical entries-aware "current balance" for *account*.

    Routes through :func:`balance_resolver.balances_for` (E-25 /
    CRIT-01 / F-009 / R-1: Commit 8) so the dashboard's "current
    balance" tile cannot disagree with the grid for the same
    account / scenario / period.  Falls back to
    :attr:`Account.current_anchor_balance` when no scenario is
    configured or the anchor period is unset.
    """
    anchor_balance = account.current_anchor_balance or Decimal("0.00")
    if scenario is None or account.current_anchor_period_id is None:
        return anchor_balance
    balances = balance_resolver.balances_for(
        account, scenario.id, all_periods,
    ).balances
    if current_period is None:
        return anchor_balance
    return balances.get(current_period.id, anchor_balance)


def _projection_inputs_for_account(
    user_id: int,
    account_id: int,
    investment_params: InvestmentParams,
    all_periods: list,
    current_period,
):
    """Bundle the shared inputs feed for :func:`calculate_investment_inputs`.

    Both the dashboard and the growth-chart fragment need the same
    derived inputs -- the salary-profile gross biweekly, the
    deductions targeting this account, and the shadow-income
    contribution stream.  Centralising the load here removes the
    near-verbatim duplication that previously sat in
    ``investment.py:120-218`` and ``investment.py:444-523``.

    Returns:
        A tuple ``(inputs, adapted_deductions, acct_contributions,
        deductions, active_profile)``.  Callers consume different
        subsets so all five are returned rather than packed into a
        struct.
    """
    active_profile = _load_active_salary_profile(user_id)
    salary_gross_biweekly = _salary_gross_biweekly(user_id)
    deductions = _load_deductions_for_account(user_id, account_id)
    adapted_deductions = _adapt_deductions(deductions)

    period_ids = [p.id for p in all_periods]
    acct_contributions = _load_shadow_income_contributions(
        account_id, period_ids,
    )

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=investment_params,
        deductions=adapted_deductions,
        all_contributions=acct_contributions,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )
    return (
        inputs, adapted_deductions, acct_contributions,
        deductions, active_profile,
    )


# ── Dashboard helpers ──────────────────────────────────────────────


def _compute_limit_info(
    investment_params: InvestmentParams | None,
    ytd_contributions: Decimal,
) -> dict | None:
    """Return the contribution-limit card's data, or ``None`` to hide it.

    E-12 / HIGH-06 (Commit 24): the predicate is ``is not None``, not
    Python truthiness.  A stored ``Decimal("0")`` is a meaningful state
    ("user explicitly capped contributions at zero this year") -- the
    card renders ``$0`` with 100% used at any positive YTD, matching
    the growth engine's ``min(period_contribution, 0) = 0`` semantics.
    ``None`` continues to mean "no cap configured" and hides the card.
    """
    if investment_params is None:
        return None
    limit = investment_params.annual_contribution_limit
    if limit is None:
        return None
    if limit > 0:
        pct = min(100, int(ytd_contributions / limit * 100))
    elif ytd_contributions > 0:
        # Cap is zero, contributions exist -> 100% used (over).
        pct = 100
    else:
        # Cap and YTD both zero -> 0% used.
        pct = 0
    return {
        "limit": limit,
        "ytd": ytd_contributions,
        "pct": pct,
    }


def _compute_default_horizon(user_id: int, all_periods: list) -> int:
    """Return the chart slider's default horizon in years.

    Order of preference: the user's planned retirement year if set,
    else the last projection period's year, else the
    :data:`_FALLBACK_HORIZON_YEARS` constant.  Always >= 1.
    """
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )
    if settings and settings.planned_retirement_date:
        return max(
            1, settings.planned_retirement_date.year - date.today().year,
        )
    if all_periods:
        last_period = all_periods[-1]
        return max(1, (last_period.end_date.year - date.today().year) + 1)
    return _FALLBACK_HORIZON_YEARS


def _compute_suggested_contribution(
    investment_params: InvestmentParams,
    ytd_contributions: Decimal,
    all_periods: list,
) -> Decimal:
    """Return the per-period contribution suggestion under the annual limit.

    E-12 / HIGH-06 (Commit 24): same ``is not None`` convention as
    :func:`_compute_limit_info`.  A stored zero cap produces a zero
    suggestion (no contribution within the cap), not the legacy
    $500 fallback that truthiness conflated with the "no cap
    configured" state.  When no cap is configured the suggestion is
    zero (Brokerage-style accounts -- no IRS limit to spread over
    remaining periods).
    """
    if investment_params.annual_contribution_limit is None:
        return Decimal("0")
    today_date = date.today()
    remaining_periods = sum(
        1 for p in all_periods
        if p.start_date.year == today_date.year
        and p.start_date >= today_date
    )
    remaining_limit = max(
        investment_params.annual_contribution_limit
        - (ytd_contributions or Decimal("0")),
        Decimal("0"),
    )
    return (
        remaining_limit / max(remaining_periods, 1)
    ).quantize(TWO_PLACES)


def _load_transfer_source_accounts(
    user_id: int, exclude_account_id: int,
) -> tuple[list[Account], int | None]:
    """Return source accounts for a contribution transfer plus a default ID.

    The default is the first checking-type account in the list,
    matching the pre-Commit-28 selection order.  Returns
    ``(accounts, default_source_id)`` where ``default_source_id``
    is ``None`` when no checking account is found.
    """
    source_accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            Account.id != exclude_account_id,
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id: int | None = None
    for acct in source_accounts:
        if acct.account_type_id == checking_type_id:
            default_source_id = acct.id
            break
    return source_accounts, default_source_id


def _has_active_recurring_transfer_to(account_id: int, user_id: int) -> bool:
    """Return True iff an active recurring transfer targets *account_id*."""
    template = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.to_account_id == account_id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    )
    return template is not None


# ── Public entry points ────────────────────────────────────────────


def compute_dashboard_data(user_id: int, account: Account) -> dict:
    """Build the full template context for ``investment/dashboard.html``.

    Mirrors :func:`savings_dashboard_service.compute_dashboard_data`:
    plain inputs (user id + the already-ownership-checked account
    instance), plain dict output, no Flask reads.

    The returned dict carries two underscore-prefixed keys
    (``_salary_profile_action`` / ``_active_profile_id``) that the
    route consumes via :func:`flask.url_for` to fill the template's
    ``salary_profile_url`` slot; the underscore prefix marks them as
    internal to the route-service contract.  Every other key is a
    template-facing context value.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked
            :class:`~app.models.account.Account` instance the route
            already loaded via
            :func:`app.utils.auth_helpers.get_or_404`.

    Returns:
        A dict with the template context plus the two route-side
        URL-resolution hints.
    """
    account_id = account.id
    params = _load_investment_params(account_id)

    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    # Canonical entries-aware current balance (E-25 / CRIT-01 / F-009
    # / R-1: Commit 8).  Routing through ``balances_for`` keeps the
    # investment dashboard's "current balance" tile aligned with the
    # grid for the same account/scenario/period.
    scenario = get_baseline_scenario(user_id)
    current_balance = _resolve_current_balance(
        account, scenario, current_period, all_periods,
    )

    (
        inputs, adapted_deductions, acct_contributions,
        deductions, active_profile,
    ) = _projection_inputs_for_account(
        user_id, account_id, params, all_periods, current_period,
    )

    periodic_contribution = inputs.periodic_contribution
    employer_params = inputs.employer_params
    ytd_contributions = inputs.ytd_contributions

    # HIGH-07 / F-043 / F-055: feed the limit-capped contribution to
    # ``calculate_employer_contribution`` so the per-period employer
    # card matches the growth chart's employer line and the year-end
    # ``year_summary_employer_total`` -- all three surfaces now read
    # the same capped value.
    capped_contribution_this_period = growth_engine.cap_contribution_at_limit(
        periodic_contribution,
        inputs.annual_contribution_limit,
        ytd_contributions,
    )
    employer_contribution_per_period = Decimal("0")
    if employer_params:
        employer_contribution_per_period = growth_engine.calculate_employer_contribution(
            employer_params, capped_contribution_this_period,
        )

    # Per-period contribution timeline from deductions and transfers.
    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=all_periods,
    )

    projection, chart_date_labels, chart_balances, chart_contributions = (
        _project_dashboard_balances(
            params=params,
            current_period=current_period,
            current_balance=current_balance,
            periodic_contribution=periodic_contribution,
            employer_params=employer_params,
            ytd_contributions=ytd_contributions,
            contributions=contributions,
            all_periods=all_periods,
        )
    )

    limit_info = _compute_limit_info(params, ytd_contributions)
    default_horizon = _compute_default_horizon(user_id, all_periods)

    contribution_prompt = _compute_contribution_prompt(
        user_id=user_id,
        account=account,
        params=params,
        deductions=deductions,
        active_profile=active_profile,
        ytd_contributions=ytd_contributions,
        all_periods=all_periods,
    )

    return {
        "account": account,
        "params": params,
        "current_balance": current_balance,
        "periodic_contribution": periodic_contribution,
        "employer_contribution_per_period": employer_contribution_per_period,
        "employer_params": employer_params,
        "limit_info": limit_info,
        "projection": projection,
        "chart_labels": chart_date_labels,
        "chart_balances": chart_balances,
        "chart_contributions": chart_contributions,
        "default_horizon": default_horizon,
        "show_contribution_prompt": contribution_prompt["show"],
        "is_deduction_path": contribution_prompt["is_deduction_path"],
        "source_accounts": contribution_prompt["source_accounts"],
        "default_source_id": contribution_prompt["default_source_id"],
        "suggested_amount": contribution_prompt["suggested_amount"],
        # Underscore-prefixed: route uses ``url_for`` to fill
        # ``salary_profile_url`` from these two values.
        "_salary_profile_action": contribution_prompt["salary_profile_action"],
        "_active_profile_id": contribution_prompt["active_profile_id"],
    }


def _project_dashboard_balances(
    *,
    params: InvestmentParams | None,
    current_period,
    current_balance: Decimal,
    periodic_contribution: Decimal,
    employer_params,
    ytd_contributions: Decimal,
    contributions,
    all_periods: list,
):
    """Run the dashboard growth projection and build the chart series.

    Empty results (no params or no current period) produce the four
    empty containers the template expects, preserving the
    pre-Commit-28 default-empty behaviour exactly.

    Returns:
        A 4-tuple ``(projection, chart_date_labels, chart_balances,
        chart_contributions)``: the engine's
        :class:`PeriodicBalance` list and the three string-formatted
        chart-data lists.
    """
    projection: list = []
    chart_period_ids: list[int] = []
    chart_balances: list[str] = []
    chart_contributions: list[str] = []

    if params and current_period:
        future_periods = [
            p for p in all_periods
            if p.period_index >= current_period.period_index
        ]
        projection = growth_engine.project_balance(
            current_balance=current_balance,
            assumed_annual_return=params.assumed_annual_return,
            periods=future_periods,
            periodic_contribution=periodic_contribution,
            employer_params=employer_params,
            annual_contribution_limit=params.annual_contribution_limit,
            ytd_contributions_start=ytd_contributions,
            contributions=contributions,
        )

        cumulative_contrib = Decimal("0")
        for pb in projection:
            chart_period_ids.append(pb.period_id)
            chart_balances.append(str(pb.end_balance.quantize(TWO_PLACES)))
            cumulative_contrib += pb.contribution + pb.employer_contribution
            chart_contributions.append(
                str((current_balance + cumulative_contrib).quantize(TWO_PLACES))
            )

    # Resolve period labels last so the lookup happens once.
    period_map = {p.id: p for p in all_periods}
    chart_date_labels: list[str] = []
    for pid in chart_period_ids:
        p = period_map.get(pid)
        if p:
            chart_date_labels.append(p.start_date.strftime("%b %Y"))

    return projection, chart_date_labels, chart_balances, chart_contributions


def _compute_contribution_prompt(
    *,
    user_id: int,
    account: Account,
    params: InvestmentParams | None,
    deductions: list[PaycheckDeduction],
    active_profile: SalaryProfile | None,
    ytd_contributions: Decimal,
    all_periods: list,
) -> dict:
    """Decide whether to show the "set up contributions" prompt + how.

    Three states:

    * **Hidden** when no params row exists or a deduction / recurring
      transfer is already linked.
    * **Deduction-path** when the account type is payroll-deduction
      funded (S6-04 centralised helper:
      :func:`account_projection.is_payroll_deduction_funded`).  The
      route renders a link to the salary profile -- this helper
      returns the action + profile id so the route can
      ``url_for`` the result.
    * **Transfer-path** otherwise: returns a suggested per-period
      amount, eligible source accounts, and the default source id.
    """
    result = {
        "show": False,
        "is_deduction_path": False,
        "source_accounts": [],
        "default_source_id": None,
        "suggested_amount": Decimal("0"),
        "salary_profile_action": None,
        "active_profile_id": None,
    }
    if not params:
        return result

    has_linked_deduction = bool(deductions)
    has_recurring_transfer = _has_active_recurring_transfer_to(
        account.id, user_id,
    )
    show = not has_linked_deduction and not has_recurring_transfer
    result["show"] = show
    if not show:
        return result

    is_deduction_path = is_payroll_deduction_funded(
        account.account_type_id, ref_cache,
    )
    result["is_deduction_path"] = is_deduction_path

    if is_deduction_path:
        if active_profile is not None:
            result["salary_profile_action"] = "edit"
            result["active_profile_id"] = active_profile.id
        else:
            result["salary_profile_action"] = "list"
        return result

    # Transfer-path: compute the suggested per-period amount and
    # load eligible source accounts.
    result["suggested_amount"] = _compute_suggested_contribution(
        params, ytd_contributions, all_periods,
    )
    result["source_accounts"], result["default_source_id"] = (
        _load_transfer_source_accounts(user_id, account.id)
    )
    return result


def compute_growth_chart_data(
    user_id: int,
    account: Account,
    horizon_years: int,
    what_if_raw: str | None,
) -> dict:
    """Build the context for the ``investment/_growth_chart.html`` fragment.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked
            :class:`~app.models.account.Account` instance.
        horizon_years: Slider value, post-validation; the caller is
            expected to clamp to ``[1, 40]`` but this helper does
            so defensively as well.
        what_if_raw: Optional unparsed ``what_if_contribution`` query
            value.  Invalid or negative inputs degrade gracefully to
            the single-line chart; ``Decimal("0")`` is a valid
            growth-only scenario.

    Returns:
        A dict with the chart fragment's context keys.  Returns the
        empty-chart shape when no :class:`InvestmentParams` row
        exists or the engine produced no periods.  Returns ``None``
        instead of the dict when the chart engine could not build any
        periods at all (the caller renders the empty chart in that
        case too).  The caller distinguishes ``None`` from "empty
        chart with what-if fields" by the presence/absence of the
        ``chart_labels`` key.
    """
    params = _load_investment_params(account.id)
    if not params:
        return {
            "chart_labels": [],
            "chart_balances": [],
            "chart_contributions": [],
        }

    horizon_years = max(1, min(horizon_years, 40))

    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    scenario = get_baseline_scenario(user_id)

    current_balance = _resolve_current_balance(
        account, scenario, current_period, all_periods,
    )

    # Synthetic future periods for the chosen horizon.
    end_date = date.today() + timedelta(days=horizon_years * 365)
    periods = growth_engine.generate_projection_periods(
        start_date=date.today(),
        end_date=end_date,
    )
    if not periods:
        return {
            "chart_labels": [],
            "chart_balances": [],
            "chart_contributions": [],
        }

    (
        inputs, adapted_deductions, acct_contributions, _deds, _profile,
    ) = _projection_inputs_for_account(
        user_id, account.id, params, all_periods, current_period,
    )

    contributions = build_contribution_timeline(
        deductions=adapted_deductions,
        contribution_transactions=acct_contributions,
        periods=all_periods,
    )

    projection = growth_engine.project_balance(
        current_balance=current_balance,
        assumed_annual_return=params.assumed_annual_return,
        periods=periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=params.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
        contributions=contributions,
    )

    period_map = {p.id: p for p in periods}
    chart_labels: list[str] = []
    chart_balances: list[str] = []
    chart_contributions: list[str] = []
    cumulative_contrib = Decimal("0")

    for pb in projection:
        p = period_map.get(pb.period_id)
        if p:
            chart_labels.append(p.start_date.strftime("%b %Y"))
        chart_balances.append(str(pb.end_balance.quantize(TWO_PLACES)))
        cumulative_contrib += pb.contribution + pb.employer_contribution
        chart_contributions.append(
            str((current_balance + cumulative_contrib).quantize(TWO_PLACES))
        )

    what_if_amount = _parse_what_if(what_if_raw)
    what_if_balances, comparison = _compute_what_if_overlay(
        what_if_amount=what_if_amount,
        params=params,
        inputs=inputs,
        current_balance=current_balance,
        periods=periods,
        projection=projection,
    )

    return {
        "chart_labels": chart_labels,
        "chart_balances": chart_balances,
        "chart_contributions": chart_contributions,
        "what_if_balances": what_if_balances,
        "what_if_amount": what_if_amount,
        "comparison": comparison,
    }


def _parse_what_if(what_if_raw: str | None) -> Decimal | None:
    """Parse the what-if string, returning ``None`` for invalid / negative input.

    Zero is a valid input ("growth-only scenario: what if I stop
    contributing?").  Anything that fails :class:`Decimal` parsing
    or is strictly negative degrades to ``None`` -- the caller
    interprets ``None`` as "no what-if overlay" and renders the
    single-line chart.
    """
    if not what_if_raw:
        return None
    try:
        value = Decimal(what_if_raw)
    except (InvalidOperation, ValueError):
        return None
    if value < Decimal("0"):
        return None
    return value


def _compute_what_if_overlay(
    *,
    what_if_amount: Decimal | None,
    params: InvestmentParams,
    inputs,
    current_balance: Decimal,
    periods: list,
    projection: list,
):
    """Run the what-if projection (when an amount is supplied) plus comparison.

    Returns:
        ``(what_if_balances, comparison)`` where ``what_if_balances``
        is a list of string-formatted end balances (one per period)
        and ``comparison`` is ``None`` or a 5-key dict describing
        committed-vs-what-if end balances.
    """
    if what_if_amount is None or not periods:
        return [], None

    # contributions=None forces the engine to use periodic_contribution
    # for every period (a flat-rate what-if).  Employer match is
    # recalculated automatically because the per-period loop passes
    # each period's contribution to ``calculate_employer_contribution``.
    what_if_projection = growth_engine.project_balance(
        current_balance=current_balance,
        assumed_annual_return=params.assumed_annual_return,
        periods=periods,
        periodic_contribution=what_if_amount,
        employer_params=inputs.employer_params,
        annual_contribution_limit=params.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
        contributions=None,
    )

    what_if_balances = [
        str(pb.end_balance.quantize(TWO_PLACES))
        for pb in what_if_projection
    ]

    comparison = None
    if projection and what_if_projection:
        committed_end = projection[-1].end_balance.quantize(TWO_PLACES)
        whatif_end = what_if_projection[-1].end_balance.quantize(TWO_PLACES)
        difference = (whatif_end - committed_end).quantize(TWO_PLACES)
        comparison = {
            "committed_end": committed_end,
            "whatif_end": whatif_end,
            "difference": difference,
            "is_positive": difference > Decimal("0"),
            "is_zero": difference == Decimal("0"),
        }
    return what_if_balances, comparison
