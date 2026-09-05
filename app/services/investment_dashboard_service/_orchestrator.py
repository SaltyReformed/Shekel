"""Investment dashboard -- the two orchestrators the route delegates to.

Thin composition only: each entry loads the shared feed
(:mod:`._context`) once and merges what :mod:`._cards` and :mod:`._chart`
return, so ``investment.py`` stays a delegator mirroring ``savings.py``.

Boundary discipline (``CLAUDE.md``): no Flask symbol; the route owns
``current_user`` / ``request`` / ``url_for`` and the HTTP responses, and
``salary_profile_url`` is resolved route-side from the two underscore-prefixed
hints these return.
"""

from app.models.account import Account
from app.extensions import db
from app.services import balance_at
from app.services.balance_at import BalanceContext

from ._cards import (
    _compute_contribution_prompt,
    _compute_default_horizon,
    _compute_employer_funding,
    _compute_employer_per_period,
    _compute_limit_info,
)
from ._chart import _assemble_chart_context, _empty_chart_context
from ._context import (
    _current_period,
    _load_investment_params,
    _load_projection_context,
    _resolve_anchor_as_of,
    _resolve_current_balance,
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
    ctx = _load_projection_context(user_id, account, params)
    default_horizon = _compute_default_horizon(ctx)
    # C2: initial chart at the default horizon on the fragment's own basis.
    chart_context = (
        _assemble_chart_context(account, ctx, default_horizon, None)
        if params else _empty_chart_context()
    )
    # Measured growth since the anchor (None -> chip hidden), via the seam.
    # The ``scenario is not None`` ternary that used to wrap this went at plan
    # step X-v2 (ruling R-BW): hiding the chip was this surface's private
    # answer to a state fifteen others answered differently, and the seam's own
    # raise plus one handler is the answer now.  ``None`` here still hides the
    # chip, and now means only what the producer means by it.
    growth = balance_at.investment_growth_since_anchor(
        account, ctx.balance_ctx, ctx.current_period,
    )

    return {
        "account": account,
        "params": params,
        "current_balance": ctx.current_balance,
        "anchor_as_of": ctx.anchor_as_of,
        "periodic_contribution": ctx.inputs.periodic_contribution,
        "employer_contribution_per_period": _compute_employer_per_period(
            ctx.inputs, ctx.feed, ctx.current_period,
        ),
        "employer_params": ctx.inputs.employer_params,
        "limit_info": _compute_limit_info(params, ctx.inputs.ytd_contributions),
        "default_horizon": default_horizon,
        "growth_since_anchor": growth[0] if growth else None,
        "growth_since_anchor_contributed": growth[1] if growth else None,
        **chart_context,  # projection + history + Today/retirement markers (C2)
        **_compute_contribution_prompt(user_id, account, ctx),  # + funding, hints
        # R-SAL5's selector options, and the notice for a configured employer
        # contribution whose funding job is not set (salary:R14-b).
        **_compute_employer_funding(ctx),
    }


def compute_balance_hero_cell(user_id: int, account_id: int) -> dict | None:
    """Narrow producer for the investment balance hero cell (C4 revert target).

    Backs ``investment.balance_hero`` (the anchor editor's Cancel / Escape /
    409 revert target): the model-from-anchor balance the headline shows plus
    the anchor caption date, so the reverted cell restores the page's figure.
    ``None`` (a 404) when the account is not the user's active account
    (404-for-both).

    **It reads the headline's OWN producer** (:func:`._context._resolve_current_balance`,
    finding N-81).  It used to read the seam SCALAR instead, which agreed only
    by accident: the kind-correct scalar for an investment was period-granular,
    so it WAS the map read at the containing period.  Plan step X-g2b makes the
    scalar date-precise, and the two then separate by the accrual between today
    and the period's end -- measured $22.59 / $9.65 / $26.05 on the three real
    accounts -- so cancelling the editor would have restored a figure the page
    was never showing.  One cell, one producer.

    **The period it reads at comes off its own read pass** (plan step C2-f2c).
    It was ``pay_period_service.get_current_period(user_id)`` -- a second query
    on a second clock, beside a ``BalanceContext`` built one line above whose
    calendar already answers it.  The two agreed; what they could not
    guarantee is that they would, and this cell exists to restore the figure
    the page beside it is showing.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != user_id or not account.is_active:
        return None
    balance_ctx = BalanceContext.build(user_id)
    balance = _resolve_current_balance(
        account, balance_ctx, _current_period(balance_ctx),
    )
    return {
        "account": account,
        "current_balance": balance,
        "anchor_as_of": _resolve_anchor_as_of(account),
    }
