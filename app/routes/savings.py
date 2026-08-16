"""
Shekel Budget App -- Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.  Goal CRUD endpoints for creating, editing,
and deleting savings goals.
"""

import json
import logging
from collections.abc import Mapping
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.digit_strings import parse_row_id
from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import GoalMode, IncomeUnit
from app.models.savings_goal import SavingsGoal
from app.routes._commit_helpers import (
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
    handle_stale_form_conflict,
)
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import account_service, savings_dashboard_service
from app.services.savings_dashboard_service import NetWorthRegion

logger = logging.getLogger(__name__)

savings_bp = Blueprint("savings", __name__)

# Chart.js x-axis label format for the net-worth trend: month abbreviation
# plus un-padded day (e.g. "Jun 5"), matching the dashboard pulse chart.
_NET_WORTH_LABEL_FORMAT = "%b %-d"

_create_schema = SavingsGoalCreateSchema()
_update_schema = SavingsGoalUpdateSchema()


# Chart.js x-axis label format for the Horizon range: month + full year
# (e.g. "Dec 2049") -- the annual samples span decades, so the year matters.
_HORIZON_LABEL_FORMAT = "%b %Y"


def _milestone_axis_x(dates: list[date], target: date) -> float:
    """Map a milestone date to its fractional x-index on the annual axis.

    The Horizon stream's x-axis is the annual sample dates (a Chart.js
    category axis, evenly spaced), but a milestone (a loan payoff, a
    net-worth crossing) falls on an exact date BETWEEN two samples.  This
    locates it as a fractional index -- ``i + (target - dates[i]) /
    (dates[i + 1] - dates[i])`` for the bracket ``dates[i] <= target <=
    dates[i + 1]`` -- so the client's flag plugin positions the flag
    precisely via ``xScale.getPixelForValue(x)`` (Chart.js interpolates
    fractional category indices linearly).  Presentation geometry, so
    ``float`` lives here; a date at or beyond the sampled domain clamps to
    its edge, so ``x`` is always in ``[0, len(dates) - 1]``.

    Args:
        dates: The horizon sample dates (ascending; index 0 is today).
        target: The milestone's :class:`datetime.date`.

    Returns:
        The fractional x-index of *target* on the sample axis.
    """
    last = len(dates) - 1
    if target <= dates[0]:
        return 0.0
    if target >= dates[last]:
        return float(last)
    for index in range(last):
        if dates[index] <= target <= dates[index + 1]:
            # The samples are distinct calendar year-ends, so the span in
            # days is never zero (no divide-by-zero guard needed).
            span_days = (dates[index + 1] - dates[index]).days
            offset = (target - dates[index]).days / span_days
            return index + offset
    return float(last)


def _serialize_horizon(horizon: dict | None) -> dict | None:
    """Serialize the Horizon-range producer output to Chart.js-ready data.

    The presentation boundary for the cockpit's ``Horizon`` range (P-AC1
    Loop B P1): maps the producer's parallel ``Decimal`` band series
    (``composition``) and net trajectory to ``float`` arrays, the annual
    sample dates to ``%b %Y`` labels, and each milestone to its chip ``label``
    plus a fractional axis position ``x`` (via :func:`_milestone_axis_x`, so
    the client's flag plugin places the flag between the annual samples).
    ``None`` (the user has no pay periods) passes straight through so the
    client hides the range.

    **It consumes every key the producer publishes, at EVERY level, and that is
    a contract rather than a coincidence** (plan steps X-q2 / X-s1, findings
    N-100 / N-104): each key is subscripted here, so a producer output nothing
    renders cannot exist without failing
    ``TestHorizonSerialization.test_every_published_key_is_read``, which removes
    each key in turn -- the horizon's own, and each MILESTONE's -- and requires
    this to raise.  ``build_horizon`` published ``horizon_end`` and
    ``is_loan_free`` for months with no consumer anywhere (deleted at X-q2,
    along with the narrow producer that carried them to nobody), and the
    milestone dicts then carried a machine ``kind`` this function copied
    STRAIGHT INTO the payload, where the client never read it -- the same
    defect one level down, riding inside a live key where the guard could not
    see it, which is why the guard now descends (deleted at X-s1).

    **What this EMITS is likewise only what the client reads.**  The flag
    plugin takes ``x`` and ``label`` and nothing else
    (``net_worth_cockpit.js:393``, ``:407``, ``:419``), so the milestone's
    ``date`` -- which ``x`` is computed FROM, and which is therefore already
    spent by the time the payload is built -- is not carried across.

    Args:
        horizon: The ``net_worth.horizon`` dict from
            :func:`~app.services.savings_dashboard_service._horizon.build_horizon`
            (``dates`` / ``net`` / ``composition`` / ``milestones`` /
            ``current_index`` -- its complete key set), or ``None``.

    Returns:
        A dict ``{"labels", "net", "composition", "milestones",
        "current_index"}`` for the ``data-chart`` payload, or ``None``.
    """
    if horizon is None:
        return None
    return {
        "labels": [
            point.strftime(_HORIZON_LABEL_FORMAT) for point in horizon["dates"]
        ],
        "net": [float(value) for value in horizon["net"]],
        "composition": {
            band: [float(value) for value in band_series]
            for band, band_series in horizon["composition"].items()
        },
        "milestones": [
            {
                "label": milestone["label"],
                "x": _milestone_axis_x(horizon["dates"], milestone["date"]),
            }
            for milestone in horizon["milestones"]
        ],
        "current_index": horizon["current_index"],
    }


def _serialize_net_worth_chart(net_worth: NetWorthRegion) -> str:
    """Serialize BOTH net-worth ranges into one Chart.js JSON payload.

    The single Chart.js serialization boundary for the cockpit's net-worth
    region (coding-standards: ``float`` lives only here, never in a
    calculation).  Emits ONE payload carrying both ranges the element toggles
    between (P-AC1): the ``2 years`` engine-real series (its ``net`` total and
    the per-category ``composition`` split) with the ``current_index``
    solid/dashed boundary, plus the nested ``horizon`` range from
    :func:`_serialize_horizon`.  Every money figure is mapped from the
    producer's money-precise ``Decimal`` to ``float`` here.

    ``current_index`` is the position of the current period within the
    ``2 years`` series: the leading points are the honest history tail the
    client draws solid, the rest the forward projection.

    **It published ``assets`` and ``liabilities`` too, and nothing read them**
    (plan step X-s1, finding N-104).  They were kept "so the current chart
    script renders unchanged until the P2 element replaces it" -- and that
    element is ``net_worth_cockpit.js``, which has shipped: ``selectRange``
    (``:173-192``) takes ``labels`` / ``net`` / ``composition`` /
    ``current_index`` / ``horizon`` and never touches the two totals, because
    the stacked bands ARE the two totals (the asset-side bands sum to
    ``assets`` and the ``liability`` band equals ``liabilities`` -- asserted
    against the PRODUCER series in
    ``TestDashboardNetWorthContext.test_chart_json_parses_to_expected_shape_with_floats``).
    A justification that names a consumer which does not read the value is the
    shape this arc keeps finding.  The PRODUCER's copies went too, one review
    later (ruling R-BG): with the payload copies gone they had no ``app/``
    reader either, and the cross-page equality oracle now sums the bands the
    chart actually draws.  (This sentence said the producer keys stay until
    plan step X-t5 -- a docstring describing the tree as it was for one commit.)

    **The two ranges are read DIFFERENTLY on purpose** (plan step X-w3, ruling
    R-CI).  The ``2 years`` half is a value object -- ``series.net``,
    ``series.composition`` -- while the nested ``horizon`` half stays a dict and
    is subscripted in :func:`_serialize_horizon`.  That asymmetry is the ruling,
    not an oversight: the horizon's key set at every level is pinned by
    ``TestHorizonSerialization``, which removes each key in turn and requires
    this module to raise, so it proves every published key is READ.  A dataclass
    would state the contract and not prove it, and findings N-100 and N-104 are
    what that guard cost.

    Args:
        net_worth: The ``compute_dashboard_data`` ``net_worth`` region (its
            ``series`` and its ``horizon``).

    Returns:
        A JSON string for the ``data-chart`` attribute.
    """
    series = net_worth.series
    return json.dumps({
        "labels": [
            point.end_date.strftime(_NET_WORTH_LABEL_FORMAT)
            for point in series.periods
        ],
        "net": [float(value) for value in series.net],
        "current_index": series.current_index,
        "composition": {
            band: [float(value) for value in band_series]
            for band, band_series in series.composition.items()
        },
        "horizon": _serialize_horizon(net_worth.horizon),
    })


# Sparkline SVG geometry: the normalized polyline viewBox the cards draw in.
_SPARK_VIEW_W = 100
_SPARK_VIEW_H = 28


def _serialize_sparklines(sparklines: dict) -> dict:
    """Normalize each account's sparkline series to an SVG polyline string.

    The presentation boundary for the per-account sparklines: the only place
    ``float`` enters for them.  A sparkline is a SHAPE, not a value (the
    money figures are rendered by the macro), so this maps each series to
    evenly-spaced x and a y inverted into the ``_SPARK_VIEW_W`` x
    ``_SPARK_VIEW_H`` viewBox (SVG y grows downward, so a rising balance
    rises on screen).  The producer only passes informative series (spread
    above a positive floor), so ``max != min`` and there is no
    divide-by-zero.

    Args:
        sparklines: ``{account_id: [Decimal series]}`` from
            :func:`~app.services.savings_dashboard_service._net_worth.compute_sparklines`.

    Returns:
        ``{account_id: "x0,y0 x1,y1 ..."}`` -- the ``<polyline>`` points for
        each account's sparkline.
    """
    points_by_id = {}
    for account_id, series in sparklines.items():
        low = float(min(series))
        span = float(max(series)) - low
        last = len(series) - 1
        coords = []
        for index, value in enumerate(series):
            x = (index / last) * _SPARK_VIEW_W if last else 0.0
            y = _SPARK_VIEW_H - ((float(value) - low) / span) * _SPARK_VIEW_H
            coords.append(f"{x:.2f},{y:.2f}")
        points_by_id[account_id] = " ".join(coords)
    return points_by_id

# Fields allowed in goal updates.  Income-relative fields are included
# so mode changes propagate correctly.
_GOAL_UPDATE_FIELDS = frozenset({
    "name", "target_amount", "target_date", "contribution_per_period",
    "account_id", "is_active", "goal_mode_id", "income_unit_id",
    "income_multiplier",
})


def _goal_form_context(goal=None):
    """Build common template context for the goal create/edit form.

    Loads the account list, goal mode ref table, and income unit ref
    table that the form dropdowns need.

    Args:
        goal: An existing SavingsGoal for edit mode, or None for create.

    Returns:
        dict with keys: goal, accounts, goal_modes, income_units.
    """
    accounts = account_service.list_active_accounts(current_user.id)
    goal_modes = GoalMode.query.order_by(GoalMode.id).all()
    income_units = IncomeUnit.query.order_by(IncomeUnit.id).all()
    return {
        "goal": goal,
        "accounts": accounts,
        "goal_modes": goal_modes,
        "income_units": income_units,
    }


def _clean_goal_form_data(form_data: Mapping[str, str]) -> dict[str, str]:
    """Strip stale hidden-field values from goal form submissions.

    When the user toggles between Fixed and Income-Relative mode, the
    hidden field group still submits its old values.  This function
    returns a cleaned dict suitable for schema validation -- removing
    income fields for Fixed mode and target_amount for Income-Relative.

    Must run BEFORE schema validation so the cross-field validator does
    not reject the stale combination.

    Args:
        form_data: The ``ImmutableMultiDict`` from ``request.form``.  Values
            must be the raw submitted STRINGS: the mode is resolved through
            :func:`~app.utils.digit_strings.parse_row_id`, whose domain is
            ``str | None``, so an already-typed payload would raise
            ``AttributeError`` rather than being coerced.  Both call sites
            pass ``request.form``; the annotation states the precondition the
            previous ``int()`` did not need.

    Returns:
        dict with stale fields removed.
    """
    data = dict(form_data)
    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    # Default to Fixed when omitted (backward compatibility).  The shared
    # rule rather than a local ``int()`` (plan step X-ae): this never crashed
    # -- it already attempted the parse -- but it read a mode id spelled in
    # any digit script, which no ``<select>`` of ours emits.  A value that
    # names no mode leaves the payload untouched so the schema reports it.
    mode = parse_row_id(data.get("goal_mode_id", str(fixed_id)))
    if mode is None:
        return data

    if mode == fixed_id:
        data.pop("income_unit_id", None)
        data.pop("income_multiplier", None)
    else:
        data.pop("target_amount", None)

    return data


def _cockpit_context(user_id: int) -> dict:
    """Build the cockpit render context: dashboard data + the chart JSON.

    The single producer + serialization prologue shared by the full-page
    ``dashboard`` render and the ``cockpit_section`` partial re-render, so
    both feed the template the identical contract (the money-precise
    ``net_worth`` figures, the ``net_worth_chart_json`` the net-worth stream
    canvas reads, and the ``sparkline_points`` SVG polylines).  ``float`` is
    applied only in the two serializers (:func:`_serialize_net_worth_chart`,
    the Chart.js boundary; and :func:`_serialize_sparklines`, the
    sparkline-geometry boundary); every other figure stays ``Decimal``.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        The ``compute_dashboard_data`` dict with ``net_worth_chart_json`` and
        ``sparkline_points`` (``{account_id: svg points}``) added.
    """
    ctx = savings_dashboard_service.compute_dashboard_data(user_id)
    ctx["net_worth_chart_json"] = _serialize_net_worth_chart(
        ctx["net_worth"]
    )
    ctx["sparkline_points"] = _serialize_sparklines(ctx["sparklines"])
    return ctx


@savings_bp.route("/savings")
@login_required
@require_owner
def dashboard():
    """Savings dashboard: the Net Worth Cockpit, goals, and emergency fund.

    Renders the full page.  The cockpit region (net-worth hero, the
    account grid, and the home-equity cards) is wrapped in
    ``#cockpit-section`` and re-renders on ``balanceChanged`` via
    :func:`cockpit_section`; the savings goals, emergency-fund coverage,
    and archived list below it are page-load-only.  The shared context
    (including the serialized ``net_worth_chart_json``) comes from
    :func:`_cockpit_context`.
    """
    return render_template(
        "savings/dashboard.html", **_cockpit_context(current_user.id),
    )


@savings_bp.route("/savings/cockpit")
@login_required
@require_owner
def cockpit_section():
    """HTMX partial: re-render the Net Worth Cockpit region on balanceChanged.

    The single ``balanceChanged from:body`` swap target for the cockpit's
    ``#cockpit-section`` (the net-worth hero + chips + trend, the account
    grid with its group subtotals and the debt summary, and the
    home-equity cards), so an inline balance edit re-syncs every
    balance-derived figure in that region at once.  Re-renders
    ``savings/_cockpit.html`` with the same :func:`_cockpit_context` the
    page uses, so the swapped-in markup reads the identical contract.

    Non-HTMX requests redirect to the dashboard page (the section is a
    fragment, not a standalone page), matching ``dashboard.pulse_section``.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("savings.dashboard"))

    return render_template(
        "savings/_cockpit.html", **_cockpit_context(current_user.id),
    )


@savings_bp.route("/savings/cockpit/<int:account_id>/balance")
@login_required
@require_owner
def cockpit_balance(account_id):
    """HTMX partial: re-render one account's cockpit balance cell.

    The Cancel / Escape (and 409-conflict retry) revert target for the
    cockpit's per-card inline anchor editor: ``accounts._anchor_revert_url``
    maps the editor's ``revert=accounts`` token here, mirroring how
    ``revert=dashboard`` maps to ``dashboard.balance_section``.  Renders
    ``savings/_cockpit_balance.html`` -- the ``#acct-balance-<id>`` cell the
    editor replaced -- with the seam-derived balance from the
    narrow :func:`~app.services.savings_dashboard_service.compute_account_balance_cell`
    producer, so the reverted cell shows the exact figure the grid showed.

    The producer is the IDOR + active gate (as ``balance_section``'s
    producer is for the dashboard): it returns ``None`` -- a 404 -- for an
    account that is not among the user's active accounts (not found, not
    owned, or archived between page load and the revert), satisfying the
    404-for-both security rule.  Non-HTMX requests redirect to the
    dashboard page.

    The partial is rendered with the SAME value the grid loop passes it (plan
    step X-t1): the producer returns the account's ``AccountProjection``, so
    the reverted cell and the cell it replaces read one object rather than two
    dicts that have to agree.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("savings.dashboard"))

    projection = savings_dashboard_service.compute_account_balance_cell(
        current_user.id, account_id,
    )
    if projection is None:
        abort(404)

    return render_template("savings/_cockpit_balance.html", ad=projection)


@savings_bp.route("/savings/goals/new", methods=["GET"])
@login_required
@require_owner
def new_goal():
    """Display the savings goal creation form."""
    return render_template("savings/goal_form.html", **_goal_form_context())


@savings_bp.route("/savings/goals", methods=["POST"])
@login_required
@require_owner
def create_goal():
    """Create a new savings goal."""
    cleaned = _clean_goal_form_data(request.form)
    errors = _create_schema.validate(cleaned)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.new_goal"))

    data = _create_schema.load(cleaned)

    # Validate account ownership and active status.
    acct = db.session.get(Account, data.get("account_id"))
    if not acct or acct.user_id != current_user.id or not acct.is_active:
        flash("Invalid account.", "danger")
        return redirect(url_for("savings.new_goal"))

    goal = SavingsGoal(user_id=current_user.id, **data)
    db.session.add(goal)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "A savings goal with that name already exists for this account.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    logger.info("user_id=%d created savings goal (id=%d)", current_user.id, goal.id)

    flash(f"Savings goal '{goal.name}' created.", "success")
    return redirect(url_for("savings.dashboard"))


@savings_bp.route("/savings/goals/<int:goal_id>/edit", methods=["GET"])
@login_required
@require_owner
def edit_goal(goal_id):
    """Display the savings goal edit form."""
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    return render_template(
        "savings/goal_form.html", **_goal_form_context(goal),
    )


@savings_bp.route("/savings/goals/<int:goal_id>", methods=["POST"])
@login_required
@require_owner
def update_goal(goal_id):
    """Update a savings goal.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    cleaned = _clean_goal_form_data(request.form)
    errors = _update_schema.validate(cleaned)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    data = _update_schema.load(cleaned)

    # Stale-form check (commit C-18 / F-010).  One shared context drives
    # both the pre-flush form-side handler and the commit-time handler so
    # the log label, flash wording, and redirect target are defined once.
    submitted_version = data.pop("version_id", None)
    stale_ctx = StaleConflictContext(
        logger=logger,
        log_label="update_goal",
        log_id=goal_id,
        flash_message=STALE_EDITING_MESSAGE.format(noun="savings goal"),
        redirect=RedirectTarget("savings.edit_goal", {"goal_id": goal_id}),
    )
    if submitted_version is not None and submitted_version != goal.version_id:
        return handle_stale_form_conflict(
            stale_ctx,
            submitted=submitted_version,
            current=goal.version_id,
        )

    # Validate account ownership if account is being changed.
    if "account_id" in data:
        acct = db.session.get(Account, data["account_id"])
        if not acct or acct.user_id != current_user.id:
            flash("Invalid account.", "danger")
            return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    # When switching modes, explicitly clear the now-irrelevant fields
    # so the update loop sets them to None on the goal object.
    if "goal_mode_id" in data:
        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        if data["goal_mode_id"] == fixed_id:
            data.setdefault("income_unit_id", None)
            data.setdefault("income_multiplier", None)
        else:
            data.setdefault("target_amount", None)

    for field, value in data.items():
        if field in _GOAL_UPDATE_FIELDS:
            setattr(goal, field, value)

    conflict = commit_or_handle_stale(stale_ctx)
    if conflict is not None:
        return conflict
    logger.info("user_id=%d updated savings goal %d", current_user.id, goal_id)
    flash(f"Savings goal '{goal.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))


@savings_bp.route("/savings/goals/<int:goal_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_goal(goal_id):
    """Deactivate a savings goal.

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts into a flash + redirect.
    """
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    goal.is_active = False
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="delete_goal",
        log_id=goal_id,
        flash_message=(
            "This savings goal was changed by another action.  "
            "Please reload and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("user_id=%d deleted savings goal %d", current_user.id, goal_id)

    flash(f"Savings goal '{goal.name}' deactivated.", "info")
    return redirect(url_for("savings.dashboard"))
