"""The PATCH handler's PRE-MUTATION GATE CHAIN, in one place.

**Split out of :mod:`.mutations` at plan step X-au-i**, the leaf whose own
refusal pushed that module past ``max-module-lines``.  The cut is the one
``_apply_regular_update``'s docstring already names: these run BEFORE the
``setattr`` loop dirties the session, so an illegal transition reports before a
finalised-field lock or an FK error and the row is left untouched on rejection.
They share one error exit, which is what keeps the handler inside pylint's
return-count limit as the arc adds refusals to it.

Every guard here answers the same shape: ``(txn, data) -> response | None``,
where ``None`` means *this gate passes*.  A route-tier guard is the
crafted-request and stale-form BACKSTOP for a rule the popover already obeys by
not rendering the control; the rule itself lives in the service or the model.

Boundary discipline: these read the request's already-schema-loaded ``data`` and
the loaded row, and write nothing.
"""

import logging

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.services.state_machine import verify_transition

from app.routes.transactions._helpers import _error_transaction_response

logger = logging.getLogger(__name__)

def _resolve_status_change(txn, data):
    """Validate a PATCH status transition early, before any column is mutated.

    Runs the status-dependent guards for a regular (non-shadow)
    :func:`update_transaction` before the ``setattr`` loop dirties the session:
    verifies the requested transition through the state machine (F-161 / C-21)
    and blocks the Credit status on purchase-tracking transactions (credit is
    per-entry, scope doc 5.2).  Doing it here gives the precise 400 precedence
    (an illegal transition reports before a finalised-field lock or an FK error)
    and leaves the row untouched on rejection.  ``settled_on`` is NOT decided here:
    the status seam (:func:`status_seam.apply_status_change`, invoked
    once the field is applied) owns the stamp/clear and re-runs this same
    verification as the single source of truth -- this early call exists purely
    for error precedence.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        ``None`` when the status change is allowed (or absent), or a Flask
        ``(msg, 400)`` response tuple the caller returns directly when a guard
        rejects the request.
    """
    if "status_id" not in data:
        return None

    # Verify the transition BEFORE any other status-dependent work.  An illegal
    # transition -- for example settled -> projected -- short-circuits the
    # request with a 400 and leaves the row untouched.  Audit reference: F-161 /
    # commit C-21 of the 2026-04-15 security remediation plan.
    try:
        verify_transition(txn, data["status_id"])
    except ValidationError as exc:
        return _error_transaction_response(txn.id, str(exc))

    # Block Credit status on entry-capable transactions -- credit
    # handling is per-entry, not per-transaction (scope doc section 5.2).
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if data["status_id"] == credit_id and txn.tracks_purchases:
        return _error_transaction_response(
            txn.id,
            "Cannot set Credit status on transactions with individual "
            "purchase tracking. Use entry-level credit instead.",
        )

    return None


def _reject_tracking_on_income(txn, data):
    """Reject enabling purchase tracking on an income row.

    Purchase tracking is expense-only.  The popover only renders the
    ``is_envelope`` checkbox for ad-hoc EXPENSE rows, so this is the crafted-
    request backstop -- the same layering every other route-tier guard here
    uses.  Checked against the STORED type because ``TransactionUpdateSchema``
    carries no ``transaction_type_id``, so a PATCH cannot change it.

    It is a function rather than an inline branch so it joins
    :func:`_apply_regular_update`'s single pre-mutation gate chain: three guards
    sharing one error exit, which is what keeps that handler inside pylint's
    return-count limit as the arc adds refusals to it.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if data.get("is_envelope") and txn.is_income:
        return _error_transaction_response(
            txn.id, "Purchase tracking is only available for expenses.",
        )
    return None
