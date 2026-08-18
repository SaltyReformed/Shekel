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
from app.services import posting_service
from app.services.entry_credit_workflow import sync_entry_payback
from app.utils.balance_predicates import is_cancelled
# ``is_credit`` from balance_predicates collides with the
# ``is_credit: bool`` keyword argument on this module's
# ``create_entry`` / ``update_entry`` functions.  Aliasing the
# predicate keeps both the helper accessible and the public
# function signatures stable.
from app.utils.balance_predicates import is_credit as txn_is_credit
from app.utils.dates import display_today
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
    "amount", "description", "purchased_on", "settled_on", "is_credit",
})

#: The purchase facts that change what its PARENT ROW COST -- every field a
#: purchase carries except ``settled_on``.
#:
#: ``settled_on`` is the day the BANK took this purchase, and it is the one
#: fact about a purchase that is an OBSERVATION rather than a restatement of
#: what was spent: recording it moves that purchase's cash out of its
#: envelope's close and onto its own day
#: (``cash_ledger.settled_cash_leg``'s third term, ruling **R-FM**), and the
#: two always sum to the same total.  That is the SAME split this whole step is
#: built on -- ``settled_on`` / ``reconciled_by_id`` are the ASSERTION and
#: ``settled_amount`` / ``settled_basis_id`` are WHAT MOVED -- read one level
#: down, on the purchase instead of on the row.
_COST_BEARING_FIELDS = _UPDATABLE_FIELDS - {"settled_on"}


def _reject_settled_parent(
    txn: Transaction, changing: "frozenset[str]",
) -> None:
    """Refuse an entry mutation that RE-COSTS a row whose money has MOVED.

    **Finding N-229's door half, widened to the settled BAND at plan step
    X-au-c3** (developer ruling, 2026-08-17).  A settled envelope's purchases
    are closed: the user has said this money moved, and every one of the three
    doors would move it again.  Adding a purchase grows what the row cost,
    deleting one shrinks it, and re-pricing one does either -- so all three
    refuse, on Paid and Received as well as on the terminal ``Settled``.

    **The reason it is the BAND and not the archive is carry-forward, and it is
    the argument that decides this.**  ``carry_forward_service`` rolls an
    envelope's UNSPENT remainder (``estimated - Sigma(entries)``) into the next
    period's row and then settles the source at what was spent.  So the moment
    an envelope closes, its leftover has already moved on and is sitting in a
    LATER row.  A purchase recorded against the closed source afterwards would
    raise its cost while that later row still holds the rolled-forward money --
    the same dollars counted twice, in two periods, with nothing to reconcile
    them.  A forgotten purchase belongs in the period that now holds the money.

    **What a user does instead**: put the row back to Projected, record the
    purchase, and close it again.  That is the same act the refusal names, and
    it is honest about what happened -- the close was premature, so the settle
    day it stamped and the statement it was reconciled against were premature
    too.  Leaving the settled band releases the ASSERTION -- the settle day and
    the statement link, in ``status_seam.apply_status_change`` -- and KEEPS what
    the row recorded, which is correct rather than a cost: the purchases are
    what the figure is made of, so a re-close restates it from them on the day
    the money really moved.

    **The rejected alternative, and why**: letting a settled envelope re-derive
    its figure from a late purchase is what the deleted
    ``_update_actual_if_paid`` did (``actual_amount = Sigma(entries)`` on any
    settled row with entries).  It moves money in the OPTIMISTIC direction
    without a human act -- one ``$50`` purchase back-filled into a ``$500``
    close crashes the recorded cost to ``$50`` and hands ``$450`` of
    already-spent money back to the projection.  That is precisely the failure
    :func:`~app.services.status_seam.reject_future_settle_day` exists to
    prevent, and the reason ``TransactionEntry.settled_on`` deliberately bounds
    only from below: where the app must guess, it keeps the balance LOW.

    **It is FIELD-AWARE, and the one field it admits is ``settled_on``**
    (developer ruling, 2026-08-17).  Everything above is an argument about what
    the row COST; the day the BANK took a purchase is not that.  Recording it
    changes no total -- it moves that purchase's cash out of the envelope's
    close and onto its own day, and ``settled_cash_leg`` subtracts exactly what
    the purchase's own leg books, so the two always sum to the row's whole debit
    total.  Refusing it would leave already-spent money dated on the day the
    envelope happened to be closed with no door to correct it: measured on the
    2026-08-17 production dump, 28 closed envelopes hold 61 debit purchases
    with no posting day recorded, totalling ``$4,360.07``.

    That split is this step's own three-lifetime model read one level down.  A
    purchase's amount is WHAT MOVED and its posting day is an ASSERTION about
    when -- the same two facts ``settled_amount`` and ``settled_on`` are on the
    parent, with the same answer: the assertion may be recorded, corrected and
    withdrawn long after the figure is final.

    ``Status.is_settled`` is the band -- Paid, Received AND the terminal
    ``Settled`` -- where :func:`~app.utils.balance_predicates.is_archived` is
    that last status alone.  The band is what this rule is about, so it reads
    the band; the archive keeps its own predicate because other readers mean it.

    Args:
        txn: The parent transaction the entry belongs (or would belong) to.
            Its ``status`` relationship is read (``lazy="joined"``).
        changing: The purchase facts this act writes.  The create and delete
            doors pass :data:`_COST_BEARING_FIELDS` -- a purchase appearing or
            vanishing changes every one of them -- and the update door passes
            the fields it was actually given, which is what lets a posting-day
            edit through where a re-price is refused.

    Raises:
        ValidationError: When *txn* is in a settled status and *changing*
            touches any cost-bearing field.
    """
    if txn.status is None or not txn.status.is_settled:
        return
    if not changing & _COST_BEARING_FIELDS:
        return
    raise ValidationError(
        f"Transaction {txn.id} has settled; its purchases are closed and "
        "cannot be added, removed, or re-priced. Doing so would change what "
        "the row cost after its money moved -- and a carry-forward has "
        "already rolled its leftover into a later period, so the same dollars "
        "would be counted twice. Set the row back to Projected to change a "
        "purchase, then mark it paid again. Recording the day your bank took "
        "a purchase is still allowed: that says when this money moved, not "
        "how much of it did."
    )


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
        looked worse than a stale number.  Neither state is reachable now: the
        delete is refused, and a ``purchases`` row that was closed empty answers
        ``$0.00`` because that is what its records say;
      * adding a purchase to a settled row whose figure was a HUMAN's correction
        overwrote that correction with the entry sum.  The add is refused, and
        ruling **R-FB**'s rule -- a figure somebody read off a statement is a
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


def _reject_future_posting_day(settled_on: "date | None") -> None:
    """Refuse a bank posting day after the user's today -- ruling **R-FM**.

    The purchase twin of
    :func:`app.services.status_seam.reject_future_settle_day`, and it arrived at
    plan step X-f3b because that step INVERTED the reason this column had no
    upper bound: a forward day was conservative while a purchase was not a cash
    movement, and now RELEASES the reservation today while booking the cash
    later.  The argument is stated once, at the rule it is about
    (``cash_ledger._amounts._entry_aware_amount``), rather than a second time
    here.  A purchase the bank has not taken yet leaves ``settled_on`` NULL,
    which is what that state has always meant, so nothing expressible is lost;
    measured before the bound was added, ZERO of 91 production purchases carried
    a forward day.

    The clock is the USER's (:func:`~app.utils.dates.display_today`), for the
    reason :func:`_reject_future_purchase_date` beside it states.

    Args:
        settled_on: The posting day the caller wants the entry to carry, or
            ``None`` to clear it (always allowed -- it is the outstanding
            state).

    Raises:
        ValidationError: When *settled_on* is after the user's today.
    """
    if settled_on is None:
        return
    today = display_today()
    if settled_on > today:
        raise ValidationError(
            f"A posting date records the day your bank TOOK the money, so it "
            f"cannot be in the future: {settled_on.isoformat()} is after today "
            f"({today.isoformat()}).  Leave the posting date empty until you "
            f"see the purchase on a statement -- an unposted purchase already "
            f"holds its whole budget back."
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

    The UPPER bound is :func:`_reject_future_posting_day`'s and it arrived at
    plan step X-f3b (ruling **R-FM**), which inverted the reason there was
    none: a recorded posting day is now the moment the money leaves the book,
    so a forward one takes already-spent money out of today's projection instead
    of holding it conservatively.  The two bounds are separate functions because
    they are separate rules -- this one is about a PAIR of the row's own dates
    and that one is about the clock -- and both are checked on the RESULT of an
    update rather than on its submission.

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
            is income, or has a blocked status (Cancelled, Credit, or any
            SETTLED status -- see :func:`_reject_settled_parent`).
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
    # A SETTLED parent is the third refusal, and it is finding **N-229**: an
    # entry on such a row used to be accepted, persisted, and silently inert --
    # the actual was not recomputed (that half graded ``is_done``) while the
    # postings were reconciled anyway (that half graded the settled BAND).  A
    # new purchase states every cost-bearing fact at once, so it is refused on
    # the whole set rather than on any subset the caller happened to supply.
    _reject_settled_parent(txn, _COST_BEARING_FIELDS)

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
    _resync_after_entry_change(txn)

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
            passed, or the parent row has SETTLED and this update touches
            anything that changes what the row cost
            (:func:`_reject_settled_parent`).  An update touching only
            ``settled_on`` is admitted on a settled parent: it records when the
            bank took the purchase, not how much of it moved.
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
    # a submission touching only ``settled_on`` records when the bank took the
    # purchase and is admitted, where anything cost-bearing is refused.
    # Checked after ownership so a non-owner still gets the 404 rather than a
    # message confirming the row exists, exactly as the create door orders its
    # guards.
    _reject_settled_parent(entry.transaction, frozenset(valid_updates))

    # The same boundary the create door applies, and only when the caller is
    # actually moving the date -- a partial update that leaves ``purchased_on``
    # alone must not be refused for a value it is not setting (ruling R-M).
    if "purchased_on" in valid_updates:
        _reject_future_purchase_date(valid_updates["purchased_on"])
    # Both date rules are checked on the RESULT, not the submission: a
    # partial update moves one side against a stored other side, and both
    # directions can break the pair invariant.
    resulting_settled_on = valid_updates.get("settled_on", entry.settled_on)
    _reject_settled_before_purchase(
        valid_updates.get("purchased_on", entry.purchased_on),
        resulting_settled_on,
    )
    # The posting day's own bound (ruling **R-FM**, plan step X-f3b): a day the
    # bank has not reached yet would release this purchase's reservation now and
    # book its cash later.  On the RESULT for the same reason as the pair above,
    # and it therefore also re-refuses a stored forward day a partial update
    # would otherwise carry through -- of which production has none.
    _reject_future_posting_day(resulting_settled_on)

    # The two edits that make a recorded clearing fact FALSE.  Both release it
    # below; see the comment there for why releasing rather than refusing.
    releases_the_link = (
        "settled_on" in valid_updates
        and valid_updates["settled_on"] != entry.settled_on
    ) or (
        "is_credit" in valid_updates
        and valid_updates["is_credit"]
        and not entry.is_credit
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

    sync_entry_payback(entry.transaction_id, owner_id)
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
        ValidationError: If the parent row has settled
            (:func:`_reject_settled_parent`).
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership through the parent transaction chain.
    owner_id = resolve_owner_id(user_id)
    if entry.transaction.pay_period.user_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # A settled row's purchases are history (finding **N-229**), and removing
    # one would rewrite what the books already say the row cost -- every
    # cost-bearing fact of it at once, which is the set passed here.
    _reject_settled_parent(entry.transaction, _COST_BEARING_FIELDS)

    txn = entry.transaction
    transaction_id = entry.transaction_id
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

    sync_entry_payback(transaction_id, owner_id)
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
