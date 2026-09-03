"""
Shekel Budget App -- Recurrence Engine: applying the owner's conflict decisions

:func:`resolve_conflicts`, called by the route layer after the owner answers
the chooser a :class:`~app.exceptions.RecurrenceConflict` raised.

**Nothing here deletes.**  "Keep" leaves the row untouched and "use" clears the
override / soft-delete flags and re-applies the template's amount, so a row
that reaches the chooser survives whichever branch the owner picks.

**"The template's amount" is a statement of OWNERSHIP rather than a figure**
(plan step **X-au-d**).  A definition that STATES its price gives the row that
figure to own; one whose price is COMPUTED gives the row a declaration and no
figure at all.  Writing the figure unconditionally is finding **N-437**: it
hands a derived row back to its owner silently, where the two-column model it
was written under made the same write an ``IntegrityError``.

**It answers the OVERRIDDEN and SOFT-DELETED conflicts only, never a RETAINED
one.**  ``RecurrenceConflict.retained`` (plan step R10-a) names rows a maintain
pass left untouched because the owner has records against them, and there is no
keep-vs-use question to put to the owner about such a row: the pass already
took the only safe outcome.  The route reports them with
``flash_retained_notice`` and does not render the chooser for them, and
``apply_conflict_decisions`` allow-lists ``overridden | deleted``, so a
retained id cannot reach this module even from a crafted form.
"""
import logging

from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.exceptions import ValidationError
from app.services import posting_service, template_amount_service
from app.services.amount_ownership import declare_derived, state_own_amount
from app.services._recurrence_common import log_resource_access_denied
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED,
    log_event,
)

logger = logging.getLogger(__name__)



def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.
    Each transaction is ownership-checked via its pay_period.user_id before
    any modification -- transactions not owned by ``user_id`` are silently
    skipped (defense-in-depth against IDOR).

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' -- clear override/delete, apply new amount.
                         'keep' -- leave the transaction unchanged.
        user_id:         The requesting user's ID.  Transactions not owned
                         by this user are skipped.
        new_amount:      The new default amount (required if action='update').
    """
    if action == "keep":
        # Nothing to do -- the user wants to keep their overrides.
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts kept (no mutation)",
            user_id=user_id, action=action,
            transaction_id_count=len(transaction_ids),
        )
        return

    if action == "update":
        resolved_count = 0
        skipped_count = 0
        # The rows this pass actually restored, collected so the ledger
        # reconcile below runs over exactly them (see its comment).
        restored = []
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                skipped_count += 1
                continue

            # Ownership check: Transaction -> PayPeriod -> user_id.
            if txn.pay_period.user_id != user_id:
                # Cross-user request: emit the IDOR-detection event so
                # SOC tooling sees the probe.  ACCESS-category is the
                # right home for this -- the requester does not own
                # the row even though we silently skip it.
                log_resource_access_denied(
                    logger,
                    user_id=user_id,
                    model="Transaction",
                    pk=txn_id,
                    owner_id=txn.pay_period.user_id,
                )
                skipped_count += 1
                continue

            # Transfer shadow guard (CLAUDE.md Transfer invariant 4 / F-007).
            # Shadow rows (transfer_id IS NOT NULL) are owned by the transfer
            # service.  resolve_conflicts is reachable only from the
            # transaction-template regeneration flow, which never produces
            # shadow IDs in its conflict set; a shadow ID arriving here is
            # therefore an internal logic error or an attacker probe.
            # Mutating a shadow directly would desynchronise the parent
            # transfer's amount/status/period from its sibling shadow and
            # silently corrupt the user's balance projections.  Refuse.
            if txn.transfer_id is not None:
                log_event(
                    logger, logging.WARNING,
                    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED, BUSINESS,
                    "Refused to mutate transfer shadow via resolve_conflicts",
                    user_id=user_id,
                    transaction_id=txn_id,
                    transfer_id=txn.transfer_id,
                    action=action,
                )
                raise ValidationError(
                    "Cannot modify transfer shadow transactions via "
                    "resolve_conflicts.  Route transfer mutations through "
                    "transfer_service."
                )

            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                # **"Use the template's amount" is answered by the DEFINITION,
                # and after plan step X-au-d the answer is not always a
                # figure** (finding **N-437**).  The chooser offers one
                # decision -- keep your figure, or move to the template's --
                # and it is offered only where the template's own scalar
                # actually prices its rows: ``regenerate_or_conflict_chooser``
                # suppresses it for a salary-linked definition, whose
                # ``default_amount`` is vestigial.  This arm is still
                # REACHABLE for such a definition, because that route applies
                # submitted decisions before it consults that suppression, so
                # a crafted POST reaches here.  Writing the figure then handed
                # a DERIVED paycheck back to OWN in silence -- legal,
                # flushable, and a plan nothing recomputes again, which is
                # exactly the stale cache ruling R-FI deletes.
                #
                # ``template_amount_service.owns_its_amount`` is the app's one
                # eligibility test for a STATED price, shared with the write
                # door, the backfill and generation's own
                # ``_amounts._generated_amount_ownership``; a definition it
                # answers False for prices its rows by computing, so the row
                # goes back to DECLARING that definition and holds no figure.
                #
                # A row carrying no template at all is priced by no definition,
                # so a figure applied to it is simply its own -- the same
                # ``template is None`` arm ``cash_ledger._amount_source._stated_amount``
                # carries, and for the same reason: asking the predicate about
                # ``None`` is an ``AttributeError`` where every other
                # unanswerable shape here is a designed outcome.
                if (
                    txn.template is None
                    or template_amount_service.owns_its_amount(txn.template)
                ):
                    state_own_amount(txn, new_amount)
                else:
                    declare_derived(txn, AmountSourceEnum.TEMPLATE)
            restored.append(txn)
            resolved_count += 1
        db.session.flush()
        # **Restoring a row restores its purchases' cash legs** (plan step
        # X-f3b, ruling **R-FM**).  This loop un-deletes rows and may re-price
        # them, and both moves are ledger acts now that a PROJECTED envelope can
        # hold postings: ``delete_transaction`` reversed a soft-deleted row's
        # family on the way out, so without this the read fold re-acquires a
        # movement the ledger no longer holds.  Idempotent and empty-handed for
        # a row whose family never posted, which is every other row here.
        for txn in restored:
            posting_service.sync_transaction_postings(
                txn, settled=txn.status.is_settled,
            )
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts resolved (update)",
            user_id=user_id, action=action,
            resolved_count=resolved_count,
            skipped_count=skipped_count,
            new_amount=str(new_amount) if new_amount is not None else None,
        )
