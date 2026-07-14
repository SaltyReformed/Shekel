"""
Shekel Budget App -- Per-Account Detail Pages

Detail / projection pages for cash and physical-asset accounts.  Split
out of the historical monolithic ``app/routes/accounts.py`` in Commit 21
of the financial-calculation audit follow-up (F-1); rebuilt for the
Fable 5 UI/UX overhaul (``docs/design/account_detail_audit.md``, "Rebuild
decisions").

The overhaul merged the former ``checking_detail`` and ``interest_detail``
pages into ONE :func:`cash_detail` page that serves EVERY cash account
kind: Checking, the ``has_interest`` types (HYSA / Money Market / CD /
HSA), and the previously page-less plain types (Savings, Credit Card, and
plain custom types).  Loans (``has_amortization``), physical assets
(``has_appreciation``), and retirement / investment accounts are NOT
served here -- they keep their own screens
(:func:`loan.dashboard`, :func:`property_detail`,
:func:`investment.dashboard`).  ``checking_detail`` and
``interest_detail`` survive only as thin redirect stubs to
:func:`cash_detail` so external bookmarks and the not-yet-updated cockpit
``detail_endpoint`` macro still resolve.

Balance production is unchanged from the two pre-merge routes (the audit's
finding is a PRESENTATION rebuild, not a data change).  :func:`cash_detail`
routes every balance through the balance-at seam (Level-1 Commit 8):

* interest-bearing accounts via the kind-correct ``balance_at.balance_map``
  (interest-accrued balance) plus the kernel's
  ``interest_by_period_for_account`` accessor for the earned-interest
  figure, and
* plain cash accounts via the cash-flow entry
  ``balance_at.cash_balance_map``.

Both seam entries delegate to the canonical entries-aware producers, so
the silent-degrade seam fixed by CRIT-01 / F-009 cannot reappear here.
The F-6 static guard in :mod:`tests.test_routes.test_accounts` pins this
contract by asserting that the seam (``balance_at.``) is used and the bare
entries-blind producer ``calculate_balances`` (in ``balance_calculator``)
is not; that guard reads this file directly.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctCategoryEnum, CompoundingFrequencyEnum
from app.extensions import db
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.interest_params import InterestParams
from app.models.ref import CompoundingFrequency
from app.routes.accounts._bp import accounts_bp
from app.services import (
    balance_at,
    balance_resolver,
    home_equity_service,
    net_worth_kernel,
    pay_period_service,
    property_equity_chart,
)
from app.services.resolution_context import BalanceContext
from app.utils.account_validation import (
    _appreciation_params_schema,
    _interest_params_schema,
)
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.period_projections import project_balance_horizons

if TYPE_CHECKING:
    # Typing-only imports for the per-page helper signatures (lazy strings
    # via ``from __future__ import annotations``; no runtime cost).
    from app.models.pay_period import PayPeriod
    from app.services.balance_resolver import AnchorPoint

logger = logging.getLogger(__name__)

# The number of biweekly pay periods that make up one year -- the window
# width for the "Interest, next 12 months" health chip.  Matches the
# ``("1 year", 26)`` horizon offset in
# :mod:`app.utils.period_projections` (26 biweekly periods per year); an
# int (not the ``Decimal`` ``PAY_PERIODS_PER_YEAR``) because it indexes
# ``period_index`` arithmetic, not a money calculation.
_ONE_YEAR_PERIODS = 26

# Chart.js x-axis label format for the balance-projection trend: month
# abbreviation plus un-padded day (e.g. "Jun 5").  The SAME convention as
# the savings cockpit's ``_serialize_net_worth_chart`` so the two trend
# charts read identically.
_CHART_LABEL_FORMAT = "%b %-d"


# ── Shared detail-page helpers ────────────────────────────────────


def _current_period_balance(
    balances: dict[int, Decimal],
    current_period: PayPeriod | None,
    anchor: AnchorPoint | None,
) -> Decimal | None:
    """Return the current-period projected balance, else the anchor balance.

    The page hero figure: the projected balance at the current period
    when one exists, otherwise the resolved anchor balance (E-19),
    otherwise ``None``.
    """
    current_bal = balances.get(current_period.id) if current_period else None
    if current_bal is None and anchor is not None:
        current_bal = anchor.balance
    return current_bal


def _ensure_interest_params(account: Account) -> InterestParams:
    """Return the account's :class:`InterestParams`, auto-creating if missing.

    Mirrors the pre-merge ``interest_detail`` safety fallback: an
    interest-bearing account should always carry a params row (the create
    flow seeds it), but if one was lost this defensively recreates it with
    an explicit ``apy=0`` sentinel and the DAILY compounding ref id.

    The explicit zero (E-12 / HIGH-06) is deliberate: relying on a column
    ``server_default`` would silently project 4.5% interest the user never
    configured.  ``compounding_frequency_id`` is a ref FK now (#38, no
    server_default), so the DAILY id is supplied explicitly.
    """
    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        params = InterestParams(
            account_id=account.id, apy=Decimal("0"),
            compounding_frequency_id=ref_cache.compounding_frequency_id(
                CompoundingFrequencyEnum.DAILY,
            ),
        )
        db.session.add(params)
        db.session.commit()
    return params


def _build_horizons(
    current_balance: Decimal | None,
    current_period: PayPeriod | None,
    all_periods: list[PayPeriod],
    balances: dict[int, Decimal],
) -> list[dict]:
    """Build the 3 / 6 / 12-month horizon chip rows for the template.

    One row per horizon that has a projected balance, in the shared
    :data:`~app.utils.period_projections.HORIZON_OFFSETS` order.  Each row
    carries the horizon ``label`` ("3 months" / "6 months" / "1 year"),
    its projected ``value``, and the ``delta`` from the current balance
    (``value - current_balance``), all ``Decimal``.  Returns an empty list
    when there is no current balance to project or delta from.
    """
    if current_balance is None:
        return []
    projected = project_balance_horizons(current_period, all_periods, balances)
    return [
        {"label": label, "value": value, "delta": value - current_balance}
        for label, value in projected.items()
    ]


def _interest_next_year(
    interest_by_period: dict[int, Decimal],
    current_period: PayPeriod,
    all_periods: list[PayPeriod],
) -> Decimal:
    """Sum the interest earned over the next year (26 biweekly periods).

    The health-chip figure: the ``Decimal`` sum of ``interest_by_period``
    for every period whose ``period_index`` falls in
    ``[current + 1, current + 26]`` (the next full year of biweekly
    periods after the current one).  ``Decimal("0.00")`` is a legitimate
    result (a zero-APY account, or a horizon with no projected interest),
    NOT a "missing" sentinel; the caller only invokes this for an
    interest-bearing account with a current period.
    """
    lo = current_period.period_index + 1
    hi = current_period.period_index + _ONE_YEAR_PERIODS
    total = Decimal("0.00")
    for period in all_periods:
        if lo <= period.period_index <= hi:
            total += interest_by_period.get(period.id, Decimal("0.00"))
    return total


def _build_chart(
    all_periods: list[PayPeriod],
    balances: dict[int, Decimal],
    current_period: PayPeriod | None,
) -> tuple[str, bool]:
    """Serialize the balance-projection trend to a Chart.js JSON string.

    The single Chart.js serialization boundary for this page (coding
    standards: ``float`` lives only here, never in a calculation).  The
    series is every period that HAS a projected balance, in
    ``period_index`` order; ``labels`` uses the same ``%b %-d`` convention
    as the cockpit's ``_serialize_net_worth_chart``, ``balance`` is the
    parallel ``float`` array, and ``current_index`` is the position of the
    current period within the series (the solid/dashed "today" boundary,
    an int in ``[0, len(series)]``, mirroring the cockpit serializer).
    Defaults ``current_index`` to ``0`` when the current period is not in
    the series (no current period, or it precedes the anchor).

    Returns:
        ``(chart_json, has_chart)`` -- the JSON string and whether the
        series is non-empty.
    """
    ordered = sorted(all_periods, key=lambda p: p.period_index)
    series = [p for p in ordered if p.id in balances]

    current_index = 0
    if current_period is not None:
        for i, period in enumerate(series):
            if period.id == current_period.id:
                current_index = i
                break

    chart_json = json.dumps({
        "labels": [p.end_date.strftime(_CHART_LABEL_FORMAT) for p in series],
        "balance": [float(balances[p.id]) for p in series],
        "current_index": current_index,
    })
    return chart_json, bool(series)


def _cash_projection(
    account: Account,
    is_interest: bool,
    balance_ctx: BalanceContext,
    all_periods: list[PayPeriod],
    params: InterestParams | None,
) -> "tuple[dict[int, Decimal], dict[int, Decimal], AnchorPoint | None]":
    """Produce the per-period balances (and interest) for a cash account.

    The single balance-production site, preserving the two pre-merge
    routes' producer paths verbatim (Level-1 Commit 8):

    * interest-bearing accounts read the KIND-CORRECT
      ``balance_at.balance_map`` (interest-accrued balances) plus the
      kernel's ``interest_by_period_for_account`` accessor for the
      per-period earned interest, and
    * plain cash accounts read the cash-flow ``balance_at.cash_balance_map``
      (pure transaction running-balance).

    The anchor is resolved via the dated ``AccountAnchorHistory`` SoT for
    the hero caption and the current-period fallback.  Returns empty maps
    and a ``None`` anchor in the legitimate empty states (no baseline
    scenario, or -- for a plain account -- no pay periods), so the template
    renders cleanly.

    Returns:
        ``(balances, interest_by_period, anchor)``.  ``interest_by_period``
        is always empty for a plain account.
    """
    balances: dict[int, Decimal] = {}
    interest_by_period: dict[int, Decimal] = {}
    anchor: AnchorPoint | None = None
    scenario = balance_ctx.scenario
    if is_interest:
        if scenario is not None:
            anchor = balance_resolver.resolve_anchor(account, scenario.id)
            balances = balance_at.balance_map(
                account, balance_ctx, all_periods,
            ) or {}
            interest_by_period = net_worth_kernel.interest_by_period_for_account(
                account, scenario, all_periods, params,
            )
    elif scenario is not None and all_periods:
        result = balance_at.cash_balance_map(account, balance_ctx, all_periods)
        balances = result.balances
        anchor = balance_resolver.resolve_anchor(account, scenario.id)
    return balances, interest_by_period, anchor


def _cash_detail_wrong_type(account: Account) -> bool:
    """Return True when *account* is a kind the cash detail page does NOT serve.

    The merged cash detail page serves Checking, the ``has_interest`` types
    (HYSA / Money Market / CD / HSA), and plain cash types (Savings, Credit
    Card, plain custom).  It does NOT serve loans (``has_amortization``),
    physical assets (``has_appreciation``), or retirement / investment
    accounts (category RETIREMENT or INVESTMENT) -- those keep their own
    screens.  Resolves by boolean type flag and integer category id only,
    never a ref-table ``name`` string (the IDs-for-logic invariant).  An
    account with no ``account_type`` (degenerate / partially loaded) is
    served as a plain cash account, matching ``classify_account``'s
    None-is-PLAIN convention.
    """
    acct_type = account.account_type
    if acct_type is None:
        return False
    return bool(
        acct_type.has_amortization
        or acct_type.has_appreciation
        or acct_type.category_id in (
            ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
            ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT),
        )
    )


# ── Cash Detail (checking + interest + plain cash) ────────────────


def _load_cash_account_or_404(account_id: int) -> Account:
    """Load a cash-detail-served account for the current user, or 404.

    The shared gate for :func:`cash_detail` and its two HTMX fragments
    (:func:`cash_band`, :func:`cash_balance_hero`): ``get_or_404``
    resolves cross-owner / non-existent accounts to ``None`` (the
    project's "404 for not-found and not-yours" rule), and
    :func:`_cash_detail_wrong_type` 404s the kinds this page does not
    serve (loans, physical assets, retirement / investment).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)
    if _cash_detail_wrong_type(account):
        abort(404)
    return account


def _cash_detail_context(account: Account) -> dict:
    """Assemble the cash detail template context (page AND band fragments).

    Extracted from :func:`cash_detail` when the D14 click-to-edit port
    added the band-refresh fragment (:func:`cash_band`): both render
    paths must compute the hero balance, horizon chips, interest chip,
    and chart from the same producers or a band refresh could disagree
    with the page render.

    Balance production is preserved verbatim from the two pre-merge
    routes: interest accounts read the kind-correct
    ``balance_at.balance_map`` plus the kernel's
    ``interest_by_period_for_account`` accessor; plain cash accounts
    read the cash-flow ``balance_at.cash_balance_map``.  Both seam
    entries delegate to the canonical entries-aware producers (Level-1
    Commit 8), so this module calls no balance producer directly.  The
    anchor is resolved via the dated ``AccountAnchorHistory`` SoT (E-19,
    Commit 4) for the hero caption and the current-period fallback; the
    ``scenario is None`` / ``no pay periods`` empty-state guards are
    kept (a fixture without a baseline scenario, a freshly-registered
    user with no generated periods) and the templates render cleanly
    when ``balances`` is empty.
    """
    is_interest = bool(
        account.account_type and account.account_type.has_interest
    )

    all_periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)
    balance_ctx = BalanceContext.build(current_user.id)

    # Preserve the pre-merge ``interest_detail`` behaviour: the params row is
    # auto-created before any projection so the parameters card always
    # renders for an interest-bearing account.  Plain accounts carry no
    # params / compounding list.
    params = _ensure_interest_params(account) if is_interest else None
    compounding_frequencies = (
        CompoundingFrequency.query.order_by(CompoundingFrequency.id).all()
        if is_interest else []
    )

    balances, interest_by_period, anchor = _cash_projection(
        account, is_interest, balance_ctx, all_periods, params,
    )

    current_balance = _current_period_balance(balances, current_period, anchor)
    chart_json, has_chart = _build_chart(all_periods, balances, current_period)

    return {
        "account": account,
        "is_interest": is_interest,
        "current_balance": current_balance,
        "current_period": current_period,
        # ``anchor_as_of`` is the anchor EVENT instant
        # (``AnchorPoint.created_at``, the dated ``AccountAnchorHistory`` row),
        # NOT the anchor period's start date -- fixing the audit's finding #2
        # (a mid-period true-up used to show the period start instead of the
        # true-up date).  It is passed as the stored UTC INSTANT (not the
        # UTC-day ``as_of_date``) so the template renders it in the user's
        # display timezone via ``local_datetime`` -- a late-evening-Eastern
        # anchor otherwise shows on the next UTC day.
        "anchor_as_of": anchor.created_at if anchor is not None else None,
        "horizons": _build_horizons(
            current_balance, current_period, all_periods, balances,
        ),
        # The next-year interest chip is interest-only; a plain account
        # carries ``None`` (the template omits the chip).  ``Decimal("0.00")``
        # is a legitimate value for a zero-APY interest account.
        "interest_next_year": (
            _interest_next_year(interest_by_period, current_period, all_periods)
            if is_interest and current_period is not None else None
        ),
        "params": params,
        "compounding_frequencies": compounding_frequencies,
        "chart_json": chart_json,
        "has_chart": has_chart,
    }


@accounts_bp.route("/accounts/<int:account_id>/details")
@login_required
@require_owner
def cash_detail(account_id):
    """Unified cash-account detail page (checking / interest / plain cash).

    Shows the account's balance hero (the D14 click-to-edit anchor
    control) with an honest anchored caption, the 3 / 6 / 12-month
    horizon chips, and a trend chart; interest-bearing accounts
    additionally get an APY / compounding parameters card, a "next 12
    months" projected-interest chip, and their interest-accrued
    balances.  Serves every cash account kind (see
    :func:`_cash_detail_wrong_type` for the type gate); loans, physical
    assets, and retirement / investment accounts 404 out.  The balance
    production contract lives on :func:`_cash_detail_context` (shared
    with the band fragment).
    """
    account = _load_cash_account_or_404(account_id)
    return render_template(
        "accounts/cash_detail.html", **_cash_detail_context(account),
    )


@accounts_bp.route("/accounts/<int:account_id>/details/band")
@login_required
@require_owner
def cash_band(account_id):
    """HTMX partial: re-render the cash detail band (D14 click-to-edit port).

    The ``balanceChanged`` refresh target: after an anchor save through
    the hero's inline editor, the page's ``#cash-band-region`` re-fetches
    this fragment so the hero, the horizon chips, the interest chip, AND
    the trend chart all recompute from the new anchor together -- a
    hero-only refresh would leave the chips and chart disagreeing with
    it (``account_detail.js`` re-creates the chart when the canvas
    returns).  Renders ``accounts/_cash_band.html`` with the same
    context builder the page uses, so the swapped-in band reads the
    identical contract.

    Non-HTMX requests redirect to the detail page (the band is a
    fragment, not a standalone page).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("accounts.cash_detail", account_id=account_id))
    account = _load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_cash_band.html", **_cash_detail_context(account),
    )


@accounts_bp.route("/accounts/<int:account_id>/details/balance-hero")
@login_required
@require_owner
def cash_balance_hero(account_id):
    """HTMX partial: the cash balance hero cell (D14 click-to-edit port).

    The Cancel / Escape and 409-conflict revert target for the cash
    detail page's click-to-edit anchor editor:
    ``accounts._anchor_revert_url`` maps ``revert=cash`` here, mirroring
    how the cockpit's ``revert=accounts`` maps to
    ``savings.cockpit_balance``.  Renders
    ``accounts/_cash_balance_hero.html`` with the resolver
    current-period balance the detail headline shows, so a reverted
    cell restores the exact figure.  (A SAVE does not land here -- the
    editor's success response fires ``balanceChanged`` and the whole
    band re-renders via :func:`cash_band`.)

    Non-HTMX requests redirect to the detail page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("accounts.cash_detail", account_id=account_id))
    account = _load_cash_account_or_404(account_id)
    ctx = _cash_detail_context(account)
    return render_template(
        "accounts/_cash_balance_hero.html",
        account=account,
        current_balance=ctx["current_balance"],
    )


def _redirect_to_cash_detail(account_id):
    """Redirect a legacy detail URL to :func:`cash_detail`, preserving setup=1.

    The shared body of the ``checking_detail`` / ``interest_detail``
    redirect stubs.  Resolves per-resource ownership FIRST via the
    established ``get_or_404`` + ``abort(404)`` pattern the sibling routes
    (:func:`cash_detail`, :func:`update_interest_params`) use, then
    redirects.  ``@require_owner`` only gates the owner-vs-companion ROLE,
    not per-account ownership; without this check a cross-user "not yours"
    probe would leak resource existence as a 302 redirect instead of the
    security response rule's 404 (a 404 for both "not found" and "not
    yours").  Forwards the ``setup=1`` onboarding query arg when present so
    a post-create redirect still lands on the wizard banner.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if request.args.get("setup") == "1":
        return redirect(
            url_for("accounts.cash_detail", account_id=account_id, setup=1),
        )
    return redirect(url_for("accounts.cash_detail", account_id=account_id))


@accounts_bp.route("/accounts/<int:account_id>/checking")
@login_required
@require_owner
def checking_detail(account_id):
    """Deprecated alias: redirect to the unified :func:`cash_detail` page.

    The Fable 5 overhaul merged the checking detail page into
    :func:`cash_detail`.  Kept as a redirect (not deleted) so external
    bookmarks and the not-yet-updated cockpit ``detail_endpoint`` macro
    still resolve.
    """
    return _redirect_to_cash_detail(account_id)


# ── Interest Params ──────────────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/interest")
@login_required
@require_owner
def interest_detail(account_id):
    """Deprecated alias: redirect to the unified :func:`cash_detail` page.

    The Fable 5 overhaul merged the interest detail page into
    :func:`cash_detail`.  Kept as a redirect (not deleted) so external
    bookmarks and the not-yet-updated cockpit ``detail_endpoint`` macro
    still resolve; ``setup=1`` is preserved for the post-create wizard.
    """
    return _redirect_to_cash_detail(account_id)


@accounts_bp.route("/accounts/<int:account_id>/interest/params", methods=["POST"])
@login_required
@require_owner
def update_interest_params(account_id):
    """Update interest parameters (APY, compounding frequency).

    URL and behaviour are unchanged from before the cash-detail merge; only
    the success / validation redirect target moved to
    :func:`cash_detail` (the merged page that now hosts the parameters
    card).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    errors = _interest_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.cash_detail", account_id=account_id))

    data = _interest_params_schema.load(request.form)

    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        # HIGH-06 / Commit 24: a first-save that omits ``apy`` would
        # pre-fix have silently materialised the column
        # ``server_default="0.04500"`` (4.5% rate the user never
        # configured).  The defaults are gone (see
        # :class:`~app.models.interest_params.InterestParams`); a
        # first save that omits ``apy`` is now an explicit user
        # error -- flash and redirect instead of constructing a row
        # that would fail ``NotNullViolation`` at commit.  The
        # account-create flow auto-creates the row with
        # ``apy=Decimal("0")`` so this branch only fires when an
        # InterestParams row was somehow lost (data loss, manual
        # delete) and the user is reconfiguring; requiring an
        # explicit ``apy`` keeps the failure visible.
        if "apy" not in data:
            flash(
                "An APY value is required when configuring "
                "interest parameters for the first time.",
                "danger",
            )
            return redirect(
                url_for("accounts.cash_detail", account_id=account_id),
            )
        # #38: recreate with the DAILY ref id so the NOT NULL FK is
        # satisfied even when the update payload omits the frequency.
        params = InterestParams(
            account_id=account.id,
            compounding_frequency_id=ref_cache.compounding_frequency_id(
                CompoundingFrequencyEnum.DAILY,
            ),
        )
        db.session.add(params)

    if "apy" in data:
        # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
        # already divided the form's user-facing percent by 100, so
        # ``data["apy"]`` is the storage-domain decimal fraction the
        # DB CHECK ``apy >= 0 AND apy <= 1`` enforces.  The route
        # stores it verbatim; no second divide.
        params.apy = data["apy"]
    if "compounding_frequency_id" in data:
        params.compounding_frequency_id = data["compounding_frequency_id"]

    db.session.commit()
    logger.info("Updated interest params for account %d", account.id)
    flash("Interest parameters updated.", "success")
    return redirect(url_for("accounts.cash_detail", account_id=account_id))


# ── Property (physical-asset) Detail & Params ─────────────────────


def _property_chart_context(
    params: AssetAppreciationParams,
    equity: home_equity_service.HomeEquity,
    property_account: Account,
    balance_ctx: BalanceContext,
) -> dict[str, object]:
    """Serialize the property equity-over-time chart for the detail band.

    The single Chart.js serialization boundary for the property page (coding
    standards: ``float`` lives only here, never in a money calculation).
    ``has_equity_chart`` is ``False`` -- and the band shows the "set a market
    value" empty state instead of a chart -- only when there is no positive
    market value to anchor the appreciation arc on (a freshly-created Property
    whose value has not been set yet).  Otherwise the market-value /
    secured-debt / equity series come from
    :func:`app.services.property_equity_chart.build_property_equity_chart`
    (fed by the seam's :func:`app.services.balance_at.secured_loan_series`, which
    packs each loan's rows off the read pass's ONE memoized resolution -- the same
    one the equity hero reads -- so the chart and the hero cannot disagree, and
    this route never holds a resolver bundle), floated here into the ``data-chart``
    JSON the template hands to ``property_detail.js``; ``chart_state`` drives the caption
    variant (``standard`` / ``zero_rate`` / ``no_loans``), ``today_index`` the
    Today boundary, and ``debt_tier`` the per-month estimated / confirmed /
    projected styling.  ``has_estimated_debt`` -- ``True`` when any month is the
    ``estimated`` contractual back-projection tier -- gates the caption's dotted
    pre-tracking clause so the copy names a texture only when the chart draws it
    (the "a figure and its caption never disagree" design principle).

    Args:
        params: The Property's :class:`AssetAppreciationParams` (the rate).
        equity: The :class:`~app.services.home_equity_service.HomeEquity`
            snapshot (its ``market_value`` gates ``has_equity_chart`` and is the
            chart's anchor).
        property_account: The Property account; the seam reads its
            ``secured_loans`` to pack the chart's debt series.
        balance_ctx: The read pass's
            :class:`~app.services.resolution_context.BalanceContext`; its ``as_of``
            is the chart's compounding origin.

    Returns:
        The ``has_equity_chart`` / ``chart_json`` / ``chart_state`` context the
        ``property_detail.html`` band reads.
    """
    if equity.market_value <= Decimal("0"):
        return {
            "has_equity_chart": False,
            "chart_json": json.dumps({
                "labels": [], "value": [], "debt": [], "equity": [],
                "today_index": 0, "debt_tier": [],
            }),
            "chart_state": property_equity_chart.CHART_STATE_NO_LOANS,
            "has_estimated_debt": False,
        }
    chart = property_equity_chart.build_property_equity_chart(
        balance_at.secured_loan_series(property_account, balance_ctx),
        equity.market_value,
        params.annual_appreciation_rate,
        balance_ctx.as_of,
    )
    return {
        "has_equity_chart": True,
        "chart_json": json.dumps({
            "labels": chart.labels,
            "value": [float(value) for value in chart.value],
            "debt": [float(debt) for debt in chart.debt],
            "equity": [float(equity_pt) for equity_pt in chart.equity],
            "today_index": chart.today_index,
            "debt_tier": chart.debt_tier,
        }),
        "chart_state": chart.chart_state,
        "has_estimated_debt": (
            property_equity_chart.TIER_ESTIMATED in chart.debt_tier
        ),
    }


@accounts_bp.route("/accounts/<int:account_id>/property")
@login_required
@require_owner
def property_detail(account_id):
    """Property detail page: market value, appreciation rate, equity, LTV.

    The durable home for the home-equity display this sprint (the savings
    cockpit equity card lands in the Net Worth Cockpit rebuild, reusing the
    same :mod:`app.services.home_equity_service` producer).  Equity nets the
    Property's user-set market value against the resolver-derived balances
    of the loans it secures, so the mortgage figure here equals the debt
    card and the net-worth liability.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Verify this is an appreciating physical-asset account type.
    if not account.account_type or not account.account_type.has_appreciation:
        flash("This account type does not track appreciation.", "warning")
        return redirect(url_for("savings.dashboard"))

    params = (
        db.session.query(AssetAppreciationParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        # Defensive auto-create with a zero-rate sentinel (E-12), mirroring
        # ``_ensure_interest_params``: the create flow already seeds this
        # row, so this branch only fires if it was lost (manual delete /
        # data loss).
        params = AssetAppreciationParams(
            account_id=account.id, annual_appreciation_rate=Decimal("0"),
        )
        db.session.add(params)
        db.session.commit()

    balance_ctx = BalanceContext.build(current_user.id)

    # Every secured loan is read from the read pass's ONE memoized resolution,
    # so the equity hero, the equity chart, and the /savings cockpit's equity
    # card all read the same mortgage balance -- this page used to resolve them
    # itself and hand the pure ``compute_home_equity`` its own figures, a second
    # equity path parallel to ``resolve_home_equity``.
    equity = home_equity_service.resolve_home_equity(account, balance_ctx)

    return render_template(
        "accounts/property_detail.html",
        account=account,
        params=params,
        equity=equity,
        secured_loans=account.secured_loans,
        **_property_chart_context(params, equity, account, balance_ctx),
    )


@accounts_bp.route("/accounts/<int:account_id>/property/params", methods=["POST"])
@login_required
@require_owner
def update_appreciation_params(account_id):
    """Update a Property's annual appreciation rate."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if not account.account_type or not account.account_type.has_appreciation:
        flash("This account type does not track appreciation.", "warning")
        return redirect(url_for("savings.dashboard"))

    errors = _appreciation_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.property_detail", account_id=account_id))

    data = _appreciation_params_schema.load(request.form)

    params = (
        db.session.query(AssetAppreciationParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        params = AssetAppreciationParams(
            account_id=account.id,
            annual_appreciation_rate=data["appreciation_rate"],
        )
        db.session.add(params)
    else:
        params.annual_appreciation_rate = data["appreciation_rate"]

    db.session.commit()
    logger.info("Updated appreciation params for account %d", account.id)
    flash("Appreciation rate updated.", "success")
    return redirect(url_for("accounts.property_detail", account_id=account_id))
