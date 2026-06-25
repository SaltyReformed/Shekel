"""
Shekel Budget App -- Anchor Balance Edit Routes

Anchor-balance true-up endpoints for both the accounts list (inline)
and the grid.  Split out of the historical monolithic
``app/routes/accounts.py`` in Commit 21 of the financial-calculation
audit follow-up (F-1); behaviour preserved verbatim from the
pre-split file.

Both endpoint families route the actual mutation, history-row
append, conditional entries reconcile, and commit through
:func:`app.services.anchor_service.apply_anchor_true_up` so the
C-17 / F-009 optimistic-lock contract and the F-103 / C-22 same-day
same-balance idempotency rules cannot drift between the two
surfaces.  This module is therefore deliberately thin: it owns the
HTTP-shaped concerns (form validation, version_id pre-flush check,
HTMX-fragment rendering, HX-Trigger header composition) and
delegates the database mutation to the shared service.

The grid's ``true_up`` differs from ``inline_anchor_update`` in
three response-layer details (template name, ``account=`` vs
``acct=`` template kwarg, OOB-swap "as-of" snippet + HX-Trigger).
The outcomes returned by the service are otherwise byte-equivalent.
"""

import logging
from decimal import Decimal

from flask import jsonify, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.services import anchor_service, entry_service, pay_period_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.account_validation import _anchor_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import to_display_tz

logger = logging.getLogger(__name__)


# ── Inline Anchor Balance Edit (Accounts List) ────────────────────


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor", methods=["PATCH"])
@login_required
@require_owner
def inline_anchor_update(account_id):
    """HTMX endpoint: update anchor balance inline from the accounts list.

    Optimistic locking (commit C-17 / F-009): the form ships
    ``version_id`` as a hidden input set to the row's counter at
    render time.  A submitted value that no longer matches
    ``Account.version_id`` causes the handler to render the
    ``_anchor_cell.html`` partial in conflict mode and return 409
    Conflict, which HTMX swaps in place of the form so the user
    sees the latest balance and can retry.  The same partial is
    rendered when SQLAlchemy raises ``StaleDataError`` at flush
    time, so a concurrent in-flight commit produces an identical
    UX to a long-stale form.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    errors = _anchor_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    submitted_version = data.get("version_id")
    if submitted_version is not None and submitted_version != account.version_id:
        logger.info(
            "Stale-form conflict on inline_anchor_update id=%d "
            "(submitted=%d, current=%d)",
            account_id, submitted_version, account.version_id,
        )
        return (
            render_template(
                "accounts/_anchor_cell.html",
                acct=account, editing=False, conflict=True,
            ),
            409,
        )

    current_period = pay_period_service.get_current_period(current_user.id)

    # Apply the true-up through the canonical helper.  The two paths
    # converge on the same outcome enum so the response composition
    # at the bottom of the function is shared.  No-current-period is
    # the legacy degenerate branch: write the cache column, keep the
    # existing anchor period assignment, append NO history row (no
    # period to anchor to), and otherwise run the same conditional
    # entries reconcile + commit.  Post-Commit-3 every user with an
    # account has at least one pay period, and post-auto-generation
    # they have ~2 years of forward periods; this branch fires only
    # when the user has periods none of which contain today (e.g.
    # they generated only historical periods).
    if current_period is None:
        account.current_anchor_balance = new_balance
        checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
        try:
            if account.account_type_id == checking_type_id:
                entry_service.clear_entries_for_anchor_true_up(
                    current_user.id, account.id,
                )
            db.session.commit()
            outcome = AnchorTrueUpOutcome.COMMITTED
        except StaleDataError:
            db.session.rollback()
            logger.info(
                "Stale-data conflict on inline_anchor_update id=%d "
                "(no-current-period path)", account_id,
            )
            outcome = AnchorTrueUpOutcome.STALE_CONFLICT
    else:
        # Canonical anchor true-up path: route the mutation, history-
        # row append, conditional entries reconcile, and commit through
        # the single authoritative helper so the C-17 optimistic lock
        # and the F-103 / C-22 same-day same-balance idempotency rules
        # cannot drift between this endpoint and the grid ``true_up``
        # endpoint below.  See ``app/services/anchor_service.py`` for
        # the contract.
        outcome = anchor_service.apply_anchor_true_up(
            account=account,
            new_balance=new_balance,
            anchor_period=current_period,
            user_id=current_user.id,
        )

    if outcome is AnchorTrueUpOutcome.STALE_CONFLICT:
        # Re-fetch a fresh, post-conflict copy so the partial renders
        # the winner's balance, not the loser's rolled-back in-memory
        # value.
        account = db.session.get(Account, account_id)
        return (
            render_template(
                "accounts/_anchor_cell.html",
                acct=account, editing=False, conflict=True,
            ),
            409,
        )

    if outcome is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY:
        # F-103 idempotent success: the prior request committed the
        # same value this request was trying to submit.  Re-fetch and
        # render the (already-current) balance.
        account = db.session.get(Account, account_id)
        return render_template(
            "accounts/_anchor_cell.html", acct=account, editing=False,
        )

    # COMMITTED: refresh the in-memory account so the rendered partial
    # shows the row's post-commit state (notably ``updated_at`` which
    # the audit-trigger refreshes server-side).
    db.session.refresh(account)
    logger.info(
        "Inline anchor update: account %d set to $%s",
        account.id, new_balance,
    )
    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=False,
    )


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor-form", methods=["GET"])
@login_required
@require_owner
def inline_anchor_form(account_id):
    """HTMX partial: show inline anchor balance edit form on accounts list."""
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=True,
    )


@accounts_bp.route("/accounts/<int:account_id>/inline-anchor-display", methods=["GET"])
@login_required
@require_owner
def inline_anchor_display(account_id):
    """HTMX partial: show anchor balance display on accounts list."""
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    return render_template(
        "accounts/_anchor_cell.html", acct=account, editing=False,
    )


# ── Anchor Balance True-up (Grid) ─────────────────────────────────


# The non-default surfaces the shared anchor editor can be opened from.
# The opener names its surface via the ``revert`` query token; only these
# canonical values are honored (see :func:`_normalize_revert_context`).
# Each maps to a revert endpoint in :func:`_anchor_revert_url`.
_REVERT_SURFACES = frozenset({"dashboard", "accounts"})


def _normalize_revert_context(raw_revert: str | None) -> str | None:
    """Allowlist-validate the raw ``revert`` token to a canonical value.

    The anchor editor is opened from more than one surface, and the
    opener names its surface via the ``revert`` query token.  Two
    non-default surfaces are recognized -- ``dashboard`` (the dashboard
    hero balance card) and ``accounts`` (the Net Worth Cockpit's per-card
    balance cell); every other value (unset, unknown, an attacker's probe)
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
    dashboard balance card would strand the card on the grid display cell
    (the editor's retry would reopen with no ``revert``).  ``None`` (the
    grid default) keeps the conflict cell byte-for-byte unchanged.
    """
    return (
        render_template(
            "grid/_anchor_edit.html",
            account=account, editing=False, conflict=True,
            revert_context=revert_context,
        ),
        409,
    )


def _true_up_success_response(
    account: Account, revert_context: str | None,
) -> tuple[str, int, dict[str, str]]:
    """Compose the grid anchor true-up success response.

    Shared by ``true_up``'s COMMITTED and DUPLICATE_SAME_DAY outcomes
    (both render the updated display cell and fire ``balanceChanged`` so
    other surfaces recompute).  The single-account grid and dashboard
    surfaces append an out-of-band ``#anchor-as-of`` snippet dating the
    edit; the cockpit (``revert=accounts``) is a MULTI-card surface with no
    such singleton element and re-syncs the whole region via
    ``savings.cockpit_section`` on the trigger, so emitting the OOB there
    would orphan-target (htmx:oobErrorNoTarget) -- it is skipped.

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
    if revert_context == "accounts":
        return html, 200, {"HX-Trigger": "balanceChanged"}
    as_of_html = (
        f'<small class="text-muted" id="anchor-as-of" hx-swap-oob="true">'
        f'as of {to_display_tz(account.updated_at).strftime("%b %-d, %Y")}'
        f'</small>'
    )
    return html + as_of_html, 200, {"HX-Trigger": "balanceChanged"}


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@login_required
@require_owner
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.

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

    errors = _anchor_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    submitted_version = data.get("version_id")
    if submitted_version is not None and submitted_version != account.version_id:
        logger.info(
            "Stale-form conflict on true_up id=%d "
            "(submitted=%d, current=%d)",
            account_id, submitted_version, account.version_id,
        )
        return _anchor_conflict_response(account, revert_context)

    # Find the current pay period and set it as the anchor period.
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return "No current pay period found", 400

    # Canonical anchor true-up path: see ``inline_anchor_update`` and
    # ``app/services/anchor_service.py`` for the shared rationale.
    # ``true_up`` differs from the inline endpoint in three places:
    # (1) the template (``grid/_anchor_edit.html`` and the ``account=``
    # kwarg rather than ``acct=``), (2) the success response appends
    # an OOB "as-of" snippet, and (3) the success response carries an
    # ``HX-Trigger: balanceChanged`` header so other grid cells
    # recompute.  The outcomes are otherwise byte-equivalent.
    outcome = anchor_service.apply_anchor_true_up(
        account=account,
        new_balance=new_balance,
        anchor_period=current_period,
        user_id=current_user.id,
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
