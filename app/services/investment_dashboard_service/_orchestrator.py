"""Investment dashboard -- the two orchestrators the route delegates to.

Thin composition only: each entry loads the shared feed
(:mod:`._context`) once and merges what :mod:`._cards` and :mod:`._chart`
return, so ``investment.py`` stays a delegator mirroring ``savings.py``.

Boundary discipline (``CLAUDE.md``): no Flask symbol; the route owns
``current_user`` / ``request`` / ``url_for`` and the HTTP responses, and
``salary_profile_url`` is resolved route-side from the two underscore-prefixed
hints these return.
"""

from decimal import Decimal

from app.models.account import Account
from app.extensions import db
from app.services import balance_at, pay_period_service
from app.services.balance_at import BalanceContext

from ._cards import (
    _compute_contribution_prompt,
    _compute_default_horizon,
    _compute_employer_per_period,
    _compute_limit_info,
)
from ._chart import _assemble_chart_context, _empty_chart_context
from ._context import (
    _load_investment_params,
    _load_projection_context,
    _resolve_anchor_as_of,
)


def compute_dashboard_data(user_id: int, account: Account) -> dict:
    """Build the full template context for ``investment/dashboard.html``.

    Mirrors :func:`savings_dashboard_service.compute_dashboard_data`: plain
    inputs (user id + the ownership-checked account), plain dict output, no
    Flask reads.  The dict carries two underscore-prefixed route hints
    (``_salary_profile_action`` / ``_active_profile_id``) the route resolves
    via :func:`flask.url_for` into ``salary_profile_url``; every other key is
    template-facing.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked account the route loaded.

    Returns:
        The template context plus the two route-side URL hints.
    """
    params = _load_investment_params(account.id)
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    ctx = _load_projection_context(
        user_id, account, params, all_periods, current_period,
    )
    default_horizon = _compute_default_horizon(user_id, all_periods)
    # C2: initial chart at the default horizon on the fragment's synthetic basis.
    chart_context = (
        _assemble_chart_context(account, ctx, default_horizon, None)
        if params else _empty_chart_context()
    )
    # Measured growth since the anchor (None -> chip hidden), via the seam.
    growth = (
        balance_at.investment_growth_since_anchor(
            account, ctx.balance_ctx, all_periods, current_period,
        )
        if ctx.balance_ctx.scenario is not None else None
    )

    return {
        "account": account,
        "params": params,
        "current_balance": ctx.current_balance,
        "anchor_as_of": ctx.anchor_as_of,
        "periodic_contribution": ctx.inputs.periodic_contribution,
        "employer_contribution_per_period": _compute_employer_per_period(
            ctx.inputs,
        ),
        "employer_params": ctx.inputs.employer_params,
        "limit_info": _compute_limit_info(params, ctx.inputs.ytd_contributions),
        "default_horizon": default_horizon,
        "growth_since_anchor": growth[0] if growth else None,
        "growth_since_anchor_contributed": growth[1] if growth else None,
        **chart_context,  # projection + history + Today/retirement markers (C2)
        **_compute_contribution_prompt(user_id, account, ctx),  # + funding, hints
    }


def compute_balance_hero_cell(user_id: int, account_id: int) -> dict | None:
    """Narrow producer for the investment balance hero cell (C4 revert target).

    Backs ``investment.balance_hero`` (the anchor editor's Cancel / Escape /
    409 revert target): the model-from-anchor balance the headline shows (via
    the :mod:`app.services.balance_at` seam scalar) plus the anchor caption
    date, so the reverted cell restores the page's figure.  ``None`` (a 404)
    when the account is not the user's active account (404-for-both).
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != user_id or not account.is_active:
        return None
    balance_ctx = BalanceContext.build(user_id)
    balance = (
        balance_at.balance_at(account, balance_ctx, balance_ctx.as_of)
        if balance_ctx.scenario is not None
        else account.current_anchor_balance or Decimal("0.00")
    )
    return {
        "account": account,
        "current_balance": balance,
        "anchor_as_of": _resolve_anchor_as_of(account, balance_ctx),
    }
