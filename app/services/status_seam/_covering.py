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

**The 3c gate, stated so that leaf deletes its arm.**  This module covers
every parent that is not a transfer shadow.  ``X-bi-3a`` covered EXPENSE
parents; ``X-bi-3b`` covered INCOME parents once a movement's direction was
its parent's everywhere it is read (``cash_ledger.movement_cash_leg``, ruling
**R-BAL35**) -- until then six readers spelled *a purchase is money leaving*
and a covered paycheck read ``-figure``.  A transfer shadow waits for
``X-bi-3c``, which writes both legs through ``transfer_service`` under
Transfer Invariant 4.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
mutates in place and never commits; the release arm's posting reversal
FLUSHES, as every ledger write does, and the caller owns the session
boundary.  Money is ``Decimal`` throughout, read off the
:class:`~app.services.status_seam._record.Settlement` the seam was handed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    SettlementBasisEnum,
)
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services.cash_ledger import movement_cash_leg, settled_cash_leg
from app.services.settle_day import (
    figure_source_of,
    record_settle_day,
    recorded_settle_day,
)
from app.services.status_seam._record import Settlement

#: The settlement bases whose record a covering movement mirrors.  A
#: ``purchases`` settlement stores no figure because the row's own purchases ARE
#: the record, so there is nothing to cover.
_COVERED_BASES = frozenset({
    SettlementBasisEnum.DERIVED, SettlementBasisEnum.CORRECTED,
})



def _source_of(row: Transaction, settlement: Settlement) -> MovementFigureSourceEnum:
    """Return WHO WROTE the figure a settle records.

    A ``derived`` record is the settle's own resolution of the plan
    (``resolved``); a ``corrected`` one was STATED, and who stated it is the
    day's basis question ``settle_day.figure_source_of`` answers -- the bank,
    when the statement matcher settled the row on an ``observed`` day with
    the line's figure (``_moving``), else a person.  One rule with the
    purchase doors and the migration's backfill, so the cutover
    (``X-bi-3d``) classifies a row exactly as the seam would have.
    """
    if settlement.basis is SettlementBasisEnum.DERIVED:
        return MovementFigureSourceEnum.RESOLVED
    return figure_source_of(recorded_settle_day(row))


def _is_covered_kind(row: Transaction) -> bool:
    """Return whether the seam writes a covering movement for *row*.

    The 3c gate from the module docstring: any row that is not a transfer
    shadow.  It read ``and row.is_expense`` until plan step ``X-bi-3b``
    deleted the income half; ``X-bi-3c`` deletes the rest.
    """
    return row.transfer_id is None


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

    Each posted covering movement is worth
    :func:`app.services.cash_ledger.movement_cash_leg` -- the ONE valuation
    of a movement, its whole figure in its PARENT's direction (plan step
    ``X-bi-3b``, ruling **R-BAL35**), which is what the fact producer and the
    ledger writer book for it.  This read ``-movement.amount`` for itself
    until that step, a second spelling stated rather than hidden because a
    spelling that agrees today is still two spellings (rule 14); one producer
    is what deleted it.

    **TOTAL over the parent's contributing gate, as every reader of a
    purchase is** (ruling **R-FM**): a soft-deleted or Credit / Cancelled
    parent's purchases post nothing and fold to nothing
    (``_posted_purchase_facts``, ``purchase_posts``), so its covering
    movement is worth nothing here too -- the producer's own gate, and this
    module's ``is_balance_contributing`` guard went with the spelling.  The
    first cut summed the movement regardless and the accepted register read a
    soft-deleted bill as still holding (``test_withdrawal``'s soft-delete
    case, 2026-09-16).

    Args:
        row: The transaction, with ``entries`` loaded or loadable.

    Returns:
        The signed sum, ``Decimal("0")`` when nothing is covered, posted or
        contributing.
    """
    return sum(
        (
            movement_cash_leg(row, movement)
            for movement in covering_movements(row)
            if movement.settled_on is not None
        ),
        Decimal("0"),
    )


def settled_family_leg(row: Transaction) -> Decimal:
    """Return what a settled *row* is WORTH: its own leg plus its record's.

    **The one valuation of a settled row's family**, for every reader that
    asks what the row moves rather than what its own leg books: the
    statement matcher's offer and post-apply check (``_candidates._price``),
    its accepted register (``_accepted_view``) and its undo dialog
    (``_release``).  ``cash_ledger.settled_cash_leg`` answers zero for a
    covered bill by ruling **R-FM**'s identity; this adds the movement back.
    Three readers spelled ``settled_cash_leg`` alone after X-bi-3a's first
    cut and two of them read every accepted bill as a match that stopped
    holding and every undo as moving no money (adversarial review,
    2026-09-16); one producer is the remedy, not three patches.

    Args:
        row: The settled transaction, with ``entries`` loaded or loadable.

    Returns:
        The signed ``Decimal`` the family moves through its account.

    Raises:
        AmountUnresolvable: From ``settled_cash_leg``, for a row it refuses.
    """
    return settled_cash_leg(row) + covered_cash_leg(row)


def _mirror_assertion(row: Transaction, movement: TransactionEntry) -> None:
    """Bring *movement*'s settle-day pair and statement link up to the row's.

    **A day that did not MOVE never lowers evidence, and a confirmation
    RAISES it** -- the seam's own rule for the row, applied to its movement.
    The ROW is what the matcher offers and the panel ticks (its mirror is
    kept out of the purchase candidates), so the row's assertion is the one
    that moves and the movement follows it:

    * the row's day DIFFERS from the movement's -- a settle, a re-settle
      after a revert, a settle-day correction: the movement takes the row's
      pair, through ``record_settle_day`` so the pair keeps its one writer and
      its books-boundary refusal, and the row's link with it (the row's own
      release logic already decided that link for this move).  A covering
      movement's purchase day IS its settle day (ruling R-BAL39), so the
      correction moves both;
    * the days are EQUAL and the row's basis rose to ``observed`` -- a bank
      line confirmed the day the panel had only bounded (``_moving``, finding
      **N-332**): the movement's basis rises with it and the link stands, the
      same strengthening the row itself records;
    * the days are EQUAL otherwise: the pair stands, and the movement takes
      the row's link only where it holds none -- the reconcile panel ticked
      the row on an asserted day, and the movement sits inside that assertion
      too.  An identity re-submit (a popover Save with the status untouched)
      lands here and changes nothing.
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
        return
    observed = ref_cache.settled_day_basis_id(SettledDayBasisEnum.OBSERVED)
    if (
        row.settled_day_basis_id == observed
        and movement.settled_day_basis_id != observed
    ):
        record_settle_day(movement, recorded_settle_day(row))
    if movement.reconciled_by_id is None:
        movement.reconciled_by_id = row.reconciled_by_id


def _record_onto(
    row: Transaction, movement: TransactionEntry, settlement: Settlement,
) -> None:
    """Write what a settle RECORDS onto *movement*: figure, source, name, day."""
    movement.amount = settlement.amount
    movement.figure_source_id = ref_cache.movement_figure_source_id(
        _source_of(row, settlement),
    )
    # The plan's name as it reads at the settle -- the movement's OWN fact
    # (ruling R-BAL39): a bill's payment has no receipt text, and a later
    # rename of the plan no more rewrites this than renaming an envelope
    # rewrites its purchases.  Its day, likewise its own, is the mirror's.
    movement.description = row.name
    _mirror_assertion(row, movement)


def _cover(row: Transaction, settlement: Settlement) -> None:
    """Ensure *row* holds exactly one covering movement mirroring *settlement*.

    **A ``$0.00`` settlement writes NO movement** -- zero movements is a
    legal count and ``ck_transaction_entries_positive_amount`` (``<> 0``)
    says a movement of nothing is not one -- so a re-record to zero
    withdraws an existing mirror.  The same arm ruling **R-BAL40** gives the
    cutover.  Reachable: a bill budgeted at ``$0.00`` marked paid, or a typed
    ``$0.00`` on the panel or the popover (both schemas admit it).
    """
    if not settlement.amount:
        _release(row)
        return
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
      the record, or none for a ``$0.00`` figure.  A ``purchases`` record
      covers nothing (the row's purchases are the record), and withdraws a
      mirror it finds;
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
    if settlement is not None:
        # A ``purchases`` record: the row's own purchases ARE the record, so
        # a mirror left from an earlier manual-branch close would be a second
        # statement of money the purchases already carry.  Unreachable today
        # (a revert deletes the mirror before purchases can be added), and
        # stated rather than tolerated.
        _release(row)
        return
    for movement in covering_movements(row):
        _mirror_assertion(row, movement)
