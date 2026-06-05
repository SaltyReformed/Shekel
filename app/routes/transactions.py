"""
Shekel Budget App -- Transaction Routes

CRUD operations and status workflow for individual transactions.
Returns HTMX fragments for inline editing in the grid.
"""

import logging

from dataclasses import dataclass
from datetime import date

from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user, login_required
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.account import Account
from app.models.category import Category
from app.models.ref import Status
from app.schemas.validation import (
    MarkDoneSchema,
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import (
    credit_workflow,
    carry_forward_service,
    grid_view_service,
    pay_period_service,
    transaction_service,
    transfer_service,
)
from app.services.entry_service import (
    build_entry_lists_dict,
    build_entry_sums_dict,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.state_machine import verify_transition
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import get_accessible_transaction, require_owner
from app.utils.db_errors import is_unique_violation

# Name of the partial unique index that backstops commit C-19's
# duplicate CC Payback fix.  Mirrors the literal in
# ``migrations/versions/b3d8f4a01c92_*.py`` and
# ``app.models.transaction.Transaction.__table_args__``; renaming
# the index requires a coordinated edit across all three sites.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

logger = logging.getLogger(__name__)

transactions_bp = Blueprint("transactions", __name__)

# Marshmallow schema instances.
_update_schema = TransactionUpdateSchema()
_create_schema = TransactionCreateSchema()
_inline_create_schema = InlineTransactionCreateSchema()

# Schema for the optional ``actual_amount`` form field on
# ``mark_done``.  Single instance per process (Marshmallow contract);
# replaces the per-branch raw ``Decimal(request.form.get("actual_amount"))``
# parse the route used before commit C-27 / F-042 / F-162 of the
# 2026-04-15 security remediation plan.
_mark_done_schema = MarkDoneSchema()


@dataclass(frozen=True)
class _RenderTarget:
    """The response surface a mark_done request renders into.

    Bundles the three render-routing fields the mobile / companion card
    action bar posts (``render=mobile_card`` plus the per-tab
    ``card_prefix`` and the ``can_edit`` flag) so :func:`mark_done` and
    its helpers thread one value instead of three parallel arguments.
    The desktop grid and full-edit popover omit these, so the default
    (empty ``render_mode``) resolves to the cell + ``gridRefresh`` path.
    """

    render_mode: str
    card_prefix: str
    can_edit: bool


def _render_cell(txn, **extra):
    """Render the transaction cell template with entry_sums context.

    Wraps render_template so every HTMX cell response includes the
    entry_sums dict needed for the progress indicator on tracked
    transactions.

    Args:
        txn: The Transaction object to render.
        **extra: Additional keyword arguments forwarded to render_template
            (e.g. wrap_div=True, conflict=True).

    Returns:
        Rendered HTML string.
    """
    return render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        entry_sums=build_entry_sums_dict([txn]),
        **extra,
    )


def _render_mobile_card(txn, *, card_prefix, can_edit):
    """Render a single mobile transaction card for an HTMX swap.

    The mobile / companion Mark Paid form targets the card wrapper
    (``hx-target="#card-<prefix-><id>"``, ``hx-swap="outerHTML"``), so
    this returns exactly that one card re-rendered in its post-action
    state -- the settled badge shows and the Mark Paid button drops --
    without the full-page reload the desktop ``gridRefresh`` path uses.

    Reuses the canonical producers so the swapped-in card is identical
    to the page-load card: :func:`grid_view_service.build_row_keys` for
    the row label, :func:`build_entry_sums_dict` for the progress
    aggregate, and :func:`build_entry_lists_dict` for the inline
    envelope entries.  Ownership scoping uses ``txn.pay_period.user_id``
    (the data owner) so the companion path resolves the linked owner's
    categories, not the companion's own (empty) set.

    Args:
        txn: The Transaction just settled, with ``entries`` and
            ``template`` accessible.
        card_prefix: The per-tab id namespace the requesting card used
            (``"tp"`` for This Period and the companion view; ``""`` for
            prefix-less direct renders).  Drives the wrapper id so the
            outerHTML swap resolves.
        can_edit: ``True`` for the owner card, ``False`` for companion.

    Returns:
        Rendered HTML string for one ``grid/_mobile_card_single.html``.
    """
    owner_id = txn.pay_period.user_id
    categories = (
        db.session.query(Category)
        .filter_by(user_id=owner_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    row_keys = grid_view_service.build_row_keys(
        [txn], categories, is_income_section=txn.is_income,
    )
    # A just-settled transaction is neither cancelled nor deleted, so
    # build_row_keys always yields its row; guard defensively so an
    # unexpected empty result degrades to the desktop cell rather than
    # raising IndexError.
    if not row_keys:
        return _render_cell(txn)
    return render_template(
        "grid/_mobile_card_single.html",
        rk=row_keys[0],
        period=txn.pay_period,
        txn=txn,
        entry_sums=build_entry_sums_dict([txn]),
        entry_lists=build_entry_lists_dict([txn]),
        can_edit=can_edit,
        id_prefix=card_prefix,
        today=date.today(),
    )


def _mark_done_success_response(txn, render_mode, card_prefix, can_edit):
    """Build the success response tuple for a mark_done request.

    Forks on the rendering surface the request came from:

      * ``render_mode == "mobile_card"``: return the single re-rendered
        mobile card + ``HX-Trigger: mobileCardSettled``.  The card swaps
        in place (no reload); the owner This Period summary blocks
        listen for ``mobileCardSettled`` and self-refresh, while the
        companion page has no summary blocks so only the card updates.
      * otherwise (desktop grid / full-edit popover): the desktop cell +
        ``HX-Trigger: gridRefresh`` -- the existing reload-driven path,
        unchanged.

    Args:
        txn: The settled Transaction.
        render_mode: The ``render`` form field (``"mobile_card"`` or
            absent/empty).
        card_prefix: The ``card_prefix`` form field (per-tab id
            namespace) -- only meaningful for the mobile_card path.
        can_edit: The ``can_edit`` form field as a bool -- only
            meaningful for the mobile_card path.

    Returns:
        A Flask ``(html, status, headers)`` response tuple.
    """
    if render_mode == "mobile_card":
        return (
            _render_mobile_card(txn, card_prefix=card_prefix, can_edit=can_edit),
            200,
            {"HX-Trigger": "mobileCardSettled"},
        )
    return _render_cell(txn), 200, {"HX-Trigger": "gridRefresh"}


def _credit_payback_idempotent_response(exc, txn_id):
    """Translate a credit-payback unique-index violation into a 200.

    Backstop for commit C-19 (audit finding F-008): if a future
    caller bypasses ``credit_workflow.mark_as_credit``'s row lock
    and a duplicate payback INSERT reaches PostgreSQL,
    ``uq_transactions_credit_payback_unique`` rejects it and this
    helper rolls back, re-fetches the source row, and renders the
    cell at HTTP 200 -- matching what a serialised request would
    have produced.  Other ``IntegrityError`` constraint hits return
    the standard 400 so unrelated FK / check failures stay visible.
    """
    db.session.rollback()
    if not is_unique_violation(exc, _CREDIT_PAYBACK_UNIQUE_INDEX):
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "Duplicate CC payback prevented on mark_credit id=%d "
        "(idempotent success)", txn_id,
    )
    refreshed = _get_owned_transaction(txn_id)
    if refreshed is None:
        return "Not found", 404
    return (
        _render_cell(refreshed),
        200,
        {"HX-Trigger": "gridRefresh"},
    )


def _stale_transaction_response(
    txn_id, render_mode="", card_prefix="", can_edit=False,
):
    """Roll back the session and render the cell in conflict mode + 409.

    Used by every PATCH/POST/DELETE handler that can race a
    concurrent commit against the version-pinned UPDATE.  Re-fetches
    the transaction from the database so the user sees the winner's
    state -- never the loser's stale in-memory copy -- and tags the
    cell with ``conflict=True`` so the template surfaces a warning
    indicator.  Returns a 404 if the row was hard-deleted by the
    winning request.

    The mobile/companion Mark Paid path passes ``render_mode=
    "mobile_card"`` so the 409 body is the re-rendered mobile card
    (latest state) rather than the desktop cell; the card's
    ``hx-target`` is the card wrapper, so a desktop-cell body would not
    swap.  That path re-fetches through
    :func:`get_accessible_transaction` so a companion's
    conflict resolves against the linked owner's row (the desktop path
    uses :func:`_get_owned_transaction`, which is owner-only).

    Args:
        txn_id: Primary key of the transaction the route was trying
            to mutate.  Used to re-fetch under ownership checks so
            the conflict UI renders the correct row.
        render_mode: ``"mobile_card"`` to return a mobile card body;
            anything else returns the desktop cell.
        card_prefix: Per-tab id namespace for the mobile card wrapper
            id (only used when ``render_mode == "mobile_card"``).
        can_edit: Owner-vs-companion flag forwarded to the mobile card
            render (only used for the mobile_card path).

    Returns:
        Flask response tuple ``(html, 409)`` or ``("Not found", 404)``
        when the row vanished entirely.
    """
    db.session.rollback()
    db.session.expire_all()
    if render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            return "Not found", 404
        return (
            _render_mobile_card(txn, card_prefix=card_prefix, can_edit=can_edit),
            409,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return _render_cell(txn, conflict=True), 409


def _get_owned_transaction(txn_id):
    """Fetch a transaction and verify it belongs to the current user.

    Ownership is determined via the pay_period's user_id since
    transactions don't have a direct user_id column.

    Returns:
        Transaction if found and owned by current_user, else None.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.pay_period.user_id != current_user.id:
        return None
    return txn


def _resolve_owned_fks(specs):
    """Fetch and ownership-check a sequence of user-scoped FK ids.

    Centralizes the IDOR probe shared by the transaction create routes
    (:func:`create_inline`, :func:`create_transaction`) and the grid
    form-partial routes (:func:`get_quick_create`,
    :func:`get_full_create`, :func:`get_empty_cell`).  For each
    ``(model, obj_id, not_found_msg)`` spec the row is fetched by
    primary key and confirmed to belong to ``current_user``; a missing
    row and a cross-user row return the identical 404 so an attacker
    cannot distinguish the two (security response rule: "404 for both
    not found and not yours").  Specs are checked in order and the
    first failure short-circuits, so callers list them so the most
    specific message surfaces first -- mirroring the sequential per-FK
    checks these routes used before the extraction.

    A ``None`` ``obj_id`` resolves to the 404 without issuing a
    NULL-primary-key query (which SQLAlchemy warns cannot load a row);
    the create routes never pass ``None`` (schema-required fields), but
    the form-partial routes read ids straight off the query string.

    Args:
        specs: ordered ``(model, obj_id, not_found_msg)`` tuples.

    Returns:
        ``(resolved, None)`` on success, where *resolved* maps each
        spec's model class to its fetched row, or ``(None, (msg, 404))``
        on the first ownership failure -- a Flask response tuple the
        caller returns directly to HTMX.
    """
    resolved = {}
    for model, obj_id, not_found_msg in specs:
        obj = db.session.get(model, obj_id) if obj_id is not None else None
        if obj is None or obj.user_id != current_user.id:
            return None, (not_found_msg, 404)
        resolved[model] = obj
    return resolved, None


def _verify_owned_fks_in_update(data):
    """Verify cross-user FK ownership for the PATCH update payload.

    Used by :func:`update_transaction` to reject ``pay_period_id``
    and ``category_id`` values that belong to another user before
    any state-changing work runs.  Without this probe an
    authenticated owner could submit a victim's ``pay_period_id``
    or ``category_id`` and the unfiltered ``setattr`` loop in
    :func:`update_transaction` would silently re-parent the
    transaction into the victim's namespace -- the FK constraint
    passes because the row exists, just under another user, and
    PostgreSQL never raises ``IntegrityError``.

    Audit reference: F-029 / commit C-29 of the 2026-04-15
    security remediation plan.  Mirrors the route-boundary FK
    probes already in :func:`create_inline` and
    :func:`create_transaction`; ``status_id`` is a reference table
    FK (not user-scoped) and so does not need an ownership check.
    The 404 strings deliberately match the messages used by the
    create routes so the client cannot tell whether the row does
    not exist or belongs to another user (security response rule:
    "404 for both not found and not yours").

    Args:
        data: The schema-loaded PATCH payload.  ``pay_period_id``
            and ``category_id`` are the only user-scoped FK keys
            inspected; absent keys are skipped.

    Returns:
        ``None`` on success.  On failure, a Flask response tuple
        ``(body, 404)`` the caller returns directly to HTMX.
    """
    specs = []
    if "pay_period_id" in data:
        specs.append((PayPeriod, data["pay_period_id"], "Pay period not found"))
    if "category_id" in data:
        specs.append((Category, data["category_id"], "Category not found"))
    _, error = _resolve_owned_fks(specs)
    return error


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@login_required
@require_owner
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return _render_cell(txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@login_required
@require_owner
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_quick_edit.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@login_required
@require_owner
def get_full_edit(txn_id):
    """HTMX partial: return the full edit popover form.

    For shadow transactions (transfer_id IS NOT NULL), returns the
    transfer edit form instead of the transaction edit form so the
    user edits the parent transfer and both shadows stay in sync.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection: return transfer edit form for shadows ---
    if txn.transfer_id is not None:
        xfer = db.session.get(Transfer, txn.transfer_id)
        if xfer is None:
            return "Not found", 404
        statuses = db.session.query(Status).all()
        categories = (
            db.session.query(Category)
            .filter_by(user_id=current_user.id)
            .order_by(Category.group_name, Category.item_name)
            .all()
        )
        # Current + future periods (plus the transfer's own) power the
        # period-move selector when a transfer is edited from a grid
        # shadow cell -- same set the transfers blueprint supplies.
        periods = pay_period_service.get_current_and_future_periods(
            current_user.id, include_period_id=xfer.pay_period_id,
        )
        return render_template(
            "transfers/_transfer_full_edit.html",
            xfer=xfer,
            statuses=statuses,
            categories=categories,
            source_txn_id=txn.id,
            periods=periods,
        )

    statuses = db.session.query(Status).all()
    # Pay periods power the in-popover period-move selector.  Only the
    # current and future periods are offered -- moving an expense into an
    # already-closed period is not a supported workflow -- but the row's
    # own period is always included so a transaction that currently sits
    # in a past period stays selected (and is not silently re-pointed at
    # the first current period on save).  Periods are per-user; the PATCH
    # handler re-checks ownership of the submitted id (F-029).
    periods = pay_period_service.get_current_and_future_periods(
        current_user.id, include_period_id=txn.pay_period_id,
    )
    return render_template(
        "grid/_transaction_full_edit.html",
        txn=txn,
        statuses=statuses,
        periods=periods,
    )


def _apply_shadow_update(txn, txn_id, data):
    """Apply a PATCH update to a transfer shadow via the transfer service.

    Shadow transactions (``transfer_id IS NOT NULL``) cannot be mutated
    directly -- the parent transfer and both shadows must move together
    (design doc invariants 3-5).  Maps the submitted transaction fields
    onto :func:`transfer_service.update_transfer` kwargs, commits, and
    renders the refreshed cell.  Reverting to a non-settled status nulls
    ``paid_at`` so a re-opened shadow stops showing a paid timestamp.

    Args:
        txn: The shadow Transaction being edited.
        txn_id: The shadow's id, used for stale-conflict logging and the
            conflict re-fetch.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``balanceChanged`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the transfer service rejects the change.
    """
    # Map transaction field names to transfer service kwargs.
    svc_kwargs = {}
    if "estimated_amount" in data:
        svc_kwargs["amount"] = data["estimated_amount"]
    if "actual_amount" in data:
        svc_kwargs["actual_amount"] = data["actual_amount"]
    if "status_id" in data:
        svc_kwargs["status_id"] = data["status_id"]
        # Null paid_at when reverting to a non-settled status.
        new_status = db.session.get(Status, data["status_id"])
        if new_status and not new_status.is_settled:
            svc_kwargs["paid_at"] = None
    if "notes" in data:
        svc_kwargs["notes"] = data["notes"]
    if "category_id" in data:
        svc_kwargs["category_id"] = data["category_id"]
    if "due_date" in data:
        svc_kwargs["due_date"] = data["due_date"]

    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction shadow id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except (NotFoundError, ValidationError) as exc:
        return str(exc), 400

    db.session.refresh(txn)
    logger.info(
        "user_id=%d updated shadow transaction %d (transfer %d)",
        current_user.id, txn_id, txn.transfer_id,
    )
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


def _resolve_status_change(txn, data):
    """Validate a status transition and decide whether to clear paid_at.

    Runs the status-dependent guards for a regular (non-shadow)
    :func:`update_transaction` before any column is mutated: verifies
    the requested transition through the state machine (F-161 / C-21),
    blocks the Credit status on purchase-tracking transactions (credit
    is per-entry, scope doc 5.2), and reports whether reverting to a
    non-settled status should null ``paid_at``.  Looking the status up
    here -- before the ``setattr`` loop dirties the session -- avoids an
    autoflush firing an FK violation mid-validation.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        ``(revert_paid_at, None)`` when the change is allowed --
        *revert_paid_at* is ``True`` when ``paid_at`` must be cleared --
        or ``(False, (msg, 400))`` when a guard rejects the request, a
        Flask response tuple the caller returns directly.
    """
    if "status_id" not in data:
        return False, None

    # Verify the transition BEFORE any other status-dependent work
    # (envelope guard, paid_at revert).  An illegal transition -- for
    # example settled -> projected -- short-circuits the request with a
    # 400 and leaves the row untouched.  Audit reference: F-161 /
    # commit C-21 of the 2026-04-15 security remediation plan.
    try:
        verify_transition(
            txn.status_id, data["status_id"], context="transaction",
        )
    except ValidationError as exc:
        return False, (str(exc), 400)

    # Block Credit status on entry-capable transactions -- credit
    # handling is per-entry, not per-transaction (scope doc section 5.2).
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if data["status_id"] == credit_id and txn.tracks_purchases:
        return False, (
            "Cannot set Credit status on transactions with individual "
            "purchase tracking. Use entry-level credit instead.",
            400,
        )

    new_status = db.session.get(Status, data["status_id"])
    revert_paid_at = bool(
        new_status and not new_status.is_settled and txn.paid_at is not None
    )
    return revert_paid_at, None


def _apply_regular_update(txn, txn_id, data):
    """Apply a PATCH update to a regular (non-shadow) transaction.

    Validates any status change (:func:`_resolve_status_change`),
    enforces the expense-only purchase-tracking guard, writes the
    submitted fields, flags template-generated rows as overridden when
    the amount or period changed, and commits under the optimistic
    lock.  A ``pay_period_id`` change relocates the row across the grid,
    so it triggers a full ``gridRefresh`` instead of the in-place
    ``balanceChanged`` swap.

    Args:
        txn: The Transaction being edited.
        txn_id: The transaction's id, used for stale-conflict logging.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``gridRefresh`` (on a
        period move) or ``balanceChanged`` on success, a 409 conflict
        cell on a concurrent commit, or a 400 on a rejected status
        change, the income purchase-tracking guard, or a bad FK.
    """
    revert_paid_at, status_error = _resolve_status_change(txn, data)
    if status_error is not None:
        return status_error

    # Purchase tracking is expense-only.  The popover only renders the
    # is_envelope checkbox for ad-hoc expense rows, but guard the route
    # too (defense in depth) so a crafted request cannot enable tracking
    # on an income transaction.  Checked against the stored type because
    # TransactionUpdateSchema carries no transaction_type_id.
    if data.get("is_envelope") and txn.is_income:
        return "Purchase tracking is only available for expenses.", 400

    # Detect a period move before the setattr loop mutates the row.  A
    # move relocates the row to a different period in the grid, which an
    # in-place cell swap (hx-target="#txn-cell-<id>") cannot express --
    # the cell would re-render in its old position.  When the period
    # actually changes the response triggers a full grid refresh (see
    # the ``HX-Trigger`` selection at the end of the handler), matching
    # the ``gridRefresh`` pattern carry-forward uses for cross-period
    # moves.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != txn.pay_period_id
    )

    # Apply updates (regular transactions only).
    for field, value in data.items():
        setattr(txn, field, value)

    if revert_paid_at:
        txn.paid_at = None

    # If the user changed amount or period on a template-generated item,
    # flag as override.
    if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
        txn.is_override = True

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

    # A period move needs a full grid refresh so the row appears under
    # its new period; an in-place edit only needs the balance rows
    # recomputed.  ``gridRefresh`` reloads the page (app.js); the
    # returned cell still swaps first, which is harmless before reload.
    response = _render_cell(txn)
    return response, 200, {
        "HX-Trigger": "gridRefresh" if period_changed else "balanceChanged",
    }


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@login_required
@require_owner
def update_transaction(txn_id):
    """Update a transaction's fields (inline edit save).

    Shadow transactions (transfer_id IS NOT NULL) are routed through
    the transfer service so both shadows and the parent transfer stay
    in sync (design doc invariants 3-5).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.

    Optimistic locking (commit C-18 / F-010) operates in two layers:

      1. Stale-form check: the cell ships ``version_id`` as a hidden
         input set to ``Transaction.version_id`` at render time.
         When the submitted value differs from the row's current
         counter, the handler short-circuits with a 409 + conflict
         cell partial and records nothing.  This catches the
         sequential Tab-1/Tab-2 race documented in C-17.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same 409 + conflict cell.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.

    Route-boundary FK ownership (commit C-29 / F-029 of the
    2026-04-15 security remediation plan): when the schema accepts
    a user-scoped FK -- ``pay_period_id`` or ``category_id`` -- the
    submitted id is verified against ``current_user.id`` here,
    before any state-changing work runs.  Without this probe an
    authenticated owner could submit another user's
    ``pay_period_id`` or ``category_id`` and the unfiltered
    ``setattr`` loop would silently re-parent the transaction into
    the victim's namespace (the FK row exists, the FK constraint
    passes, and PostgreSQL never raises ``IntegrityError``).
    ``status_id`` is a reference table FK (not user-scoped) and so
    does not need an ownership check.  The probe runs before the
    transfer-shadow branch so a malicious request that targets a
    transfer shadow with a cross-user FK is rejected even though
    the transfer-shadow path drops ``pay_period_id`` silently --
    matching the layered defense ``transfers.update_transfer``
    received in commit C-27.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Parse and validate input.
    errors = _update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _update_schema.load(request.form)

    # Route-boundary FK ownership (commit C-29 / F-029).  Reject
    # cross-user ``pay_period_id`` / ``category_id`` before the
    # stale-form check or the transfer-shadow branch so the
    # security response (404) takes precedence over the UX
    # response (409 conflict cell) when the same request triggers
    # both.  See :func:`_verify_owned_fks_in_update` for the
    # threat-model details.
    fk_error = _verify_owned_fks_in_update(data)
    if fk_error is not None:
        return fk_error

    # Stale-form check.  Performed before any mutation so audit-log
    # triggers record only successful edits.  Conditional on the
    # form having submitted a version (clients that omit it fall
    # through to the SQLAlchemy-tier check at flush time).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != txn.version_id:
        logger.info(
            "Stale-form conflict on update_transaction id=%d "
            "(submitted=%d, current=%d)",
            txn_id, submitted_version, txn.version_id,
        )
        return _render_cell(txn, conflict=True), 409

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _apply_shadow_update(txn, txn_id, data)
    # --- End guard ---

    return _apply_regular_update(txn, txn_id, data)


def _mark_done_shadow(txn, txn_id, actual_amount, target):
    """Settle a transfer shadow through the transfer service.

    Marks both shadows and the parent transfer done atomically (the
    transfer service owns the shadow invariants).  Uses the DONE status
    for the service -- the 'done'/'received' split is a display
    convention for regular transactions only -- and forwards an optional
    manual ``actual_amount``.

    Args:
        txn: The shadow Transaction being settled.
        txn_id: The shadow's id, for stale-conflict logging / re-fetch.
        actual_amount: Optional manual actual amount from the form, or
            ``None`` to leave it to the service.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict surface on a concurrent commit, or a 400
        on a bad FK or a state-machine rejection.
    """
    # Use 'done' for the transfer service -- it sets the same status on
    # both shadows.  The 'done'/'received' distinction is a display
    # convention for regular transactions.
    svc_kwargs = {
        "status_id": ref_cache.status_id(StatusEnum.DONE),
        "paid_at": db.func.now(),
    }

    if actual_amount is not None:
        svc_kwargs["actual_amount"] = actual_amount

    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done shadow id=%d", txn_id,
        )
        return _stale_transaction_response(
            txn_id, target.render_mode, target.card_prefix, target.can_edit,
        )
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    except ValidationError as exc:
        # transfer_service.update_transfer runs the transition through
        # the state machine (commit C-21).  A mark-done request against
        # a Cancelled or Settled transfer shadow surfaces here as 400
        # instead of crashing the request.
        db.session.rollback()
        return str(exc), 400
    db.session.refresh(txn)
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


def _mark_done_regular(txn, txn_id, status_id, actual_amount, target):
    """Settle a regular (non-shadow) transaction.

    Envelope-tracked rows with entries settle at the entry sum via
    :func:`transaction_service.settle_from_entries` (the single source
    of truth shared with carry-forward); all others take the manual flow
    -- a state-machine-guarded status flip to *status_id* plus an
    optional manual ``actual_amount`` -- then commit under the
    optimistic lock.

    Args:
        txn: The Transaction being settled.
        txn_id: The transaction's id, for stale-conflict logging.
        status_id: The settled status id ('received' for income, 'done'
            for expenses) used by the manual branch.
        actual_amount: Optional manual actual amount from the form, or
            ``None`` to leave ``actual_amount`` untouched.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the success surface on commit, a 409
        conflict surface on a concurrent commit, or a 400 on a bad FK or
        a rejected transition.
    """
    # Auto-populate actual from entries for envelope-tracked transactions
    # with at least one entry.  Entry sum takes precedence over any manual
    # actual_amount from the form (scope doc section 4.2).  When no entries
    # exist (or the template is not envelope-tracked), fall through to the
    # manual flow so non-tracked and empty-tracked transactions behave
    # identically to pre-entry behavior -- the form's optional
    # ``actual_amount`` is honoured and a missing value leaves
    # ``txn.actual_amount`` untouched.
    #
    # The envelope-with-entries branch routes through
    # ``transaction_service.settle_from_entries`` so the manual mark-done
    # path and the carry-forward envelope branch (Phase 4) share a single
    # source of truth for "settle a tracked row at sum(entries)."  The
    # helper writes ``status_id``, ``paid_at``, and ``actual_amount``
    # together; the route does not need to set them itself in this branch.
    if txn.tracks_purchases and txn.entries:
        try:
            transaction_service.settle_from_entries(txn)
        except ValidationError as exc:
            return str(exc), 400
    else:
        # State-machine guard: only Projected (or the identity edge from
        # Paid/Received) can transition into Paid/Received via mark_done.
        # The envelope branch above already enforces the same rule via
        # ``settle_from_entries``'s stricter ``is_immutable`` precondition,
        # so the guard sits on the direct branch where the gap was.
        # Audit reference: F-047 / F-161 follow-up to commit C-21.
        try:
            verify_transition(txn.status_id, status_id, context="transaction")
        except ValidationError as exc:
            return str(exc), 400
        txn.status_id = status_id
        txn.paid_at = db.func.now()
        # Accept an optional manual actual amount from the form.
        if actual_amount is not None:
            txn.actual_amount = actual_amount

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done id=%d", txn_id,
        )
        return _stale_transaction_response(
            txn_id, target.render_mode, target.card_prefix, target.can_edit,
        )
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d marked transaction %d status_id=%d", current_user.id, txn_id, status_id)

    return _mark_done_success_response(
        txn, target.render_mode, target.card_prefix, target.can_edit,
    )


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@login_required
def mark_done(txn_id):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Shadow transactions route through the transfer service so both
    shadows and the parent transfer are updated atomically.

    Automatically picks the correct status based on transaction type.
    For entry-capable transactions with entries, auto-computes
    actual_amount from the entry sum.  For all others, accepts an
    optional actual_amount from the form -- parsed via
    :class:`MarkDoneSchema` so a malformed numeric value returns a
    clean 400 with the Marshmallow per-field message instead of the
    legacy ``"Invalid actual amount"`` translation, and a negative
    value is rejected at the schema tier (commit C-27 / F-042 /
    F-162 of the 2026-04-15 security remediation plan).

    Optimistic locking (commit C-18 / F-010): the button-click path
    has no form-side ``version_id`` to compare, so the optimistic
    lock relies on SQLAlchemy's ``version_id_col`` race detection
    at flush time.  ``StaleDataError`` is converted to a 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Validate the optional ``actual_amount`` form field once,
    # before branching on transfer detection, so both code paths
    # apply identical validation.  ``MarkDoneSchema`` strips empty
    # strings via its pre_load hook so the missing-field UX (a
    # button click with no body) yields ``actual_amount`` absent
    # from the loaded dict; that branches into "leave the column
    # untouched" below, matching the legacy behaviour.
    try:
        mark_done_data = _mark_done_schema.load(request.form)
    except MarshmallowValidationError as exc:
        return jsonify(errors=exc.messages), 400
    actual_amount = mark_done_data.get("actual_amount")

    # Rendering surface for the response.  The mobile / companion card
    # action bar posts ``render=mobile_card`` plus the per-tab
    # ``card_prefix`` and the ``can_edit`` flag so the response is a
    # single re-rendered card (in-place swap, no reload); the desktop
    # grid and full-edit popover omit these, so the response defaults
    # to the cell + gridRefresh reload.  Read off ``request.form``
    # directly -- these are render-routing fields, not part of the
    # money-only ``MarkDoneSchema``.
    render_mode = request.form.get("render", "")
    card_prefix = request.form.get("card_prefix", "")
    card_can_edit = request.form.get("can_edit") == "1"
    target = _RenderTarget(render_mode, card_prefix, card_can_edit)

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        status_id = ref_cache.status_id(StatusEnum.DONE)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _mark_done_shadow(txn, txn_id, actual_amount, target)
    # --- End guard ---

    return _mark_done_regular(txn, txn_id, status_id, actual_amount, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@login_required
@require_owner
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Optimistic locking (commit C-18 / F-010):
    ``StaleDataError`` -> 409 conflict cell.

    TOCTOU duplicate-payback prevention (commit C-19 / F-008):
    ``credit_workflow.mark_as_credit`` acquires
    ``SELECT ... FOR NO KEY UPDATE`` on the source row to serialise
    concurrent requests; the partial unique index
    ``uq_transactions_credit_payback_unique`` backstops any future
    caller that bypasses the lock, and the IntegrityError catch
    below converts the violation into idempotent success.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot mark a transfer shadow as credit.", 400

    try:
        credit_workflow.mark_as_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(exc, txn_id)
    except (NotFoundError, ValidationError) as exc:
        return str(exc), 400
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@login_required
@require_owner
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot unmark credit on a transfer shadow.", 400

    try:
        credit_workflow.unmark_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on unmark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Raised when the bespoke source-state guard or the
        # state-machine verification in
        # ``credit_workflow.unmark_credit`` rejects the request --
        # e.g. attempting to unmark a Paid row.  The body names the
        # offending status so the user understands why.
        db.session.rollback()
        return str(exc), 400
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


def _cancel_shadow(txn, txn_id, cancelled_id):
    """Cancel a transfer shadow through the transfer service.

    Cancels the parent transfer and both shadows atomically (the
    transfer service owns the shadow invariants); the route never
    mutates a shadow directly.

    Args:
        txn: The shadow Transaction being cancelled.
        txn_id: The shadow's id, for stale-conflict logging.
        cancelled_id: The Cancelled status id.

    Returns:
        A Flask response tuple: the refreshed cell + ``gridRefresh`` on
        success, a 409 conflict cell on a concurrent commit, or a 400
        when the state machine rejects cancelling a settled transfer.
    """
    try:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            status_id=cancelled_id,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction shadow id=%d",
            txn_id,
        )
        return _stale_transaction_response(txn_id)
    except ValidationError as exc:
        # transfer_service runs the transition through the state
        # machine.  An attempt to cancel a Paid/Received/Settled
        # transfer surfaces here as 400 instead of crashing the request
        # -- the transfer-service path was already wired by commit C-21;
        # this except clause is the route's corresponding translation.
        db.session.rollback()
        return str(exc), 400
    db.session.refresh(txn)
    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@login_required
@require_owner
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'.

    Shadow transactions route through the transfer service to cancel
    the parent transfer and both shadows atomically.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _cancel_shadow(txn, txn_id, cancelled_id)
    # --- End guard ---

    # State-machine guard: Cancelled is reachable only from Projected
    # (or the Cancelled identity edge for idempotent re-submits).  A
    # direct done -> cancelled or settled -> cancelled would erase the
    # paid/archived audit trail and is rejected with 400.  Audit
    # reference: F-047 / F-161 follow-up to commit C-21.
    try:
        verify_transition(txn.status_id, cancelled_id, context="transaction")
    except ValidationError as exc:
        return str(exc), 400

    txn.status_id = cancelled_id

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d cancelled transaction %d", current_user.id, txn_id)

    response = _render_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
@require_owner
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: prevent IDOR -- return identical 404 for "does
    # not exist" and "belongs to another user" so attackers cannot
    # distinguish the two cases.  See audit finding H1.
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    # Look up the baseline scenario for hidden fields.
    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    return render_template(
        "grid/_transaction_quick_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
        txn_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@login_required
@require_owner
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    statuses = db.session.query(Status).all()

    return render_template(
        "grid/_transaction_full_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
        statuses=statuses,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@login_required
@require_owner
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    account = objs[Account]

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=category,
        period=period,
        account=account,
        txn_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/inline", methods=["POST"])
@login_required
@require_owner
def create_inline():
    """Create a transaction from inline grid interaction.

    Auto-derives the name from the category.  Returns the new
    transaction cell wrapped in a div with a unique ID for HTMX
    targeting.

    Double-submit handling (F-102 / C-22): unlike the ad-hoc
    transfer create path (F-050), no database-level uniqueness
    constraint is enforced here.  Two transactions with identical
    (account_id, category_id, amount, pay_period_id) are a
    legitimate use case -- two $4 coffees on the same day, two
    identical fast-food charges, the user genuinely buying the
    same thing twice -- and rejecting them at the database layer
    would force the user to artificially differentiate amounts
    that match real-world receipts.  The mitigation is the
    client-side ``hx-disabled-elt`` HTMX directive on every
    transaction-create form (``_transaction_quick_create.html``,
    ``_transaction_full_create.html``,
    ``grid.html#addTransactionModal``): the submit control is
    disabled while the request is in flight, preventing accidental
    re-submits from a double-click or network retry.  The residual
    risk -- a user clicks rapidly enough to bypass the disable
    state, or replays the request via the back button -- is
    accepted as operator UX rather than a financial-correctness
    concern.
    """
    errors = _inline_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _inline_create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write.  Order matches the historical per-FK checks so the first
    # invalid id returns the same 404 body as before; the resolved
    # Category drives the derived transaction name below.
    objs, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (Category, data["category_id"], "Category not found"),
        (PayPeriod, data["pay_period_id"], "Pay period not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    # Set the name from the category display name.
    data["name"] = category.display_name

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created inline transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    # Return the cell wrapped in a div with a unique ID, matching
    # the pattern used in grid.html for existing transactions.
    response = _render_cell(txn, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions", methods=["POST"])
@login_required
@require_owner
def create_transaction():
    """Create an ad-hoc transaction (not from a template)."""
    errors = _create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write (same IDOR probe as create_inline; this route carries no
    # category).  None of the resolved rows are needed afterward.
    _, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (PayPeriod, data["pay_period_id"], "Pay period not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created ad-hoc transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    response = _render_cell(txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
@require_owner
def delete_transaction(txn_id):
    """Soft-delete a transaction (or hard-delete if it's ad-hoc).

    Shadow transactions cannot be directly deleted -- the user must
    delete the parent transfer instead.

    Optimistic locking (commit C-18 / F-010): both the soft-delete
    UPDATE and the hard-delete DELETE are version-pinned by
    SQLAlchemy.  A concurrent commit that bumps the row's version
    raises ``StaleDataError`` which the handler converts to a 409 +
    conflict cell so the user can retry against fresh state.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: block direct shadow deletion ---
    if txn.transfer_id is not None:
        return "Cannot delete a transfer shadow directly. Delete the parent transfer instead.", 400

    if txn.template_id:
        # Template-linked: soft-delete so the recurrence engine knows.
        txn.is_deleted = True
    else:
        # Ad-hoc: hard delete.
        db.session.delete(txn)

    try:
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d deleted transaction %d", current_user.id, txn_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


def _resolve_carry_forward_context(period_id):
    """Resolve source period, current period, and baseline scenario.

    Shared by both ``carry_forward`` (POST mutator) and
    ``carry_forward_preview`` (GET preview) so they apply identical
    ownership and configuration checks.

    Each return is a ``(payload, status, headers)`` tuple where
    *payload* is None when the lookups succeed.  Caller pattern:

        ctx, err = _resolve_carry_forward_context(period_id)
        if err is not None:
            return err
        source_period, current_period, scenario = ctx

    Returns:
        Tuple of ``((source_period, current_period, scenario), None)``
        on success, or ``(None, error_response)`` on failure.  The
        error response is a Flask-compatible ``(body, status_code)``
        tuple that the caller returns directly to HTMX.
    """
    source_period = db.session.get(PayPeriod, period_id)
    if source_period is None or source_period.user_id != current_user.id:
        return None, ("Not found", 404)

    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return None, ("No current period found", 400)

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return None, ("No baseline scenario", 400)

    return (source_period, current_period, scenario), None


@transactions_bp.route(
    "/pay-periods/<int:period_id>/carry-forward-preview", methods=["GET"],
)
@login_required
@require_owner
def carry_forward_preview(period_id: int):
    """HTMX partial: return the carry-forward preview modal.

    Mirrors the POST ``carry_forward`` route's ownership/configuration
    checks, then asks the service for a read-only plan and renders the
    Bootstrap 5 modal partial.  No database writes happen here -- the
    user sees what WOULD happen and confirms via the modal's button,
    which posts to the existing ``carry_forward`` endpoint.

    Returns 404 for "period not found" and "period not yours" (security
    response rule), 400 for missing pay-period configuration (no
    current period, no baseline scenario), 200 with the rendered
    modal HTML for the success case.

    Args:
        period_id: pay_period.id of the source period (the past
            period the user clicked Carry Fwd on).

    Returns:
        Flask response tuple: rendered modal HTML or an error message
        with the appropriate status code.
    """
    ctx, err = _resolve_carry_forward_context(period_id)
    if err is not None:
        return err
    source_period, current_period, scenario = ctx

    try:
        preview = carry_forward_service.preview_carry_forward(
            period_id, current_period.id, current_user.id, scenario.id,
        )
    except NotFoundError as exc:
        return str(exc), 404

    return render_template(
        "grid/_carry_forward_preview_modal.html",
        preview=preview,
        source_period=source_period,
        current_period=current_period,
    )


@transactions_bp.route("/pay-periods/<int:period_id>/carry-forward", methods=["POST"])
@login_required
@require_owner
def carry_forward(period_id):
    """Carry forward all unpaid items from a period to the current period."""
    ctx, err = _resolve_carry_forward_context(period_id)
    if err is not None:
        return err
    _source_period, current_period, scenario = ctx

    try:
        count = carry_forward_service.carry_forward_unpaid(
            period_id, current_period.id, current_user.id, scenario.id
        )
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Envelope branch refused -- e.g. settled target canonical,
        # template inactive in target period, or a corrupt multi-row
        # target state.  Rollback so no source row is left settled
        # and no target row is left bumped (batch atomicity per
        # docs/carry-forward-aftermath-implementation-plan.md).
        db.session.rollback()
        return str(exc), 400

    logger.info("user_id=%d carried forward %d items from period %d", current_user.id, count, period_id)
    # Trigger a full grid refresh.
    return "", 200, {"HX-Trigger": "gridRefresh"}
