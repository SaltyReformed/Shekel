"""
Shekel Budget App -- Status seam: the COVERING MOVEMENT a settle writes.

Plan step **balance:X-bi-3a**, rulings **R-BAL39** and **R-BAL41**.  A bill
ticked Paid used to record what moved on the bill row alone -- ``settled_amount``
beside how the figure is known -- while an envelope's purchases were each a row
of money that moved.  Every settle now records its money the envelope's way: the
MANUAL branch's settlement is mirrored as ONE covering movement, a
``budget.transaction_entries`` row carrying the figure the settle booked, who
wrote it, the day the money moved and how that day is known, and the statement
that showed it.  ``X-bi-3d`` cuts every already-settled row over to this shape
and ``X-bi-4`` re-points the fold onto movements; until then the bill row keeps
its own record and this module keeps the two in step -- the interval ruling
**R-BAL40** accepts, and the stale cache ``X-bi-4`` deletes.

**Why the seam, and not the settle verb.**  ``apply_status_change`` is the ONE
writer of the settlement record (plan step X-au-c3), reached by every settle
door and both settle verbs (``transaction_service._settle.settle_transaction``
and ``transfer_service._settle.settle`` through ``apply_status_to_all_three``).
The covering movement is that record's new home, so it is written where the
record is: a second call site in each verb would be the maintenance contract
rule 14 deletes, and a verb added later would inherit the write with none of
the seam's refusals.

**Balance-neutral by construction** (ruling **R-FM**'s identity, plan step
X-f3b): ``cash_ledger.settled_cash_leg`` books a settled row's figure MINUS its
posted purchases, and ``cash_ledger._events._posted_purchase_facts`` books each
posted purchase at its own day -- so a bill whose covering movement carries its
whole figure has a leg of exactly zero and the movement carries the money on
the same day.  The projection reads the same family (``_amounts.
_entry_aware_amount``), the posting writer posts it (``posting_service.
sync_transaction_postings`` walks ``txn.entries``), and the statement matcher
drops a zero-effect row from its offer (``_candidates.transaction_candidate``)
and offers the movement instead.  None of those readers branches on the row's
kind, which is why this leaf changes no reader.

**A revert DELETES the covering movement, and the row's retained record is
what carries the figure across.**  Leaving the settled band releases the
row's assertion and keeps what moved (plan step X-au-c3: ``settled_amount``
and ``settled_basis_id`` outlive a revert), and the next settle honours a
retained ``corrected`` record or re-prices a ``derived`` one
(``Settlement.from_settle``).  The movement is that record's mirror, so it is
rebuilt from the record at the re-settle -- ``typed`` again for a honoured
correction, ``resolved`` again for a re-priced derivation -- and nothing the
row does not also hold is lost: a revert releases the row's own bank-observed
day and link today, and the movement's go with it the same way.  Keeping a
movement undated across the revert was considered and rejected: an envelope
closed EMPTY at the door and then given real purchases would sum the stale
close into them on its next settle (``settles_from_entries`` is
``tracks_purchases and entries``), and a re-settle would have to tell the
mirror from a purchase by a source both can share, since an empty envelope's
manual-branch close may take a typed correction.

**Which entry is the covering movement is a STORED fact of the movement**
(``transaction_entries.covers_settlement``), never a derivation over the
row.  A first cut read "a settled row that stores a figure holds no
purchases, so its entries are the seam's" -- and a door refutes it: *Track
individual purchases* unticked on a settled envelope, then a figure typed
over it, leaves a ``corrected`` record beside real purchases
(``test_release``'s container-beyond-the-door case drives it end to end).
Under the derivation the seam would have overwritten one of those purchases
as its mirror.  So the seam marks what it writes, finds it by the mark, the
entry doors refuse to touch a marked row, and a partial unique index holds
the count at one per row.

**The 3b / 3c gate, stated so each leaf deletes its arm.**  This leaf covers
EXPENSE parents that are not transfer shadows.  An INCOME parent waits for
``X-bi-3b``: three readers hardcode a purchase's direction as an expense
(``_posted_purchase_facts``, ``_posting_purchases.emit_purchase_deltas``,
``posting_reads.settled_transaction_effect``), so a covered paycheck would read
``-figure`` today.  A transfer shadow waits for ``X-bi-3c``, which writes both
legs through ``transfer_service`` under Transfer Invariant 4.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
mutates in place; flushes nothing the caller did not already flush; the caller
owns the session boundary.  Money is ``Decimal`` throughout, read off the
:class:`~app.services.status_seam._record.Settlement` the seam was handed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettlementBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services.settle_day import record_settle_day, recorded_settle_day
from app.services.status_seam._record import Settlement

#: The settlement bases whose record a covering movement mirrors.  A
#: ``purchases`` settlement stores no figure because the row's own purchases ARE
#: the record, so there is nothing to cover.
_COVERED_BASES = frozenset({
    SettlementBasisEnum.DERIVED, SettlementBasisEnum.CORRECTED,
})

#: WHO WROTE the figure, by the record's basis: the settle resolved a
#: ``derived`` figure, a person typed a ``corrected`` one.
_SOURCE_OF_BASIS = {
    SettlementBasisEnum.DERIVED: MovementFigureSourceEnum.RESOLVED,
    SettlementBasisEnum.CORRECTED: MovementFigureSourceEnum.TYPED,
}


def _is_covered_kind(row: Transaction) -> bool:
    """Return whether this leaf writes a covering movement for *row*.

    The 3b / 3c gate from the module docstring: an expense that is not a
    transfer shadow.  Each of those leaves deletes its half of this predicate.
    """
    return row.transfer_id is None and row.is_expense


def covering_clause():
    """Return the SQL form of *this purchase is a covering movement*.

    :func:`covering_movements` asks the question of ONE loaded row; this asks
    it of a query over ``TransactionEntry``, for a reader that must leave the
    seam's mirrors OUT of a row set -- the statement matcher's purchase
    candidates, which offer a person's purchases and never the row's own
    payment record.

    Returns:
        A SQLAlchemy boolean expression over ``TransactionEntry``.
    """
    return TransactionEntry.covers_settlement.is_(True)


def covering_movements(row: Transaction) -> list[TransactionEntry]:
    """Return the covering movements *row* holds -- the settle's, not a person's.

    By the mark the seam left (module docstring); at most one, by the partial
    unique index, and a list rather than an optional so a caller that walks
    the family needs no branch.

    Args:
        row: The transaction, with ``entries`` loaded or loadable.

    Returns:
        The covering movements, in ``entries`` order; empty when none.
    """
    return [entry for entry in row.entries if entry.covers_settlement]


def covered_cash_leg(row: Transaction) -> Decimal:
    """Return the cash *row*'s POSTED covering movements carry, signed.

    **The other half of ruling R-FM's identity, for a reader that asks what a
    row is WORTH rather than what its own leg books.**  Once a bill is
    covered, ``cash_ledger.settled_cash_leg`` answers zero for it and its
    movement carries the money; the statement matcher prices a row by what
    the bank would see for it (``_candidates._price``), which is the family:
    the row's leg plus this.  The ROW stays the matcher's subject through the
    interval -- its mirror is excluded from the purchase candidates by
    :func:`covering_clause` -- so a bill is offered, matched and re-dated as
    one thing, and the seam's mirror carries the bank's day down to the
    movement.

    Each posted covering movement is worth what
    ``cash_ledger._events._posted_purchase_facts`` books for it -- its amount
    as money LEAVING the account, because this leaf covers EXPENSE parents
    only.  **That is the direction rule spelled a second time, and leaf
    ``X-bi-3b`` is what deletes one spelling**: it makes the fact producer
    derive a movement's direction from its parent's type (ruling **R-BAL35**)
    and this reads it from there.  Stated rather than hidden, because a second
    spelling that agrees today is still two spellings (rule 14).

    Args:
        row: The transaction, with ``entries`` loaded or loadable.

    Returns:
        The signed sum, ``Decimal("0")`` when nothing is covered or posted.
    """
    return sum(
        (
            -movement.amount
            for movement in covering_movements(row)
            if movement.settled_on is not None
        ),
        Decimal("0"),
    )


def _mirror_assertion(row: Transaction, movement: TransactionEntry) -> None:
    """Bring *movement*'s settle-day pair and statement link up to the row's.

    **A day that did not MOVE never lowers evidence** -- the seam's own rule
    for the row, applied to its movement.  Once a bill is covered, its own
    leg is zero and the statement matcher offers the MOVEMENT (never the
    row), so the movement can hold an ``observed`` day and a statement link
    the row has never seen; an identity re-submit of the row (a popover Save
    with the status untouched) reaches here too, and copying the row's
    ``entered`` pair over that would launder a bank observation into the
    owner's word and drop the link.  So:

    * the row's day DIFFERS from the movement's -- a settle, a re-settle
      after a revert, a settle-day correction: the movement takes the row's
      pair, through ``record_settle_day`` so the pair keeps its one writer and
      its books-boundary refusal, and the row's link with it (the row's own
      release logic already decided that link for this move);
    * the days are EQUAL: the pair stands, and the movement takes the row's
      link only where it holds none -- the reconcile panel ticked the row on
      an asserted day, and the movement sits inside that assertion too.
    """
    if movement.settled_on != row.settled_on:
        # A covering movement's purchase day IS its settle day (ruling
        # R-BAL39: a bill's payment has no other day), so a correction moves
        # both -- and ``ck_transaction_entries_settled_not_before_purchase``
        # holds as the equality it always was.
        if row.settled_on is not None:
            movement.purchased_on = row.settled_on
        record_settle_day(movement, recorded_settle_day(row))
        movement.reconciled_by_id = row.reconciled_by_id
    elif movement.reconciled_by_id is None:
        movement.reconciled_by_id = row.reconciled_by_id


def _record_onto(
    row: Transaction, movement: TransactionEntry, settlement: Settlement,
) -> None:
    """Write what a settle RECORDS onto *movement*: figure, source, name, day."""
    movement.amount = settlement.amount
    movement.figure_source_id = ref_cache.movement_figure_source_id(
        _SOURCE_OF_BASIS[settlement.basis],
    )
    # The plan's name as it reads at the settle -- the movement's OWN fact
    # (ruling R-BAL39): a bill's payment has no receipt text, and a later
    # rename of the plan no more rewrites this than renaming an envelope
    # rewrites its purchases.  Its day, likewise its own, is the mirror's.
    movement.description = row.name
    _mirror_assertion(row, movement)


def _cover(row: Transaction, settlement: Settlement) -> None:
    """Ensure *row* holds exactly one covering movement mirroring *settlement*."""
    existing = covering_movements(row)
    if existing:
        movement, *extra = existing
        if extra:
            raise ValueError(
                f"Transaction {row.id} holds {len(existing)} covering movements; "
                "a settle writes exactly one, so a second can only have reached "
                "the table around the status seam."
            )
        _record_onto(row, movement, settlement)
        return
    movement = TransactionEntry(
        transaction_id=row.id,
        # The parent's account, as ``entry_service.create_entry`` writes it:
        # ``fk_transaction_entries_parent_account`` refuses any other value.
        account_id=row.account_id,
        # The AUTHOR column names who recorded the movement; the seam records
        # it on the owner's behalf, and the owner is the row's.
        user_id=row.user_id,
        is_credit=False,
        # The mark the seam finds its own mirror by (module docstring).
        covers_settlement=True,
    )
    _record_onto(row, movement, settlement)
    # Appended to the relationship rather than only added to the session, so
    # the verbs' ledger reconcile -- which walks ``txn.entries`` after the seam
    # returns -- posts it in the same pass as the parent's now-zero leg.
    row.entries.append(movement)
    db.session.add(movement)


def _release(row: Transaction) -> None:
    """Delete *row*'s covering movements: leaving the band withdraws them."""
    for movement in covering_movements(row):
        # Reverse FIRST: ``journal_entries.transaction_entry_id`` is SET NULL
        # on delete, so legs left behind could never be reversed.
        posting_service.reverse_purchase_postings_before_delete(movement)
        # Removed from the collection, not only marked deleted: the verbs'
        # ledger reconcile walks ``txn.entries`` after the seam returns, and
        # ``delete-orphan`` on the relationship is what issues the DELETE.
        row.entries.remove(movement)


def record_clearing(row: Transaction, anchor_id: int) -> None:
    """Record WHICH statement showed *row*'s money, on the row and its mirror.

    The transaction twin of ``transfer_service._settle.record_clearing``, and
    the ONE writer of a transaction's ``reconciled_by_id`` outside the seam's
    own release arms (plan step **X-bi-3a**, ruling **R-FL**).  The reconcile
    panel records the link AFTER the settle verb returns -- the verb is shared
    with the grid's Mark Paid, which no statement has shown -- and the settle
    has by then mirrored the row's money onto its covering movement, whose
    fact is the one the fold and ``StatementCoverage`` read.  A link written
    on the row alone would leave that fact unlinked and the panel's own
    clearing rule inert for every bill it ticks (found by
    ``test_cash_walk``'s governing-assertion case, 2026-09-16).

    Args:
        row: The settled transaction the statement showed.
        anchor_id: The ``account_anchor_history`` row that was being read.
    """
    row.reconciled_by_id = anchor_id
    for movement in covering_movements(row):
        movement.reconciled_by_id = anchor_id


def sync_covering_movement(
    row: Transaction,
    *,
    was_settled: bool,
    now_settled: bool,
    settlement: Optional[Settlement],
) -> None:
    """Keep *row*'s covering movement in step with the record the seam wrote.

    Called by ``apply_status_change`` after it has written the row's status,
    settle-day pair, clearing link and settlement record, so every value
    mirrored here is the row's FINAL one for this act.  Three cases, total over
    what the seam can be asked to do:

    * **leaving the settled band** -- delete the covering movement, whatever
      *settlement* says; the row's retained record rebuilds it at the next
      settle;
    * **in the band with a record** -- the row is settling or re-settling on a
      ``derived`` / ``corrected`` basis: ensure one covering movement carries
      the record.  A ``purchases`` record covers nothing (the row's purchases
      are the record);
    * **in the band with no record** -- an identity re-submit or a settle-day
      correction: the movement's day pair and link follow the row's.

    Args:
        row: The transaction the seam just wrote.
        was_settled: Whether the row was in the settled band BEFORE the seam
            assigned its new status.
        now_settled: Whether it is in the band after.
        settlement: The record the seam was handed for this act, or ``None``.
    """
    if not _is_covered_kind(row):
        return
    if was_settled and not now_settled:
        _release(row)
        return
    if not now_settled:
        return
    if settlement is not None and settlement.basis in _COVERED_BASES:
        _cover(row, settlement)
        return
    for movement in covering_movements(row):
        _mirror_assertion(row, movement)
