"""
Shekel Budget App -- Status Seam: the settlement RECORD

WHAT a settle records -- the figure that moved, who wrote it, and the reads
over it -- as a value type plus the three questions asked about one.  The
STATUS mechanics that write it are :mod:`._seam`'s; the invariants that refuse
a malformed one are :mod:`._refusals`'.

Split out of the single ``status_seam`` module at plan step **X-au-c3**, on the
ground ``transfer_service`` and ``cash_ledger`` were split on before it: the
module reached the 1000-line ceiling, and the split is BY RESPONSIBILITY rather
than by line count.  The record is a different subject from the status: a status
says where a row IS and a record says what its money DID, and the second
outlives a change to the first (a revert releases the assertion and keeps what
moved).

**The record carries WHO WROTE the figure since plan step X-bi-3e-1** (rulings
**R-BAL61**, **R-BAL69**), and the basis that names HOW the figure is known is
derived from it rather than stated beside it: a figure the settle resolved is
``derived``, a figure somebody stated is ``corrected``, and a row whose
purchases are the record has neither.  Two stated fields for one fact would be
rule 14's two homes with a fence between them; one field and a property is the
value type holding the invariant by construction.

Pure: reads columns and the ref cache, constructs values.  No session, no
mutation, no Flask.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettlementBasisEnum
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.row_valuation import recorded_figure
from app.services.stated_figure import StatedFigure


@dataclass(frozen=True)
class Settlement:
    """WHAT a settle records: the figure that moved and who wrote it.

    The value a settle door hands :func:`apply_status_change` so the whole
    record -- the day, the figure and its source -- is written in ONE act (plan
    step **X-au-c3**).  Handing the seam one value is what keeps the figure and
    its provenance from being written apart -- ``Settlement.__post_init__``
    refuses a malformed pair, so a door cannot build one to hand over, and
    ``ck_transactions_settled_amount_needs_basis`` is the storage-tier backstop
    for the half a constructor cannot reach.

    **The constructor is where "``purchases`` stores no figure" is enforced**,
    and that placement is the point.  It is the one half of the record's pairing
    a CHECK cannot state: saying it needs the constraint to name a
    ``ref.settlement_bases`` id, which is what this project's ref convention
    keeps out of a schema.  A rule that cannot be a constraint should be a
    CONSTRUCTOR invariant rather than prose -- a settle door cannot build a
    malformed record to hand over, so no door can write one.

    **The SOURCE is the stated field and the BASIS is derived from it** (plan
    step **X-bi-3e-1**, ruling **R-BAL69**).  ``figure_source_id`` on the
    covering movement records who wrote the figure -- the settle's own pricing
    (``resolved``), a person (``typed``) or the bank's line (``observed``) --
    and ``settled_basis_id`` on the row records how the figure is known
    (``derived``, ``corrected``, ``purchases``); the second is a function of
    the first, so the value carries one and answers the other.  The row's
    column is that answer's projection through the interval ruling
    **R-BAL40** accepts, and plan step ``X-bi-4`` deletes it.

    Attributes:
        amount: What moved.  ``None`` exactly when the row's own purchases
            state the figure (:attr:`basis` ``purchases``), where a stored copy
            would need a reconciler.
        source: Who wrote it (:class:`app.enums.MovementFigureSourceEnum`).
            ``None`` exactly when *amount* is: a purchases record has no
            figure of its own to have been written.
    """

    amount: Optional[Decimal]
    source: Optional[MovementFigureSourceEnum]

    def __post_init__(self) -> None:
        """Refuse a record whose figure and source contradict each other.

        Raises:
            ValueError: When a figure arrives with no source, or a source with
                no figure.  A programming error at the call site rather than a
                user error, so it is not a ``ValidationError``: no form can
                express either state.
        """
        if self.amount is not None and self.source is None:
            raise ValueError(
                f"A settlement stating the figure {self.amount} must say who "
                "wrote it: the settle's own pricing ('resolved'), a person "
                "('typed') or the bank's line ('observed'). Only a "
                "'purchases' record leaves both out, because there the row's "
                "own entries state the figure."
            )
        if self.amount is None and self.source is not None:
            raise ValueError(
                "A 'purchases' settlement stores no figure, so it names no "
                f"writer: {self.source.value!r} here would credit someone "
                "with a figure the row's own children state. Pass source=None "
                "with amount=None."
            )

    @property
    def basis(self) -> SettlementBasisEnum:
        """Return HOW the figure is known, derived from who wrote it.

        The row's ``settled_basis_id`` column, answered from the record's one
        stated field: the settle's own pricing is ``derived``; a figure a
        person or the bank stated is ``corrected``; a record with no figure is
        ``purchases``.  ``SettlementBasisEnum``'s ``corrected`` member reads
        *a human typed it* in its own docstring, and a bank-stated figure files
        under it through the interval because the row's column has no member
        for the bank -- the catalogue that does, ``figure_source_id``'s, is on
        the movement, and it is the one that outlives ``X-bi-4``.

        Returns:
            The :class:`~app.enums.SettlementBasisEnum` member.
        """
        if self.source is None:
            return SettlementBasisEnum.PURCHASES
        if self.source is MovementFigureSourceEnum.RESOLVED:
            return SettlementBasisEnum.DERIVED
        return SettlementBasisEnum.CORRECTED

    @classmethod
    def from_settle(
        cls,
        booked: Decimal,
        correction: "StatedFigure | None",
        retained: "Settlement | None" = None,
    ) -> "Settlement":
        """Return the record a settle makes, given every figure it may have.

        **A stated figure beats the app's, and the rule is stated ONCE here**
        because both settle verbs make the same choice -- the transaction's
        (``transaction_service._settle.settle_transaction``) and the transfer's
        (``transfer_service._settle.settle``).  Two spellings of one money rule
        is this arc's own root cause 1.

        A figure somebody read off a statement is a FACT; what the app resolved
        is an inference, however good.  So a correction wins, and the record says
        WHO stated it -- the person who typed it or the bank line the matcher
        matched (ruling **R-BAL61**) -- which is what makes "did somebody
        correct this, and who" a stored answer rather than one re-derived by
        comparing the figure against a recomputation that may since have moved.

        *correction* is already the ECHO rule's answer: a submitted figure equal
        to what the row would book anyway is not a correction, and both callers
        resolve that through their own echo predicate before they
        get here (finding **N-231**).  So a ``None`` here means "nobody stated
        a different number", not "nobody stated one".

        **A RETAINED correction outlives the settle that recorded it, and
        honouring it here is what makes a revert non-destructive** (plan step
        X-au-c3, developer 2026-08-17).  A revert releases the assertion --
        ``settled_on`` and the clearing link -- and keeps what moved, so a row
        the user reverted in order to edit still carries the figure they read
        off their statement.  Re-deriving over it would delete that figure one
        step later than the release used to, which is the same data loss with an
        extra hop: the popover TELLS the user to revert in order to edit, so the
        round trip has to be lossless or the instruction is a trap.  The
        retained record is returned WHOLE, its source with it: who wrote a
        figure does not change because the row was reverted.

        Only a ``corrected`` record is honoured.  A ``derived`` one is the app's
        own inference about a moment that has passed, and re-resolving it is
        strictly better than reusing it -- the plan may legitimately have been
        re-priced meanwhile.  A ``purchases`` record stores no figure at all, so
        there is nothing to retain: its entries still state it.

        Args:
            booked: What the app resolved this row to be worth at the moment of
                the settle.
            correction: The figure a door stated and who wrote it
                (:class:`~app.services.stated_figure.StatedFigure`), when it
                differs from *booked*; ``None`` otherwise.
            retained: What the row already records, when it still carries a
                record from an earlier settle it has since been reverted out of
                (``status_seam.recorded_settlement``); ``None`` when it carries
                none.

        Returns:
            A ``corrected`` record for a figure stated now or stated before and
            not withdrawn, else a ``derived`` one on the ``resolved`` source.
        """
        if correction is not None:
            return cls(amount=correction.amount, source=correction.source)
        if retained is not None and retained.basis is (
            SettlementBasisEnum.CORRECTED
        ):
            return retained
        return cls(amount=booked, source=MovementFigureSourceEnum.RESOLVED)


def covering_movements(row: Transaction) -> list[TransactionEntry]:
    """Return the covering movements *row* holds -- the settle's, not a person's.

    By the mark the seam left (:mod:`._covering`'s module docstring); at most
    one, by the partial unique index, and a list rather than an optional so a
    caller that walks the family needs no branch.  It lives HERE rather than
    beside the writer in :mod:`._covering` because the record's read
    (:func:`recorded_settlement`) needs it and the writer imports this
    module; the package re-exports it unchanged.

    Args:
        row: The transaction, with ``entries`` loaded or loadable.

    Returns:
        The covering movements, in ``entries`` order; empty when none.
    """
    return [entry for entry in row.entries if entry.covers_settlement]


def _recorded_basis(row: Transaction) -> Optional[SettlementBasisEnum]:
    """Return HOW *row*'s recorded figure is known, or ``None`` for no record.

    The ONE decode of ``settled_basis_id``, shared by the two reads over it:
    :func:`recorded_settlement`, which goes on to read the movement for the
    source, and :func:`honoured_correction`, which needs only the basis and
    must stay a column read (it prices a whole grid).

    Raises:
        KeyError: When ``settled_basis_id`` names no
            :class:`~app.enums.SettlementBasisEnum` member.  Unreachable
            through the FK, which admits only the seeded rows; it is how a
            member ADDED without this map being extended fails loudly.
    """
    if row.settled_basis_id is None:
        return None
    return {
        ref_cache.settlement_basis_id(member): member
        for member in SettlementBasisEnum
    }[row.settled_basis_id]


def recorded_settlement(row: Transaction) -> Optional[Settlement]:
    """Return the settlement *row* already records, or ``None`` if it has none.

    The read half of the record, for the callers that must carry a row's
    record forward without inventing either term: both settle verbs honouring
    a RETAINED correction across a revert (:meth:`Settlement.from_settle`), and
    ``transfer_service._status.apply_status_to_all_three`` resolving a drifted
    shadow's record from its SIBLING's -- Transfer Invariant 3 read rather than
    maintained, the exact rule the pair's settle DAY already follows.

    **The figure and its basis are the row's columns; the SOURCE is the
    covering movement's** (plan step **X-bi-3e-1**, ruling **R-BAL61**).  The
    row stores no writer -- it never did -- and the movement the seam wrote
    for the record is where that fact lives (``figure_source_id``, the
    column that outlives ``X-bi-4``).  A row reverted out of the band holds
    that movement undated from plan step ``X-bi-3e-2``; until then a revert
    deletes it, and every reverted row reads by the mapping below.

    **A record with NO covering movement reads by ruling R-BAL61's cutover
    mapping** (ruling **R-BAL70**): a ``derived`` record was the settle's own
    pricing (``resolved``) and a ``corrected`` one was stated (``typed``) --
    the classification the cutover migration ``ad573b07bede`` gave every row
    whose writer was never stored, applied here to the records it could not
    reach.  Two such records exist: a ``$0.00`` figure, which
    ``ck_transaction_entries_positive_amount`` lets no movement carry (so its
    writer is stored nowhere, and a re-settle writes none again), and a row
    reverted on a tree that still deleted the movement.  It is a mapping over
    the record's BASIS, never over the day beside it -- the inference R-BAL61
    refutes -- and it retires with the row's columns at ``X-bi-4``.

    Args:
        row: The transaction to read, with ``entries`` loaded or loadable.

    Returns:
        The recorded :class:`Settlement`, or ``None`` when the row has not
        settled.

    Raises:
        KeyError: When ``settled_basis_id`` or the movement's
            ``figure_source_id`` names no member of its enum.  Unreachable
            through the foreign keys, which admit only the seeded rows; it is
            how a member ADDED without this map being extended fails loudly.
    """
    basis = _recorded_basis(row)
    if basis is None:
        return None
    if basis is SettlementBasisEnum.PURCHASES:
        return Settlement(amount=None, source=None)
    movements = covering_movements(row)
    if movements:
        source = {
            ref_cache.movement_figure_source_id(member): member
            for member in MovementFigureSourceEnum
        }[movements[0].figure_source_id]
    elif basis is SettlementBasisEnum.DERIVED:
        source = MovementFigureSourceEnum.RESOLVED
    else:
        source = MovementFigureSourceEnum.TYPED
    return Settlement(amount=row.settled_amount, source=source)


def honoured_correction(row: Transaction) -> Optional[Decimal]:
    """Return the figure a RETAINED correction still states, or ``None``.

    **The ONE statement of "a human's figure outlives the settle that recorded
    it"**, asked by :func:`settle_amount` -- what the reconcile panel OFFERS --
    and by :func:`settle_transaction` -- what a tick BOOKS -- so the two cannot
    answer differently (plan step X-au-c3, developer 2026-08-17).

    A revert releases the ASSERTION and keeps what moved
    (``status_seam.apply_status_change``), so a row the user reverted in order
    to edit still carries the figure they read off their statement.  This is
    what makes that figure AUTHORITATIVE rather than merely remembered: it is
    what a re-settle books, and therefore what the panel must show.

    **Both halves are load-bearing, and the first draft had only the second.**
    ``Settlement.from_settle`` honoured a retained correction while
    :func:`settle_amount` went on pricing the PLAN, so a reverted ``$500.00``
    bill that had been corrected to ``$245.32`` was OFFERED at ``$500.00`` and
    BOOKED at ``$245.32`` -- measured end to end through the panel.  Two
    consequences, and the second is worse than the drift: the figure a tick
    booked was one the screen never showed, and because a submitted figure
    counts as a correction only when it DIFFERS from the offer
    (:func:`_is_correction`), no input the user could give meant "book the
    plan".  Answering here fixes both -- the offer equals the booking, and
    typing any other number is a genuine correction that displaces this one.

    Only a ``corrected`` record is honoured.  A ``derived`` one is the app's own
    inference about a moment that has passed, and re-resolving it is strictly
    better -- the plan may legitimately have been re-priced meanwhile.  A
    ``purchases`` record stores no figure, and this is not reached for such a
    row: :func:`settle_amount` takes the entries branch above it.

    Pure: a column read plus one ``ref_cache`` lookup.  No producer runs, which
    is why an honoured row costs no paycheck engine at all -- and no
    ``entries`` load either: the figure's SOURCE lives on the movement
    (:func:`recorded_settlement`), but this read asks only whether a
    correction stands and what it says, which the row's own columns answer.
    A whole grid asks it per row (``retained_settle_amounts_by_id``).

    Args:
        row: The row about to be offered or settled.

    Returns:
        The retained correction's figure, or ``None`` when the row holds no
        ``corrected`` record.
    """
    if _recorded_basis(row) is not SettlementBasisEnum.CORRECTED:
        return None
    return row.settled_amount


def correction_record(
    row: Transaction, submitted: StatedFigure,
) -> Optional[Settlement]:
    """Return the record a stated CORRECTION makes, or ``None`` for an echo.

    **The Actual box's rule, stated once for both tables** (developer ruling,
    2026-08-17): the estimate and the actual are two different facts about a
    row, so they get two boxes, and editing the actual states what the bank
    really took.  ``transaction_service._door`` asks it of a plain row and
    ``transfer_service._update`` asks it of a transfer's expense leg; a second
    spelling of the echo comparison is how the two tables would come to disagree
    about what counts as a correction.

    **An ECHO writes nothing.**  Both popovers PREFILL the box with what the row
    already records, so an untouched Save posts the same figure back; recording
    it would restamp a ``derived`` record as ``corrected`` and manufacture a
    correction nobody made -- destroying the only stored signal that says a
    human read a number off a statement, which ruling **R-FB**'s production
    measurement ("11 of 93 settled bills carry a hand-typed correction") is made
    of.  A real correction records WHO stated it (``typed`` from either
    popover; the source is the door's word, ruling **R-BAL61**), and its
    basis is ``corrected`` for the same reason: that column's whole meaning is
    telling a figure somebody stated from one the app resolved.

    **The comparison is against what the row RECORDS, not its plan**
    (:func:`app.services.row_valuation.recorded_figure`).  The two differ
    whenever a correction stands, so comparing against the plan would read every
    re-save of a corrected row as a fresh correction of the same figure.  The
    TOTAL read answers rather than the refusing one because **a row that records
    NOTHING cannot echo**: that row predates the settlement record (finding
    **N-181**) and the only way to repair it is to state what moved, so the
    refusing read would make the repair surface raise instead of repairing.

    Args:
        row: The settled row being corrected -- a plain transaction, or either
            leg of a transfer (both carry the same record, Transfer Invariant
            3).  The CALLER establishes that it is settled;
            :func:`reject_figure_without_settled_status` is that check.
        submitted: The figure the door stated and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`).

    Returns:
        A ``corrected`` :class:`Settlement`, or ``None`` when *submitted*'s
        figure equals what the row already records.

    Raises:
        AmountUnresolvable: From
            :func:`~app.services.row_valuation.recorded_figure`, for a row whose
            record CONTRADICTS itself -- a basis that stores its figure, storing
            none.  Deliberately not caught: no door can produce that state, so
            reaching it means something wrote around the seam.
    """
    if recorded_figure(row) == submitted.amount:
        return None
    return Settlement(amount=submitted.amount, source=submitted.source)
