"""
Shekel Budget App -- Companion View Routes

Mobile-first companion interface for viewing budgeted transactions,
tracking spending progress via entries, and marking transactions
as Paid.  The companion sees only transactions from templates
flagged ``companion_visible=True`` belonging to their linked owner.

Period navigation allows browsing past and future pay periods.
Entry CRUD is handled by the entries blueprint, which already
supports companion access via ``_get_accessible_transaction``.

Rendering pipeline (mobile-first v3 plan Commit 13):

  * ``companion_service.get_visible_transactions`` returns everything one
    render needs off the linked owner's ONE derived pay calendar: the
    visibility-filtered transactions, the period itself as a
    ``DerivedPeriod``, and the paychecks either side of it for the
    navigation header (plan step **C2-f2b**).  ``_period_neighbours`` lived
    here until that step and asked the last two itself, which meant loading
    the owner's whole calendar a second time.
  * ``grid_view_service.build_row_keys`` collapses those into
    one row per (category, template) for income + expense
    sections; ``grid_view_service.build_matched_by_row_period``
    builds the per-cell match dict.
  * ``entry_service.build_entry_sums_dict`` produces the unified
    entry aggregate (debit, credit, total, count, remaining,
    over_budget, pct) consumed by the mobile progress bar.
  * The shared ``grid/_mobile_this_period.html`` partial renders
    the cards via ``render_row_card`` with ``can_edit=False``
    so the per-card inline action bar drops Edit Amount / Open
    Full while keeping Mark Paid (R-7).  The partial's own
    period-nav header is suppressed via ``show_period_nav=False``
    because companion's prev/next URLs target
    ``/companion/period/<id>`` (rendered by ``companion/index.html``
    directly above the include) rather than the partial's
    ``/grid?periods=1&offset=N`` shape.
"""

import logging

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.category import Category
from app.services import companion_service, grid_view_service
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import amounts_by_id
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.utils.dates import display_today
from app.exceptions import NotFoundError

logger = logging.getLogger(__name__)

companion_bp = Blueprint("companion", __name__, url_prefix="/companion")


def _companion_or_redirect():
    """Check if current user is a companion; redirect owners to grid.

    Returns:
        None if the current user is a companion, or a redirect
        Response if they are an owner.
    """
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id != companion_role_id:
        return redirect(url_for("grid.index"))
    return None


def _build_partial_context(
    view: companion_service.CompanionPageRead,
) -> dict:
    """Assemble the ``_mobile_this_period.html`` context for companion.

    Centralises the rendering inputs shared by :func:`index` and
    :func:`period_view`: row-key generation, cell-match dict, and
    entry-sums dict.  Builds the linked owner's category list for
    row-key grouping; ``build_row_keys`` needs the full category
    set (active + archived) so transactions on archived categories
    still render in their original group.

    **The period it publishes is a
    :class:`~app.services.pay_calendar.DerivedPeriod` since plan step
    C2-f2b**, which is what keeps ``grid/_mobile_this_period.html`` ONE
    partial: the owner's mobile grid moved to derived periods in that step,
    and a shared partial reading ``period.period_id`` from one caller and
    ``period.id`` from the other is not shared, it is forked.

    Args:
        view: The page's :class:`~app.services.companion_service.CompanionPageRead`.

    Returns:
        Dict with the keys the shared partial expects, ready to
        unpack into ``render_template``.  Notably absent: ``columns``
        (companion shows no money summary per Q-2 (c), and the partial's
        ``columns is defined`` guard is what suppresses it);
        ``all_periods`` / ``start_offset``
        (the partial's jump-to and prev/next are suppressed via
        ``show_period_nav=False``).
    """
    transactions = view.transactions
    owner_id = current_user.linked_owner_id
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=owner_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    income_row_keys = grid_view_service.build_row_keys(
        transactions, all_categories, is_income_section=True,
    )
    expense_row_keys = grid_view_service.build_row_keys(
        transactions, all_categories, is_income_section=False,
    )
    matched_by_row_period = grid_view_service.build_matched_by_row_period(
        income_row_keys, expense_row_keys, [view.period], transactions,
    )
    # The read pass's OWN basis, built for the LINKED OWNER: the companion is
    # looking at their schedule, so the salary profiles and loans that price
    # these rows are the owner's (plan step X-au-c2b).  One basis for the whole
    # page, and both builders below read the same map, so a card's amount and
    # its progress numerator cannot come from two derivations.
    budgets = amounts_by_id(
        transactions, BalanceContext.build(owner_id).amounts(),
    )
    entry_sums = build_entry_sums_dict(transactions, budgets)
    # Pre-render context for the inline envelope entries list -- see
    # the matching comment in app/routes/grid/page.py::_build_grid_row_data
    # for the rate-limit rationale.  Companion shares the macro with
    # owner mobile (mobile-first v3 plan Commit 13), so it needs the
    # same context shape.
    entry_lists = build_entry_lists_dict(transactions, budgets)
    return {
        "periods": [view.period],
        "current_period": view.period,
        "income_row_keys": income_row_keys,
        "expense_row_keys": expense_row_keys,
        "matched_by_row_period": matched_by_row_period,
        "budgets": budgets,
        "entry_sums": entry_sums,
        "entry_lists": entry_lists,
        # The USER's civil day, never the process's.  This reaches
        # ``grid/_transaction_entries.html``'s add-purchase form as the
        # ``purchased_on`` default and both date pickers' ``max``, and
        # ``entry_service._reject_future_purchase_date`` judges that field
        # against ``display_today()`` (ruling R-M).  With ``date.today()``
        # here -- which is what this passed until plan step C2-f2b -- the two
        # clocks disagree on any process not pinned to ``America/New_York``,
        # and the companion's own form defaulted to a date its own server
        # rejects.  The owner's mobile card path
        # (``routes/transactions/_helpers._render_mobile_card``) had already
        # been corrected; this was the same two-clock shape left on the
        # surface that shares its macro.
        "today": display_today(),
        "can_edit": False,
        "show_period_nav": False,
    }


@companion_bp.route("/")
@login_required
def index():
    """Companion landing page: current period's visible transactions.

    Redirects owner users to the grid.  For companions, loads the
    current pay period's transactions (filtered by companion
    visibility), computes the shared row-key + match-dict +
    entry-sums context, and renders the companion view via the
    shared ``_mobile_this_period.html`` partial.
    """
    redir = _companion_or_redirect()
    if redir is not None:
        return redir

    try:
        view = companion_service.get_visible_transactions(current_user.id)
    except NotFoundError:
        # No current period or misconfigured companion -- show empty view.
        return render_template(
            "companion/index.html",
            transactions=[],
            period=None,
            prev_period=None,
            next_period=None,
        )

    return render_template(
        "companion/index.html",
        transactions=view.transactions,
        period=view.period,
        prev_period=view.previous,
        next_period=view.next_period,
        **_build_partial_context(view),
    )


@companion_bp.route("/period/<int:period_id>")
@login_required
def period_view(period_id):
    """Navigate to a specific pay period's visible transactions.

    Same logic as ``index`` but with an explicit period_id.
    Redirects owner users to the grid.  Returns 404 if the
    period does not exist or does not belong to the companion's
    linked owner.

    Args:
        period_id (int): The pay period ID to display.
    """
    redir = _companion_or_redirect()
    if redir is not None:
        return redir

    try:
        view = companion_service.get_visible_transactions(
            current_user.id,
            period_id=period_id,
        )
    except NotFoundError:
        return "Not found", 404

    return render_template(
        "companion/index.html",
        transactions=view.transactions,
        period=view.period,
        prev_period=view.previous,
        next_period=view.next_period,
        **_build_partial_context(view),
    )
