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
the same day.  The fold's fact producer reads the same family
(``_posted_purchase_facts``, every POSTED movement), the posting writer posts
it (``posting_service.sync_transaction_postings`` walks ``txn.entries``), and
the statement matcher drops a zero-effect row from its offer
(``_candidates.transaction_candidate``) and offers the ROW priced at its
family instead (``_candidates._price`` reads :func:`settled_family_leg`; the
mirror itself is kept out of the purchase candidates by
:func:`covering_clause`).  None of those readers branches on the row's
kind; the one place a kind branch stands is the POSTING doors, which return
for a transfer shadow's entries, and that branch is ruling **R-BAL45**'s
interval rather than a reader deciding for itself (below).

**A revert UN-DATES the covering movement and KEEPS it** (ruling
**R-BAL61**, plan step ``X-bi-3e-2``).  Leaving the settled band releases
the row's assertion and keeps what moved (plan step X-au-c3:
``settled_amount`` and ``settled_basis_id`` outlive a revert), and the next
settle honours a retained ``corrected`` record or re-prices a ``derived``
one (``Settlement.from_settle``).  The movement is that record's mirror and,
since plan step ``X-bi-3e-1``, the record's only home for WHO WROTE the
figure, which the row's columns never held -- so the mirror follows the
record: its day pair and clearing link are released with the row's
(``_follow_assertion``), its figure, source and row survive, and the re-settle
re-dates the SAME row (``_cover`` through ``_mirror_assertion``; the id
survives).  A revert deleted it through ``X-bi-3e-1``, and the retained read
answered by R-BAL61's cutover mapping (``_record.recorded_settlement``,
ruling **R-BAL70**) for every reverted row; that mapping now answers only a
record no movement can carry.  What a kept, un-dated movement must NOT do is
read as a purchase -- an envelope closed EMPTY at the door, reverted and
then given real purchases would sum the stale close into them -- and ruling
**R-BAL68** answers that where the readers are: every purchase-meaning
reader asks :attr:`~app.models.transaction.Transaction.purchases`, the
entries less the mark, and the family readers named above keep ``entries``.
Two records still WITHDRAW the mirror outright (``_withdraw``): a ``$0.00``
figure, which ``ck_transaction_entries_positive_amount`` lets no movement
carry, and a ``purchases`` record, whose figure the row's own purchases
state.

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

**Every settled row is covered, whatever its kind, and the kinds arrived one
leaf at a time.**  ``X-bi-3a`` covered EXPENSE parents; ``X-bi-3b`` covered
INCOME parents once a movement's direction was its parent's everywhere it is
read (``cash_ledger.movement_cash_leg``, ruling **R-BAL35**) -- until then
six readers spelled *a purchase is money leaving* and a covered paycheck read
``-figure``; ``X-bi-3c`` covered the two shadows of a TRANSFER.  A shadow is
reached here through ``transfer_service`` alone
(``_status.apply_status_to_all_three`` hands the seam one ``Settlement`` for
the pair, one call per leg), which is Transfer Invariant 4 kept without a
second writer: the transfer settle and the transaction settle call the ONE
writer, and it is this module.  Each leg's movement moves in its own leg's
direction -- the expense shadow's ``-figure`` off the from-account, the
income shadow's ``+figure`` into the to-account -- through the same producer
a bill's and a paycheck's read.

**A transfer's movements POST nowhere until the ledger takes its ruled
shape** (ruling **R-BAL45**, developer 2026-09-16).  The posted ledger books
a settled transfer as ONE journal entry ``{from -figure, to +figure}`` off the
income shadow's record (``posting_service.sync_transfer_postings``), and every
purchase-posting door returns for a shadow's entries; so through the interval
the walk reads a covered leg as ``0 + movement`` and the ledger as the row's
record, and the two agree because this module mirrors one record into both
homes (the same interval ruling **R-BAL40** accepts for a bill).  The ruled
endpoint is two entries per transfer, one per movement on its own bank day,
each against a transfers-in-transit clearing account -- which needs per-leg
settle days and so waits for ``X-bi-6``, the step that deletes the shadow
mirror and Transfer Invariant 3's one-day-per-pair clause with it.  Posting a
shadow's movement through the purchase source was rejected there: that
source's counter leg is the parent's CATEGORY account, and a transfer between
two of the owner's accounts is neither income nor expense.

**A movement moves with its parent** (ruling **R-BAL46**).  The one parent
whose account can change is a shadow re-pointed by
``transfer_service._endpoints._apply_endpoint_move``; the co-located key
``fk_transaction_entries_parent_account`` cascades the move (migration
``c4e8a2d7f1b3``) and the applier assigns the movements' account as well, so
the session agrees with the database.  Nothing here reads the account.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
mutates in place and never commits; the withdraw arm's posting reversal
FLUSHES, as every ledger write does, and the caller owns the session
boundary.  The ledger is otherwise the DOOR's: a revert un-dates the mirror
here and the verb's family reconcile (``posting_service.
sync_transaction_postings``, which walks ``txn.entries`` after the seam
returns and posts nothing for an un-dated movement) reverses its legs, as it
reverses the parent's own -- every production revert of a transaction reaches
the seam through ``transaction_service.apply_requested_status``, and a
transfer's shadows post nowhere (R-BAL45); ``scripts/integrity_check.py``'s
DC-10 grades the state a caller of the bare seam would leave.  Money is
``Decimal`` throughout, read off the
:class:`~app.services.status_seam._record.Settlement` the seam was handed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services.cash_ledger import movement_cash_leg, settled_cash_leg
from app.services.settle_day import record_settle_day, recorded_settle_day
from app.services.status_seam._record import Settlement


def covering_clause():
    """Return the SQL form of *this purchase is a covering movement*.

    :attr:`~app.models.transaction.Transaction.covering_movements` asks the
    question of ONE loaded row; this asks it of a query over
    ``TransactionEntry``, for a reader that must leave the seam's mirrors OUT
    of a row set -- the statement matcher's purchase candidates and the
    reconcile panel's outstanding purchases, which offer a person's purchases
    and never the row's own payment record.

    Returns:
        A SQLAlchemy boolean expression over ``TransactionEntry``.
    """
    return TransactionEntry.covers_settlement.is_(True)


def covered_cash_leg(row: Transaction) -> Decimal:
    """Return the cash *row*'s POSTED covering movements carry, signed.

    **The other half of ruling R-FM's identity, for a reader that asks what a
    row is WORTH rather than what its own leg books.**  Once a bill is
    covered, ``cash_ledger.settled_cash_leg`` answers zero for it and its
    movement carries the money; the statement matcher prices a row by what
    the bank would see for it (``_candidates._price``), which is the family:
    the row's leg plus this.  The ROW stays the matcher's subject until plan
    step ``X-bi-4`` re-points the fold onto movements -- its mirror is
    excluded from the purchase candidates by :func:`covering_clause` -- so a
    bill is offered, matched and re-dated as one thing, and the seam's mirror
    carries the bank's day down to the movement.  An UN-DATED movement -- a
    reverted row's, kept since plan step ``X-bi-3e-2`` -- is worth nothing
    here, as it posts nothing (``purchase_posts``) and folds to nothing
    (``_posted_purchase_facts``): the same three-way agreement, stated by
    the day rather than by the row's status.

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
            for movement in row.covering_movements
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
    """Write what a settle RECORDS onto *movement*: figure, source, name, day.

    The source is the record's own -- WHO WROTE the figure, stated by the
    door that handed the verb a :class:`~app.services.stated_figure.
    StatedFigure` or by the verb's own ``resolved`` arm (ruling **R-BAL61**,
    plan step X-bi-3e-1).  It was inferred here from the day's basis beside
    the figure until that step, and the premise was measured false: a figure
    a person typed over a standing bank-observed day was labelled the bank's.
    """
    movement.amount = settlement.amount
    movement.figure_source_id = ref_cache.movement_figure_source_id(
        settlement.source,
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

    **A re-settle re-dates the movement the revert kept** (plan step
    ``X-bi-3e-2``): the survivor is found by its mark, the record is written
    onto it -- a re-priced figure and its source over the old ones, the
    plan's name as it reads now -- and ``_mirror_assertion`` dates it on the
    row's new day.  The id survives, so a match or a log line that named it
    still names it.
    """
    if not settlement.amount:
        _withdraw(row)
        return
    existing = row.covering_movements
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


def _withdraw(row: Transaction) -> None:
    """Delete *row*'s covering movements: a record that carries nothing.

    The two records that WITHDRAW a mirror rather than un-date it (module
    docstring): a ``$0.00`` figure, which
    ``ck_transaction_entries_positive_amount`` lets no movement carry, and a
    ``purchases`` record, whose figure the row's own purchases already state
    -- a mirror kept beside them would be a second statement of that money.
    Leaving the band is NOT one of these; that arm keeps the movement
    (``_follow_assertion``).
    """
    for movement in row.covering_movements:
        # Reverse FIRST: ``journal_entries.transaction_entry_id`` is SET NULL
        # on delete, so legs left behind could never be reversed.
        posting_service.reverse_purchase_postings_before_delete(movement)
        # Removed from the collection, not only marked deleted: the verbs'
        # ledger reconcile walks ``txn.entries`` after the seam returns, and
        # ``delete-orphan`` on the relationship is what issues the DELETE.
        row.entries.remove(movement)


def _follow_assertion(row: Transaction) -> None:
    """Bring *row*'s covering movements' day pair and link up to the row's.

    The mirror follows the row's ASSERTION, through ``_mirror_assertion``'s
    one rule per movement, and the act that arm answers is the row's:

    * **a revert** (ruling **R-BAL61**, plan step ``X-bi-3e-2``): the seam
      has already cleared the row's day pair and clearing link, and the
      differing-day arm writes that release onto the movement --
      ``record_settle_day(movement, None)`` clears the pair
      (``ck_transaction_entries_settle_day_basis_pairing`` is a
      biconditional) and the link goes with it
      (``ck_transaction_entries_cleared_needs_settle_day``).  The figure,
      its source and the row itself STAY: what moved is retained across a
      revert exactly as the row's own ``settled_amount`` is (plan step
      X-au-c3), and the next settle re-dates the same row (``_cover``);
    * **a settle-day correction**: the movement takes the row's new pair;
    * **an identity re-submit**, or a non-settled row that still carries a
      kept movement (re-submitted, cancelled, reactivated): nothing moves.

    The ledger is the door's.  An un-dated movement posts nothing
    (``_posting_purchases.purchase_posts`` needs a day), so the family
    reconcile every revert door runs after the seam reverses whatever the
    movement had posted -- the same walk that reverses the parent's own leg,
    one spelling rather than an explicit reversal here beside it.  The revert
    arm deleted the row and reversed its legs itself through ``X-bi-3e-1``,
    because ``journal_entries.transaction_entry_id`` is SET NULL on delete
    and legs left behind a deleted row could never be reversed; a kept row
    has no such hazard.
    """
    for movement in row.covering_movements:
        _mirror_assertion(row, movement)


def record_clearing(row: Transaction, anchor_id: int) -> None:
    """Record WHICH statement showed *row*'s money, on the row and its mirror.

    The ONE writer of a transaction's ``reconciled_by_id`` outside the seam's
    own release arms (plan step **X-bi-3a**, ruling **R-FL**), for a plain
    row and for a transfer shadow alike: the reconcile panel's transaction
    arm calls it directly, and its transfer arm through
    ``transfer_service.record_clearing``, the shadow's door under Transfer
    Invariant 4, which delegates here since plan step **X-bi-3c** (a shadow
    carries a covering movement from that step, so the door's own one-column
    write would have left the leg's fact unlinked).  The reconcile panel
    records the link AFTER the settle verb returns -- the verb is shared with
    the grid's Mark Paid, which no statement has shown -- and the settle has
    by then mirrored the row's money onto its covering movement, whose fact
    is the one the fold and ``StatementCoverage`` read.  A link written on
    the row alone would leave that fact unlinked and the panel's own clearing
    rule inert for every bill it ticks (found by ``test_cash_walk``'s
    governing-assertion case, 2026-09-16).

    Args:
        row: The settled transaction the statement showed -- a plain row, or
            the one LEG of a transfer on the account whose statement was read
            (clearing is per leg; ``transfer_service.record_clearing`` says
            why the sibling takes none).
        anchor_id: The ``account_anchor_history`` row that was being read.
    """
    row.reconciled_by_id = anchor_id
    for movement in row.covering_movements:
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
    mirrored here is the row's FINAL one for this act.  Two cases, total over
    what the seam can be asked to do:

    * **in the band with a record** -- the row is settling or re-settling on a
      ``derived`` / ``corrected`` basis: ensure one covering movement carries
      the record (``_cover``), or none for a ``$0.00`` figure.  A
      ``purchases`` record covers nothing (the row's purchases are the
      record), and withdraws a mirror it finds (``_withdraw``);
    * **otherwise the mirror follows the row's assertion** -- a revert
      (the row's pair and link were released, so the movement's are:
      ``_follow_assertion``, plan step ``X-bi-3e-2``), an identity re-submit
      or a settle-day correction.

    **A row in the band neither before nor after this act is skipped**: its
    assertion did not change, so a movement a revert kept has nothing to
    follow (the days already agree at ``None`` and the link is already
    ``None`` -- ``_mirror_assertion``'s equal-days arm would write nothing),
    and reading it would cost a Projected TRANSFER's every popover Save two
    lazy loads and a mid-update autoflush for a no-op (each shadow's
    ``entries``; a transaction's door loads them for its reconcile anyway).
    The seam refuses a record beside a non-settled status before it gets here
    (``reject_settlement_without_settled_status``), so *settlement* is
    ``None`` whenever *now_settled* is ``False``.  The status the row was
    LEAVING was a third CASE through ``X-bi-3e-1`` (leaving the band deleted
    the movement); it is a load gate now, and the act it gates is the same
    "follow the row" a correction runs.

    Args:
        row: The transaction the seam just wrote.
        was_settled: Whether the row was in the settled band BEFORE the seam
            assigned its new status.
        now_settled: Whether it is in the band after.
        settlement: The record the seam was handed for this act, or ``None``.
    """
    if not was_settled and not now_settled:
        return
    if now_settled and settlement is not None:
        # A record that STATES a figure names who wrote it (``Settlement``
        # refuses one without the other), and only such a record has anything
        # to mirror: a ``purchases`` record has neither.
        if settlement.source is not None:
            _cover(row, settlement)
        else:
            _withdraw(row)
        return
    _follow_assertion(row)
