"""
Shekel Budget App -- Transfer route package: a template's LIFECYCLE.

The three doors that move a transfer template between its states -- archive,
unarchive and hard-delete -- split out of the sibling module
:mod:`app.routes.transfers.templates` at plan step R7d-g-2 (developer ruling
**R-R84**, 2026-09-13), where they had lived since the package split, when
that module stood at pylint's 1,000-line cap with this leaf's edits to every
one of them still to land.  A pure move, graded by AST: every definition
identical, code and docstrings, nothing new.  Every URL and endpoint name is
preserved verbatim, so no ``url_for`` site or template changed.

The three are one concern: which of the template's ``budget.transfers`` rows
still stand for it.  Archiving soft-deletes its projected rows (and their
shadow pairs, through the transfer service), unarchiving restores them and
fills forward, and hard-delete either destroys the unsettled rows with the
template or -- where settled history stands -- falls back to the archive.
What the template IS (its CRUD routes, its form payload, its recurrence rule)
stays in ``templates.py``, and what happens to a NON-repeating template's
single row on an edit is :mod:`app.routes.transfers._instances`.
"""

import logging
from datetime import date

from flask import abort, flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    generate_transfers_for_all_periods,
)
from app.routes.transfers._bp import transfers_bp
from app.services import transfer_service
from app.utils import archive_helpers
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.balance_predicates import is_projected_clause

logger = logging.getLogger(__name__)


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@require_owner
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes projected transfers and their shadow transactions via
    the transfer service to maintain the three-level cascade:
    template archival -> transfer soft-delete -> shadow soft-delete.

    Optimistic locking (commit C-18 / F-010): the template's
    ``version_id`` is enforced by SQLAlchemy on the
    ``is_active = False`` flush; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect so the user retries against fresh state.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = False

    # Find projected, non-deleted transfers to soft-delete.  Routed
    # through the centralized ``is_projected_clause`` (D6-09 / MED-02)
    # parameterised on ``Transfer`` so the rule "what does a
    # Projected filter look like in SQL" is shared with the
    # Transaction filter sites.
    transfers_to_delete = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    # Route each through the service to ensure shadows are soft-deleted.
    for xfer in transfers_to_delete:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(
        f"Recurring transfer '{template.name}' archived. "
        f"{len(transfers_to_delete)} projected transfer(s) removed.",
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/unarchive", methods=["POST"])
@require_owner
def unarchive_transfer_template(template_id):
    """Unarchive a transfer template.

    Restores soft-deleted transfers and their shadow transactions.

    Optimistic locking: see :func:`archive_transfer_template`.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = True

    # Find soft-deleted projected transfers to restore.  Routed
    # through ``is_projected_clause(Transfer)`` (D6-09 / MED-02);
    # see ``archive_transfer_template`` above.
    transfers_to_restore = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(True),
        )
        .all()
    )

    # Restore transfers and shadows via the service so all mutations
    # flow through the single enforcement point (design doc section 4.1).
    for xfer in transfers_to_restore:
        transfer_service.restore_transfer(xfer.id, current_user.id)

    restored_count = len(transfers_to_restore)

    if template.recurrence_rule:
        generate_transfers_for_all_periods(template, effective_from=date.today())

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict
    flash(
        f"Recurring transfer '{template.name}' unarchived. "
        f"{restored_count} projected transfer(s) restored.",
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@require_owner
def hard_delete_transfer_template(template_id):
    """Permanently delete a transfer template if it has no payment history.

    Maintains all five transfer invariants from CLAUDE.md:
      1. Two linked shadows per transfer -- CASCADE on Transaction.transfer_id
         removes both shadows when the parent Transfer is hard-deleted via
         transfer_service.delete_transfer(soft=False).
      2. No orphaned shadows -- shadows are removed atomically with their
         parent transfer through the service's CASCADE verification.
      3. Amount/status/period parity -- not applicable; entire records are
         removed, not mutated.
      4. All mutations through the transfer service -- every transfer
         deletion is routed through transfer_service.delete_transfer().
      5. Balance calculator queries only budget.transactions -- after
         deletion, shadow transactions no longer exist in the table.

    Two-path logic:
      - History exists (Paid transfers): permanent deletion is
        blocked.  Template is archived instead (if not already) and the
        user is warned.
      - No history: linked transfers are hard-deleted through the
        transfer service (which CASCADE-deletes shadows), then the
        template itself is permanently removed.

    Defense in depth (F-14): the bulk delete is constrained to non-
    settled transfers via the semantic ``Status.is_settled`` boolean,
    mirroring the ``templates.py::hard_delete_template`` shape added
    after CRIT-05.  Even if the guard predicate above regresses, is
    bypassed, or races a concurrent mark-done that lands between the
    guard check and the loop, settled transfers (Paid, Received,
    Received) and their two-shadow pairs cannot be physically destroyed
    by this route.  Survivors retain their ``transfer_template_id``;
    the column's FK is ``ON DELETE SET NULL`` so they become detached
    settled history when the parent template is removed.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    if archive_helpers.transfer_template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # Soft-delete projected transfers via the service (same as
            # archive_transfer_template) to maintain shadow invariants.
            # Routed through ``is_projected_clause(Transfer)``
            # (D6-09 / MED-02); see ``archive_transfer_template`` above.
            transfers_to_delete = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    is_projected_clause(Transfer),
                    Transfer.is_deleted.is_(False),
                )
                .all()
            )
            for xfer in transfers_to_delete:
                transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_transfer_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget("transfers.list_transfer_templates"),
            ))
            if conflict is not None:
                return conflict
        return redirect(url_for("transfers.list_transfer_templates"))

    # No history -- safe to permanently delete linked transfers through
    # the transfer service so that shadow transactions are CASCADE-
    # deleted (invariants 1, 2, 4).  ``transfer_service.delete_transfer``
    # flushes but does not commit, so all deletions are atomic within a
    # single DB transaction.
    #
    # Defense in depth (F-14 / commit C-21 mirror): the bulk delete is
    # additionally constrained to ``Status.is_settled = False`` rows via
    # the semantic ``Status.is_settled`` boolean -- the same shape
    # ``templates.py::hard_delete_template`` applies after CRIT-05.
    # Even if ``transfer_template_has_paid_history`` regresses, is
    # bypassed, or races a concurrent mark-done that lands between the
    # guard check and the loop below, settled transfers (Paid,
    # Received) and their two-shadow pairs cannot be
    # physically destroyed by this route.  Survivors retain their
    # ``transfer_template_id``; the column's FK is ``ON DELETE SET
    # NULL`` (see ``app/models/transfer.py``) so they become detached
    # settled history when the parent template is removed below.
    template_name = template.name
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    deletable_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id.notin_(settled_status_ids),
        )
        .all()
    )
    for xfer in deletable_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    db.session.delete(template)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_transfer_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transfer",
        ),
        redirect=RedirectTarget("transfers.list_transfer_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))
