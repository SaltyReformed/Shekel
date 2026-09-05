"""The PATCH handler's PRE-MUTATION GATE CHAIN.

**Split out of :mod:`.mutations` at plan step X-au-j**, whose CC-payback
refusal pushed that module past ``max-module-lines``.  The cut is the seam
``_apply_field_updates``'s own docstring already names: these run BEFORE the
``setattr`` loop dirties the session, so an illegal transition reports ahead of
a finalised-field lock or an FK error and the row is left untouched on
rejection.  They share one error exit, which is what keeps the handler inside
pylint's return-count limit as the arc adds refusals to it.

**It holds TWO of the chain's three guards, and says which and why rather
than claiming the set.**  The third, ``_helpers._finalised_edit_response``,
stays where it is because :mod:`._shadow_mutations` calls it too -- moving it
here would make one route module import another's private leaf, which is the
cycle the split exists to avoid.  A first draft of this docstring said "in one
place", which an adversarial review measured as false.

Every guard here answers the same shape -- ``(txn, data) -> response | None``,
where ``None`` means *this gate passes*.  A route-tier guard is the
crafted-request and stale-form BACKSTOP for a rule the popover already obeys by
not rendering the control; the rule itself lives in the service or the model.

Boundary discipline: these read the request's already-schema-loaded ``data`` and
the loaded row, and write nothing.
"""

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.services.state_machine import verify_transition
from app.services.transaction_service import repays_card_spend

from app.routes.transactions._helpers import _error_transaction_response


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


def _reject_generated_due_date_edit(txn, data):
    """Refuse a due-date edit on a row a recurring definition generated.

    **A generated row's due date is its DEFINITION's** (plan step
    balance:X-au-e, developer 2026-09-03).  ``due_date`` is a member of
    ``recurrence_engine._amounts.DerivedRowFields``: generation computes it
    from the rule and the period, and the maintain splat rewrites it on every
    regeneration -- so an edit here never survived a later template save even
    before this step.  What X-au-e added is that the same date now resolves the
    row's PRICE through amount rule 3, so the field has gone from an edit that
    did not last to one that can leave a row no rule is able to price.

    **Clearing it 500ed the whole grid**, and that is measured rather than
    argued: ``routes/grid/page`` prices every row it loads with no status
    predicate and no handler -- ``AmountUnresolvable`` has five handlers in
    ``app/`` and none of them is on that path -- so ONE dateless derived row
    takes out the grid, the dashboard and the companion until the row is
    repaired in the database.  Reproduced on a clone of production: the
    identical act leaves 926 rows pricing cleanly before the cutover and
    raising after it.

    The popover no longer renders the control for such a row (it shows the
    date as text and names where the edit belongs), so this is the
    crafted-request and stale-form backstop this module exists to be.

    **Keyed on PRESENCE rather than on emptiness**, for the reason the payback
    gate states one function down: what a generated row may not accept is the
    FIELD.  Moving the date is refused as well as clearing it -- a moved date
    re-prices the row against a different point in its definition's series,
    silently, and the next regeneration puts it back.

    **An AD-HOC row is untouched.**  It owns its figure, so amount rule 1
    answers it off a column and no rule reads its date; clearing it there
    prices nothing wrongly, which is why the form still offers it.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if "due_date" not in data or txn.template_id is None:
        return None
    return _error_transaction_response(
        txn.id,
        "This instance's due date comes from its recurring transaction, "
        "which is also what prices it. Change the due day on the recurring "
        "transaction to move every instance, or type an amount here to make "
        "this month's figure its own.",
    )


def _reject_typed_payback_figure(txn, data):
    """Reject a hand-typed figure on a CC PAYBACK.

    **A payback's figure is not its own to state** (finding **N-252**): it
    repays the card spend of the row it names, so the figure is a fact about
    THAT row.  ``transaction_service.repays_card_spend``'s docstring carries
    the rule and what it cost -- ``$58.40`` on production payback 2590, edited
    to ``$123.18`` against ``$181.58`` of credit purchases and settled there,
    with no screen reporting the difference.

    Both render sites now withdraw the input (``budget_correctable`` on the
    full-edit popover and the inline quick-edit), so this is the
    crafted-request and stale-form BACKSTOP, which is what every guard in this
    module is.

    **It belongs HERE rather than beside the field writes, and an adversarial
    review moved it**: it reads ``credit_payback_for_id``, a stored column
    ``TransactionUpdateSchema`` cannot change, so nothing about it needs the
    post-loop row -- exactly the property ``_reject_tracking_on_income`` states
    for reading the stored type.  A first draft ran it after the ``setattr``
    loop and relied on ``_error_transaction_response``'s rollback to unstage
    the write; running it as a gate means the write is never staged at all.

    **Keyed on PRESENCE, not on the value.**  That reads stricter than the
    settled-actual refusal one module over (``data.get(...) is not None``) and
    resolves to the same set today: ``estimated_amount`` is not ``allow_none``,
    so ``_normalize_empty_inputs`` DROPS an empty submit instead of loading
    ``None``, and the key is present exactly when a figure was typed.  Written
    as a presence test anyway, because what this row may not accept is the
    FIELD -- a value test would start admitting ``None`` the day the schema
    gains ``allow_none``, and ``None`` on a source-less row is the state
    ``ck_transactions_amount_ownership`` forbids.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if "estimated_amount" not in data or not repays_card_spend(txn):
        return None
    return _error_transaction_response(
        txn.id,
        "This row repays what went on the card, so its figure has to stay "
        "equal to the card spend it repays. Change that instead -- a figure "
        "typed here would either be overwritten the next time the spend "
        "changes, or stay behind and stop matching the card.",
    )


def _reject_tracking_on_income(txn, data):
    """Reject enabling purchase tracking on an income row.

    Purchase tracking is expense-only.  The popover only renders the
    ``is_envelope`` checkbox for ad-hoc EXPENSE rows, so this is the crafted-
    request backstop -- the same layering every other route-tier guard here
    uses.  Checked against the STORED type because ``TransactionUpdateSchema``
    carries no ``transaction_type_id``, so a PATCH cannot change it.

    It is a function rather than an inline branch so it joins
    ``mutations._apply_regular_update``'s single pre-mutation gate chain: three
    guards sharing one error exit -- these two and
    ``_helpers._finalised_edit_response`` -- which is what keeps that handler
    inside pylint's return-count limit as the arc adds refusals to it.

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
