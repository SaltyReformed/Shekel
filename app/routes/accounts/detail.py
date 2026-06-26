"""
Shekel Budget App -- Per-Account Detail Pages

Detail / projection pages for interest-bearing and checking
accounts.  Split out of the historical monolithic
``app/routes/accounts.py`` in Commit 21 of the financial-calculation
audit follow-up (F-1); behaviour preserved verbatim from the
pre-split file.

Both detail pages route balance computation through the canonical
entries-aware producer (E-25 / Commit 5 +
``balance_resolver.balances_for``) so the silent-degrade seam
fixed by CRIT-01 / F-009 cannot reappear here.  The F-6 static
guard in :mod:`tests.test_routes.test_accounts` pins this contract
by asserting that ``balance_resolver.balances_for`` appears in the
file and the bare entries-blind producer
``calculate_balances`` (in ``balance_calculator``) does not.  When
the split in Commit 21 moved ``checking_detail`` into this module,
the F-6 guard's file-path reference was updated to point here.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app import ref_cache
from app.enums import AcctTypeEnum, CompoundingFrequencyEnum
from app.extensions import db
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.interest_params import InterestParams
from app.models.ref import CompoundingFrequency
from app.models.transaction import Transaction
from app.routes.accounts._bp import accounts_bp
from app.services import (
    balance_calculator,
    balance_resolver,
    home_equity_service,
    pay_period_service,
)
from app.services.scenario_resolver import get_baseline_scenario
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
    from app.models.scenario import Scenario
    from app.services.balance_resolver import AnchorPoint

logger = logging.getLogger(__name__)


# ── Shared detail-page helpers ────────────────────────────────────


def _current_period_balance(
    balances: dict[int, Decimal],
    current_period: PayPeriod | None,
    anchor: AnchorPoint | None,
) -> Decimal | None:
    """Return the current-period projected balance, else the anchor balance.

    Shared by both detail pages: the projected balance at the current
    period when one exists, otherwise the resolved anchor balance (E-19),
    otherwise ``None``.
    """
    current_bal = balances.get(current_period.id) if current_period else None
    if current_bal is None and anchor is not None:
        current_bal = anchor.balance
    return current_bal


def _build_period_data(
    all_periods: list[PayPeriod],
    balances: dict[int, Decimal],
    interest_by_period: dict[int, Decimal] | None = None,
) -> list[dict]:
    """Build the per-period projection rows the detail templates render.

    One row per period that has a projected balance, in ``all_periods``
    order.  When ``interest_by_period`` is supplied (the interest detail
    page) each row also carries that period's interest, defaulting to
    ``0.00`` for a period that has a balance but no recorded interest;
    the checking page omits the interest field.
    """
    rows = []
    for period in all_periods:
        if period.id in balances:
            row = {"period": period, "balance": balances[period.id]}
            if interest_by_period is not None:
                row["interest"] = interest_by_period.get(
                    period.id, Decimal("0.00"),
                )
            rows.append(row)
    return rows


def _load_account_transactions(
    account: Account, scenario: Scenario | None, all_periods: list[PayPeriod]
) -> list[Transaction]:
    """Load this account's non-deleted transactions for the projection window.

    Scoped to the account, the given scenario, and the supplied periods,
    with ``Transaction.entries`` eager-loaded so the entries-aware
    reduction in ``_entry_aware_amount`` applies unconditionally (closing
    the CRIT-01 / F-009 silent-degrade seam this route used to share with
    /savings).  Returns ``[]`` when there is no scenario or no periods.
    """
    period_ids = [p.id for p in all_periods]
    if not scenario or not period_ids:
        return []
    transactions = (
        db.session.query(Transaction)
        .options(selectinload(Transaction.entries))
        .filter(
            Transaction.account_id == account.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    # pylint: enable=duplicate-code
    return transactions


# ── Interest Detail & Params ──────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/interest")
@login_required
@require_owner
def interest_detail(account_id):
    """Interest-bearing account detail page with interest projections."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Verify this is an interest-bearing account type.
    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        # Auto-create params if missing (shouldn't happen normally).
        # Same E-12 / HIGH-06 zero-sentinel rationale as the create
        # path in :func:`create_account`: explicit ``apy=0`` instead
        # of relying on a column ``server_default`` that would
        # otherwise silently project 4.5% interest the user never
        # configured.
        # #38: compounding frequency is a ref FK now (no server_default),
        # so the auto-create supplies the DAILY id explicitly.
        params = InterestParams(
            account_id=account.id, apy=Decimal("0"),
            compounding_frequency_id=ref_cache.compounding_frequency_id(
                CompoundingFrequencyEnum.DAILY,
            ),
        )
        db.session.add(params)
        db.session.commit()

    user_id = current_user.id
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    scenario = get_baseline_scenario(user_id)

    # Resolve the anchor via the canonical date-anchored source of truth
    # (E-19, Commit 4): the latest ``AccountAnchorHistory`` row wins
    # over the ``Account.current_anchor_*`` cache so a future cache
    # divergence cannot show a stale projection.  Post-Commit-3 the
    # anchor columns are NOT NULL and the resolver never returns
    # ``None``, so the legacy fallback that substituted the current
    # period (which papered over the NULL-anchor producer drift in
    # CRIT-01) is dead code and is deleted here rather than left
    # unreachable (CLAUDE.md rule 1: do it right, no shortcuts).
    anchor = balance_resolver.resolve_anchor(account, scenario.id) if scenario else None

    # Interest layering flows through ``calculate_balances_with_interest``
    # below (MED-01 / Commit 28: a single canonical resolver, not the old
    # dual interest/no-interest dispatcher).  The entries-aware per-account
    # transaction load (CRIT-01 / F-009 silent-degrade seam) is
    # encapsulated in ``_load_account_transactions``.
    acct_transactions = _load_account_transactions(account, scenario, all_periods)

    balances = {}
    interest_by_period = {}
    if anchor is not None:
        balances, interest_by_period = balance_calculator.calculate_balances_with_interest(
            anchor_balance=anchor.balance,
            anchor_period_id=anchor.period.id,
            periods=all_periods,
            transactions=acct_transactions,
            interest_params=params,
        )

    current_bal = _current_period_balance(balances, current_period, anchor)

    # Build period projection data for the template.
    period_data = _build_period_data(all_periods, balances, interest_by_period)

    # 3/6/12 month horizon projections.
    projected = project_balance_horizons(current_period, all_periods, balances)

    return render_template(
        "accounts/interest_detail.html",
        account=account,
        params=params,
        current_balance=current_bal,
        projected=projected,
        period_data=period_data,
        # #38: the compounding-frequency <select> renders one option per
        # ref row (value = id) so the template never string-compares the
        # frequency name.
        compounding_frequencies=(
            CompoundingFrequency.query
            .order_by(CompoundingFrequency.id)
            .all()
        ),
    )


@accounts_bp.route("/accounts/<int:account_id>/interest/params", methods=["POST"])
@login_required
@require_owner
def update_interest_params(account_id):
    """Update interest parameters (APY, compounding frequency)."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    errors = _interest_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.interest_detail", account_id=account_id))

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
                url_for("accounts.interest_detail", account_id=account_id),
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
    return redirect(url_for("accounts.interest_detail", account_id=account_id))


# ── Property (physical-asset) Detail & Params ─────────────────────


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
        # ``interest_detail``: the create flow already seeds this row, so
        # this branch only fires if it was lost (manual delete / data loss).
        params = AssetAppreciationParams(
            account_id=account.id, annual_appreciation_rate=Decimal("0"),
        )
        db.session.add(params)
        db.session.commit()

    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    equity = home_equity_service.resolve_home_equity(
        account, scenario_id, date.today(),
    )

    return render_template(
        "accounts/property_detail.html",
        account=account,
        params=params,
        equity=equity,
        secured_loans=account.secured_loans,
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


# ── Checking Detail ──────────────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/checking")
@login_required
@require_owner
def checking_detail(account_id):
    """Checking account detail page with balance projections.

    Shows the current anchor balance and projected balances at
    3, 6, and 12-month intervals.  Balances flow through the
    canonical entries-aware producer (E-25 / Commit 5):
    ``balance_resolver.balances_for`` owns the transaction query
    (always ``selectinload``s entries so the entry-aware reduction
    in ``_entry_aware_amount`` applies unconditionally) and the
    anchor resolution (dated ``AccountAnchorHistory`` source of
    truth, never NULL post-Commit-3).  Routing through this single
    producer is the structural fix for CRIT-01 / symptom #5: pre-
    fix the same tuple yielded $160.00 on the grid (entries
    eager-loaded) and $114.29 here (entries unloaded -> silent
    degrade to ``effective_amount`` -- $45.71 of already-cleared
    debits double-subtracted off the anchor).  No interest
    calculations: APY on checking is negligible.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    # Verify this is a checking account.
    if (not account.account_type
            or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.CHECKING)):
        return "Not found", 404

    user_id = current_user.id
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    scenario = get_baseline_scenario(user_id)

    # Route balance projection through the canonical entries-aware
    # producer (E-25, Commit 5).  ``balances_for`` resolves the anchor
    # via the dated ``AccountAnchorHistory`` SoT (E-19, Commit 4), so
    # the legacy NULL-anchor fallback (which substituted the current
    # period when the anchor column was unset) is dead code post-
    # Commit-3 and is deleted rather than left unreachable
    # (CLAUDE.md rule 1).  The
    # ``scenario is None`` and ``no pay periods`` guards are kept --
    # both are legitimately empty-state inputs (a fixture without a
    # baseline scenario, a freshly-registered user with no generated
    # periods) and the template renders cleanly when ``balances`` is
    # empty.
    balances = {}
    anchor = None
    if scenario is not None and all_periods:
        result = balance_resolver.balances_for(
            account, scenario.id, all_periods,
        )
        balances = result.balances
        anchor = balance_resolver.resolve_anchor(account, scenario.id)

    current_bal = _current_period_balance(balances, current_period, anchor)

    # Build period projection data for the template.
    period_data = _build_period_data(all_periods, balances)

    # 3/6/12 month horizon projections (same offsets as HYSA detail; the
    # shared producer also backs interest_detail and the savings dashboard).
    projected = project_balance_horizons(current_period, all_periods, balances)

    # The anchor period for the template header.  ``resolve_anchor``
    # returns the relationship-loaded ``PayPeriod`` directly, so no
    # additional lookup is needed; ``None`` when the user has no
    # baseline scenario (the template guards with ``{% if anchor_period %}``).
    anchor_period = anchor.period if anchor is not None else None

    return render_template(
        "accounts/checking_detail.html",
        account=account,
        current_balance=current_bal,
        projected=projected,
        period_data=period_data,
        anchor_period=anchor_period,
    )
