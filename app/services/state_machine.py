"""
Shekel Budget App -- Transaction and Transfer Status State Machine

Defines the legal status transitions for ``ref.statuses`` rows and the
``verify_transition`` helper that every state-changing code path must
call before mutating ``status_id`` on a Transaction or Transfer.

Workflow per CLAUDE.md
----------------------

  projected -> done | received | credit | cancelled
  done | received -> settled
  done -> projected (revert mistakes)
  received -> projected (revert mistakes)
  credit -> projected (unmark credit)
  cancelled -> projected (reactivate cancelled item)
  settled -> settled (terminal -- archived rows cannot be mutated)

Identity transitions (for example projected -> projected, done -> done)
are always legal so an idempotent re-submission of a state-changing
request -- a typical HTMX double-click or carry-forward of an
already-marked row -- never spuriously raises.

Why the helper exists
---------------------

Audit findings F-046, F-047, and F-161 all stem from the same gap: the
transfer and transaction services accept any caller-supplied
``status_id`` without checking the current state, so an attacker (or a
defective caller) can move a row directly from ``Settled`` to
``Projected`` -- bypassing the workflow that the dashboard, the audit
log, and the carry-forward service all rely on.  Centralising the
transition table in this module gives every state-changing path a
single, auditable choke point and produces a uniform 400-class error
message on illegal transitions instead of letting the row drift.

Consumers
---------

* ``app/services/transfer_service.py:update_transfer`` -- the transfer
  status branch.  Verifies before propagating ``status_id`` to the
  parent transfer and both shadow transactions.

* ``app/routes/transactions.py:update_transaction`` -- the PATCH
  endpoint for non-transfer transactions.  Verifies before applying
  the ``setattr`` loop.

Future extension: every other service-layer or route-layer site that
assigns ``Transaction.status_id`` or ``Transfer.status_id`` should
adopt this helper.  Doing so is intentionally out of scope for the
C-21 commit -- F-046 / F-047 / F-161 are scoped to the two sites
above, and the audit's outstanding finding ledger gates the broader
rollout.

Caching
-------

The transitions dict is rebuilt lazily on every ``verify_transition``
call.  This is deliberate: ``ref_cache`` may be reinitialised (for
example by the test fixtures that wipe and re-seed the ``ref``
schema between sessions), and a module-level cache would silently
hold stale integer IDs after such a reset.  Building the dict from
the live ``ref_cache`` mappings on every call is microsecond-cheap
and matches the read-only access pattern used elsewhere in the
service layer.
"""

import logging

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError

logger = logging.getLogger(__name__)


def _build_transitions():
    """Return the transitions dict keyed by ``ref.statuses.id`` integers.

    Lazily computed (see module docstring) -- ``ref_cache`` must be
    initialised before this runs.  ``ref_cache.status_id`` raises
    ``RuntimeError`` for an uninitialised cache, which surfaces as
    a 500 if the helper is somehow called before ``create_app()``
    finishes wiring up reference data.

    Returns:
        dict mapping the integer PK of each StatusEnum member to the
        set of integer PKs reachable from it.  Identity transitions
        are included so idempotent re-submits succeed.
    """
    projected = ref_cache.status_id(StatusEnum.PROJECTED)
    done = ref_cache.status_id(StatusEnum.DONE)
    received = ref_cache.status_id(StatusEnum.RECEIVED)
    credit = ref_cache.status_id(StatusEnum.CREDIT)
    cancelled = ref_cache.status_id(StatusEnum.CANCELLED)
    settled = ref_cache.status_id(StatusEnum.SETTLED)

    return {
        # Projected can move to any active workflow state and absorbs
        # idempotent re-submission via the projected -> projected entry.
        projected: {projected, done, received, credit, cancelled},
        # Paid expenses can be archived (settled), reverted, or re-marked.
        done: {done, projected, settled},
        # Received income mirrors the Paid expense transitions.
        received: {received, projected, settled},
        # Credit can only revert to Projected -- the dedicated
        # unmark_credit workflow handles cleanup of the auto-generated
        # payback row.  No direct -> Done jump.
        credit: {credit, projected},
        # Cancelled rows can be reactivated to Projected.  No direct
        # transitions to Done / Received -- the user must reproject
        # first so the audit trail records both the reactivation and
        # the subsequent settle.
        cancelled: {cancelled, projected},
        # Terminal: a Settled row must not be mutated.  Identity is
        # included so an idempotent resubmit of "settle this row" on
        # an already-settled row does not raise.
        settled: {settled},
    }


def verify_transition(current_status_id, new_status_id, context="transaction"):
    """Raise ``ValidationError`` when the proposed transition is illegal.

    Args:
        current_status_id: Integer PK of the row's current
            ``status_id`` (or the loaded relationship's
            ``Status.id``).  Typically ``txn.status_id`` or
            ``xfer.status_id``.
        new_status_id: Integer PK of the proposed status.
        context: Short human-readable label embedded in the
            exception message ("transaction" or "transfer") so the
            route layer can surface a precise 400 to the user.

    Raises:
        ValidationError: The new state is not in the set of legal
            successors for the current state, OR the current state
            is not a recognised StatusEnum member (defensive check
            against a corrupt row that holds a non-enum status_id).
            Successful return (no exception) signals that the caller
            may proceed to mutate ``status_id``.  Identity transitions
            return without raising so idempotent re-submission is
            always safe.
    """
    transitions = _build_transitions()
    if current_status_id not in transitions:
        # The row's current status is not a recognised StatusEnum
        # member.  Refuse the transition rather than silently
        # accepting it -- a corrupt row should fail loudly so the
        # operator can investigate the source of the bad ID.
        logger.error(
            "Refusing %s status transition: current_status_id=%s "
            "is not a recognised StatusEnum member.",
            context, current_status_id,
        )
        raise ValidationError(
            f"Invalid {context} status transition: current status "
            f"{current_status_id} is not a recognised status."
        )

    allowed = transitions[current_status_id]
    if new_status_id not in allowed:
        logger.info(
            "Refusing %s status transition from %s to %s "
            "(allowed: %s).",
            context, current_status_id, new_status_id, sorted(allowed),
        )
        raise ValidationError(
            f"Invalid {context} status transition from "
            f"{current_status_id} to {new_status_id}."
        )


def finalised_edit_rejection(current_status, new_status, context="transaction"):
    """Return a rejection message when a finalised row's fields are locked.

    The companion to :func:`verify_transition`: where that gates the
    ``status_id`` mutation, this gates the *other* field mutations on a
    finalised row.  A row whose status ``is_immutable`` (every status
    except Projected) must not have its money / period / category /
    due-date fields silently rewritten through the manual edit routes --
    the same lock the recurrence engine
    (:mod:`app.services._recurrence_common`), carry-forward
    (:mod:`app.services.carry_forward_service`), and
    ``transaction_service.settle_from_entries`` already enforce against
    *programmatic* mutation.  Without it, an owner (or a replayed stale
    form) can retroactively change the amount of an already-paid
    movement, shifting the projected balance and the audit trail (#26).

    The lock lifts when the same request reverts the row to a mutable
    status (Projected), so a "revert and correct" edit is still atomic:
    ``done -> projected`` (or ``received -> projected``) is a legal
    transition, after which the fields are editable.  A row already in a
    mutable status is never blocked.

    Callers invoke this ONLY when the request actually edits a locked
    field; this function does not know the per-schema field names (they
    differ -- ``estimated_amount`` for a transaction, ``amount`` for a
    transfer), so the caller owns the locked-field set and this owns the
    status policy and the message.

    Args:
        current_status: The row's current :class:`~app.models.ref.Status`
            (or ``None`` -- treated as mutable, fail-open, since the
            transition guard owns the corrupt-status case).
        new_status: The :class:`~app.models.ref.Status` the request
            transitions to, or ``None`` when the request changes no
            status.
        context: Short human-readable label embedded in the message
            ("transaction" or "transfer"), mirroring
            :func:`verify_transition`.

    Returns:
        A user-facing rejection message string when the edit must be
        refused, or ``None`` when the row is mutable (or is being
        reverted to a mutable status) and the edit may proceed.
    """
    if current_status is None or not current_status.is_immutable:
        return None
    if new_status is not None and not new_status.is_immutable:
        return None
    return (
        f"Cannot edit a finalised ({current_status.name}) {context}. "
        "Revert it to Projected before changing the amount, category, "
        "period, or due date."
    )
