"""The UNDO: what releasing a match takes back, and the ONE derivation of it.

Plan step ``bank_import:X-f6f``, developer ruling **R-GG** (2026-08-24).  Split
out of :mod:`._accept` because the two are opposite acts on one relation and
because that module stood at 996 of this project's 1,000-line bound -- but the
seam is a subject rather than a line count, and it is the same seam
:mod:`._variance` was cut on: everything here reads what an act CREATED, and
nothing in :mod:`._accept` does.

**Deleting the record does NOT put the days back, and that is the honest
direction.**  A settle day is what the app knows about when money moved, and
the bank is still the best evidence it has; reverting one because the owner
unlinked a record would throw away a correction in order to tidy a relation.
What the release restores is the QUESTION -- the bank lines become unexplained
again and the rows become matchable again -- which is the repair door finding
**N-302** says a refusal owes.

**A row the act CREATED is the exception, and it is the same argument rather
than a departure from it.**  A settle day is a fact about money that moved and
survives the unlinking; a row that exists only because the act recorded it
states nothing once the act is withdrawn.  Keeping it is not conservative, it
double-counts.  Measured through these doors: one `-$57.96` bank line recorded,
released and recorded again moved the balance **`-$115.92`** and left two
purchases and two budget lines for one swipe (findings **N-333**, **N-340**).

**A SUBJECT and a CONTAINER are removed on different terms, and the difference
is MEMBERSHIP** (ruling **R-GG**).

* A creation the act also NAMES is what the act is ABOUT -- a group's
  residual, a purchase recorded from a bank line.  It is removed, and a row
  the owner has EDITED since REFUSES the undo instead: deleting that would
  throw away their record in order to tidy a relation, which is the direction
  this module already refuses to go for a settle day.
* A creation the act does NOT name is a CONTAINER -- the budget line the
  create-a-purchase arm may mint to hold its purchase.  It is removed only
  when nothing is left in it and nothing has touched it since, and otherwise
  it simply stays.  **It never refuses**, because the container is not what
  the act is about and leaving one standing costs nothing: it budgets `0.00`,
  holds nothing, and books nothing (``settled_cash_leg`` over an empty
  ``purchases`` settlement is ``0.00``), so it is an ordinary row the owner
  deletes in one click if they want it gone.

**THREE things refuse an undo, and all THREE are asked before anything is
written -- including the refusals of the doors this one calls.**  A created
SUBJECT the owner has edited since; one the door that removes it would
refuse anyway; and one the amount model can no longer price, which refuses
because a door that cannot say what removing a row would take out of the
books may not remove it.  A CONTAINER refuses nothing.  ``entry_service`` admits removing a
purchase from a settled row only where the removal cannot change what that
row's own close booked, and a container this act created can be put beyond
that afterwards.  Measured on this step's own first build: the panel offered
*"Undo removes 1 row"* over an archived container and the release then raised
with the act already deleted from the session.  :func:`_subject_removal` asks
that door's question (``entry_service.removal_refusal``) rather than
discovering the answer halfway through, which is what keeps this module's
promise a property rather than a hope.

**The measured consequence of that asymmetry, stated rather than discovered.**
One press can file several lines into one envelope (11 of the 47 on the
developer's own dev database hold 2-4 purchases), and each later line RE-CLOSES
that envelope on its own posting day -- which moves the container's revision
past what the creating act recorded.  So an envelope a multi-line press built
survives every undo, empty, and the owner removes it themselves.  The
alternative was to guess which writes count as "untouched", which is exactly
the guess ``created_version_id`` exists to avoid.

**The undo reaches only what was recorded AFTER the creations relation
existed.**  An act carries a creation record because the door that made the row
wrote one, so the 230 acts already on the developer's database carry none and
this door removes nothing for them.  A backfill was considered and is measured
unsafe: the tightest available signature claims **62 purchases the app already
had** alongside the 103 the pass created, because an accepted match writes the
same facts onto a row it merely re-dates.  Those rows are reached instead
through ``entry_service``, whose removal rule the same step corrected at the
root -- measured, 103 admitted and 0 refused.

**What the screen shows and what the door does are ONE derivation**
(:func:`planned_removals`), which is the shape :func:`~._preview
.preview_hand_build` already has one door over: the accepted-matches panel
prints what an Undo would remove, the confirm dialog carries the same figure,
and the door then removes exactly that.  Two derivations would let the screen
promise one thing and the button do another.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.exceptions import AmountUnresolvable, ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchCreation
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    cash_ledger,
    entry_service,
    transaction_service,
)
from app.utils.balance_predicates import is_balance_contributing
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCH_RELEASED,
    log_event,
)

from ._offers import RowKind

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannedRemoval:
    """One row an undo would take back, as the screen and the door see it.

    Attributes:
        kind: Which table the row is in.
        row_id: Its primary key.
        label: What to call it on screen -- the purchase's own wording, or the
            budget line's name.
        cash_amount: The signed cash the account would stop recording if this
            row went, positive INTO the account.  ``0.00`` for a row that
            books nothing: a card purchase (its money leaves through the CC
            Payback), one whose parent no longer contributes to the balance,
            or an emptied container.  Carried because the Undo button destroys
            money records and a control that names no figure is the bare
            consent box ruling **R-GD(a)** refused one tier up.
        is_container: Whether this row is the CONTAINER a created purchase
            went into rather than a subject the act names.  The two are
            removed on different terms; see the module docstring.
    """

    kind: RowKind
    row_id: int
    label: str
    cash_amount: Decimal
    is_container: bool


@dataclass(frozen=True)
class PlannedRemovals:
    """What releasing one act would take back, derived once for both readers.

    Attributes:
        rows: The rows the undo would remove, subjects before containers --
            which is the order they must go in, not a presentation choice: a
            container's foreign key CASCADES to its purchases, so removing it
            first would take a purchase away without reversing the ledger legs
            it booked (``journal_entries.transaction_entry_id`` is ``ON DELETE
            SET NULL``, so the legs would be stranded with nothing to offset
            them).  **EMPTY whenever :attr:`refusal` is set**, because a
            refused act removes nothing: the two fields are exclusive by
            construction, so a reader cannot print a destruction the press will
            not perform.
        refusal: The sentence explaining why this act cannot be released, or
            ``None``.  **THREE things produce one and all three are about a
            created SUBJECT** (:func:`_subject_removal`): the owner has edited
            it since, so it is their record; the door that removes it would
            refuse anyway, which a container put beyond that door -- archived,
            or re-closed at a stored figure -- is what produces; or the amount
            model can no longer price it.  A CONTAINER never produces one; it
            simply stays.
        cash_amount: What :attr:`rows` come to -- the signed cash the account
            would stop recording.
        kept_containers: How many budget lines this act created would be LEFT
            standing -- something is still filed under them, or they have been
            edited since.  Counted HERE rather than by the door, because the
            door would have to re-derive which creations are containers and
            which of them survive, and a first version of it did: it asked
            whether a creation's ``transaction_id`` appears among the removed
            rows' ids, which is true of an unrelated PURCHASE that happens to
            share the number.  Its own test caught it at ids ``1`` and ``1``.
    """

    rows: "tuple[PlannedRemoval, ...]"
    refusal: "str | None"
    cash_amount: Decimal
    kept_containers: int = 0

    @property
    def moves_money(self) -> bool:
        """Return whether this undo would change what the account records."""
        return bool(self.cash_amount)


@dataclass(frozen=True)
class ReleasedMatch:
    """What releasing one act actually did.

    Attributes:
        released_count: How many member rows were deleted -- the act's whole
            membership, lines and app rows alike.
        removed_rows: How many rows the act had CREATED were removed with it.
        removed_cash: The signed cash the account has stopped recording,
            positive INTO the account.  **A FIGURE rather than a count**, for
            the reason :attr:`~._accept.AcceptedMatch.residual` is one: what
            the owner needs told about a destructive act is HOW MUCH, and a
            receipt that says only "1 row" over a `$213.49` swipe is the
            *"Nothing moved."* sentence this arc has already shipped once.
        kept_containers: How many budget lines the act created were LEFT
            standing -- because something is still in them, or because they
            have been edited since.  Reported rather than silent: a row the
            owner expected to go and that did not is exactly what a receipt
            is for.
    """

    released_count: int
    removed_rows: int
    removed_cash: Decimal
    kept_containers: int


def _entry_cash(entry: TransactionEntry) -> Decimal:
    """Return the cash the account stops recording if *entry* goes.

    A purchase's cash is money LEAVING, so it is the negated stored figure --
    the sign convention :func:`~._candidates.purchase_candidate` states and
    :mod:`app.models.statement_import` defines.  Two shapes book nothing on
    this account and answer ``0.00``: a CARD purchase, whose money leaves
    through its envelope's CC Payback sibling rather than through this row
    (``cash_ledger.credit_entry_sum`` is the term that removes it), and one
    under a row that no longer contributes to the balance at all.  It is the
    same rule :func:`~._accepted_view._accepted_row` applies to a member, asked
    of a row about to be destroyed.

    Args:
        entry: The purchase, with its parent transaction loaded.

    Returns:
        Its signed cash effect, positive INTO the account.
    """
    if entry.is_credit or not is_balance_contributing(entry.transaction):
        return Decimal("0.00")
    return -Decimal(str(entry.amount))


def _subject_of(creation: StatementMatchCreation):
    """Return the row a creation names, or ``None`` if it has gone.

    A subject the database has already taken cascades its creation record
    away, so a ``None`` here means the row went between this read and now --
    nothing to remove and nothing to refuse.

    Args:
        creation: The creation record.

    Returns:
        The :class:`~app.models.transaction.Transaction` or
        :class:`~app.models.transaction_entry.TransactionEntry`, or ``None``.
    """
    if creation.transaction_id is not None:
        return db.session.get(Transaction, creation.transaction_id)
    return db.session.get(TransactionEntry, creation.transaction_entry_id)


def _container_survives(
    container: Transaction,
    creation: StatementMatchCreation,
    going: "set[int]",
) -> bool:
    """Return whether a created CONTAINER is left standing by this undo.

    Two reasons it stays, and neither refuses (ruling **R-GG**):

    * something is still in it -- a purchase this undo is not removing,
      whether the owner added it by hand or another line of the same press
      recorded it;
    * its own revision has moved since the act left it, which means somebody
      renamed it, budgeted it, re-categorised it or re-closed it.  A later
      line of the SAME press re-closing it counts here, deliberately: the
      alternative is a list of which writes are the pass's own, and a guessed
      list is what a revision counter exists to replace.

    Args:
        container: The budget line the act created.
        creation: Its creation record, carrying the revision the act left.
        going: The ids of the purchases this undo is about to remove.

    Returns:
        Whether the container stays.
    """
    if container.version_id != creation.created_version_id:
        return True
    # **Read off the COLLECTION, which every purchase write expires.**  Both
    # ``entry_service`` doors that add or remove one call ``sync_entry_payback``,
    # which does ``db.session.expire(txn, ["entries"])`` on the parent, so a
    # loaded collection cannot be behind the database for the row this asks
    # about.  A per-container SELECT was the first spelling and it made the
    # bulk fold pay one query per container -- measured, 8 acts cost 18
    # statements where 2 cost 12, which is the per-act cost this step's own
    # warm exists to remove.
    return any(entry.id not in going for entry in container.entries)


def _names_of(match: StatementMatch) -> "tuple[set[int], set[int]]":
    """Return the transaction ids and purchase ids this act NAMES.

    A creation in one of these sets is a SUBJECT -- what the act is about --
    and one in neither is a CONTAINER.  Derived from the members rather than
    stored, because they are the one statement of what an act names and a
    second copy could disagree with them.

    Args:
        match: The act, with its members loaded.

    Returns:
        ``(transaction_ids, transaction_entry_ids)``.
    """
    return (
        {
            member.transaction_id for member in match.members
            if member.transaction_id is not None
        },
        {
            member.transaction_entry_id for member in match.members
            if member.transaction_entry_id is not None
        },
    )


def _subject_removal(
    creation: StatementMatchCreation, subject,
) -> "tuple[PlannedRemoval, str | None]":
    """Return the removal for one created SUBJECT, and why it may be refused.

    Two questions, and both have to be asked BEFORE anything is written:

    * has the owner EDITED the row since this act left it?  Then it is their
      record and the undo refuses rather than taking it.
    * would the door that removes it refuse anyway?  A purchase goes through
      ``entry_service``, which admits removing one from a settled row only
      where the removal cannot change what that row's own close booked -- and
      the container this act created can be put beyond that afterwards, by
      being archived or re-closed at a stored figure.  **Measured on the first
      build of this step**: the panel offered *"Undo removes 1 row"* over an
      archived container and the release then raised with the act already
      deleted from the session, which breaks this package's promise that a
      refused act leaves the database exactly as it was.

    Args:
        creation: The creation record, carrying the revision this act left.
        subject: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transaction_entry.TransactionEntry` it names.

    Returns:
        ``(row, refusal)`` -- what would be removed, and the sentence
        explaining why it cannot be, or ``None``.
    """
    is_purchase = creation.transaction_entry_id is not None
    label = (
        f"{subject.transaction.name}: {subject.description}"
        if is_purchase else subject.name
    )
    # **A row the amount model can no longer price REFUSES the undo, and it
    # refuses rather than raising** (adversarial security review 2026-08-24).
    # This runs on the REVIEW PAGE's own render, where its sibling
    # :func:`~._accepted_view._accepted_row` already guards the identical call
    # and states why: a raise here would make the screen permanently
    # unreachable for the account with no in-app repair, which is finding
    # **N-302**'s shape.  Refusing is the honest answer as well as the safe
    # one -- a door that cannot say what removing a row would take out of the
    # books may not remove it.
    try:
        cash = (
            _entry_cash(subject) if is_purchase
            else cash_ledger.settled_cash_leg(subject)
        )
    except AmountUnresolvable:
        return PlannedRemoval(
            kind=RowKind.TRANSACTION, row_id=subject.id, label=label,
            cash_amount=Decimal("0.00"), is_container=False,
        ), (
            f'Undoing this match would remove "{label}", which it created, '
            f"but the app can no longer work out what that row is worth -- so "
            f"it cannot tell you what removing it would take out of your "
            f"books.  Nothing was changed."
        )
    row = PlannedRemoval(
        kind=RowKind.PURCHASE if is_purchase else RowKind.TRANSACTION,
        row_id=subject.id,
        label=label,
        cash_amount=cash,
        is_container=False,
    )
    if subject.version_id != creation.created_version_id:
        return row, (
            f'Undoing this match would remove "{label}", which it created -- '
            f"but you have edited that row since, so it is your record now.  "
            f"Delete it yourself if you want it gone, then undo the match.  "
            f"Nothing was changed."
        )
    blocked = (
        entry_service.removal_refusal(subject.transaction, subject)
        if is_purchase else None
    )
    if blocked is not None:
        return row, (
            f'Undoing this match would remove "{label}", which it created, '
            f"and that is refused.  {blocked}"
        )
    return row, None


def _container_removal(container: Transaction) -> "PlannedRemoval | None":
    """Return the removal for an emptied CONTAINER, or ``None`` to keep it.

    **The price is READ rather than assumed**, because a settled row's own leg
    is the amount model's answer and this module is not a second one -- an
    emptied ``purchases`` settlement records ``0.00`` because that is what its
    entries say.

    **A container the model cannot price STAYS, and it does not refuse.**  Its
    twin :func:`_subject_removal` turns the same failure into a refusal
    because a subject is what the act is about; a container is not, so the
    conservative answer is simply to leave it -- and the call has to be
    guarded either way, because this runs on the review page's own render
    where a raise would make the screen permanently unreachable for the
    account, which is finding **N-302**'s shape.  Named by adversarial
    financial review 2026-08-24 as the one such call left bare.

    Args:
        container: The budget line this act created, now holding nothing.

    Returns:
        Its :class:`PlannedRemoval`, or ``None`` when it cannot be priced.
    """
    try:
        cash = cash_ledger.settled_cash_leg(container)
    except AmountUnresolvable:
        return None
    return PlannedRemoval(
        kind=RowKind.TRANSACTION, row_id=container.id,
        label=container.name, cash_amount=cash, is_container=True,
    )


def planned_removals(match: StatementMatch) -> PlannedRemovals:
    """Return what releasing *match* would take back, WITHOUT taking it.

    The one derivation the screen renders and the door acts on.  It reads and
    never writes, so the accepted-matches panel can call it per act while the
    review page is rendered.

    Args:
        match: The act, with its members and creations loaded.

    Returns:
        Its :class:`PlannedRemovals`.  ``rows`` is empty for an act that
        created nothing, which is every match between rows that already
        existed.
    """
    if not match.creations:
        return PlannedRemovals(
            rows=(), refusal=None, cash_amount=Decimal("0.00"),
            kept_containers=0,
        )
    named_transactions, named_entries = _names_of(match)
    subjects: "list[PlannedRemoval]" = []
    containers: "list[tuple[Transaction, StatementMatchCreation]]" = []
    refusal: "str | None" = None
    for creation in sorted(match.creations, key=lambda row: row.id):
        subject = _subject_of(creation)
        if subject is None:
            continue
        named = (
            creation.transaction_entry_id in named_entries
            if creation.transaction_entry_id is not None
            else creation.transaction_id in named_transactions
        )
        if not named:
            containers.append((subject, creation))
            continue
        row, blocked = _subject_removal(creation, subject)
        subjects.append(row)
        refusal = refusal if refusal is not None else blocked

    # **A REFUSED act removes NOTHING, so it reports nothing to remove**
    # (adversarial security review 2026-08-24).  Carrying the rows alongside
    # the refusal made this value describe a removal that cannot happen, and
    # every reader but the one that knew to branch on ``refusal`` read it as a
    # promise: the import page printed *"DESTROYS 2 row(s) ... worth -$57.96"*
    # over a press that destroys nothing and cannot succeed.  Returning early
    # makes the two fields exclusive by construction rather than by each
    # reader remembering.
    if refusal is not None:
        return PlannedRemovals(
            rows=(), refusal=refusal, cash_amount=Decimal("0.00"),
            kept_containers=0,
        )

    # The containers are decided against the purchases that are actually
    # going, so a screen and a door that disagree about which purchases go
    # cannot disagree about which envelopes follow them.
    going = {
        row.row_id for row in subjects if row.kind is RowKind.PURCHASE
    }
    emptied = [
        container for container, creation in containers
        if not _container_survives(container, creation, going)
    ]
    kept = [row for row in (_container_removal(c) for c in emptied) if row]
    rows = (*subjects, *kept)
    return PlannedRemovals(
        rows=rows,
        refusal=refusal,
        cash_amount=sum(
            (row.cash_amount for row in rows), Decimal("0.00"),
        ),
        kept_containers=len(containers) - len(kept),
    )


def _remove(row: PlannedRemoval, owner_id: int) -> None:
    """Remove one row an act created, through the door that owns its table.

    A PURCHASE goes through ``entry_service.delete_entry``, which is the one
    door for removing one: it reverses the purchase's own dated cash leg while
    ``journal_entries.transaction_entry_id`` still links it, re-derives the
    envelope's CC Payback, and reconciles the family.  **Its settled-parent
    refusal is what plan step X-f6f corrected at the root** rather than
    bypassed here: removing a purchase that carries a posting day cannot change
    what a ``purchases``-basis close booked, measured `0.00` -> `0.00` on the
    envelope's own leg with only the purchase's own `$57.96` leg reversed.  A
    matcher that deleted around that refusal would have been a second
    purchase-delete door restating a money rule.

    A TRANSACTION -- a group's residual, or an emptied container -- goes
    through ``transaction_service.delete_transaction``, for exactly the reason
    the purchase arm goes through ``entry_service``.  **It SPELLED that verb's
    sequence itself until plan step ``bank_import:X-gb``**, saying it "takes
    the transaction delete sequence WHOLE" beside the delete route that spelled
    the same four steps -- and each step's ORDER is a money rule, so two copies
    was two places for that order to drift.  Both created kinds are always
    ad-hoc (a residual names no template, and a created envelope is built
    without one), so the verb's soft arm is unreachable from here and its hard
    delete is what runs.

    **Its match WITHDRAWAL is provably a no-op on this path, which is why
    calling the shared verb is safe here.**  A subject belongs to at most one
    act (``uq_statement_match_members_transaction``), and :func:`release_match`
    has already deleted and flushed the only act that could name this row, so
    the withdrawal's own query finds nothing.  Asserted rather than assumed at
    ``TestReleasingAnActDoesNotWithdrawTwice``, whose first version released an
    act that had CREATED nothing and so never reached this function at all.

    Args:
        row: The planned removal.
        owner_id: The user the route proved owns the account.

    Raises:
        PostingError: From a ledger reconcile, on a broken invariant.
        ValidationError: From the purchase door, which this step's own rule
            has already been asked (:func:`planned_removals` reads the same
            state) -- reachable only if the row moved between the two.  The
            transaction verb's own refusals (a transfer shadow, a CC payback)
            cannot fire: an act creates neither shape.
    """
    if row.kind is RowKind.PURCHASE:
        entry_service.delete_entry(row.row_id, owner_id)
        return
    subject = db.session.get(Transaction, row.row_id)
    transaction_service.delete_transaction(subject, owner_id)


def release_match(
    match_id: int, owner_id: int, account_id: int,
) -> ReleasedMatch:
    """Undo one match: restore the question, and remove what the act CREATED.

    The whole argument -- why the days stay, why a created row does not, and
    why a container is decided on different terms -- is the module docstring.

    Does NOT commit -- the route owns the session boundary.

    Args:
        match_id: The act to release.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        Its :class:`ReleasedMatch`.

    Raises:
        ValidationError: When *match_id* names no act on this owner's account
            -- the set-operation form of the project's "404 for both not-found
            and not-yours" rule, raised rather than ignored because this door
            names ONE act on purpose -- or on either of
            :func:`planned_removals`' two refusals: a created row the owner has
            edited since, and one the door that removes it would refuse anyway.
            **Both fire before anything is written.**
        PostingError: From reversing a created row's postings, on a broken
            ledger invariant.
    """
    match = (
        db.session.query(StatementMatch)
        .filter(
            StatementMatch.id == match_id,
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
        )
        .one_or_none()
    )
    if match is None:
        raise ValidationError(
            "That match is no longer there.  Reload the page; nothing was "
            "changed."
        )
    # BEFORE anything is deleted, so a refusal leaves the act standing -- the
    # discipline :mod:`._accept` states for its own door, kept here without
    # depending on the caller's rollback.
    planned = planned_removals(match)
    if planned.refusal is not None:
        raise ValidationError(planned.refusal)

    released = len(match.members)
    db.session.delete(match)
    # FLUSHED before the removals, so the act and its members are really gone
    # before :func:`_remove` reaches the shared transaction delete verb -- whose
    # own match withdrawal would otherwise depend on autoflush ordering to find
    # nothing.  A subject belongs to at most one act, so there is nothing else
    # for it to find; this makes that a property of the code rather than of
    # SQLAlchemy's flush policy.
    db.session.flush()
    for row in planned.rows:
        _remove(row, owner_id)
    db.session.flush()

    log_event(
        _logger, logging.INFO, EVT_STATEMENT_MATCH_RELEASED, BUSINESS,
        "A statement match was released; its lines are unexplained again.",
        user_id=owner_id, account_id=account_id, match_id=match_id,
        released_count=released,
        removed_count=len(planned.rows),
        removed_cash=str(planned.cash_amount),
        kept_containers=planned.kept_containers,
    )
    return ReleasedMatch(
        released_count=released,
        removed_rows=len(planned.rows),
        removed_cash=planned.cash_amount,
        kept_containers=planned.kept_containers,
    )


def acts_of(
    owner_id: int, account_id: int, match_ids: "set[int] | None" = None,
) -> "list[StatementMatch]":
    """Return match acts WHOLE, newest first, in one read.

    **The ONE loader, because an act is only readable with both of its
    relations** (plan step ``bank_import:X-f6f``): what it NAMES decides
    whether it still holds, and what it CREATED decides what an undo would take
    back.  Two callers need exactly that -- the accepted-matches panel and the
    import page's delete preview -- and they spelled the same query with the
    same two eager loads until pylint's ``duplicate-code`` said so.  A third
    relation added later would otherwise be loaded by one reader and lazily
    fetched per row by the other.

    **It filters on the OWNER as well as the account**, which the write door
    :func:`release_match` already does.  The account implies the owner
    (``fk_statement_matches_owner``), so the second column can only ever be
    redundant -- and a reader feeding a destructive control's confirmation
    narrows by the same two columns the control itself does rather than by one
    of them.  Named by adversarial security review 2026-08-24.

    The ORDER is the panel's (newest first) and costs the other caller nothing,
    where an unordered read would have to be sorted twice.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose acts to read.
        match_ids: The acts to consider, or ``None`` for all of this account's.
            **The import page passes a set**, because it renders at most 20
            imports and every act outside them is a row it will never show --
            an unbounded read of an account's every act is work a page that
            renders 20 imports never uses.

    Returns:
        Its :class:`~app.models.statement_match.StatementMatch` rows, newest
        first, with ``members`` and ``creations`` loaded.
    """
    if match_ids is not None and not match_ids:
        return []
    query = (
        db.session.query(StatementMatch)
        .options(
            selectinload(StatementMatch.members),
            selectinload(StatementMatch.creations),
        )
        .filter(
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
        )
    )
    if match_ids is not None:
        query = query.filter(StatementMatch.id.in_(match_ids))
    return query.order_by(
        StatementMatch.created_at.desc(), StatementMatch.id.desc(),
    ).all()


def _warm_subjects(matches: "list[StatementMatch]") -> list:
    """Load every creation's SUBJECT into the session in two statements.

    :func:`planned_removals` reaches each subject with ``db.session.get``,
    which is the right shape for the DOOR -- one act, one or two rows -- and
    the wrong one for a reader folding many acts: measured on a 230-act
    account carrying 235 creations, the per-row gets and the lazy loads behind
    them cost 478 queries and 0.458 s.  ``session.get`` answers from the
    identity map when the row is already there, so warming it leaves that
    function unchanged and makes the bulk path two queries plus what the
    eager loads pull.

    The relations warmed are the ones :func:`_subject_removal` and
    :func:`_container_survives` then read: a purchase's parent (for its label
    and for ``entry_service``'s refusal) and a transaction's entries (for its
    settled figure).

    **The loaded rows are RETURNED and the caller must HOLD them**, which is
    not a style choice: SQLAlchemy's identity map keeps WEAK references, so a
    warm whose results are discarded is collected before the fold reaches them
    and every ``get`` queries again.  Measured on the developer's own database
    with 235 creations over 230 acts: 478 queries and 0.458 s either way
    until the reference was kept, and 9 queries and 0.039 s with it.

    Args:
        matches: The acts about to be folded, with ``creations`` loaded.

    Returns:
        The loaded subjects, to be held for the length of the fold.
    """
    transaction_ids = {
        creation.transaction_id
        for match in matches for creation in match.creations
        if creation.transaction_id is not None
    }
    entry_ids = {
        creation.transaction_entry_id
        for match in matches for creation in match.creations
        if creation.transaction_entry_id is not None
    }
    warmed = []
    if transaction_ids:
        warmed += db.session.query(Transaction).options(
            selectinload(Transaction.entries),
        ).filter(Transaction.id.in_(transaction_ids)).all()
    if entry_ids:
        warmed += db.session.query(TransactionEntry).options(
            joinedload(TransactionEntry.transaction).selectinload(
                Transaction.entries,
            ),
        ).filter(TransactionEntry.id.in_(entry_ids)).all()
    return warmed


def removals_by_match(
    owner_id: int, account_id: int, match_ids: "set[int]",
) -> "dict[int, PlannedRemovals]":
    """Return what releasing each of *match_ids* would remove, or refuse.

    **The bulk form of :func:`planned_removals`, for the door that releases
    MANY acts at once**: deleting an import releases every match naming one of
    its lines (``statement_import.delete_import``), so the page offering that
    delete has to say what it would destroy -- and whether it would be refused
    at all -- before the button is pressed.  Folding the same per-act
    derivation is what keeps the confirmation and the act in step; a second,
    cheaper estimate on the page would be a confirmation that lies, which is
    the property ``matches_by_import`` already exists to protect one question
    over.

    **A REFUSING act is in the result, and leaving it out was a defect.**  A
    release that refuses takes the whole import delete down with it
    (``_release_matches``), so an act that refuses is the single most important
    thing that page can say -- and a first version dropped it and went on
    printing *"DESTROYS 2 row(s) ... worth -$57.96"* over a press that
    destroys nothing and cannot succeed.  Reproduced by adversarial security
    review 2026-08-24 in one ordinary edit.  An act that would neither remove
    nor refuse is absent, which every caller reads as "nothing to say".

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose acts to read.
        match_ids: The acts to consider -- the caller's own bound on the work,
            for the reason :func:`acts_of` states.

    Returns:
        ``{match_id: PlannedRemovals}`` for the acts that would remove a row or
        refuse.
    """
    matches = [
        match for match in acts_of(owner_id, account_id, match_ids)
        if match.creations
    ]
    warmed = _warm_subjects(matches)
    planned = {match.id: planned_removals(match) for match in matches}
    # ``warmed`` is read for its LIFETIME rather than its value, and the
    # ``del`` is what says so: SQLAlchemy's identity map holds WEAK references,
    # so a warm nothing points at is collected before the fold above reaches
    # it and every subject is fetched again one row at a time.  Measured on the
    # developer's own database with 235 creations: 478 queries and 0.458 s
    # without the reference, 9 and 0.039 s with it.
    del warmed
    return {
        match_id: removals for match_id, removals in planned.items()
        if removals.rows or removals.refusal is not None
    }
