"""
Shekel Budget App -- Transaction Entry Routes

CRUD operations for individual purchase entries on entry-capable
transactions.  Returns HTMX fragments for inline management in
the transaction detail popover and the companion view.
"""

import logging
import re
from datetime import date
from typing import Any

from flask import Blueprint, render_template, request
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.routes.transactions._helpers import _render_cell
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.services import entry_service
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import get_accessible_transaction
from app.utils.db_errors import is_unique_violation

logger = logging.getLogger(__name__)

entries_bp = Blueprint("entries", __name__)

# Marshmallow schema instances -- reused across requests.
_create_schema = EntryCreateSchema()
_update_schema = EntryUpdateSchema()

# Name of the partial unique index that backstops the duplicate CC
# Payback bug closed in commit C-19.  ``entry_service.create_entry``,
# ``update_entry``, and ``delete_entry`` all funnel through
# ``entry_credit_workflow.sync_entry_payback`` which acquires
# ``SELECT ... FOR NO KEY UPDATE`` on the parent transaction; if any
# future caller bypasses that lock, the partial index rejects the
# duplicate INSERT and the matching catch below converts the
# ``IntegrityError`` to idempotent success.  Mirrors the literal in
# the matching Alembic migration (b3d8f4a01c92) and the
# ``__table_args__`` declaration on
# ``app.models.transaction.Transaction``.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

# Accepted shape of the ``host`` query param: a short lowercase id-prefix
# token.  Today the only non-empty value the templates emit is ``tp``
# (the inline mobile/companion card list); 16 chars is generous headroom
# for future prefixes while keeping free-form input out of DOM ids.
_HOST_TOKEN_RE = re.compile(r"[a-z0-9-]{1,16}")


def _request_host() -> str:
    """Read the validated ``host`` query param for this request.

    Every entries-CRUD control in ``grid/_transaction_entries.html``
    carries ``?host=<prefix>`` so the route's re-render reconstructs the
    same entry-list root id the request's ``hx-target`` named: the
    inline mobile/companion card list sends ``tp``, the desktop popover
    sends the empty default.  A value outside the short-token shape
    degrades to ``""`` (the popover surface) instead of echoing
    free-form input into a DOM id.

    Returns:
        The validated host prefix segment, possibly ``""``.
    """
    host = request.args.get("host", "")
    if host and _HOST_TOKEN_RE.fullmatch(host) is None:
        return ""
    return host


def _entry_list_host_id(txn_id: int, host: str) -> str:
    """Compose the entry-list root DOM id from a txn id and host prefix.

    Single Python-side source of truth that mirrors the Jinja default in
    ``grid/_transaction_entries.html`` (and the inline-card host set in
    ``_grid_row_macros.html``).  ``host=""`` yields the bare
    ``entry-list-<txn_id>`` (the full-edit popover's id); a non-empty
    host (e.g. ``"tp"``) yields ``entry-list-<host>-<txn_id>`` so the
    inline mobile-card list and the popover list never collide.  The
    route reconstructs the id this way so an entries-CRUD re-render
    lands on the same element the request's ``hx-target`` named.

    Args:
        txn_id: The parent transaction id.
        host: The host prefix segment (``""`` for the bare popover id).

    Returns:
        The DOM id string for the entry-list root element.
    """
    return "entry-list-" + (host + "-" if host else "") + str(txn_id)


def _render_entry_list(
    txn: Transaction,
    editing_id: int | None = None,
    conflict: bool = False,
    host: str = "",
) -> str:
    """Render the entry list partial for a transaction.

    Loads entries, computes remaining balance, and checks for
    out-of-period dates (OP-4 date awareness).

    Args:
        txn: The parent Transaction object.
        editing_id: Optional entry ID currently being edited.
            When set, the template shows an inline edit form
            for that entry instead of the display row.
        conflict: When True, surface a warning banner that the
            most recent edit was rejected by the optimistic-lock
            check.  See commit C-18.

    Returns:
        Rendered HTML string.
    """
    entries = entry_service.get_entries_for_transaction(
        txn.id, current_user.id,
    )
    remaining = entry_service.compute_remaining(
        txn.estimated_amount, entries,
    )
    out_of_period_ids = {
        e.id for e in entries
        if not entry_service.check_entry_date_in_period(e.entry_date, txn)
    }
    return render_template(
        "grid/_transaction_entries.html",
        txn=txn,
        entries=entries,
        remaining=remaining,
        today=date.today().isoformat(),
        editing_id=editing_id,
        out_of_period_ids=out_of_period_ids,
        conflict=conflict,
        entry_list_host=host,
        entry_list_host_id=_entry_list_host_id(txn.id, host),
    )


def _entry_mutation_response(txn: Transaction, host: str) -> ResponseReturnValue:
    """Build the shared success response for an entries mutation.

    The refreshed entry list for the requesting surface plus, on the
    OWNER's desktop popover surface only, an out-of-band re-render of
    the parent transaction's grid cell.  An entry mutation changes the
    cell's "spent / budget" progress display, but the request's primary
    swap only replaces the entry list inside the popover -- without the
    OOB fragment the on-grid amount stays stale until an unrelated
    action re-renders the cell.

    Two gates on the fragment, both required:

    * ``host == ""`` -- the inline card surfaces (``"tp"``: mobile grid
      and companion page) have no ``#txn-cell-<id>`` element on the
      companion page, so an unconditional fragment would raise
      ``htmx:oobErrorNoTarget`` there.
    * the requester OWNS the transaction -- ``host`` is
      client-controlled, and these routes also admit companions (via
      :func:`get_accessible_transaction`).  The desktop grid cell is an
      owner-only surface whose markup includes ``txn.notes`` in
      aria-label/title; a companion stripping the ``host`` param must
      not receive it.

    The ``balanceChanged`` trigger drives the existing self-refreshes:
    the desktop tfoot balance row and subtotal tbodies, and (via the
    viewport-gated re-dispatch in ``mobile_grid.js``) the mobile This
    Period summary.

    Args:
        txn: The parent transaction, post-mutation and post-commit.
        host: The validated host prefix from :func:`_request_host`.

    Returns:
        Flask response tuple ``(html, 200, headers)``.
    """
    response = _render_entry_list(txn, host=host)
    is_owner = txn.pay_period.user_id == current_user.id
    if host == "" and is_owner:
        response += _render_cell(txn, wrap_div=True, wrap_oob=True)
    return response, 200, {"HX-Trigger": "balanceChanged"}


def _credit_payback_idempotent_response(
    exc: IntegrityError, txn_id: int, log_context: str, host: str,
) -> ResponseReturnValue:
    """Translate a credit-payback unique-index violation into a 200.

    Shared between :func:`create_entry`, :func:`update_entry`, and
    :func:`delete_entry`.  All three routes funnel through
    ``entry_credit_workflow.sync_entry_payback`` (commit C-19) where
    a SELECT FOR NO KEY UPDATE on the parent transaction prevents
    the duplicate-payback race in normal flow.  This helper is the
    backstop for any future caller that bypasses the lock: the
    partial unique index ``uq_transactions_credit_payback_unique``
    rejects the duplicate INSERT, the calling route catches the
    resulting :class:`IntegrityError`, and this helper either returns
    the user the refreshed entry list at HTTP 200 (matching what a
    serialised request would have produced) or returns the standard
    400 response when the IntegrityError is for some other
    constraint we should not silently swallow.

    Args:
        exc: The caught :class:`IntegrityError`.
        txn_id: Parent transaction ID for re-fetching after rollback.
        log_context: Short human-readable string describing the route
            (e.g. ``"create_entry txn_id=42"``) for the structured
            log line emitted on the idempotent-success path.
        host: The validated host prefix from :func:`_request_host`,
            forwarded so the idempotent-success body matches the
            regular success response for the same surface.

    Returns:
        Flask response tuple suitable for direct return from the
        calling route.

    Side effects:
        Calls ``db.session.rollback()`` -- the caller has already
        seen the flush fail, so leaving pending changes in the
        session would block subsequent commits.
    """
    db.session.rollback()
    if not is_unique_violation(exc, _CREDIT_PAYBACK_UNIQUE_INDEX):
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "Duplicate CC payback prevented on %s (idempotent success)",
        log_context,
    )
    refreshed = get_accessible_transaction(txn_id)
    if refreshed is None:
        return "Not found", 404
    return _entry_mutation_response(refreshed, host)


def _stale_entry_response(
    txn: Transaction | None, host: str,
) -> ResponseReturnValue:
    """Roll back the session and render the entry list in conflict mode.

    Used by every entry-mutating PATCH/DELETE handler to convert a
    stale form or ``StaleDataError`` flush failure into a coherent
    UI response instead of a 500.  Re-fetches the entries so the
    user's view shows the winner's state -- never the loser's
    stale in-memory copies -- and tags the list with
    ``conflict=True`` so the template surfaces a warning banner.

    Args:
        txn: The parent Transaction whose entry list is being
            rendered.  The transaction itself is reloaded as well
            so any sibling-level changes (e.g. a concurrent paid
            event) are reflected.
        host: The validated host prefix from :func:`_request_host`,
            so the conflict re-render reconstructs the same
            entry-list root id the request's ``hx-target`` named.

    Returns:
        Flask response tuple ``(html, 409)``.
    """
    db.session.rollback()
    db.session.expire_all()
    fresh_txn = db.session.get(Transaction, txn.id) if txn is not None else None
    if fresh_txn is None:
        return "Not found", 404
    return _render_entry_list(fresh_txn, conflict=True, host=host), 409


def _accessible_txn_and_entry(
    txn_id: int, entry_id: int,
) -> tuple[Transaction, TransactionEntry] | None:
    """Resolve the parent transaction and its owned entry, or ``None``.

    The shared ownership preamble for the per-entry mutation routes
    (:func:`update_entry`, :func:`toggle_cleared`, :func:`delete_entry`):
    the parent must be accessible to the requester
    (:func:`get_accessible_transaction` -- owner, or companion with
    visibility) AND the entry must belong to that parent.  The second
    check prevents parameter-confusion attacks where a visible
    transaction's URL is paired with another transaction's entry id.
    A single ``None`` covers every failure -- missing transaction,
    missing entry, or an entry on a different transaction -- so the
    caller's one guard clause returns the uniform 404 (the "404 for
    both not-found and not-yours" security response rule), and the
    security check has exactly one definition.

    Args:
        txn_id: Parent transaction id from the URL.
        entry_id: Entry id from the URL.

    Returns:
        ``(txn, entry)`` when both checks pass; ``None`` otherwise.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return None
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None or entry.transaction_id != txn.id:
        return None
    return txn, entry


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["GET"])
@login_required
def list_entries(txn_id):
    """HTMX partial: return the entry list for a transaction.

    Accepts an optional ``editing`` query parameter (entry ID) to
    render an inline edit form for the specified entry.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    editing_id = request.args.get("editing", type=int)
    return _render_entry_list(
        txn, editing_id=editing_id, host=_request_host(),
    )


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["POST"])
@login_required
def create_entry(txn_id):
    """Create a new entry and return the updated entry list.

    Validates input via EntryCreateSchema, delegates to
    entry_service.create_entry (which syncs CC payback and
    updates actual_amount if Paid), then commits atomically.
    Returns the refreshed entry list with a balanceChanged trigger
    (plus, on the desktop popover surface, the OOB grid-cell
    re-render -- see :func:`_entry_mutation_response`).
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    host = _request_host()

    errors = _create_schema.validate(request.form)
    if errors:
        return str(errors), 422

    data = _create_schema.load(request.form)
    try:
        entry_service.create_entry(
            transaction_id=txn.id,
            user_id=current_user.id,
            details=entry_service.EntryDetails(**data),
        )
        db.session.commit()
    except IntegrityError as exc:
        # Defensive backstop for commit C-19: see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"create_entry txn_id={txn.id}", host,
        )
    except (NotFoundError, ValidationError) as exc:
        db.session.rollback()
        return str(exc), 400

    return _entry_mutation_response(txn, host)


def _execute_entry_update(
    entry_id: int, txn: Transaction, data: dict[str, Any], host: str,
) -> ResponseReturnValue:
    """Run the entry update + commit, translating service outcomes to HTTP.

    ``StaleDataError`` at flush -> 409 conflict entry list; the C-19
    ``IntegrityError`` backstop -> the idempotent credit-payback response;
    ``NotFoundError`` / ``ValidationError`` -> 400.  On success, the
    shared mutation response (refreshed entry list + OOB cell on the
    popover surface + ``balanceChanged``).  Extracted so
    ``update_entry`` keeps only its ownership guards + form validation;
    this owns the service-call/commit/error-translation tail (the
    ``transfers._execute_transfer_update`` precedent).  ``host`` is the
    validated surface prefix from :func:`_request_host`.
    """
    try:
        entry_service.update_entry(entry_id, current_user.id, **data)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_entry id=%d", entry_id,
        )
        return _stale_entry_response(txn, host)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"update_entry id={entry_id}", host,
        )
    except (NotFoundError, ValidationError) as exc:
        db.session.rollback()
        return str(exc), 400

    return _entry_mutation_response(txn, host)


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["PATCH"],
)
@login_required
def update_entry(txn_id, entry_id):
    """Update an entry and return the updated entry list.

    Verifies the entry belongs to the specified transaction before
    calling the service, preventing parameter confusion attacks
    where a companion could modify entries on non-visible
    transactions by using a visible transaction's URL with a
    different entry ID.

    Optimistic locking (commit C-18 / F-010): the inline edit form
    ships ``version_id`` as a hidden input.  When the submitted
    value differs from the entry's current counter, the handler
    short-circuits with a 409 + entry list refreshed in conflict
    mode so the user sees the winner's values.  ``StaleDataError``
    raised at flush time is caught and produces the same response.
    """
    target = _accessible_txn_and_entry(txn_id, entry_id)
    if target is None:
        return "Not found", 404
    txn, entry = target
    host = _request_host()

    errors = _update_schema.validate(request.form)
    if errors:
        return str(errors), 422

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != entry.version_id:
        logger.info(
            "Stale-form conflict on update_entry id=%d "
            "(submitted=%d, current=%d)",
            entry_id, submitted_version, entry.version_id,
        )
        return _stale_entry_response(txn, host)

    return _execute_entry_update(entry_id, txn, data, host)


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>/cleared",
    methods=["PATCH"],
)
@login_required
def toggle_cleared(txn_id, entry_id):
    """Manually flip the is_cleared flag on a single entry.

    The auto-clear on anchor true-up covers the common case, but the
    user may need to correct a specific entry (e.g. a debit that
    posted after their most recent true-up but was auto-cleared in
    error, or one they want to exclude from the reservation before
    they've formally updated the anchor).

    Returns the refreshed entry list and a balanceChanged HX-Trigger
    so the grid re-renders with the new projection.

    Optimistic locking (commit C-18 / F-010): no form-side
    ``version_id`` is shipped with the toggle button; the
    SQLAlchemy ``version_id_col`` lock catches concurrent races at
    flush time and the handler converts ``StaleDataError`` into a
    409 + conflict entry list.
    """
    target = _accessible_txn_and_entry(txn_id, entry_id)
    if target is None:
        return "Not found", 404
    txn, _entry = target
    host = _request_host()

    try:
        entry_service.toggle_cleared(entry_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on toggle_cleared id=%d", entry_id,
        )
        return _stale_entry_response(txn, host)
    except NotFoundError as exc:
        db.session.rollback()
        return str(exc), 404

    return _entry_mutation_response(txn, host)


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["DELETE"],
)
@login_required
def delete_entry(txn_id, entry_id):
    """Delete an entry and return the updated entry list.

    Same parameter confusion guard as update_entry: verifies the
    entry belongs to the specified transaction.

    Optimistic locking: see :func:`toggle_cleared`.
    """
    target = _accessible_txn_and_entry(txn_id, entry_id)
    if target is None:
        return "Not found", 404
    txn, _entry = target
    host = _request_host()

    try:
        entry_service.delete_entry(entry_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_entry id=%d", entry_id,
        )
        return _stale_entry_response(txn, host)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- ``delete_entry``
        # also calls ``sync_entry_payback``, so the same race window
        # exists if a future caller bypasses the row lock.  See
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"delete_entry id={entry_id}", host,
        )
    except NotFoundError as exc:
        db.session.rollback()
        return str(exc), 404

    return _entry_mutation_response(txn, host)
