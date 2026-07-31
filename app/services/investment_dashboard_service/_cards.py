"""Investment dashboard -- the CARDS beside the chart.

The contribution limit card, the employer-match card, the horizon slider's
default, the "set up contributions" prompt and the transfer-source picker it
offers.  Split from the chart at this package's module-size ceiling on plan
step D1c's cohesion line: these answer "what does this account look like right
now", where :mod:`._chart` answers "what does it look like from here on".

Boundary discipline (``CLAUDE.md``): no Flask symbol, all money is
:class:`~decimal.Decimal`.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.services import growth_engine
from app.services.account_projection import is_payroll_deduction_funded
from app.services.investment_projection import InvestmentInputs
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.utils.money import percent_complete, round_money

from ._context import _ProjectionContext, _load_planned_retirement_date

# The chart's horizon when the user has set no planned retirement date.
_FALLBACK_HORIZON_YEARS = 20

# The contribution-funding discriminator the C1 chip captions off.
CONTRIBUTION_FUNDING_DEDUCTION = "deduction"
CONTRIBUTION_FUNDING_TRANSFER = "transfer"
CONTRIBUTION_FUNDING_NONE = "none"


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

    C1 (Loop B P1): the clamped ``pct`` cannot express OVER-contribution
    (an excess $600 and a perfect max both read 100%), so the dict also
    carries ``is_over`` (``ytd > limit``) and ``over_amount``
    (``ytd - limit`` :class:`~decimal.Decimal`, else ``None``) for the
    goal-framed bar's overage text.  E-12 zero-cap preserved exactly: a zero
    cap is over by the full positive YTD (``100 > 0``), not over at zero YTD.

    Returns:
        ``{"limit", "ytd", "pct", "is_over", "over_amount"}`` when a cap is
        configured, else ``None`` (no cap / no params) to hide the card.
    """
    if investment_params is None:
        return None
    limit = investment_params.annual_contribution_limit
    if limit is None:
        return None
    is_over = ytd_contributions > limit
    over_amount = (
        round_money(ytd_contributions - limit) if is_over else None
    )
    if limit > 0:
        # Canonical money.percent_complete (ROUND_HALF_UP, clamped [0, 100],
        # Decimal) -- the one "percent funded" contract the budget-dashboard
        # savings cards and the companion entry view also use, so a fractional
        # YTD rounds the same everywhere instead of truncating only here
        # (deep-quality-hunt #78).  limit > 0 guards the divide, so
        # percent_complete's own target <= 0 branch never collides with the
        # E-12 zero-cap semantics below.
        pct = percent_complete(ytd_contributions, limit)
    elif ytd_contributions > 0:
        # Cap is zero, contributions exist -> 100% used (over).  Kept explicit
        # (not percent_complete, which returns 0 for a <= 0 target) to preserve
        # the E-12 / HIGH-06 zero-cap semantics matching the growth engine's
        # min(contribution, 0) = 0.
        pct = Decimal("100")
    else:
        # Cap and YTD both zero -> 0% used.
        pct = Decimal("0")
    return {
        "limit": limit,
        "ytd": ytd_contributions,
        "pct": pct,
        "is_over": is_over,
        "over_amount": over_amount,
    }


def _compute_default_horizon(user_id: int, all_periods: list) -> int:
    """Return the chart slider's default horizon in years.

    Order of preference: the user's planned retirement year if set,
    else the last projection period's year, else the
    :data:`_FALLBACK_HORIZON_YEARS` constant.  Always >= 1.
    """
    retirement_date = _load_planned_retirement_date(user_id)
    if retirement_date is not None:
        return max(1, retirement_date.year - date.today().year)
    if all_periods:
        last_period = all_periods[-1]
        return max(1, (last_period.end_date.year - date.today().year) + 1)
    return _FALLBACK_HORIZON_YEARS


def _compute_suggested_contribution(
    investment_params: InvestmentParams,
    ytd_contributions: Decimal,
    all_periods: list,
    current_period: PayPeriod | None,
) -> Decimal:
    """Return the per-period contribution suggestion under the annual limit.

    E-12 / HIGH-06 (Commit 24): same ``is not None`` convention as
    :func:`_compute_limit_info`.  A stored zero cap produces a zero
    suggestion (no contribution within the cap), not the legacy
    $500 fallback that truthiness conflated with the "no cap
    configured" state.  When no cap is configured the suggestion is
    zero (Brokerage-style accounts -- no IRS limit to spread over
    remaining periods).

    ``remaining_periods`` is anchored on ``current_period.start_date`` --
    the SAME boundary the subtracted ``ytd_contributions`` uses
    (:func:`investment_projection._current_year_period_ids`: same
    calendar year, ``<= current_period.start_date``).  So the current
    period is counted once -- in YTD (already contributed) -- and the
    remaining limit is spread over the periods STRICTLY AFTER it.
    Anchoring on ``date.today()`` instead double-counted the current
    period on the single calendar day a period begins
    (``today == period start``), where it landed in BOTH the YTD window
    and the remaining spread (deep-quality-hunt #59).

    **The no-current-period fallback to ``date.today()`` went at plan step X-x2**
    (ruling R-CY): its caller resolves the period through
    ``require_current_period``, so the state it covered is answered once by the
    application handler rather than by a second boundary date here.
    """
    if investment_params.annual_contribution_limit is None:
        return Decimal("0")
    boundary = current_period.start_date
    remaining_periods = sum(
        1 for p in all_periods
        if p.start_date.year == boundary.year
        and p.start_date > boundary
    )
    remaining_limit = max(
        investment_params.annual_contribution_limit
        - (ytd_contributions or Decimal("0")),
        Decimal("0"),
    )
    return round_money(remaining_limit / max(remaining_periods, 1))


def _compute_employer_per_period(inputs: InvestmentInputs) -> Decimal:
    """Return the per-period employer contribution at the capped employee rate.

    HIGH-07 / F-043 / F-055: feeds the limit-capped contribution to
    :func:`growth_engine.calculate_employer_contribution` so the
    per-period employer card matches the growth chart's employer line
    and the year-end ``year_summary_employer_total`` -- all three
    surfaces read the same capped value.  Returns ``Decimal("0")`` when
    the account configures no employer match.
    """
    capped_contribution = growth_engine.cap_contribution_at_limit(
        inputs.periodic_contribution,
        inputs.annual_contribution_limit,
        inputs.ytd_contributions,
    )
    if not inputs.employer_params:
        return Decimal("0")
    return growth_engine.calculate_employer_contribution(
        inputs.employer_params, capped_contribution,
    )


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
    return active_recurring_transfer_template(account_id, user_id) is not None


def _compute_contribution_prompt(
    user_id: int, account: Account, ctx: _ProjectionContext,
) -> dict:
    """Decide whether to show the "set up contributions" prompt + how.

    Returns the prompt keys (``show_contribution_prompt`` /
    ``is_deduction_path`` / ``source_accounts`` / ``default_source_id`` /
    ``suggested_amount``), the C1 ``contribution_funding`` discriminator,
    and the two underscore-prefixed ``_salary_profile_action`` /
    ``_active_profile_id`` route hints so :func:`compute_dashboard_data`
    can merge them with ``**``.  Prompt states: hidden (no params, or a
    deduction / recurring transfer already linked); deduction-path (payroll
    funded -- hands the route the salary-profile action + id); transfer-path
    (suggested amount + eligible sources).
    """
    has_linked_deduction = bool(ctx.deductions)
    has_recurring_transfer = _has_active_recurring_transfer_to(
        account.id, user_id,
    )
    # C1: funding provenance for the contribution chip's caption -- resolved
    # here (where both funding signals are already computed) and surfaced
    # even when the setup prompt is hidden.  A linked deduction wins over a
    # recurring transfer so a doubly-funded 401(k) captions its payroll
    # source.
    if has_linked_deduction:
        funding = CONTRIBUTION_FUNDING_DEDUCTION
    elif has_recurring_transfer:
        funding = CONTRIBUTION_FUNDING_TRANSFER
    else:
        funding = CONTRIBUTION_FUNDING_NONE
    result = {
        "show_contribution_prompt": False,
        "is_deduction_path": False,
        "source_accounts": [],
        "default_source_id": None,
        "suggested_amount": Decimal("0"),
        "contribution_funding": funding,
        "_salary_profile_action": None,
        "_active_profile_id": None,
    }
    if not ctx.params:
        return result

    show = not has_linked_deduction and not has_recurring_transfer
    result["show_contribution_prompt"] = show
    if not show:
        return result

    is_deduction_path = is_payroll_deduction_funded(
        account.account_type_id, ref_cache,
    )
    result["is_deduction_path"] = is_deduction_path

    if is_deduction_path:
        if ctx.active_profile is not None:
            result["_salary_profile_action"] = "edit"
            result["_active_profile_id"] = ctx.active_profile.id
        else:
            result["_salary_profile_action"] = "list"
        return result

    # Transfer-path: compute the suggested per-period amount and
    # load eligible source accounts.
    result["suggested_amount"] = _compute_suggested_contribution(
        ctx.params, ctx.inputs.ytd_contributions,
        ctx.all_periods, ctx.current_period,
    )
    result["source_accounts"], result["default_source_id"] = (
        _load_transfer_source_accounts(user_id, account.id)
    )
    return result
