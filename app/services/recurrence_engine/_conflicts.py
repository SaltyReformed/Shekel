"""
Shekel Budget App -- Recurrence Engine: applying the owner's conflict decisions

:func:`resolve_conflicts`, called by the route layer after the owner answers
the chooser a :class:`~app.exceptions.RecurrenceConflict` raised.

**Nothing here deletes.**  "Keep" leaves the row untouched and "use" clears the
override / soft-delete flags and hands the row back to its definition, so a row
that reaches the chooser survives whichever branch the owner picks.

**"Use" WRITES NO FIGURE, and that is plan step X-au-e** (ruling **R-JD**).
Every generated transaction row is derived now, so "move this instance to the
template's amount" has no amount to move it to: the act is to stop overriding,
and the definition's own effective-dated series then prices the row as of its
OWN due date.  That is what closes finding **N-244** -- the old "use" wrote the
template's CURRENT ``default_amount`` onto a row whose due date could precede
the edit and cleared the flag that would have marked it, so a $100 history with
one row resolved to $120 read as THREE price changes where one occurred.  A
figure it does not write is a figure it cannot back-date.

**The DECISION survives even though the collision it once mediated cannot
occur** (ruling **R-JD**, developer 2026-09-03).  X-au-e's specification had it
deleted outright, on the ground that a hand-edited month owns its figure and a
regeneration that writes no figure cannot overwrite one.  Both halves of that
are true and neither reaches the offer: ``txn.is_override = False`` below is the
only writer in ``app/`` that clears that flag on a TEMPLATE-LINKED transaction,
and likewise its only per-row un-delete outside archiving and unarchiving the
whole template.

*The qualifier is load-bearing and a first draft omitted it.*  An AST census of
every writer of the two flags finds nine that clear one, not six: besides the
create paths, ``transfer_service._update`` and ``._restore``
clear ``is_override`` on a shadow through a VARIABLE
(``shadow.is_override = flag``), which a grep for the literal cannot see, and
``._restore`` un-deletes both legs.
Those doors reach SHADOWS only, and ``ck_transactions_one_pricing_link`` makes
``transfer_id`` exclusive with ``template_id`` -- so the conclusion holds by the
schema rather than by the count, which is the stronger ground anyway.

Deleting the decision would have stranded every
overridden row permanently OWN, 40 of them on the 2026-09-03 production clone,
with no route back to the definition until plan step X-au-h reworks the flag.

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
from app.services import posting_service
from app.services.amount_ownership import declare_derived
from app.services._recurrence_common import log_resource_access_denied
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED,
    log_event,
)

logger = logging.getLogger(__name__)



def resolve_conflicts(transaction_ids, action, user_id):
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.
    Each transaction is ownership-checked via its pay_period.user_id before
    any modification -- transactions not owned by ``user_id`` are silently
    skipped (defense-in-depth against IDOR).

    **It took a ``new_amount`` until plan step X-au-e** (ruling **R-JD**), and
    the parameter is gone rather than defaulted: "use" hands the row back to
    its definition and the definition prices it, so there is no figure for a
    caller to supply and no arm left that would read one.  The transfer twin
    ``transfer_recurrence.resolve_conflicts`` still takes one, because a
    generated transfer still stores its amount until plan step X-au-f -- which
    is why ``routes._recurrence_conflict_chooser.RecurrenceConflictKind`` asks
    the KIND whether "use" states a figure rather than assuming both do.

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' -- clear override/delete and hand the row
                         back to its definition.
                         'keep' -- leave the transaction unchanged.
        user_id:         The requesting user's ID.  Transactions not owned
                         by this user are skipped.
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

            # **A row whose definition is GONE cannot be handed back to
            # it** (ledger row **N-440**).  ``fk_transactions_template`` is ON
            # DELETE SET NULL, so a row can outlive its template carrying no
            # link -- and declaring such a row derived would write exactly the
            # state that has no rule able to price it:
            # ``_rule_within_definition`` answers TEMPLATE for a ``None``
            # template and ``_stated_amount`` then refuses in a money path.
            # The row keeps the figure it already owns, which is the only
            # answer left that is true.
            #
            # **UNREACHABLE from the route today, and the honest reason is not
            # the one a first draft gave.**  That comment said it covered "a
            # row that lost its template between the raise and this call" --
            # but the only way a row loses its template is that template's
            # hard delete, and the same delete makes the Apply POST 404 at
            # ``get_or_404`` before this function runs; the conflict set is
            # also built by selecting on ``template_id``, so no such id can
            # reach the allow-list.  What this is is the same DEFENCE IN DEPTH
            # the ownership check twenty lines up is: ``resolve_conflicts`` is
            # a published service entry, and a future caller assembling ids
            # some other way would otherwise write a row no rule can price.
            # Skipped rather than raised because it is a row to leave alone,
            # not a caller error, and it is counted in ``skipped_count``.
            if txn.template_id is None:
                skipped_count += 1
                continue

            txn.is_override = False
            txn.is_deleted = False
            # **The whole of "use the template's amount"** since plan step
            # X-au-e: the row stops owning a figure and DECLARES the definition
            # that prices it, which is the same statement
            # ``_amounts._derive_row_fields`` makes about a row generation
            # writes.  One producer of "what ownership does a row of this
            # definition take", so generation, the maintain splat and this
            # chooser cannot come to disagree (``CLAUDE.md`` rule 14).
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
        )
