"""The WRITE doors: what a user may do to one purchase, and what follows.

``entry_service``'s CRUD half -- the guards a mutation must pass, the three
doors themselves (create, update, delete), the owner resolution they share, and
the re-derivation every one of them triggers.  What a set of purchases ADDS UP
TO, and the screen contexts built from those sums, is the sibling leaf
(:mod:`._sums`); the arrow runs one way -- this module reads
the reductions in :mod:`._sums`, and that module reads nothing here.

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
from app.services import match_withdrawal, posting_service
from app.services.entry_credit_workflow import sync_entry_payback
from app.services.settle_day import (
    SettleDay,
    record_settle_day,
    recorded_settle_day,
)
from app.services.entry_service._refusals import (
    _reject_future_posting_day,
    _reject_future_purchase_date,
    _reject_zero_amount,
    _reject_settled_addition,
    _reject_settled_before_purchase,
    _reject_settled_parent,
    _reject_settled_removal,
    cost_fields_changing,
)
from app.utils.balance_predicates import is_cancelled
# ``is_credit`` from balance_predicates collides with the
# ``is_credit: bool`` keyword argument on this module's
# ``create_entry`` / ``update_entry`` functions.  Aliasing the
# predicate keeps both the helper accessible and the public
# function signatures stable.
from app.utils.balance_predicates import is_credit as txn_is_credit
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_UPDATED,
    log_event,
)


logger = logging.getLogger(__name__)

# Fields that can be updated on an entry via update_entry().
_UPDATABLE_FIELDS = frozenset({
    "amount", "description", "purchased_on", "settle_day", "is_credit",
})



def _resync_after_entry_change(txn: Transaction) -> None:
    """Reconcile an envelope's postings after an entry change.

    **It writes no figure, and losing that half is plan step X-au-c3's doing.**
    It re-derived ``actual_amount = sum(entries)`` for a settled envelope, and a
    settled envelope now records the ``purchases`` basis and stores no figure at
    all -- its amount IS the sum of its entries, answered on read by
    ``row_valuation.settled_figure``.  A stored copy is what needed a reconciler;
    with the copy gone the reconciler has nothing left to reconcile, and the
    entries and the figure they add up to cannot drift because there is only one
    of them.

    **Its whole gate was ``if settled and txn.entries``, and BOTH halves of that
    gate are gone.**  The figure it would have written no longer exists to
    write, and the ``settled`` half stopped being a question this function may
    ask: a settled parent DOES reach here, through the one entry edit
    :func:`_reject_settled_parent` admits on such a row -- recording the day
    the bank took a purchase -- and re-dating that purchase's cash is the whole
    point of the reconcile below.  Two defects went with the deleted arm:

      * deleting the LAST purchase from a settled envelope left the previous
        figure standing -- deliberately, because rewriting the close to ``$0.00``
        looked worse than a stale number.  That state is unreachable now for a
        different reason than when this was written: it said *the delete is
        refused*, and plan step ``bank_import:X-f6f`` admits one where removing
        the purchase cannot change what the row's own close booked.  What makes
        the stale figure impossible is that there is no stored figure left to go
        stale -- a ``purchases`` row answers ``Sigma(entries)``, so one closed
        empty answers ``$0.00`` because that is what its records say;
      * adding a purchase to a settled row whose figure was a HUMAN's correction
        overwrote that correction with the entry sum.  That cannot happen now
        for two independent reasons: the hook is gone, and
        :func:`_reject_settled_addition` admits an add only on a ``purchases``
        settlement, which stores no figure for a human to have corrected.
        Ruling **R-FB**'s rule -- a figure somebody read off a statement is a
        fact -- finally holds on this path too.

    What remains is the ledger reconcile (Build-Order Step 3).  An entry mutation
    changes the row's confirmed cash effect (``settled figure - Sigma(credit
    entries) - Sigma(posted purchases)``): adding a debit purchase grows the
    checking outflow, flipping an entry to or from credit moves the
    credit-excluded portion, deleting one shrinks it, and recording a posting day
    moves that purchase's amount out of the close and into its own dated leg.

    **It is UNGATED since plan step X-f3b, and that is ruling R-FM.**  It ran
    only on the settled band, on the premise that "a Projected envelope has no
    postings" -- false once a purchase carrying a recorded bank posting day
    books its own leg whatever its envelope's status is.  One call reconciles
    the whole family, so this door needs no second list of what changed; on a
    Projected row with no posted purchases it reads an empty ledger and writes
    nothing.

    Does NOT commit -- the calling service function owns the session boundary
    (the reconcile flushes but does not commit, matching this module's
    contract).

    **``settled`` is read off the ROW rather than passed as ``False``, and that
    choice has already paid for itself.**  The reconcile's parameter is a
    question about the row -- does its own cash leg belong in the ledger -- and
    the row is the one thing that answers it.  While ``_reject_settled_parent``
    refused every mutation on a settled row the constant would have been
    correct, and hardcoding it would have moved that guard's guarantee into a
    second module; the developer's 2026-08-17 ruling then widened the door to
    admit a posting-day edit, so ``True`` reaches here now and a hardcoded
    ``False`` would have silently stopped booking those rows' own cash legs --
    exactly the lie no test could see.  Reading it costs an already-joined
    relationship.

    Args:
        txn: The parent envelope transaction whose entries changed.  Its
            ``status`` relationship is read to tell the reconcile whether the
            row's OWN cash leg belongs in the ledger.
    """
    posting_service.sync_transaction_postings(
        txn, settled=txn.status.is_settled,
    )


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

        settle_day: The day the BANK took the money and HOW that day is
            known (:class:`app.services.settle_day.SettleDay`), when the caller
            already knows it, else ``None`` -- which is the state every
            hand-typed purchase is born in.  The one caller that supplies one
            is ``statement_match._create.create_purchase_from_line``, and its
            basis is always ``observed``: the bank line IS why the purchase
            exists.

    **The posting day was deliberately NOT here until plan step
    ``bank_import:X-f6a-3b``, and the premise that kept it out was true only of
    the doors that existed then.**  It read: *the day the bank took the money is
    an observation, and at the moment a purchase is recorded there is nothing to
    have observed.*  That is exactly right for the add-purchase form, and false
    for a purchase created FROM a bank statement line -- there the observation is
    what caused the record to exist, and it is the more reliable of the two days
    (**R-FW**).

    Recording it here rather than through a follow-up :func:`update_entry` is
    the difference between one act and two: the second call would re-run
    ``sync_entry_payback`` and the posting reconcile against an intermediate
    state in which a purchase the bank has already taken looks outstanding, and
    it would leave that state committed if anything after it refused.
    """

    amount: Decimal
    description: str
    purchased_on: date
    is_credit: bool = False
    settle_day: SettleDay | None = None




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
            description, purchased_on, is_credit, and the posting day --
            with the basis that says how it is known -- where the caller
            already has one).

    Returns:
        The newly created TransactionEntry (flushed, id available).

    Raises:
        NotFoundError: Transaction not found or not accessible by this
            user.
        ValidationError: Transaction not entry-capable, is a transfer, is
            income, or has a blocked status (Cancelled, Credit, the archive, or
            a settled row whose figure is not its purchases -- see
            :func:`_reject_settled_addition`); or a purchase day in the future,
            a posting day in the future, or a posting day before the purchase
            day.
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
    # The DAY the pair states, unwrapped once for the three refusals that are
    # about the day and not about how it is known (plan step **X-az**).  They
    # take a ``date`` rather than the pair because none of them reads the basis:
    # a day in the future is not a fact whatever named it, and money cannot
    # leave before it was spent whoever says when it left.
    posting_day = (
        details.settle_day.day if details.settle_day is not None else None
    )

    # A SETTLED parent is the third refusal, and it is finding **N-229**: an
    # entry on such a row used to be accepted, persisted, and silently inert --
    # the actual was not recomputed (that half graded ``is_done``) while the
    # postings were reconciled anyway (that half graded the settled BAND).
    # **Its own rule since plan step X-f6a-3b**: a new purchase against a row
    # whose figure IS its purchases is what a bank statement evidences, where a
    # row storing a fixed figure cannot record one at all.
    _reject_settled_addition(txn, posting_day)

    # Content guard, after the ownership and transaction guards so a
    # non-owner still gets the 404 rather than a validation message that
    # confirms the row exists (ruling R-M; see
    # _reject_future_purchase_date).
    _reject_zero_amount(details.amount)
    _reject_future_purchase_date(details.purchased_on)
    # The posting day's two bounds, the SAME pair :func:`update_entry` applies
    # and for the same reasons -- a day the bank has not reached yet (ruling
    # **R-FM**), and a day before the purchase it belongs to
    # (``ck_transaction_entries_settled_not_before_purchase``).  They arrived
    # here with ``EntryDetails``' posting day at plan step X-f6a-3b: a door that
    # accepts a field and leaves its rules to the OTHER door is a boundary that
    # holds on one and not the other, which is exactly what
    # :func:`_reject_future_purchase_date`'s own docstring warns against.  Both
    # are no-ops for the ``None`` every hand-typed purchase is born with.
    _reject_future_posting_day(posting_day)
    _reject_settled_before_purchase(details.purchased_on, posting_day)

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
    # The posting day and the basis that says how it is known, written as
    # ONE pair (plan step **X-az**).  Assigned through the shared writer
    # rather than as two constructor kwargs so this door and
    # :func:`update_entry` cannot come to disagree about which columns the
    # pair is -- and so the constructor cannot state half of it.
    record_settle_day(entry, details.settle_day)
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
        # The posting day is logged because a purchase BORN with one has
        # already moved money: it books its own dated cash leg at the reconcile
        # below, where a purchase born without one holds its envelope's budget
        # back instead.  A receipt that named only the amount could not tell
        # the two apart.
        settled_on=(
            posting_day.isoformat() if posting_day is not None else None
        ),
        # WHICH KIND of day it is, beside the day itself (plan step X-az):
        # a receipt naming only the day cannot tell a bank observation from
        # a bound, and the two mean different things about this purchase.
        settled_day_basis=(
            details.settle_day.basis.value
            if details.settle_day is not None else None
        ),
    )

    # Only a CREDIT purchase carrying money moves the envelope's credit sum
    # (finding **N-323**); a debit purchase, or a zero-amount one, cannot.
    sync_entry_payback(
        transaction_id, owner_id,
        moves_credit_total=bool(details.is_credit and details.amount),
    )
    _resync_after_entry_change(txn)

    return entry


def update_entry(entry_id: int, user_id: int, **kwargs) -> TransactionEntry:
    """Update an existing entry.

    Allowed fields: amount, description, purchased_on, settle_day, is_credit.
    Re-validates ownership through the entry's parent transaction.

    **``settle_day`` is the PAIR, not the column** (plan step **X-az**): a
    :class:`app.services.settle_day.SettleDay` carrying the day AND how that day
    is known, written to ``settled_on`` and ``settled_day_basis_id`` together by
    :func:`app.services.settle_day.record_settle_day`.  The key is not a column
    name because the value is not one column, and the three callers each state a
    different basis -- the entry PATCH ``entered``, the statement matcher
    ``observed``, and the reconcile panel writes its own ``asserted`` days
    through its bulk ``UPDATE`` rather than through this door.

    The day it carries records when the bank took the money.  For a hand-typed
    purchase that is an observation the user makes LATER -- off a statement, or
    by ticking the purchase at a balance true-up through
    :func:`app.services.reconcile_service.record_settled_days` -- which is why
    this door is where it usually arrives.  **It is no longer only this door**
    (plan step ``bank_import:X-f6a-3b``): a purchase created FROM a bank
    statement line is born carrying it, because there the observation is what
    caused the record to exist.  Passing ``None`` here clears it, putting the
    purchase back among the outstanding ones.

    Args:
        entry_id: The entry to update.
        user_id: The requesting user's ID (owner or companion).
        **kwargs: Fields to update (must be a subset of allowed fields).

    Returns:
        The updated TransactionEntry.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If no valid fields provided, unknown fields are
            passed, or the parent row has SETTLED and this update touches
            anything that changes what the row cost
            (:func:`_reject_settled_parent`).  An update touching only
            ``settle_day`` is admitted on a settled parent: it records when
            the bank took the purchase, not how much of it moved.
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

    # A settled row's purchases are closed to RE-PRICING (finding **N-229**),
    # and this is the door that passes what it was actually asked to write:
    # a submission touching only ``settle_day`` records when the bank took the
    # purchase and is admitted, where anything cost-bearing is refused.
    # Checked after ownership so a non-owner still gets the 404 rather than a
    # message confirming the row exists, exactly as the create door orders its
    # guards.
    # **Ruling R-GE**: a statement's evidence may re-cost a settled
    # purchase, and what bounds the permission is the settle day's own
    # BASIS rather than a flag -- stated once, beside the constant it
    # narrows (:func:`cost_fields_changing`).
    _reject_settled_parent(
        entry.transaction, cost_fields_changing(valid_updates),
    )

    # The same boundary the create door applies, and only when the caller is
    # actually moving the date -- a partial update that leaves ``purchased_on``
    # alone must not be refused for a value it is not setting (ruling R-M).
    # Narrowed to a submission that actually SETS the amount, for the reason
    # the date rule below is: a partial update leaving ``amount`` alone must
    # not be refused for a value it is not writing.
    _reject_zero_amount(valid_updates.get("amount"))
    if "purchased_on" in valid_updates:
        _reject_future_purchase_date(valid_updates["purchased_on"])
    # Both date rules are checked on the RESULT, not the submission: a
    # partial update moves one side against a stored other side, and both
    # directions can break the pair invariant.
    resulting_settle_day = (
        valid_updates["settle_day"] if "settle_day" in valid_updates
        else recorded_settle_day(entry)
    )
    resulting_posting_day = (
        resulting_settle_day.day if resulting_settle_day is not None else None
    )
    _reject_settled_before_purchase(
        valid_updates.get("purchased_on", entry.purchased_on),
        resulting_posting_day,
    )
    # The posting day's own bound (ruling **R-FM**, plan step X-f3b): a day the
    # bank has not reached yet would release this purchase's reservation now and
    # book its cash later.  On the RESULT for the same reason as the pair above,
    # and it therefore also re-refuses a stored forward day a partial update
    # would otherwise carry through -- of which production has none.
    _reject_future_posting_day(resulting_posting_day)

    # The two edits that make a recorded clearing fact FALSE.  Both release it
    # below; see the comment there for why releasing rather than refusing.
    releases_the_link = (
        "settle_day" in valid_updates
        and resulting_posting_day != entry.settled_on
    ) or (
        "is_credit" in valid_updates
        and valid_updates["is_credit"]
        and not entry.is_credit
    )
    # **Does THIS write move the envelope's credit total?** (finding N-323.)
    # Asked as the entry's own CONTRIBUTION to that sum before and after,
    # rather than as a case analysis over which fields were submitted: the sum
    # counts ``amount`` where ``is_credit``, so an entry contributes its amount
    # or nothing, and comparing the two is exact for every combination at once.
    # A field-name test is what the first draft used -- "amount or is_credit
    # was submitted" -- and it still refused a DEBIT row's amount edit, which
    # cannot reach the credit sum at all.  Read BEFORE the loop below, because
    # the loop is what makes ``entry`` the after-state.
    credit_before = entry.amount if entry.is_credit else Decimal("0")
    credit_after = (
        valid_updates.get("amount", entry.amount)
        if valid_updates.get("is_credit", entry.is_credit) else Decimal("0")
    )
    for field, value in valid_updates.items():
        # ``settle_day`` is the ONE key that is not a column: it is the day AND
        # the basis that says how the day is known, and
        # :func:`app.services.settle_day.record_settle_day` is what writes both
        # (plan step **X-az**).  A ``setattr`` here would put a
        # :class:`~app.services.settle_day.SettleDay` into ``settled_on`` and
        # leave the basis unwritten, which the table's own
        # ``ck_transaction_entries_settle_day_basis_pairing`` would then refuse
        # -- but the point of the branch is that the pair has exactly one writer,
        # not that the constraint would catch a second one.
        if field == "settle_day":
            record_settle_day(entry, value)
            continue
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
    # It fires on ANY move of the DAY, including to ``None``, which is also what
    # ``ck_transaction_entries_cleared_needs_settle_day`` requires.
    #
    # **It does NOT fire when only the BASIS moves** (plan step **X-az**).  The
    # link records that a named statement was seen to show this purchase ON a
    # named day; re-stating the same day on a better-known basis agrees with
    # that observation rather than contradicting it.  That is the case the
    # statement matcher produces when a bank line CONFIRMS a day the reconcile
    # panel had recorded as an upper bound -- the day is unchanged, the basis
    # rises from ``asserted`` to ``observed``, and releasing the link there
    # would drop a true observation for a write that strengthened it.  The
    # predicate above compares ``resulting_posting_day`` with the stored day for
    # exactly that reason, where a naive ``valid_updates["settle_day"] !=
    # <the stored pair>`` would compare the BASIS too and release on a
    # confirmation.
    #
    # **Flipping a purchase to CARD releases it too, and that arm is a 500 fix**
    # (found by X-f3b's trace, 2026-08-15).  A card purchase never touches this
    # account, so ``ck_transaction_entries_card_purchase_clears_nowhere``
    # refuses the pair -- and this door wrote the flag without touching the
    # link, so PATCHing ``is_credit`` on a purchase the reconcile panel had
    # ticked raised an unhandled ``IntegrityError``.  Reproduced on a production
    # clone against entry 87.  Releasing is the same answer for the same reason:
    # the user is correcting the observation, not confirming it, and the two
    # facts must not be left to disagree -- here they could not even be stored.
    if releases_the_link:
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

    sync_entry_payback(
        entry.transaction_id, owner_id,
        moves_credit_total=credit_before != credit_after,
    )
    _resync_after_entry_change(entry.transaction)

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
        ValidationError: If removing this purchase would change what a settled
            parent's own close BOOKED (:func:`_reject_settled_removal`) -- an
            undated debit under a settled row, a row recording a stored figure,
            or an archived one.
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # **A settled row's close may not be re-priced by a removal, and whether
    # this removal would re-price it is arithmetic** (plan step
    # ``bank_import:X-f6f``, ruling **R-GG**).  This passed
    # ``_COST_BEARING_FIELDS`` to ``_reject_settled_parent`` until then, which
    # refused EVERY removal from a settled row -- the exact inverse of an
    # addition ruling **R-FX** admits on the same row, and the reason 103
    # purchases a statement pass created in error had no door that removes
    # one (finding **N-333**).  ``_reject_settled_removal`` weighs what the
    # close actually booked instead.
    _reject_settled_removal(entry.transaction, entry)

    txn = entry.transaction
    transaction_id = entry.transaction_id
    # What this delete removes from the envelope's credit sum (finding
    # **N-323**), read while the row still exists: only a CREDIT purchase
    # carrying money was ever IN that sum, so deleting a debit one cannot move
    # it.
    removed_credit = bool(entry.is_credit and entry.amount)
    # A bank line matched to this purchase is no longer explained by it once
    # it goes, so the match is withdrawn and the line is unexplained again
    # (developer ruling 2026-08-25, plan step ``bank_import:X-gb``).  Its
    # PARENT is untouched: removing one purchase leaves the envelope and every
    # other purchase in it asserting exactly what they did.  BEFORE the delete
    # for the same reason the posting reversal below is -- the member's foreign
    # key is ON DELETE CASCADE, so afterwards nothing says which line was
    # freed.
    match_withdrawal.withdraw_for_purchase(entry, owner_id)
    # Reverse the purchase's OWN cash leg while the row still exists (ruling
    # **R-FM**, plan step X-f3b).  ``journal_entries.transaction_entry_id`` is
    # ON DELETE SET NULL, so reversing afterwards is impossible: the link is
    # severed and the legs are stranded on their ledger accounts with nothing
    # to offset them.  The transaction analog is
    # ``posting_service.reverse_postings_before_delete``, called at the
    # transaction-delete doors for the identical reason.  Idempotent no-op for a
    # purchase that never posted.
    posting_service.reverse_purchase_postings_before_delete(entry)
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

    sync_entry_payback(
        transaction_id, owner_id, moves_credit_total=removed_credit,
    )
    _resync_after_entry_change(txn)

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
