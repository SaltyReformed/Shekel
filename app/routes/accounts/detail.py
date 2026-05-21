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
``balance_calculator.calculate_balances`` does not.  When the split
in Commit 21 moved ``checking_detail`` into this module, the F-6
guard's file-path reference was updated to point here.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.transaction import Transaction
from app.routes.accounts import accounts_bp
from app.services import balance_calculator, balance_resolver, pay_period_service
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.account_validation import _interest_params_schema
from app.utils.auth_helpers import get_or_404, require_owner

logger = logging.getLogger(__name__)


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
        return redirect(url_for("accounts.list_accounts"))

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
        params = InterestParams(account_id=account.id, apy=Decimal("0"))
        db.session.add(params)
        db.session.commit()

    user_id = current_user.id
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    scenario = get_baseline_scenario(user_id)

    period_ids = [p.id for p in all_periods]

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

    # Load transactions scoped to this account.  ``selectinload`` on
    # ``Transaction.entries`` closes the silent-degrade seam this route
    # used to share with /savings (CRIT-01 / F-009): the entries-aware
    # reduction in ``_entry_aware_amount`` now applies unconditionally,
    # avoiding the lazy-load N+1 the math-layer fix in Commit 5 would
    # otherwise incur for this caller.  Interest layering still flows
    # through ``calculate_balances_with_interest``; MED-01 / Commit 28
    # collapses the dual interest/no-interest dispatcher into a single
    # canonical resolver.
    acct_transactions = (
        db.session.query(Transaction)
        .options(selectinload(Transaction.entries))
        .filter(
            Transaction.account_id == account.id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

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

    current_bal = (
        balances.get(current_period.id) if current_period else None
    )
    if current_bal is None and anchor is not None:
        current_bal = anchor.balance

    # Build period projection data for the template.
    period_data = []
    for p in all_periods:
        if p.id in balances:
            period_data.append({
                "period": p,
                "balance": balances[p.id],
                "interest": interest_by_period.get(p.id, Decimal("0.00")),
            })

    # 3/6/12 month horizon projections.
    projected = {}
    for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
        if current_period:
            target_idx = current_period.period_index + offset_count
            for p in all_periods:
                if p.period_index == target_idx and p.id in balances:
                    projected[offset_label] = balances[p.id]
                    break

    return render_template(
        "accounts/interest_detail.html",
        account=account,
        params=params,
        current_balance=current_bal,
        projected=projected,
        period_data=period_data,
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
        return redirect(url_for("accounts.list_accounts"))

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
        params = InterestParams(account_id=account.id)
        db.session.add(params)

    if "apy" in data:
        # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
        # already divided the form's user-facing percent by 100, so
        # ``data["apy"]`` is the storage-domain decimal fraction the
        # DB CHECK ``apy >= 0 AND apy <= 1`` enforces.  The route
        # stores it verbatim; no second divide.
        params.apy = data["apy"]
    if "compounding_frequency" in data:
        params.compounding_frequency = data["compounding_frequency"]

    db.session.commit()
    logger.info("Updated interest params for account %d", account.id)
    flash("Interest parameters updated.", "success")
    return redirect(url_for("accounts.interest_detail", account_id=account_id))


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
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
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

    current_bal = (
        balances.get(current_period.id) if current_period else None
    )
    if current_bal is None and anchor is not None:
        current_bal = anchor.balance

    # Build period projection data for the template.
    period_data = []
    for p in all_periods:
        if p.id in balances:
            period_data.append({
                "period": p,
                "balance": balances[p.id],
            })

    # 3/6/12 month horizon projections (same offsets as HYSA detail).
    projected = {}
    for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
        if current_period:
            target_idx = current_period.period_index + offset_count
            for p in all_periods:
                if p.period_index == target_idx and p.id in balances:
                    projected[offset_label] = balances[p.id]
                    break

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
