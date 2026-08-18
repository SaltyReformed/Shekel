"""
Shekel Budget App -- Transaction and Transfer Status State Machine

Defines the legal status transitions for ``ref.statuses`` rows and the
``verify_transition`` helper that every state-changing code path must
call before mutating ``status_id`` on a Transaction or Transfer.

Transaction workflow per CLAUDE.md
----------------------------------

  projected -> done | received | credit | cancelled
  done | received -> settled
  done -> projected (revert mistakes)
  received -> projected (revert mistakes)
  credit -> projected (unmark credit)
  cancelled -> projected (reactivate cancelled item)
  settled -> settled (terminal -- archived rows cannot be mutated)

Transfer workflow
-----------------

Transfers speak a smaller status vocabulary, so they get their own
transition map rather than sharing the transaction one:

  projected -> done | cancelled
  done -> projected (revert mistakes) | settled
  cancelled -> projected (reactivate)
  settled -> settled (terminal)

Credit is excluded because the credit/auto-payback workflow is
expense-only (``mark_as_credit`` refuses transfers outright); a
transfer pushed into Credit would be balance-excluded on both
accounts with no compensating payback -- it would silently vanish
from both projections.  Received is excluded because it is a display
convention for regular income transactions; the transfer service
settles both shadows with Done (see ``_mark_done_shadow``).  Before
the split, the shared map let a crafted PATCH (the shadow path or
the direct transfer PATCH -- both schemas accept any integer
``status_id``) move a transfer into either state.

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

The workflow is chosen by the ROW, never by the caller
------------------------------------------------------

:func:`verify_transition` and :func:`allowed_transitions` take the row
whose status is in question and derive the map from its own type.  They
used to take ``(status_id, context="transaction")`` -- an id lifted off a
row plus a separate string naming that row's kind, which is ONE fact
stated twice and kept in step by hand.  Every call site spelled it
correctly and every one of them could have got it wrong; the transaction
default made the wrong answer the silent one, since a transfer's
``status_id`` passed without the kwarg would have been graded against the
map that admits Credit and Received -- the two states this module's
transfer map exists to exclude.

Plan step X-aj1 (``docs/audits/balance_architecture/README.md``, ruling
**R-DN**).  The balance arc's Section 8 states the rule this applies: an
argument a caller can get wrong is a defect, not a contract.

Consumers
---------

* ``app/services/status_seam/_seam.py:apply_status_change`` -- the ONE seam
  that ASSIGNS a status, for both row types.  Every status-changing path
  in the application writes through it.

The other four callers READ the rules without assigning, so they are
listed too rather than left to be discovered -- the sentence above is
about writes, and a "everything goes through the seam" claim that
quietly meant "every write" is the kind of overclaim this arc keeps
paying for:

* ``app/services/transfer_service/_status.py:apply_status_to_all_three`` --
  verifies all three of a transfer's rows BEFORE the seam assigns any,
  so an illegal move leaves the trio untouched (F-047 atomicity).
* ``app/services/transfer_service/_validation.py:assert_restorable`` -- asks
  :func:`allowed_transitions` whether a drifted shadow can legally be
  pulled back to its parent, and refuses the restore when it cannot
  (ruling R-DO).
* ``app/routes/transactions/mutations.py`` -- the PATCH handler's
  error-precedence pre-check, which deliberately duplicates the seam's
  own verification so an illegal transition reports its own message
  before any other guard speaks.
* ``app/routes/transfers/forms.py`` / ``app/routes/transactions/forms.py``
  -- the two status dropdowns' pre-hint.

In all four the seam remains the enforcement point; none of them writes
a ``status_id``.

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
from app.models.transaction import Transaction
from app.models.transfer import Transfer

logger = logging.getLogger(__name__)

# The two status-bearing models and the workflow each one speaks.  Keyed by
# CLASS rather than by a caller-supplied label so the map is chosen by the row
# whose status is being changed, and a row can no longer be graded against the
# other entity's rules (ruling R-DN).  A model absent from this map is a
# programming error and :func:`_context_for` says so loudly rather than
# defaulting -- fail closed, because the transaction map is the PERMISSIVE one
# and a silent default would admit Credit and Received onto a transfer.
_ROW_CONTEXTS = {
    Transaction: "transaction",
    Transfer: "transfer",
}


def _context_for(row):
    """Return the state-machine context for *row*, by its model class.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status is in
            question.

    Returns:
        ``"transaction"`` or ``"transfer"`` -- the key
        :func:`_build_transitions` selects a map with.

    Raises:
        TypeError: If *row* is neither status-bearing model.  A programming
            error at the call site, raised rather than defaulted: the
            transaction map is a strict SUPERSET of the transfer map, so
            guessing would silently widen the workflow of whatever was passed.
    """
    for model, context in _ROW_CONTEXTS.items():
        if isinstance(row, model):
            return context
    raise TypeError(
        f"{type(row).__name__} is not a status-bearing row; "
        f"expected one of {sorted(m.__name__ for m in _ROW_CONTEXTS)}."
    )


def _build_transitions():
    """Return BOTH workflows' transition dicts, keyed by entity label.

    Lazily computed (see module docstring) -- ``ref_cache`` must be
    initialised before this runs.  ``ref_cache.status_id`` raises
    ``RuntimeError`` for an uninitialised cache, which surfaces as
    a 500 if the helper is somehow called before ``create_app()``
    finishes wiring up reference data.

    The two maps are written out explicitly rather than deriving one
    from the other: each is a statement of policy, and a future edit
    to the transaction workflow must not silently leak into the
    transfer workflow (or vice versa).

    **It returns both rather than selecting between them**, and that is
    what makes the selection total: the caller indexes this by a label
    that came from :data:`_ROW_CONTEXTS`, so there is no unrecognised
    label to raise on.  It used to take a ``context`` argument and raise
    ``ValueError`` on a typo; once the label stopped being a caller's
    string that arm became a guard against an impossible shape, which
    ``CLAUDE.md`` rule 13 forbids and this arc's Section 8 records as
    reading like coverage without being any.  It was deleted WITH the
    parameter rather than left (plan step X-aj1).

    Returns:
        ``{"transaction": {...}, "transfer": {...}}`` -- each mapping the
        integer PK of a status legal for that entity to the set of PKs
        reachable from it.  Identity transitions are included so
        idempotent re-submits succeed.
    """
    projected = ref_cache.status_id(StatusEnum.PROJECTED)
    done = ref_cache.status_id(StatusEnum.DONE)
    received = ref_cache.status_id(StatusEnum.RECEIVED)
    credit = ref_cache.status_id(StatusEnum.CREDIT)
    cancelled = ref_cache.status_id(StatusEnum.CANCELLED)
    settled = ref_cache.status_id(StatusEnum.SETTLED)

    return {
        "transaction": {
            # Projected can move to any active workflow state and absorbs
            # idempotent re-submission via the projected -> projected entry.
            projected: {projected, done, received, credit, cancelled},
            # Paid expenses can be archived (settled), reverted, or re-marked.
            done: {done, projected, settled},
            # Received income mirrors the Paid expense transitions.
            received: {received, projected, settled},
            # Credit can only revert to Projected -- both revert paths
            # (the dedicated unmark_credit workflow and the PATCH status
            # edit) delete the auto-generated payback row through the
            # shared credit_workflow cleanup helper.  No direct -> Done
            # jump.
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
        },
        "transfer": {
            # No Credit (the credit workflow is expense-only and refuses
            # transfers) and no Received (a display convention for
            # regular income rows; transfers settle with Done) -- see
            # the module docstring's transfer workflow section.
            projected: {projected, done, cancelled},
            done: {done, projected, settled},
            cancelled: {cancelled, projected},
            # Terminal, as for transactions.
            settled: {settled},
        },
    }


def allowed_transitions(row):
    """Return the set of status ids legally reachable from *row*'s current one.

    The template-facing half of the state machine (grid audit D2, ruled
    2026-07-11): the action cards' status dropdowns disable options this
    set excludes, so the user is pre-hinted instead of discovering an
    illegal transition through a 400.  :func:`verify_transition` remains
    the enforcement seam -- this helper is display guidance only, and a
    crafted request that ignores it is still rejected there.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` the dropdown is being
            rendered for.  Its class selects the workflow and its
            ``status_id`` is the state read from (ruling R-DN).

    Returns:
        frozenset of legal successor status ids (identity included).
        Empty for a current status the map does not recognise (a
        corrupt row -- the dropdown then offers nothing rather than
        guessing).

    Raises:
        TypeError: If *row* is not a status-bearing model (programming
            error at the call site).
    """
    transitions = _build_transitions()[_context_for(row)]
    return frozenset(transitions.get(row.status_id, ()))


def _status_labels():
    """Return ``{status_id: "Name (id)"}`` for every StatusEnum member.

    Used to compose the user-facing rejection message: the designed
    error fragments (closeout plan session 4) surface
    ``verify_transition``'s message directly in the UI, where a bare
    integer PK reads as noise.  The id stays in parentheses because it
    is still the debugging handle (and the message contract several
    tests pin).  An id outside the enum set falls back to the bare
    integer via the caller's ``.get`` default.

    Returns:
        dict mapping each seeded status id to its display label.
    """
    return {
        ref_cache.status_id(member): f"{member.value} ({ref_cache.status_id(member)})"
        for member in StatusEnum
    }


def verify_transition(row, new_status_id):
    """Raise ``ValidationError`` when the proposed transition is illegal.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being transitioned.
            Its class selects the workflow -- transfers exclude Credit
            and Received; see the module docstring -- and labels the
            exception message so the route layer can surface a precise
            400 to the user.  Its ``status_id`` is the current state.
        new_status_id: Integer PK of the proposed status.

    Raises:
        ValidationError: The new state is not in the set of legal
            successors for the current state, OR the current state
            is not a legal status for the row's workflow (defensive
            check against a corrupt row -- a non-enum ``status_id``
            or, for a transfer, a transaction-only status).  Successful
            return (no exception) signals that the caller may
            proceed to mutate ``status_id``.  Identity transitions
            return without raising so idempotent re-submission is
            always safe.
        TypeError: If *row* is not a status-bearing model (programming
            error at the call site).
    """
    context = _context_for(row)
    current_status_id = row.status_id
    transitions = _build_transitions()[context]
    if current_status_id not in transitions:
        # The row's current status is not a legal state for this
        # entity.  Refuse the transition rather than silently
        # accepting it -- a corrupt row should fail loudly so the
        # operator can investigate the source of the bad ID.
        logger.error(
            "Refusing %s status transition: current_status_id=%s "
            "is not a legal %s status.",
            context, current_status_id, context,
        )
        raise ValidationError(
            f"Invalid {context} status transition: current status "
            f"{current_status_id} is not a recognised {context} status."
        )

    allowed = transitions[current_status_id]
    if new_status_id not in allowed:
        logger.info(
            "Refusing %s status transition from %s to %s "
            "(allowed: %s).",
            context, current_status_id, new_status_id, sorted(allowed),
        )
        # Status NAMES lead the message because the designed error
        # fragments show it to the user verbatim; the ids stay in
        # parentheses as the debugging handle (and the contract the
        # route/service tests pin with ``str(id) in msg``).
        labels = _status_labels()
        raise ValidationError(
            f"Invalid {context} status transition from "
            f"{labels.get(current_status_id, current_status_id)} to "
            f"{labels.get(new_status_id, new_status_id)}."
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
