"""
Shekel Budget App -- Anchor Balance Edit Routes

The grid and Net Worth Cockpit anchor-balance true-up endpoints,
split out of the historical monolithic ``app/routes/accounts.py`` in
Commit 21 of the financial-calculation audit follow-up (F-1).  The
retired ``/accounts`` table's inline balance editor also lived here
until the Net Worth Cockpit replaced that table; the cockpit reuses
the grid editor below, so only that family remains.

``true_up`` routes the actual mutation, history-row append,
conditional entries reconcile, and commit through
:func:`app.services.anchor_service.apply_anchor_true_up`, so the
C-17 / F-009 optimistic-lock contract and the F-103 / C-22 same-day
same-balance idempotency rules live in exactly one place.  This
module is therefore deliberately thin: it owns the HTTP-shaped
concerns (form validation, version_id pre-flush check, HTMX-fragment
rendering, HX-Trigger header composition) and delegates the database
mutation to the shared service.

The editor opens from five surfaces -- the grid cell, the dashboard
balance card, the cockpit per-card cell, the investment / retirement
detail page's balance hero, and the cash detail page's balance hero --
each threaded through as a normalized ``revert`` token so Cancel /
Escape and a 409 conflict re-render the correct opener (see
:func:`_normalize_revert_context`).
"""

import logging
from decimal import Decimal

from flask import jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.services import (
    anchor_service,
    cash_ledger,
    entry_service,
    pay_period_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.account_validation import _anchor_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today, to_display_tz

logger = logging.getLogger(__name__)


# The kind-refusal body shared by the PATCH gate and the editor-form GET
# gate (ruling D4 / step A1): an amortizing loan's balance is
# ledger-derived and is asserted on the loan's own page
# (``apply_loan_anchor_true_up``), never as a cash anchor (B-15).
_LOAN_ANCHOR_REFUSAL = (
    "A loan's balance is not a cash anchor. Record a balance true-up "
    "on the loan's own page instead."
)


def _is_amortizing(account: Account) -> bool:
    """Return True when *account* is an amortizing loan.

    The route-layer twin of the loan package's ``_load_loan_account``
    kind test (``account_type.has_amortization``, a boolean column --
    never a type-name string).  Used to refuse the CASH anchor editor
    for a loan; the service-layer backstop is
    :class:`~app.services.anchor_service.AmortizingAccountAnchorError`.
    """
    acct_type = account.account_type
    return acct_type is not None and acct_type.has_amortization


# ── Anchor Balance True-up (Grid) ─────────────────────────────────


# The non-default surfaces the shared anchor editor can be opened from.
# The opener names its surface via the ``revert`` query token; only these
# canonical values are honored (see :func:`_normalize_revert_context`).
# Each maps to a revert endpoint in :func:`_anchor_revert_url`.  ``investment``
# is the investment / retirement detail page's balance hero (Loop B P1 C4);
# ``cash`` is the cash detail page's balance hero (the S8 / D14 port).
_REVERT_SURFACES = frozenset({"dashboard", "accounts", "investment", "cash"})


def _normalize_revert_context(raw_revert: str | None) -> str | None:
    """Allowlist-validate the raw ``revert`` token to a canonical value.

    The anchor editor is opened from more than one surface, and the
    opener names its surface via the ``revert`` query token.  Four
    non-default surfaces are recognized -- ``dashboard`` (the dashboard
    hero balance card), ``accounts`` (the Net Worth Cockpit's per-card
    balance cell), ``investment`` (the investment / retirement detail
    page's balance hero), and ``cash`` (the cash detail page's balance
    hero); every other value (unset, unknown, an attacker's probe)
    collapses to ``None`` so the grid's default revert target is used.
    Centralizing the allowlist here means the token is validated against
    :data:`_REVERT_SURFACES` in exactly one place -- :func:`_anchor_revert_url`
    (the Cancel / Escape target), the edit form's ``hx-patch`` round-trip
    token, and the conflict cell's retry opener all consume this normalized
    value rather than re-checking the raw string -- so the token is never
    interpolated unvalidated into a URL or template.

    Args:
        raw_revert: The ``revert`` query token as received, or ``None``.

    Returns:
        The canonical surface name when the token names a recognized
        surface; otherwise ``None`` (the grid default).
    """
    return raw_revert if raw_revert in _REVERT_SURFACES else None


def _anchor_conflict_response(
    account: Account, revert_context: str | None = None,
) -> tuple[str, int]:
    """Render the grid anchor-edit cell in conflict mode (HTTP 409).

    Shared by ``true_up``'s pre-flush version-mismatch guard and its
    post-service ``StaleDataError`` outcome so the C-17 / F-009
    optimistic-lock conflict UX is identical for the stale-form and the
    truly-concurrent cases.

    ``revert_context`` carries the surface that opened the editor (the
    normalized token from :func:`_normalize_revert_context`) through the
    409 so the conflict cell's retry opener re-opens ``anchor_form`` with
    the same ``revert`` token.  Without it, a conflict raised from the
    dashboard balance card (or the investment detail hero) would strand the
    card on the grid display cell (the editor's retry would reopen with no
    ``revert``).  ``None`` (the grid default) keeps the conflict cell
    byte-for-byte unchanged.
    """
    return (
        render_template(
            "grid/_anchor_edit.html",
            account=account, editing=False, conflict=True,
            revert_context=revert_context,
        ),
        409,
    )


def reconcile_context(account: Account, panel_id: str) -> dict:
    """Assemble the reconcile panel's context for one account.

    The ONE builder both surfaces read: the post-true-up prompt
    (:func:`_reconcile_prompt_fragment`) and the permanent section on the cash
    account's detail page.  Two builders would be two answers to "what is still
    outstanding on this account", which is the shape plan step S1-c exists to
    remove.

    Args:
        account: The account to reconcile.  Caller owns the ownership check.
        panel_id: The DOM id the inner partial roots at, so a POST's re-render
            swaps in place.

    Returns:
        The template context.  ``outstanding`` is empty (and the partial says
        so) for an account with nothing to reconcile or no assertion at all.
    """
    # The raw DAY, and both uses below are why the boundary offers it: an SQL
    # bound on the offer set, and a rendered caption.  Neither asks whether a
    # movement is inside the balance -- that question has one implementation
    # (``ReconciledThrough.covers``) and neither of these is a second one.
    observed_on = cash_ledger.reconciled_through(account.id).observed_day
    outstanding = (
        []
        if observed_on is None
        else entry_service.outstanding_purchases(
            current_user.id, account.id, observed_on,
        )
    )
    return {
        "account": account,
        "outstanding": outstanding,
        "outstanding_total": sum(
            (entry.amount for entry in outstanding), Decimal("0.00"),
        ),
        "reconciled_through": observed_on,
        "anchor_balance": account.current_anchor_balance,
        "panel_id": panel_id,
    }


def _reconcile_prompt_fragment(account: Account) -> str:
    """Return the out-of-band reconcile prompt, or ``""`` when there is none.

    Appended to a successful true-up's body so the modal lands in
    ``#modal-mount`` (``base.html``) whichever of the five surfaces opened the
    editor.  It is empty whenever the account has nothing outstanding, which is
    the steady state for a user who reconciles as they go -- the one-click
    true-up habit is not taxed by a prompt with nothing in it.

    Args:
        account: The just-asserted account.

    Returns:
        The rendered fragment, or ``""``.
    """
    context = reconcile_context(account, panel_id="reconcile-panel-modal")
    if not context["outstanding"]:
        return ""
    return render_template("accounts/_reconcile_modal.html", **context)


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["GET"],
)
@login_required
@require_owner
def reconcile_panel(account_id):
    """HTMX partial: re-render the account's outstanding-purchase list.

    The ``balanceChanged`` refresh target on the cash detail page's section.
    A true-up moves the day the list is computed against, so a list left
    un-refreshed would offer purchases that are no longer outstanding -- the
    same reason the band above it re-fetches on the same event.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404
    return render_template(
        "accounts/_reconcile_purchases.html",
        **reconcile_context(account, panel_id=_panel_id(account.id)),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["POST"],
)
@login_required
@require_owner
def reconcile_purchases(account_id):
    """Record that the ticked purchases had reached the bank.

    The reconcile step's write door (ruling R-DH (d) / the R-M re-ruling).
    Each ticked entry's ``settled_on`` becomes the civil day the account's
    latest balance was asserted for, after which the projection stops holding
    that purchase's budget back -- the same predicate the balance walk applies
    to a settled transaction, on a date the USER supplied rather than one the
    engine guessed.

    **It is its own request, and that is deliberate.**  Folding it into
    ``apply_anchor_true_up`` would put it inside that function's F-103
    duplicate handler, which catches an ``IntegrityError``, rolls the session
    back and reports idempotent success -- so a same-day re-assert would
    silently discard every reconciliation the user had just made while the UI
    said it saved.  A separate transaction cannot be swallowed by another
    one's rollback.

    Every submitted id is re-scoped in the service against the outstanding set
    (owner, account, debit, projected parent, still unrecorded), so a forged id
    matches nothing rather than raising -- the set-operation form of the
    project's "404 for both not-found and not-yours" rule.

    Returns the refreshed panel plus ``HX-Trigger: balanceChanged`` so every
    surface showing a projection recomputes.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    # The raw DAY: it bounds the offer set in SQL and is STAMPED onto every
    # ticked purchase as its posting day, neither of which is the "is this
    # inside the balance" question (that has one implementation, and this
    # writes the fact that implementation later reads).
    observed_on = cash_ledger.reconciled_through(account.id).observed_day
    if observed_on is None:
        # No balance has ever been asserted for this account, so there is
        # nothing for a purchase to be inside of.  Unreachable through the UI
        # (the panel renders no form in that state) and answered rather than
        # raised, because it is a legitimate empty state.
        return render_template(
            "accounts/_reconcile_purchases.html",
            **reconcile_context(account, panel_id=_panel_id(account.id)),
        )

    entry_service.record_settled_days(
        current_user.id,
        account.id,
        {int(raw) for raw in request.form.getlist("entry_ids") if raw.isdigit()},
        observed_on,
    )
    db.session.commit()

    return (
        render_template(
            "accounts/_reconcile_purchases.html",
            **reconcile_context(account, panel_id=_panel_id(account.id)),
        ),
        200,
        {"HX-Trigger": "balanceChanged"},
    )


def _panel_id(account_id: int) -> str:
    """Return the reconcile panel's DOM id for one account.

    Stated once so the POST's re-render lands on whichever copy of the panel
    submitted it: the modal prompt roots at a fixed id, the detail page's
    section at a per-account one, and both post to the same route.

    Args:
        account_id: The account whose panel id to compose.

    Returns:
        The DOM id string.
    """
    return f"reconcile-panel-{account_id}"


def _true_up_success_response(
    account: Account, revert_context: str | None,
) -> tuple[str, int, dict[str, str]]:
    """Compose the grid anchor true-up success response.

    Shared by ``true_up``'s COMMITTED and DUPLICATE_SAME_DAY outcomes
    (both render the updated display cell and fire ``balanceChanged`` so
    other surfaces recompute).  The single-account grid and dashboard
    surfaces append an out-of-band ``#anchor-as-of`` snippet dating the
    edit; the cockpit (``revert=accounts``), the investment detail hero
    (``revert=investment``), and the cash detail hero (``revert=cash``)
    have no such singleton element and re-render their region on the
    ``balanceChanged`` trigger instead, so emitting the OOB there would
    orphan-target (htmx:oobErrorNoTarget) -- it is skipped.

    Args:
        account: The post-commit account (its ``updated_at`` dates the
            "as of" snippet).
        revert_context: The normalized surface token, or ``None``.

    Returns:
        The ``(body, status, headers)`` tuple Flask returns, carrying the
        ``HX-Trigger: balanceChanged`` header.
    """
    html = render_template(
        "grid/_anchor_edit.html", account=account, editing=False,
    )
    # The reconcile prompt rides along on EVERY surface, unlike the "as of"
    # snippet below: its mount is in ``base.html`` rather than being a
    # per-surface singleton, precisely so the one question worth asking after a
    # balance reading -- which of these purchases has your bank taken? -- is
    # asked wherever the reading was entered.  Empty when nothing is
    # outstanding, so a routine true-up is unchanged.
    prompt = _reconcile_prompt_fragment(account)
    if revert_context in ("accounts", "investment", "cash"):
        return html + prompt, 200, {"HX-Trigger": "balanceChanged"}
    as_of_html = (
        f'<small class="text-muted" id="anchor-as-of" hx-swap-oob="true">'
        f'as of {to_display_tz(account.updated_at).strftime("%b %-d, %Y")}'
        f'</small>'
    )
    return html + as_of_html + prompt, 200, {"HX-Trigger": "balanceChanged"}


def _true_up_request_gates(
    account: Account, revert_context: str | None,
) -> tuple[Decimal | None, object, tuple | None]:
    """Run every pre-mutation gate for ``true_up`` in one place.

    The route grew a fifth early-return gate when the amortizing-kind
    refusal landed (ruling D4 / step A1), tripping Pylint's
    return-statement ceiling; consolidating the gates into a
    ``(values, failure)`` helper mirrors ``_validate_update_account``'s
    established shape.  Gate order: kind refusal first (a loan is
    rejected before its form is even validated -- the KIND of edit is
    wrong, not the payload), then schema validation, the C-17 stale-form
    check, and the current-period resolution.

    Args:
        account: The owned, attached :class:`Account` under edit.
        revert_context: The normalized opener-surface token, threaded
            into the 409 conflict response so its retry reopens the
            correct surface.

    Returns:
        ``(new_balance, current_period, failure)``.  On success,
        ``failure`` is ``None`` and the first two carry the validated
        values.  On rejection, ``failure`` is the ready-to-return Flask
        response and the values are ``None``.
    """
    if _is_amortizing(account):
        return None, None, (_LOAN_ANCHOR_REFUSAL, 422)

    errors = _anchor_schema.validate(request.form)
    if errors:
        return None, None, (jsonify(errors=errors), 400)

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    submitted_version = data.get("version_id")
    if submitted_version is not None and submitted_version != account.version_id:
        logger.info(
            "Stale-form conflict on true_up id=%d "
            "(submitted=%d, current=%d)",
            account.id, submitted_version, account.version_id,
        )
        return None, None, _anchor_conflict_response(account, revert_context)

    # One clock for the period and for the assertion's day -- see the
    # matching comment in ``accounts.crud.update_account``.
    current_period = pay_period_service.get_current_period(
        current_user.id, as_of=display_today(),
    )
    if current_period is None:
        return None, None, ("No current pay period found", 400)

    return new_balance, current_period, None


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@login_required
@require_owner
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.

    Refuses an AMORTIZING account with 422 (ruling D4 / step A1,
    finding B-15): a loan's balance is ledger-derived and is asserted
    through the loan page's own true-up, never as a cash anchor.

    Optimistic locking (commit C-17 / F-009): the grid edit form
    submits ``version_id`` as a hidden input.  When the value no
    longer matches ``Account.version_id`` (because another tab,
    window, or concurrent request advanced the row), the handler
    returns the ``grid/_anchor_edit.html`` partial in conflict mode
    with HTTP 409 and DOES NOT write either the balance or a
    history row -- the audit trail captures only the winner.  The
    same conflict UX is rendered when SQLAlchemy raises
    ``StaleDataError`` at flush time for the truly-concurrent
    interleaving the form-side check cannot see.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    # The opener (dashboard balance card or grid cell) threads its surface
    # on the PATCH query so a 409 conflict response can re-render the
    # conflict cell with the correct retry-reopen target.  Normalized
    # against the allowlist so the token is never interpolated unvalidated.
    revert_context = _normalize_revert_context(request.args.get("revert"))

    # Every pre-mutation gate (the D4/A1 amortizing-kind refusal, schema
    # validation, the C-17 stale-form check, current-period resolution)
    # lives in ``_true_up_request_gates``; a failure is returned as-is.
    new_balance, current_period, failure = _true_up_request_gates(
        account, revert_context,
    )
    if failure is not None:
        return failure

    # Canonical anchor true-up path: route the mutation, history-row
    # append, conditional entries reconcile, and commit through the
    # single authoritative helper (``anchor_service.apply_anchor_true_up``)
    # so the C-17 optimistic lock and the F-103 / C-22 same-day
    # same-balance idempotency rules cannot drift.  The route pre-gates
    # the amortizing kind, so the service's
    # ``AmortizingAccountAnchorError`` backstop is unreachable here (a
    # bypassing caller correctly surfaces it as a 500).  The
    # success-response composition (the updated cell, the optional OOB
    # "as-of" snippet, and the ``HX-Trigger: balanceChanged`` header)
    # lives in ``_true_up_success_response``.
    outcome = anchor_service.apply_anchor_true_up(
        account=account,
        new_balance=new_balance,
        anchor_period=current_period,
    )

    if outcome is AnchorTrueUpOutcome.STALE_CONFLICT:
        account = db.session.get(Account, account_id)
        return _anchor_conflict_response(account, revert_context)

    # DUPLICATE_SAME_DAY and COMMITTED share the success response (the
    # updated cell + an OOB "as of" snippet + the HX-Trigger that
    # recomputes other grid cells), so they converge on one return.
    if outcome is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY:
        # F-103 idempotent success: re-fetch the already-current row so
        # the partial renders the committed balance.
        account = db.session.get(Account, account_id)
    else:
        # COMMITTED: refresh the in-memory account so the partial shows
        # the post-commit state (notably ``updated_at``, refreshed by the
        # audit trigger server-side).
        db.session.refresh(account)
        logger.info(
            "True-up: account %d set to $%s at period %d",
            account.id, new_balance, current_period.id,
        )

    return _true_up_success_response(account, revert_context)


def _anchor_revert_url(account_id, revert_context):
    """Resolve the URL the anchor editor reverts to on Cancel / Escape.

    The anchor editor (``grid/_anchor_edit.html``) is opened from more
    than one surface, and Cancel / Escape must restore whichever surface
    opened it -- not always the grid display cell.  This maps the
    normalized surface token (from :func:`_normalize_revert_context`) to
    the GET endpoint that re-renders the opener.  ``None`` falls back to
    the grid's ``anchor_display`` so the grid path is byte-for-byte
    unchanged (it passes no ``revert``).

    Locked contexts:

    * ``dashboard`` -- the dashboard balance card re-renders via
      ``dashboard.balance_section`` (restores the account name, caption,
      and runway the grid display cell lacks; the audit's cancel-path
      stranding fix).
    * ``accounts`` -- the Net Worth Cockpit's per-card balance cell
      re-renders via ``savings.cockpit_balance`` (restores that one card's
      resolver balance; the cockpit is multi-card, so the revert is
      account-scoped rather than the dashboard's single hero).
    * ``investment`` -- the investment / retirement detail page's balance
      hero re-renders via ``investment.balance_hero`` (restores the
      model-from-anchor balance the detail headline shows; Loop B P1 C4).
    * ``cash`` -- the cash detail page's balance hero re-renders via
      ``accounts.cash_balance_hero`` (restores the resolver
      current-period balance the detail headline shows; S8 / D14 port).
    * default / grid -- ``accounts.anchor_display`` (the grid cell).

    Args:
        account_id: The account whose editor is being reverted.
        revert_context: The normalized surface token, or ``None``.

    Returns:
        The revert URL string.
    """
    if revert_context == "dashboard":
        return url_for("dashboard.balance_section")
    if revert_context == "accounts":
        return url_for("savings.cockpit_balance", account_id=account_id)
    if revert_context == "investment":
        return url_for("investment.balance_hero", account_id=account_id)
    if revert_context == "cash":
        return url_for("accounts.cash_balance_hero", account_id=account_id)
    return url_for("accounts.anchor_display", account_id=account_id)


@accounts_bp.route("/accounts/<int:account_id>/anchor-form", methods=["GET"])
@login_required
@require_owner
def anchor_form(account_id):
    """HTMX partial: return the inline edit form for the anchor balance.

    Accepts an optional ``revert`` query parameter naming the surface
    that opened the editor (e.g. ``dashboard``), so Cancel and Escape
    restore that surface rather than always swapping in the grid display
    cell.  See :func:`_anchor_revert_url` for the mapping; an unset value
    keeps the grid's default revert target.

    The normalized token is also passed to the template as
    ``revert_context`` so the edit form's ``hx-patch`` carries the surface
    through the mutation round-trip: a 409 conflict response can then
    re-render the conflict cell with the same retry-reopen target rather
    than stranding the dashboard card on the grid display cell.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    # Ruling D4 / step A1: never OPEN the cash anchor editor for an
    # amortizing loan -- the PATCH would be refused (B-15), so offering
    # the form would be a dead-end affordance.  The cockpit's loan cards
    # render their balance read-only for the same reason.
    if _is_amortizing(account):
        return _LOAN_ANCHOR_REFUSAL, 422

    revert_context = _normalize_revert_context(request.args.get("revert"))
    revert_url = _anchor_revert_url(account_id, revert_context)
    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=True,
        revert_url=revert_url,
        revert_context=revert_context,
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-display", methods=["GET"])
@login_required
@require_owner
def anchor_display(account_id):
    """HTMX partial: return the anchor balance display (non-editing)."""
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=False,
    )
