"""
Shekel Budget App -- Loan route package: detail dashboard.

The loan detail page (GET): summary card, payment breakdown, multi-scenario
balance chart, escrow / rate-history panels, amortization-schedule tab, and the
recurring-transfer prompt.  The route assembles its template context by merging
the per-section dicts the private helpers below return; the recurrence
end_date sync (a deliberate write on a GET, R-4) also lives here because the
dashboard is where the payoff date is computed with full payment context.
"""

from datetime import date
from decimal import Decimal, ROUND_DOWN

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _forward_boundary,
    _load_loan_account,
    _load_route_context,
    _loan_inputs,
    build_band_chart,
    build_baseline_scenarios,
)
from app.services import balance_at, escrow_calculator
from app.services.amortization_engine import AmortizationSummary
from app.services.loan_posting_service import (
    confirmed_loan_payment_history,
    loan_balance_anchor_history,
)
from app.services.resolution_context import BalanceContext
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today


def _find_current_period_row(schedule):
    """Find the schedule row for the current or next upcoming payment.

    Returns the first projected (non-confirmed) row if one exists,
    otherwise the last confirmed row.  Returns None for an empty
    schedule.

    This approach is more robust than date-based lookup because
    shadow transaction dates (biweekly) and schedule payment dates
    (monthly) use different calendars.  The confirmed/projected
    boundary is the cleanest split.

    Args:
        schedule: List of AmortizationRow objects.

    Returns:
        AmortizationRow or None.
    """
    if not schedule:
        return None
    for row in schedule:
        if not row.is_confirmed:
            return row
    # All rows confirmed -- use the last one.
    return schedule[-1]


def _distribute_payment_percentages(parts, total_payment):
    """Compute portion percentages that sum to exactly 100.0%.

    Truncate-then-distribute: each part is truncated to one decimal
    place (ROUND_DOWN), then the residual needed to reach 100.0% is
    assigned to the largest part.  Guarantees the percentages sum to
    exactly 100.0% regardless of per-part rounding.

    Args:
        parts: List of ``(name, amount)`` tuples (Decimal amounts).
        total_payment: Decimal sum of the part amounts; must be > 0.

    Returns:
        dict mapping each part name to its Decimal percentage.
    """
    one_decimal = Decimal("0.1")
    truncated = {}
    for name, amount in parts:
        raw_pct = amount / total_payment * 100
        truncated[name] = raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)

    residual = Decimal("100.0") - sum(truncated.values())
    # Assign residual to the largest portion.
    largest = max(truncated, key=truncated.get)
    truncated[largest] += residual
    return truncated


def _project_next_year_escrow(escrow_components, escrow_portion):
    """Project next year's monthly escrow when a component inflates.

    O-3: if any active line carries a positive ``inflation_rate`` and the current
    escrow portion is positive, compound today's escrow forward one whole annual
    step (:func:`~app.services.escrow_calculator.project_monthly_escrow`) so the
    dashboard can flag the likely increase.  A forward DISPLAY estimate only --
    recorded escrow is exact.

    The projection is one annual step from today (spec Sec. 8), matching the
    per-year meaning of ``inflation_rate`` -- NOT an elapsed span since a
    version's insert timestamp -- so the note is stable regardless of when the
    version was recorded or when the page is viewed.

    Args:
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
        escrow_portion: Decimal current monthly escrow.

    Returns:
        Decimal projected escrow when it differs from the current
        portion, otherwise None (no note shown).
    """
    has_inflation = any(
        c.inflation_rate is not None and c.inflation_rate > 0
        for c in escrow_components
    )
    if not has_inflation or escrow_portion <= Decimal("0.00"):
        return None

    next_year_escrow = escrow_calculator.project_monthly_escrow(
        escrow_components, 1,
    )
    # Only show the note if next year differs from current.
    if next_year_escrow == escrow_portion:
        return None
    return next_year_escrow


def _compute_payment_breakdown(schedule, escrow_components):
    """Build payment allocation breakdown for the current period.

    Combines the amortization engine's per-period principal/interest
    split with the escrow calculator's monthly total to show the user
    exactly how their payment is allocated.

    Percentages are computed with a truncate-then-distribute algorithm
    to guarantee they sum to exactly 100.0%.

    Args:
        schedule: List of AmortizationRow objects (committed schedule).
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).

    Returns:
        dict with breakdown data, or None if no schedule data.
    """
    current_row = _find_current_period_row(schedule)
    if current_row is None:
        return None

    principal_portion = current_row.principal + current_row.extra_payment
    interest_portion = current_row.interest
    escrow_portion = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )
    total_payment = principal_portion + interest_portion + escrow_portion

    if total_payment <= Decimal("0.00"):
        return None

    truncated = _distribute_payment_percentages(
        [
            ("principal", principal_portion),
            ("interest", interest_portion),
            ("escrow", escrow_portion),
        ],
        total_payment,
    )
    next_year_escrow = _project_next_year_escrow(
        escrow_components, escrow_portion,
    )

    return {
        "principal": principal_portion,
        "interest": interest_portion,
        "escrow": escrow_portion,
        "total": total_payment,
        "principal_pct": truncated["principal"],
        "interest_pct": truncated["interest"],
        "escrow_pct": truncated["escrow"],
        "is_confirmed": current_row.is_confirmed,
        "payment_date": current_row.payment_date,
        "next_year_escrow": next_year_escrow,
    }


def _build_planned_summary(monthly_payment, planned_schedule, params):
    """Build the life-of-loan AmortizationSummary from the planned schedule.

    monthly_payment comes from the seam figures (single source of truth);
    total_interest / payoff_date are summed/read over ``planned_schedule``
    (history + forward) so the "Total Interest (life of loan)" and
    "Projected Payoff" cards reflect the user's full trajectory.  The
    composer's ``total_interest_committed`` covers the forward slice
    only; summing over ``planned_schedule`` adds back the history-row
    interest the dashboard has always displayed.

    Args:
        monthly_payment: The loan's P&I payment (``ctx.monthly_payment`` --
            the seam figure).
        planned_schedule: history + committed-forward AmortizationRows.
        params: ORM :class:`LoanParams` (origination fallback date).

    Returns:
        :class:`AmortizationSummary` (no acceleration: with-extra fields
        mirror the base fields, months/interest saved zero).
    """
    planned_total_interest = sum(
        (row.interest for row in planned_schedule), Decimal("0.00"),
    )
    planned_payoff_date = (
        planned_schedule[-1].payment_date if planned_schedule
        else params.origination_date
    )
    return AmortizationSummary(
        monthly_payment=monthly_payment,
        total_interest=planned_total_interest,
        payoff_date=planned_payoff_date,
        total_interest_with_extra=planned_total_interest,
        payoff_date_with_extra=planned_payoff_date,
        months_saved=0,
        interest_saved=Decimal("0.00"),
    )


def _build_payment_summary(
    current_balance, summary, planned_schedule, escrow_components,
):
    """Build the loan-card payment-summary template context.

    Bundles the seam's current balance, the total monthly
    payment (P&I + escrow), the current-period payment breakdown, and
    the escrow display list.  The life-of-loan ``summary`` is built by
    the caller (it is also needed for the recurrence end_date sync) and
    passed in for its ``monthly_payment``.  The payment breakdown uses
    the planned schedule so it reflects the next planned payment, not
    the contractual one when the user is under-/over-paying.

    Args:
        current_balance: The loan's balance-at-today (``ctx.current_balance`` --
            the seam's fold, plan C4).
        summary: The life-of-loan :class:`AmortizationSummary` (monthly_payment
            source for the total payment).
        planned_schedule: history + committed-forward AmortizationRows.
        escrow_components: Today's active escrow lines.

    Returns:
        dict of template vars: current_principal_display, total_payment,
        payment_breakdown.  The escrow card display model
        (``escrow_components``) is built separately in the route via
        :func:`app.services.escrow_calculator.build_escrow_card`, which needs the
        raw lines + the forward-only boundary rather than the resolved-today set.
    """
    return {
        # Plan C4: the seam's fold; equals the /savings debt card balance
        # and the net-worth liability (same seam, same resolution).
        "current_principal_display": current_balance,
        "total_payment": escrow_calculator.calculate_total_payment(
            summary.monthly_payment, escrow_components,
        ),
        "payment_breakdown": _compute_payment_breakdown(
            planned_schedule, escrow_components,
        ),
    }


def _build_band_context(scenarios, has_payments):
    """Build the dashboard's band-chart template context.

    Wraps :func:`._helpers.build_band_chart` (one committed-or-contractual
    balance line on the contractual x-axis, which the client splits at the
    confirmed / projected boundary) and derives ``has_chart`` -- the band renders
    the chart when the line has points, otherwise a "paid off" note.  The client
    (``loan_detail.js``) reads the serialized ``band_chart`` dict from
    ``data-chart`` and overlays the payoff lever's accelerated preview onto it.

    Args:
        scenarios: The baseline :class:`PayoffScenarios` from
            :func:`._helpers.build_baseline_scenarios`.
        has_payments: ``True`` when the loan has a recurring payment plan
            (selects the committed line over the contractual original).

    Returns:
        dict of template vars: band_chart (the serializable dict), has_chart.
    """
    band_chart = build_band_chart(scenarios, has_payments)
    return {
        "band_chart": band_chart,
        "has_chart": bool(band_chart["balance"]),
    }


def _resolve_transfer_prompt(account, template):
    """Resolve the recurring-transfer prompt state for the dashboard.

    The prompt shows when LoanParams exist but no active recurring
    transfer template targets this account.  When shown, the eligible
    source accounts (active, non-amortizing, excluding this account) and
    the default source (the checking account, if any) are loaded.  When a
    recurring payment DOES exist, ``has_recurring_payment`` gates the
    extra-principal edit control and ``recurring_payment_extra`` prefills it
    from the payment's ``loan_payment_settings`` (0.00 when it has no settings
    row -- a legacy manual payment).

    Args:
        account: The loan account (scopes the source-account picker).
        template: The loan's active recurring transfer template, or ``None`` --
            loaded ONCE by the caller and shared with :func:`_payment_drift`, so
            the page resolves the recurring payment a single time.

    Returns:
        ``prompt_context`` -- a dict of template vars: show_transfer_prompt,
        source_accounts, default_source_id, has_recurring_payment,
        recurring_payment_extra.
    """
    if template is not None:
        settings = template.settings
        extra = (
            Decimal(str(settings.extra_principal))
            if settings is not None else Decimal("0.00")
        )
        return {
            "show_transfer_prompt": False,
            "source_accounts": [],
            "default_source_id": None,
            "has_recurring_payment": True,
            "recurring_payment_extra": extra,
        }

    source_accounts = (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == current_user.id,
            Account.is_active.is_(True),
            Account.id != account.id,
            AccountType.has_amortization.is_(False),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    # Default to the checking account if one exists.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id = next(
        (acct.id for acct in source_accounts
         if acct.account_type_id == checking_type_id),
        None,
    )
    return {
        "show_transfer_prompt": True,
        "source_accounts": source_accounts,
        "default_source_id": default_source_id,
        "has_recurring_payment": False,
        "recurring_payment_extra": Decimal("0.00"),
    }


def _payment_drift(template, total_payment):
    """Return the recurring-payment underpayment-drift warning context, or None.

    Warns (ruling D3, step C7) when the loan has a MANUAL recurring payment whose
    stored base (``default_amount`` -- the P&I + escrow captured at its last write)
    is now LESS than today's contractual monthly payment (``total_payment`` =
    resolved P&I + active escrow), i.e. the transfer underfunds the loan after an
    escrow or rate change.  Both figures are extra-free (the standing
    ``extra_principal`` is added live on top of each and so cancels in the
    comparison), so this measures the base drift regardless of any overpayment.

    Returns ``None`` -- no warning -- for exactly the cases the ruling excludes:

    * no recurring payment (``template is None``);
    * a DERIVE-mode payment, whose projected cash is recomputed to the contract on
      every read (:func:`app.services.loan_payment_service.live_loan_transfer_amounts`),
      so it can never drift;
    * a payment at or ABOVE contract -- underpayment-only, so a deliberate
      overpayment never trips it.

    Args:
        template: The loan's active recurring transfer template, or ``None``
            (loaded once by the caller, shared with :func:`_resolve_transfer_prompt`).
        total_payment: The loan's contractual monthly payment today (P&I +
            escrow) -- the same figure the loan card displays and
            :func:`app.routes.loan.payment_transfer.track_payment` writes, so
            clicking the warning's action drives the drift to exactly zero.

    Returns:
        ``{"stored": Decimal, "contract": Decimal, "shortfall": Decimal}`` when the
        manual payment is short, else ``None``.
    """
    if template is None:
        return None
    settings = template.settings
    if settings is not None and settings.derive_from_loan:
        return None
    stored = Decimal(str(template.default_amount))
    if stored >= total_payment:
        return None
    return {
        "stored": stored,
        "contract": total_payment,
        "shortfall": total_payment - stored,
    }


def _load_collateral_candidates(user_id):
    """Return the user's active Property accounts for the secured-by picker.

    The loan dashboard's "Secured by" picker offers the physical assets a
    loan can be secured by (``has_appreciation`` types, i.e. Property), so a
    mortgage / HELOC can be grouped with the home it is secured by and
    equity rendered.  Empty when the user has none -- the template then
    shows a "create a property first" prompt instead of an empty dropdown.

    Args:
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        list[Account]: active Property accounts, ordered for display.
    """
    return (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.has_appreciation.is_(True),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def _build_measured_context(account, balance_ctx: BalanceContext, current_year):
    """Build the loan detail page's ledger-MEASURED template context.

    The Loop B rebuild surfaces the genesis ledger's real, paid facts the page
    previously computed but never showed (docs/design/loan_audit.md): the
    interest and principal actually PAID this calendar year (the two band chips),
    the confirmed payment-history rows (each real cash / principal / interest /
    escrow split), and the balance-anchor drift scorecard (each opening /
    true-up recorded balance vs the ledger's computed balance just before it).

    **The two YTD chips fold the loan's SOURCE events (step C6c).**  They read the
    balance seam's paid-in-year producers
    (:func:`app.services.balance_at.loan_interest_paid_in_year` /
    :func:`~app.services.balance_at.loan_principal_paid_in_year`), which sample the
    read pass's memoized walk (:meth:`BalanceContext.loan_walk`) the balance hero
    already folded -- so the loan is walked ONCE for the whole page.  Both are
    TOTAL (a configured loan always folds a real ``Decimal``, ``0.00`` when it has
    paid nothing this year), where the posting readers they replaced returned
    ``None`` for a cold posting cache and hid the chip; the loan-detail page
    renders only for a configured loan, so the chips always show the real figure.

    The payment history and the anchor scorecard still read the posting ledger and
    still return ``None`` for an un-backfilled loan; the template gates those two
    on truthiness, so ``[]`` (an originated loan with no confirmed payment yet)
    hides them exactly as ``None`` does.

    Args:
        account: The loan account (its ``id`` scopes the history / anchor readers;
            the object itself keys the seam's memoized walk for the chips).
        balance_ctx: The read pass's
            :class:`~app.services.resolution_context.BalanceContext` -- its
            ``scenario_id`` scopes every producer and its ``as_of``
            (``date.today()``) is the history / anchor display boundary.  Shared
            with the balance hero so the chips fold the memoized walk.
        current_year: The DISPLAY-timezone civil year (``display_today().year``)
            the two YTD chips sum interest / principal within.  The producers
            attribute each payment by its display-tz civil paid date (the L9
            rule), so the summing year must be the display-tz year -- NOT
            ``balance_ctx.as_of.year`` (backend UTC) -- to keep the chips in the
            same civil year as the analytics Taxes tab in the New Year window where
            UTC and Eastern differ.

    Returns:
        dict of template vars: interest_paid_ytd, principal_paid_ytd,
        payment_history, balance_anchors.
    """
    return {
        "interest_paid_ytd": balance_at.loan_interest_paid_in_year(
            account, balance_ctx, current_year,
        ),
        "principal_paid_ytd": balance_at.loan_principal_paid_in_year(
            account, balance_ctx, current_year,
        ),
        "payment_history": confirmed_loan_payment_history(
            account.id, balance_ctx.scenario_id, balance_ctx.as_of,
        ),
        "balance_anchors": loan_balance_anchor_history(
            account.id, balance_ctx.scenario_id, balance_ctx.as_of,
        ),
    }


def _load_configured_loan_or_404(account_id):
    """Load a configured loan for a detail-page HTMX fragment, or 404.

    The shared gate for the hero-cell partials below: ``_load_loan_account``
    resolves cross-owner / non-existent / non-loan accounts to ``None``
    (the project's "404 for not-found and not-yours" rule), and an
    owner's loan WITHOUT ``LoanParams`` also 404s here -- the fragments
    are cells of the configured dashboard, which does not render for an
    unconfigured loan (that page shows setup.html), so no fragment of it
    exists to serve.  This deliberately differs from
    ``_require_configured_loan``'s flash-and-redirect, which suits the
    full-page POST flows but would swap a whole redirected page into an
    HTMX hero slot.

    Args:
        account_id: The loan account id from the route.

    Returns:
        ``(account, params)`` -- only for a configured, owned loan.
    """
    account, params, _ = _load_loan_account(account_id)
    if account is None or params is None:
        abort(404)
    return account, params


@loan_bp.route("/accounts/<int:account_id>/loan/balance-hero")
@login_required
@require_owner
def balance_hero(account_id):
    """HTMX partial: the loan balance hero cell (D14 click-to-edit port).

    The Cancel / Escape revert target for the loan detail page's
    click-to-edit dated true-up editor, mirroring
    :func:`investment.balance_hero`: renders ``loan/_balance_hero.html``
    with the seam's current balance (the fold), so a reverted cell
    restores the exact figure the page loaded with.  There is no
    save-path revert here -- a save posts :func:`loan.true_up_balance`'s
    full-page redirect flow (see the partial's docstring).

    Non-HTMX requests redirect to the loan dashboard page (the cell is
    a fragment, not a standalone page).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("loan.dashboard", account_id=account_id))
    account, params = _load_configured_loan_or_404(account_id)
    ctx = _load_route_context(account, params)
    return render_template(
        "loan/_balance_hero.html",
        account=account,
        current_principal_display=ctx.current_balance,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/anchor-form")
@login_required
@require_owner
def anchor_form(account_id):
    """HTMX partial: the loan hero's dated true-up editor (D14).

    Returns ``loan/_anchor_edit.html`` -- the inline as-of-date +
    balance form the click-to-edit hero swaps in.  The form posts the
    existing :func:`loan.true_up_balance` redirect flow (the whole page
    re-renders on save; every dependent figure recomputes together);
    Cancel / Escape swap back through :func:`balance_hero`.  The seam's
    current balance (the fold) prefills the balance field and
    ``origination_date`` floors the date input, matching the parameters
    card's "Record balance" form bounds.

    Non-HTMX requests redirect to the loan dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("loan.dashboard", account_id=account_id))
    account, params = _load_configured_loan_or_404(account_id)
    ctx = _load_route_context(account, params)
    return render_template(
        "loan/_anchor_edit.html",
        account=account,
        params=params,
        current_principal_display=ctx.current_balance,
        today_iso=date.today().isoformat(),
    )


@loan_bp.route("/accounts/<int:account_id>/loan")
@login_required
@require_owner
def dashboard(account_id):
    """Loan detail page: the balance band, what-if levers, and the section cards."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)

    if params is None:
        return render_template(
            "loan/setup.html",
            account=account,
            account_type=account_type,
        )

    ctx = _load_route_context(account, params)
    scenario_id = ctx.balance_ctx.scenario_id
    today = date.today()
    # Resolve the recurring-payment state first: it carries the standing
    # extra_principal the committed trajectory must reflect (step 5), so the
    # band chart / payoff summary accelerate exactly as the cash debit does.
    # R-4: the recurring transfer's end_date is NOT written here (that would be a
    # write on a GET); it is synced at every payoff-affecting mutation instead.
    # Loaded ONCE and shared with the drift warning below, so the page resolves
    # the recurring payment a single time.
    payment_template = active_recurring_transfer_template(
        account.id, current_user.id,
    )
    prompt_context = _resolve_transfer_prompt(account, payment_template)
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), scenario_id, today,
        prompt_context["recurring_payment_extra"],
    )
    # PLANNED-trajectory schedule: real confirmed history + projected /
    # contractual forward.  The loan card's current_balance (the seam's fold)
    # and the forward projection here both derive from the SAME genesis-ledger
    # balance (plan Section 8), so the card / debt card / net-worth
    # liability and the chart cannot diverge (the E-18 invariant).
    planned_schedule = scenarios.history_rows + scenarios.committed_forward
    summary = _build_planned_summary(
        ctx.monthly_payment, planned_schedule, params,
    )

    context = {
        "account": account,
        "account_type": account_type,
        "params": params,
        "summary": summary,
        "rate_history": ctx.loan.rate_history,
        # DH-#56: the rate columns the dashboard displays/edits, derived
        # from the resolver / RateHistory (the retired
        # ``LoanParams.interest_rate`` is gone).  ``current_rate`` is the
        # rate in effect today (the card display); ``origination_rate`` is
        # the loan's earliest RateHistory row -- the period-0 rate the
        # "Loan Parameters" form edits (and ``update_params`` upserts).
        # ``rate_history`` is ordered effective_date DESC, so the last
        # element is the earliest (origination) row; it is guaranteed
        # non-empty here because the seam resolved the loan (raising if no
        # origination row exists).
        "current_rate": ctx.current_rate,
        "origination_rate": ctx.loan.rate_history[-1].interest_rate,
        "monthly_escrow": ctx.loan.monthly_escrow,
        # E-18 / Commit 16: today's ISO date pre-fills the "Record Loan
        # Balance" form's as-of date and caps its ``max``.  Computed here
        # (not via a Jinja global) so a test that freezes ``date.today()``
        # sees the frozen value on the page.
        "today_iso": date.today().isoformat(),
        # Home-equity link: the Property accounts this loan can be secured
        # by drive the "Secured by" picker; the current selection is read
        # off ``account.collateral_account_id`` in the template.
        "collateral_candidates": _load_collateral_candidates(current_user.id),
    }
    context.update(_build_payment_summary(
        ctx.current_balance, summary, planned_schedule,
        ctx.loan.escrow_components,
    ))
    # C7 (D3): the underpayment-drift warning -- a manual recurring payment that
    # has fallen short of today's contractual payment after an escrow / rate
    # change.  Reuses the loan card's already-computed total_payment (no re-fold)
    # and the once-loaded template, so the page adds no extra resolution.
    context["payment_drift"] = _payment_drift(
        payment_template, context["total_payment"],
    )
    # Escrow card: the version-drawer model, built off the raw lines
    # (``ctx.loan.escrow_lines``, loaded with the same context) and keyed by the
    # forward-only boundary so each drawer row's edit / delete controls match the
    # HTMX routes' guard.  Same builder the escrow routes re-render on a swap.
    context["escrow_components"] = escrow_calculator.build_escrow_card(
        ctx.loan.escrow_lines, today, _forward_boundary(account.id, scenario_id),
    )
    # Merge candidates: every escrow line (incl. hidden removed ones) offered as a
    # source in each drawer, so a rename-split history can be reunified.
    context["merge_candidates"] = escrow_calculator.build_merge_candidates(
        ctx.loan.escrow_lines,
    )
    context.update(_build_band_context(scenarios, len(ctx.loan.payments) > 0))
    # YTD chips sum by the user's display-tz civil year (matching the Taxes tab
    # + the L9 attribution rule), not the backend-UTC ``today.year``.  The chips
    # fold the read pass's memoized walk (ctx.balance_ctx), so the page walks the
    # loan once for the balance hero and both chips.
    context.update(_build_measured_context(
        account, ctx.balance_ctx, display_today().year,
    ))
    context.update(prompt_context)
    return render_template("loan/dashboard.html", **context)
