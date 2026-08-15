"""
Shekel Budget App -- Transaction Entry Service

CRUD operations, validation, and computation for individual purchase
entries on entry-capable transactions.  This service is the foundation
consumed by the balance calculator (Commit 3), entry credit workflow
(Commit 4), mark-paid logic (Commit 5), and all entry UI (Commits
7, 8, 10).

**The OUTSTANDING SET left this module at plan step X-f2-c1** for
:mod:`app.services.reconcile_service`: "which of these had the bank taken by
the day you asserted a balance for" stopped being a question about ENTRIES
when ruling R-EW widened it to bills, envelope closes and transfer shadows.
That module carries the rule and the measurement that forced the cut.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User
from app import ref_cache
from app.enums import RoleEnum
from app.exceptions import NotFoundError, ValidationError
from app.services import posting_service
from app.services.cash_ledger import StatementCoverage, coverage_for
from app.services.entry_credit_workflow import sync_entry_payback
from app.utils.balance_predicates import is_archived, is_cancelled
# ``is_credit`` from balance_predicates collides with the
# ``is_credit: bool`` keyword argument on this module's
# ``create_entry`` / ``update_entry`` functions.  Aliasing the
# predicate keeps both the helper accessible and the public
# function signatures stable.
from app.utils.balance_predicates import is_credit as txn_is_credit
from app.utils.dates import display_today
from app.utils.entry_partition import partition_entries
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_UPDATED,
    log_event,
)
from app.utils.money import percent_complete

logger = logging.getLogger(__name__)

# Fields that can be updated on an entry via update_entry().
_UPDATABLE_FIELDS = frozenset({
    "amount", "description", "purchased_on", "settled_on", "is_credit",
})


def _reject_archived_parent(txn: Transaction) -> None:
    """Refuse an entry mutation against an ARCHIVED (terminal ``Settled``) row.

    **Finding N-229's door half.**  A row in ``StatusEnum.SETTLED`` is a
    historical record: the state machine gives it no outgoing edge but identity,
    so what the books say it cost can never be reopened and re-derived.
    Recording, editing or deleting a purchase against one is therefore either
    inert or a retroactive rewrite of history, and neither is what the user
    meant -- so all three doors refuse it, rather than the create door alone.

    It is the ARCHIVE status, not the settled band: a Paid envelope must keep
    accepting late-posting purchases, which is the whole reason
    :func:`_resync_settled_envelope` exists.  See
    :func:`app.utils.balance_predicates.is_archived` for why that distinction
    gets a name.

    Args:
        txn: The parent transaction the entry belongs (or would belong) to.

    Raises:
        ValidationError: When *txn* is in the terminal ``Settled`` status.
    """
    if is_archived(txn):
        raise ValidationError(
            f"Transaction {txn.id} is archived (Settled); its purchases are "
            "a historical record and cannot be added to, changed, or removed."
        )


def _resync_settled_envelope(txn: Transaction) -> None:
    """Re-derive a settled envelope's actual and its postings after an entry change.

    **ONE predicate for what used to be two, and the split was finding N-229.**
    This was ``_update_actual_if_paid`` (gated on ``is_done`` -- exactly Paid)
    followed by ``_resync_postings_if_settled`` (gated on the settled BAND), so
    the two halves of one act graded the same row differently: on any settled
    status that is not Paid the actual was NOT recomputed while the postings
    WERE reconciled -- to the stale figure.  The books moved and
    the figure they were derived from did not.  Two predicates for one act is
    two answers; the band is the right one, because the act is "this row's money
    has moved and its purchases just changed".

    Both halves, in the order they must run:

      1. ``actual_amount = sum(entries)`` -- the late-posting case (scope doc
         section 4.2: once entries exist, the entry sum takes precedence over
         any manually entered actual).  Skipped for an EMPTY entry list, which
         is the deliberate part: deleting the last purchase from a Paid envelope
         leaves the previous figure rather than silently rewriting a settled
         row's cost to ``$0.00``.
      2. the ledger reconcile (Build-Order Step 3), which must read the actual
         act 1 just wrote.  An entry mutation on a settled envelope changes its
         confirmed cash effect (``owned_contribution - Sigma(credit entries)``):
         adding a debit purchase grows the checking outflow, flipping an entry
         to or from credit moves the credit-excluded portion, deleting one
         shrinks it.

    Gated whole on the settled band because a Projected envelope -- the common
    case for entry edits -- has no postings and its actual is not yet a fact, so
    both acts would be wasted.  Recording a purchase's own ``settled_on`` DOES
    reach here through :func:`update_entry` and is a no-op in value: the
    confirmed cash effect does not contain the posting day, so the sync
    reconciles to the same target.

    Does NOT commit -- the calling service function owns the session boundary
    (the reconcile flushes but does not commit, matching this module's
    contract).

    Args:
        txn: The parent envelope transaction whose entries changed.  Its
            ``status`` relationship is read to gate both acts.
    """
    if not txn.status.is_settled:
        return
    if txn.entries:
        txn.actual_amount = compute_actual_from_entries(txn.entries)
    posting_service.sync_transaction_postings(txn, settled=True)


def resolve_owner_id(user_id: int) -> int:
    """Return the data-owning user_id.

    For owner accounts, returns user_id unchanged.  For companion
    accounts, returns the linked_owner_id (the owner whose budget
    data the companion has access to).

    Args:
        user_id: The ID of the requesting user.

    Returns:
        int -- the ID of the user who owns the budget data.

    Raises:
        NotFoundError: If user_id does not correspond to an existing user.
        ValidationError: If a companion user has no linked_owner_id
            (indicates a data integrity issue).
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id == companion_role_id:
        if user.linked_owner_id is None:
            raise ValidationError(
                f"Companion user {user_id} has no linked owner. "
                "This is a data integrity issue -- contact the administrator."
            )
        return user.linked_owner_id
    return user.id


# Backward-compatible alias -- existing tests reference the private name.
_resolve_owner_id = resolve_owner_id


@dataclass(frozen=True)
class EntryDetails:
    """The content of a purchase entry -- the add-purchase form's inputs.

    The user-supplied fields of a :class:`TransactionEntry` (what was
    bought, how much, when, and whether paid by credit card), bundled so
    ``create_entry`` takes them as one cohesive argument distinct from the
    routing/ownership context (the parent transaction and the acting user).

    Fields:
        amount:      Positive Decimal for the purchase amount.
        description: Store name or brief note (1--200 chars).
        purchased_on: Date the purchase HAPPENED.  Backdating is ordinary; a
            date after the user's today is refused (ruling R-M, see
            :func:`_reject_future_purchase_date`).
        is_credit:   Whether this was paid with a credit card.

    ``settled_on`` is deliberately not here.  The day the BANK took the money
    is an observation, and at the moment a purchase is recorded there is
    nothing to have observed; it arrives later, through
    :func:`app.services.reconcile_service.record_settled_days` at a balance true-up or through
    :func:`update_entry`.
    """

    amount: Decimal
    description: str
    purchased_on: date
    is_credit: bool = False


def _reject_future_purchase_date(purchased_on: date) -> None:
    """Refuse a purchase dated after the user's today (ruling R-M).

    The ONE statement of "a purchase entry records a purchase that HAPPENED",
    shared by both write doors (:func:`create_entry` and :func:`update_entry`)
    so the boundary cannot hold on one and not the other -- the same
    both-doors-one-derivation shape ruling R-C's origination guard uses.

    **Why the source and not the reader** (ruling R-M, whose work shipped at
    plan step S1-c, so it is recorded in
    ``docs/audits/balance_architecture/archive/phase_x_as_built_2026-08-04.md``
    Section 3 rather than in the live README).  A future
    purchase is not merely odd data: it moves a rendered balance.  The
    projection holds back
    ``max(estimated - settled_debit - credit, outstanding_debit)`` for a
    still-projected envelope, so an entry dated ahead changes today's balance in
    EITHER direction depending only on its credit flag -- measured on the live
    Groceries envelope (``$780.00`` budgeted, ``$60.55`` held back): a
    ``$150.00`` future debit takes the reservation to ``$150.00`` through the
    ``max()`` floor (``-$89.45`` on the balance), while the same amount ticked
    CC takes it to ``$0.00`` (``+$60.55``).  Refusing it here is what lets the
    reservation's ``as_of`` window -- the parameter the calendar passed and the
    grid did not, which was the divergence itself -- stay DELETED at plan step
    X-c2 rather than ruled.

    **This guard survived the 2026-08-01 re-ruling of R-M, and it survived by
    moving onto the column it was always about.**  R-M and ruling R-DH (e) had
    been defining ONE column two ways -- "the day the purchase happened, never
    in the future" and "the day the money hit the account, one to two days
    later for a debit card".  The column split rather than the guard bending:
    ``purchased_on`` keeps this boundary intact and ``settled_on`` carries the
    posting day, so the forward case the developer needs to express has its own
    field and this one no longer has to admit a forecast.  Widening THIS bound
    was rejected: the column would then mean "purchase day" on some rows and
    "posting day" on others, with nothing in the schema recording which, and
    the remaining-budget figure and the out-of-period warning both read it as
    the purchase day.

    Backdating stays fully allowed, and is used: a purchase logged days after it
    happened, or one dated into the previous pay period, is ordinary (the real
    2026-05-21 Groceries row carries entries from 05-18).  A purchase you have
    not made yet is the envelope's remaining BUDGET, which the row already
    models.

    The comparison is against :func:`~app.utils.dates.display_today` -- the
    user's wall-clock date, not the server's UTC one -- because
    ``purchased_on`` is a civil date the user types on their own clock.
    Judging it in UTC would refuse a legitimate same-day entry for the hours
    the two frames disagree.

    Args:
        purchased_on: The civil date the caller wants the entry to carry.

    Raises:
        ValidationError: When *purchased_on* is after the user's today.  The
            message carries both dates so the surface can show what was
            rejected and what the boundary was.
    """
    today = display_today()
    if purchased_on > today:
        raise ValidationError(
            f"A purchase entry records a purchase that has already happened, "
            f"so its date cannot be in the future: {purchased_on.isoformat()} "
            f"is after today ({today.isoformat()}).  Log the purchase when it "
            f"happens; money you have not spent yet is already held back by "
            f"this row's remaining budget.  If the purchase is made but has "
            f"not reached your bank yet, that is what the posting date is for."
        )


def _reject_settled_before_purchase(
    purchased_on: date, settled_on: date | None,
) -> None:
    """Refuse a posting day earlier than the purchase it belongs to.

    Money cannot leave the account before it was spent.  The database carries
    the same rule as ``ck_transaction_entries_settled_not_before_purchase`` and
    that constraint is the backstop; this is the door, so the user gets a
    message naming both dates instead of a 500 from an ``IntegrityError``.

    It is checked against the RESULTING pair rather than the submitted one,
    because either side can move: editing a purchase's date backwards past a
    posting day already recorded breaks the invariant just as surely as
    entering an early posting day does.

    There is deliberately NO upper bound.  A posting day after today is a
    legitimate statement -- "I bought this and my bank has not taken it yet" --
    and that is exactly the case ruling R-DH (e) exists for and ruling R-M
    could not express while one column carried both facts.  Any "at most N days
    ahead" ceiling would be a constant nobody can justify, and a wrong forward
    date is visible on the entry row and self-corrects at the next true-up.

    Args:
        purchased_on: The day the purchase was made, after any pending update.
        settled_on: The day the bank took it, after any pending update, or
            ``None`` when it has not been observed.

    Raises:
        ValidationError: When *settled_on* precedes *purchased_on*.
    """
    if settled_on is not None and settled_on < purchased_on:
        raise ValidationError(
            f"A purchase cannot reach your bank before you make it: "
            f"{settled_on.isoformat()} is earlier than the purchase date "
            f"({purchased_on.isoformat()}).  Correct whichever of the two is "
            f"wrong."
        )


def create_entry(
    transaction_id: int,
    user_id: int,
    details: EntryDetails,
) -> TransactionEntry:
    """Create a new purchase entry against a transaction.

    Validates ownership (including companion resolution), entry
    capability, transfer guard, expense-only guard, and status guard
    before creating the entry.

    Args:
        transaction_id: Parent transaction ID.
        user_id: The creating user's ID (owner or companion).
        details: :class:`EntryDetails` -- the purchase content (amount,
            description, purchased_on, is_credit).

    Returns:
        The newly created TransactionEntry (flushed, id available).

    Raises:
        NotFoundError: Transaction not found or not accessible by this
            user.
        ValidationError: Transaction not entry-capable, is a transfer,
            is income, or has a blocked status (Cancelled, Credit, or the
            terminal Settled -- see :func:`_reject_archived_parent`).
    """
    owner_id = resolve_owner_id(user_id)

    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # Ownership: verify via pay period (security response rule: 404).
    if txn.pay_period.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # Entry-capable: purchase tracking must be enabled, via the template
    # (template-generated rows) or the row's own is_envelope flag (ad-hoc
    # rows).  Resolved by Transaction.tracks_purchases.
    if not txn.tracks_purchases:
        raise ValidationError(
            "This transaction does not support individual purchase tracking. "
            "Enable 'Track individual purchases' on the transaction "
            "or its template first."
        )

    # Transfer guard (mirrors credit_workflow.py line 59).
    if txn.transfer_id is not None:
        raise ValidationError("Cannot add entries to transfer transactions.")

    # Expense-only guard.
    if txn.is_income:
        raise ValidationError(
            "Cannot add purchase entries to income transactions."
        )

    # Status guard: CANCELLED and CREDIT transactions cannot accept entries.
    # CANCELLED is excluded from balance -- adding entries makes no sense.
    # CREDIT is blocked for entry-capable templates (OQ-10) -- credit
    # handling happens at the entry level, not the transaction level.
    # Routed through the centralized per-status predicates
    # (D6-09 / MED-02) so the two guards share one definition with
    # every other ``status == cancelled`` / ``status == credit``
    # comparison in the project.
    if is_cancelled(txn):
        raise ValidationError(
            "Cannot add entries to a cancelled transaction."
        )
    if txn_is_credit(txn):
        raise ValidationError(
            "Cannot add entries to a transaction with Credit status. "
            "Entry-capable transactions handle credit at the entry level."
        )
    # ARCHIVED (terminal Settled) is the third refusal, and it is finding
    # **N-229**: it used to be accepted, persisted, and silently inert -- the
    # actual was not recomputed (that half graded ``is_done``) while the
    # postings were reconciled anyway (that half graded the settled BAND).
    _reject_archived_parent(txn)

    # Content guard, after the ownership and transaction guards so a
    # non-owner still gets the 404 rather than a validation message that
    # confirms the row exists (ruling R-M; see
    # _reject_future_purchase_date).
    _reject_future_purchase_date(details.purchased_on)

    entry = TransactionEntry(
        transaction_id=transaction_id,
        # The parent's account, written explicitly rather than derived at flush
        # time.  ``fk_transaction_entries_parent_account`` refuses any other
        # value, so this line cannot be silently wrong -- it can only be absent,
        # and absent is a NOT NULL violation.
        account_id=txn.account_id,
        user_id=user_id,
        amount=details.amount,
        description=details.description,
        purchased_on=details.purchased_on,
        is_credit=details.is_credit,
    )
    db.session.add(entry)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_CREATED, BUSINESS,
        "Transaction entry created",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry.id,
        amount=str(details.amount),
        is_credit=details.is_credit,
    )

    sync_entry_payback(transaction_id, owner_id)
    _resync_settled_envelope(txn)

    return entry


def update_entry(entry_id: int, user_id: int, **kwargs) -> TransactionEntry:
    """Update an existing entry.

    Allowed fields: amount, description, purchased_on, settled_on, is_credit.
    Re-validates ownership through the entry's parent transaction.

    ``settled_on`` is updatable HERE and not at the create door: it records the
    day the bank took the money, which is an observation the user makes later
    (off a statement, or by ticking the purchase at a balance true-up through
    :func:`app.services.reconcile_service.record_settled_days`).  Passing
    ``None`` clears it, putting the purchase back among the outstanding ones.

    Args:
        entry_id: The entry to update.
        user_id: The requesting user's ID (owner or companion).
        **kwargs: Fields to update (must be a subset of allowed fields).

    Returns:
        The updated TransactionEntry.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If no valid fields provided, unknown fields are
            passed, or the parent row is archived
            (:func:`_reject_archived_parent`).
    """
    unknown = set(kwargs) - _UPDATABLE_FIELDS
    if unknown:
        raise ValidationError(
            f"Cannot update fields: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_UPDATABLE_FIELDS))}."
        )

    valid_updates = {k: v for k, v in kwargs.items() if k in _UPDATABLE_FIELDS}
    if not valid_updates:
        raise ValidationError("No fields to update.")

    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # An archived row's purchases are history (finding **N-229**).  Checked
    # after ownership so a non-owner still gets the 404 rather than a message
    # confirming the row exists, exactly as the create door orders its guards.
    _reject_archived_parent(entry.transaction)

    # The same boundary the create door applies, and only when the caller is
    # actually moving the date -- a partial update that leaves ``purchased_on``
    # alone must not be refused for a value it is not setting (ruling R-M).
    if "purchased_on" in valid_updates:
        _reject_future_purchase_date(valid_updates["purchased_on"])
    # The pair invariant is checked on the RESULT, not the submission: a
    # partial update moves one side against a stored other side, and both
    # directions can break it.
    _reject_settled_before_purchase(
        valid_updates.get("purchased_on", entry.purchased_on),
        valid_updates.get("settled_on", entry.settled_on),
    )

    moved_the_posting_day = (
        "settled_on" in valid_updates
        and valid_updates["settled_on"] != entry.settled_on
    )
    for field, value in valid_updates.items():
        setattr(entry, field, value)
    # **Moving the posting day RELEASES the clearing fact** (plan step X-f3a-1,
    # ruling **R-FL**).  ``reconciled_by_id`` records that a named statement was
    # seen to show this purchase ON that day; a user moving the day is
    # correcting the observation, not confirming it, and the two facts must not
    # be left to disagree.
    #
    # **Releasing rather than refusing is deliberate, and the alternative was
    # measured.**  A link whose day the date rule would not pick is
    # UNRENDERABLE while an assertion resets the ledger -- the fold emits the
    # purchase on its settle day and the correction on the statement's, so the
    # balance stops equalling what the user asserted (see
    # ``StatementCoverage._recorded_anchor_id`` for the theorem and the
    # production figure).  Refusing the edit would trap a user who is doing
    # exactly what the panel's own copy asks -- *"correct it if your statement
    # shows a different day"* -- so the day wins and the observation is dropped
    # back to UNKNOWN, where the date rule answers it exactly as it did before
    # any of this shipped.  Re-ticking on the next statement records it again.
    #
    # It fires on ANY move, including to ``None``, which is also what
    # ``ck_transaction_entries_cleared_needs_settle_day`` requires.
    if moved_the_posting_day:
        entry.reconciled_by_id = None
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_UPDATED, BUSINESS,
        "Transaction entry updated",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=entry.transaction_id,
        entry_id=entry_id,
        # Sorting fields_changed keeps the structured log deterministic
        # so dashboards can group by it without ordering noise.
        fields_changed=sorted(valid_updates.keys()),
    )

    sync_entry_payback(entry.transaction_id, owner_id)
    _resync_settled_envelope(entry.transaction)

    return entry


def delete_entry(entry_id: int, user_id: int) -> int:
    """Hard-delete an entry.

    Re-validates ownership before deleting.  Returns the parent
    transaction_id so the caller (e.g. entry credit workflow in
    Commit 4) can sync the CC Payback amount.

    Args:
        entry_id: The entry to delete.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        int -- the parent transaction_id.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If the parent row is archived
            (:func:`_reject_archived_parent`).
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # An archived row's purchases are history (finding **N-229**), and removing
    # one would rewrite what the books already say the row cost.
    _reject_archived_parent(entry.transaction)

    txn = entry.transaction
    transaction_id = entry.transaction_id
    db.session.delete(entry)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_DELETED, BUSINESS,
        "Transaction entry deleted",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry_id,
    )

    sync_entry_payback(transaction_id, owner_id)
    _resync_settled_envelope(txn)

    return transaction_id


def get_entries_for_transaction(
    transaction_id: int, user_id: int,
) -> list[TransactionEntry]:
    """Return all entries for a transaction, ordered by purchased_on ASC.

    Validates ownership before returning entries.

    Args:
        transaction_id: The parent transaction ID.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        List of TransactionEntry objects ordered by purchased_on ASC.

    Raises:
        NotFoundError: Transaction not found or not accessible.
    """
    owner_id = resolve_owner_id(user_id)

    txn = db.session.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found.")
    if txn.pay_period.user_id != owner_id:
        raise NotFoundError(f"Transaction {transaction_id} not found.")

    # The entries relationship is ordered by ``purchased_on`` via the
    # ``order_by`` on ``Transaction.entries`` -- the BUDGET clock, which is
    # the order a user reads their purchases in.
    return list(txn.entries)


def compute_entry_sums(
    entries: list[TransactionEntry],
) -> tuple[Decimal, Decimal]:
    """Compute (sum_debit, sum_credit) from a list of entries.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Tuple of (sum_debit, sum_credit) as Decimals.
    """
    debit_entries, credit_entries = partition_entries(entries)
    sum_debit = sum((e.amount for e in debit_entries), Decimal("0"))
    sum_credit = sum((e.amount for e in credit_entries), Decimal("0"))
    return sum_debit, sum_credit


def build_entry_sums_dict(
    transactions: list,
) -> dict[int, dict]:
    """Build a {txn_id: sums_dict} mapping for transactions with entries.

    Used by grid routes and HTMX cell-render endpoints to pre-compute
    entry aggregates for the cell template.  Only transactions with
    non-empty entries are included in the result.

    The dict carries ``remaining`` and ``over_budget`` so the grid
    cell template renders without inline Jinja arithmetic
    (E-16 / MED-04).  ``remaining`` is computed via
    :func:`compute_remaining` (the E-21 declared base
    ``estimated_amount`` minus the sum of all entries), so the cell's
    over-budget styling is driven by the same single rule that the
    dashboard bill row sees via ``bill.entry_remaining``.

    Pure function -- no database access beyond what was already loaded
    on the Transaction objects (expects entries to be accessible, either
    via eager load or lazy access).

    Args:
        transactions: List of Transaction objects with entries accessible.

    Returns:
        dict mapping transaction ID to {"debit": Decimal, "credit": Decimal,
        "total": Decimal, "count": int, "remaining": Decimal,
        "over_budget": bool, "pct": Decimal}.  Empty dict if no transactions
        have entries.  ``pct`` is the entries-to-estimate ratio clamped
        to [0, 100] via :func:`pct_complete`; it drives the mobile
        progress-bar's ``data-progress-pct`` attribute on the unified
        ``render_row_card`` macro per mobile-first v3 plan Commit 13.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        if txn.entries:
            debit, credit = compute_entry_sums(txn.entries)
            total = debit + credit
            estimated = Decimal(str(txn.estimated_amount))
            remaining = compute_remaining(estimated, txn.entries)
            result[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": total,
                "count": len(txn.entries),
                "remaining": remaining,
                "over_budget": remaining < Decimal("0"),
                "pct": pct_complete(total, estimated),
            }
    return result


def build_entry_lists_dict(
    transactions: list,
) -> dict[int, dict]:
    """Build a {txn_id: entry_list_data} mapping for envelope transactions.

    Pre-computes the entry-list rendering inputs that
    ``_render_entry_list`` in ``app/routes/entries.py`` produces per
    HTMX request, so the mobile grid macro can render entries inline
    on the initial grid response instead of lazy-loading them one
    request per envelope card.  With 6 visible pay periods and ~10
    envelope templates each, the lazy-load fan-out is ~60 parallel
    GETs on the entries endpoint, which exceeds the
    ``RATELIMIT_DEFAULT`` ceiling of ``30 per minute`` and leaves the
    over-limit cards stuck on the loading spinner.  Server-side
    rendering eliminates the fan-out entirely.

    Only purchase-tracking rows (``txn.tracks_purchases`` -- a template
    with ``is_envelope`` set, or an ad-hoc row carrying its own
    ``is_envelope`` flag) get an entry, matching the macro's guard for
    whether to render the inline entries section.  Non-tracking
    transactions are silently skipped.

    Expects ``entries`` and ``template`` eager-loaded on the
    Transaction objects.  **It stopped being a PURE function on
    2026-08-13**: the reconciled indicator needs each account's
    clearing rule, which is a read.  One indexed read per distinct
    account is the cost, and the alternative -- taking the rules as
    an argument -- is the defect this change fixes, one tier up (an
    argument a caller can get wrong is a defect, not a contract).

    Args:
        transactions: List of Transaction objects with ``entries`` and
            ``template`` accessible.

    Returns:
        dict mapping envelope transaction ID to one
        :func:`entry_list_view` -- the WHOLE context
        ``grid/_transaction_entries.html`` renders an envelope from.

        Empty dict when no transaction in the input has an envelope
        template.
    """
    result: dict[int, dict] = {}
    # One clearing rule per distinct ACCOUNT, not per transaction: a grid render
    # passes every visible envelope of every account, and ``coverage_for`` is
    # one indexed read per account.  Memoising here keeps the count at the
    # number of accounts on screen (six on the developer's grid) instead of the
    # number of envelopes (~sixty), which is the fan-out this function exists to
    # remove.  It loads ROWS where the boundary it replaced was one ``MAX``, and
    # that is what asking WHICH statement cleared a line costs (ruling R-FL);
    # the memoisation is what keeps the cost per account rather than per row.
    coverages: dict[int, StatementCoverage] = {}
    for txn in transactions:
        if not txn.tracks_purchases:
            continue
        if txn.account_id not in coverages:
            coverages[txn.account_id] = coverage_for(txn.account_id)
        result[txn.id] = entry_list_view(
            txn, list(txn.entries), coverages[txn.account_id],
        )
    return result


def entry_list_view(
    txn: Transaction,
    entries: list[TransactionEntry],
    coverage: StatementCoverage,
) -> dict:
    """Return the WHOLE derived context one envelope's entry list renders from.

    **The ONE producer of that context, and it exists because a caller-built
    one was measured wrong.**  ``grid/_transaction_entries.html`` was assembled
    independently by the HTMX refresh (``routes/entries.py``) and by the grid
    macro (via :func:`build_entry_lists_dict`), and only the refresh supplied
    ``reconciled_ids`` -- so on every INITIAL render of the grid, the mobile
    card, the companion view and the full-edit popover, ``entry.id in
    reconciled_ids`` asked a Jinja ``Undefined``, which answers ``False``
    silently.  Every reconciled purchase read *"Not yet seen on a statement, so
    the budget is still held back"* while the projection had already released
    its reservation: the screen contradicted the number beside it, on 9 of 9
    such purchases on the 2026-08-13 production clone (`$640.70`).

    Two callers assembling one template's context is the shape; a forgotten key
    was the instance.  Returning the whole context from one place makes the
    omission unrepresentable at the service tier -- the route splats this
    mapping rather than naming its keys.

    Args:
        txn: The envelope transaction being rendered.  Its
            ``estimated_amount`` sets the remaining figure and its pay period
            bounds the out-of-period warning.
        entries: The transaction's entries, already loaded and ordered by
            ``purchased_on``.  Taken as an argument rather than read off *txn*
            because the two callers load them differently -- the route through
            the owner-scoped :func:`get_entries_for_transaction`, the grid off
            an eager-loaded relationship -- and neither may lose its scoping to
            share this derivation.
        coverage: The account's clearing rule
            (:func:`app.services.cash_ledger.coverage_for`).  An ARGUMENT so a
            caller rendering many envelopes resolves it once per account;
            :func:`build_entry_lists_dict` memoises it.

    Returns:
        The five keys the template consumes:

          - ``entries``: the list as given.
          - ``remaining`` (Decimal): estimated_amount minus the sum of all
            entries (debit + credit), via :func:`compute_remaining`.
          - ``out_of_period_ids`` (set[int]): entry IDs whose ``purchased_on``
            falls outside the parent pay period, surfacing the OP-4
            date-awareness warning.
          - ``reconciled_ids`` (set[int]): entry IDs a declared balance already
            contains, from the SHARED rule (ruling **R-FL**) rather than from a
            stored flag or a second date comparison, so what the row shows and
            what the projection held back cannot disagree.
          - ``reconciled_through`` (date | None): the account's latest statement
            day, for the tooltip that renders it -- a CAPTION, and the tooltip's
            sentence stays true for a purchase an earlier statement cleared,
            because a line inside that balance is inside this one too.  WHICH
            entries are reconciled is decided HERE, in Python; the template
            renders the answer and never re-derives it.
    """
    return {
        "entries": entries,
        "remaining": compute_remaining(txn.estimated_amount, entries),
        "out_of_period_ids": {
            e.id for e in entries
            if not check_purchase_date_in_period(e.purchased_on, txn)
        },
        "reconciled_ids": {
            e.id for e in entries if coverage.is_cleared(e)
        },
        "reconciled_through": coverage.latest_statement_day,
    }


def compute_remaining(
    estimated_amount: Decimal,
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute remaining budget: estimated_amount - sum of ALL entries.

    Uses the sum of ALL entries regardless of payment method (debit +
    credit) because the remaining balance represents budget consumption,
    not checking impact.  Negative values indicate overspending.

    Per E-21 (audit MED-03 / F-028 / F-056) the budget base for an
    entry-tracked bill row is ``estimated_amount`` unconditionally --
    never ``actual_amount`` and never status-dependent.  This is why
    the signature takes ``estimated_amount`` directly rather than the
    whole ``Transaction``: the base cannot be switched on at runtime;
    callers that want to display "remaining" against a different base
    are out of contract and must compute it themselves.  The dashboard
    bill row, the companion entry data builder, and the entries
    partial all pass ``txn.estimated_amount`` (verified) so they
    share one declared base with the row's amount cell and
    over-budget flag.

    Pure function -- no database access.

    Args:
        estimated_amount: The transaction's budgeted amount -- the
            E-21 declared base for the row's plan-vs-actual figures.
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- the remaining budget (negative means overspent).
    """
    total_spent = sum((e.amount for e in entries), Decimal("0"))
    return estimated_amount - total_spent


def pct_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute percent complete, clamped to [0, 100].

    Feeds the entry-tracking progress-bar widths on the companion
    transaction card (and any other surface that needs an entry
    aggregate as a percentage of its declared budget base).  Returns a
    Decimal so money math never crosses the Decimal/float boundary at
    the route layer (MED-04 / E-16): the companion route used to
    ``float(total / estimated * Decimal("100"))`` inline, which violated
    the "money math is service-layer Decimal, not route-layer float"
    standard.  Thin domain-named wrapper over
    :func:`app.utils.money.percent_complete` -- the single numeric
    contract the dashboard and companion progress surfaces both share.

    The two-decimal-place result is safe to render as-is in CSS width
    values: ``data-progress-pct="55.50"`` is parsed by
    ``app/static/js/progress_bar.js`` via ``parseFloat`` before being
    applied as an inline width, and CSS itself accepts the decimal
    notation in ``%`` values.

    Args:
        total: Sum of entries against the budgeted line.
        target: Budgeted estimated amount.  If <= 0 the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        Decimal in [0, 100] quantised to two decimal places when the
        guard does not fire; ``Decimal("0")`` when ``target <= 0``.
    """
    return percent_complete(total, target)


def compute_actual_from_entries(
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute actual_amount for a Paid transaction: sum of ALL entries.

    The actual_amount represents total spending for analytics and
    reporting.  Both debit and credit entries contribute to the total.
    The credit portion is already handled by the CC Payback in the
    next period.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- sum of all entry amounts (Decimal("0") if empty).
    """
    return sum((e.amount for e in entries), Decimal("0"))


def check_purchase_date_in_period(
    purchased_on: date,
    transaction: Transaction,
) -> bool:
    """Check whether a purchase's date falls within the pay period range.

    Informational utility for UI warnings (OP-4).  Does NOT block
    entry creation or updates -- late-posting purchases may
    legitimately fall outside the period range.

    It reads ``purchased_on`` and not ``settled_on``, and that is the
    distinction the split exists for: this warning asks "is this purchase
    budgeted to the right pay period", which is a BUDGET-clock question.  When
    the money reached the bank is a cash-clock fact and belongs to the balance
    fold, not to a budgeting warning.

    Args:
        purchased_on: The day the purchase was made.
        transaction: The parent Transaction (with pay_period loaded).

    Returns:
        True if *purchased_on* is within [start_date, end_date], False
        otherwise.
    """
    period = transaction.pay_period
    return period.start_date <= purchased_on <= period.end_date
