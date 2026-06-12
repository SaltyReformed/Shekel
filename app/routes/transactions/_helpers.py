"""
Shekel Budget App -- Transaction route package: shared helpers.

The Marshmallow schema singletons, the credit-payback unique-index name
constant, the :class:`_RenderTarget` response-surface bundle, and the
private render / ownership / FK helpers shared across the transaction
route sub-modules.  Schema instances are constructed once at import time
so every handler reuses the same instance (Marshmallow contract),
preserving the pre-split monolith's behaviour.
"""

import logging

from dataclasses import dataclass
from datetime import date

from flask import render_template
from flask_login import current_user

from app.extensions import db
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod
from app.models.category import Category
from app.routes._render_helpers import render_transaction_cell
from app.schemas.validation import (
    MarkDoneSchema,
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import grid_view_service
from app.services.entry_service import (
    build_entry_lists_dict,
    build_entry_sums_dict,
)
from app.utils.auth_helpers import get_accessible_transaction
from app.utils.db_errors import is_unique_violation

# Name of the partial unique index that backstops commit C-19's
# duplicate CC Payback fix.  Mirrors the literal in
# ``migrations/versions/b3d8f4a01c92_*.py`` and
# ``app.models.transaction.Transaction.__table_args__``; renaming
# the index requires a coordinated edit across all three sites.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

logger = logging.getLogger(__name__)

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
    (empty ``render_mode``) resolves to the cell + ``balanceChanged``
    targeted-swap path.
    """

    render_mode: str
    card_prefix: str
    can_edit: bool


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
        return render_transaction_cell(txn)
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


def _mark_done_success_response(txn, target):
    """Build the success response tuple for a mark_done request.

    Forks on the rendering surface the request came from:

      * ``target.render_mode == "mobile_card"``: return the single
        re-rendered mobile card + ``HX-Trigger: mobileCardSettled``.  The
        card swaps in place (no reload); the owner This Period summary
        blocks listen for ``mobileCardSettled`` and self-refresh, while
        the companion page has no summary blocks so only the card
        updates.
      * otherwise (desktop grid / full-edit popover): the desktop cell +
        ``HX-Trigger: balanceChanged`` -- a targeted swap, no reload.
        The freshly settled cell swaps in place (``hx-target`` is the
        cell), and ``balanceChanged from:body`` drives the self-refresh
        on the sticky ``<tfoot>`` balance row (grid/_balance_row.html)
        and the two summary subtotal ``<tbody>`` sections
        (grid/_subtotal_rows.html), so the daily desktop mark-paid feels
        instant.  This is the REGULAR (non-transfer) mark_done path only:
        the helper is reached solely from :func:`_mark_done_regular`.
        The transfer-shadow path (:func:`_mark_done_shadow`) deliberately
        keeps ``gridRefresh`` because the sibling shadow cell on the
        other leg also changes and only a full reload re-renders it
        today; ``mark_credit`` / ``cancel_transaction`` / ``unmark_credit``
        likewise keep ``gridRefresh`` because they add or remove grid
        rows, which an in-place cell swap cannot express.

    Args:
        txn: The settled Transaction.
        target: The :class:`_RenderTarget` describing the response
            surface.  ``render_mode`` selects the mobile-card vs desktop
            path; ``card_prefix`` / ``can_edit`` are forwarded to the
            card render and are only meaningful on the mobile_card path.

    Returns:
        A Flask ``(html, status, headers)`` response tuple.
    """
    if target.render_mode == "mobile_card":
        return (
            _render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            200,
            {"HX-Trigger": "mobileCardSettled"},
        )
    return render_transaction_cell(txn), 200, {"HX-Trigger": "balanceChanged"}


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
        render_transaction_cell(refreshed),
        200,
        {"HX-Trigger": "gridRefresh"},
    )


def _stale_transaction_response(txn_id, target=None):
    """Roll back the session and render the cell in conflict mode + 409.

    Used by every PATCH/POST/DELETE handler that can race a
    concurrent commit against the version-pinned UPDATE.  Re-fetches
    the transaction from the database so the user sees the winner's
    state -- never the loser's stale in-memory copy -- and tags the
    cell with ``conflict=True`` so the template surfaces a warning
    indicator.  Returns a 404 if the row was hard-deleted by the
    winning request.

    The mobile/companion Mark Paid path passes a :class:`_RenderTarget`
    with ``render_mode == "mobile_card"`` so the 409 body is the
    re-rendered mobile card (latest state) rather than the desktop cell;
    the card's ``hx-target`` is the card wrapper, so a desktop-cell body
    would not swap.  That path re-fetches through
    :func:`get_accessible_transaction` so a companion's
    conflict resolves against the linked owner's row (the desktop path
    uses :func:`_get_owned_transaction`, which is owner-only).

    Args:
        txn_id: Primary key of the transaction the route was trying
            to mutate.  Used to re-fetch under ownership checks so
            the conflict UI renders the correct row.
        target: The :class:`_RenderTarget` for the mobile/companion Mark
            Paid path, or ``None`` (the default) for the desktop-cell
            conflict every non-mobile caller wants.  When present and
            ``render_mode == "mobile_card"`` the 409 body is the mobile
            card; ``card_prefix`` / ``can_edit`` drive its wrapper id and
            the owner-vs-companion edit affordance.

    Returns:
        Flask response tuple ``(html, 409)`` or ``("Not found", 404)``
        when the row vanished entirely.
    """
    db.session.rollback()
    db.session.expire_all()
    if target is not None and target.render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            return "Not found", 404
        return (
            _render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            409,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_transaction_cell(txn, conflict=True), 409


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
        specs: ordered ``(model, obj_id, not_found_msg)`` tuples.  Use a
            distinct model class per spec: the returned map is keyed by
            model, so two specs sharing one model collapse to the last
            row fetched.  That precondition holds for every caller today;
            note it touches only the convenience map -- every spec is
            still ownership-checked, so a collision could never weaken
            the 404 gate.

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
