"""
Shekel Budget App -- Status Seam: the settlement RECORD

WHAT a settle records -- the figure that moved, how it is known, and the reads
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

Pure: reads columns and the ref cache, constructs values.  No session, no
mutation, no Flask.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import SettlementBasisEnum
from app.models.transaction import Transaction
from app.services.row_valuation import recorded_figure


@dataclass(frozen=True)
class Settlement:
    """WHAT a settle records: the figure that moved and how it is known.

    The value a settle door hands :func:`apply_status_change` so the whole
    record -- the day, the figure and the basis -- is written in ONE act (plan
    step **X-au-c3**).  Handing the seam one value is what keeps the figure and
    its basis from being written apart -- ``Settlement.__post_init__`` refuses a
    malformed pair, so a door cannot build one to hand over, and
    ``ck_transactions_settled_amount_needs_basis`` is the storage-tier backstop
    for the half a constructor cannot reach.

    **The constructor is where "``purchases`` stores no figure" is enforced**,
    and that placement is the point.  It is the one half of the record's pairing
    a CHECK cannot state: saying it needs the constraint to name a
    ``ref.settlement_bases`` id, which is what this project's ref convention
    keeps out of a schema.  A rule that cannot be a constraint should be a
    CONSTRUCTOR invariant rather than prose -- a settle door cannot build a
    malformed record to hand over, so no door can write one.

    Attributes:
        amount: What moved.  ``None`` exactly when *basis* is
            :attr:`~app.enums.SettlementBasisEnum.PURCHASES`, where the row's own
            entries state the figure and a stored copy would need a reconciler.
        basis: How that figure is known
            (:class:`app.enums.SettlementBasisEnum`).
    """

    amount: Optional[Decimal]
    basis: SettlementBasisEnum

    def __post_init__(self) -> None:
        """Refuse a record whose figure and basis contradict each other.

        Raises:
            ValueError: When a basis that stores its figure carries none, or
                when ``purchases`` carries one.  A programming error at the call
                site rather than a user error, so it is not a
                ``ValidationError``: no form can express either state.
        """
        stores_its_figure = self.basis is not SettlementBasisEnum.PURCHASES
        if stores_its_figure and self.amount is None:
            raise ValueError(
                f"A {self.basis.value!r} settlement must state the figure that "
                "moved. Only the 'purchases' basis leaves it out, because there "
                "the row's own entries state it."
            )
        if not stores_its_figure and self.amount is not None:
            raise ValueError(
                "A 'purchases' settlement stores no figure: the row's entries "
                f"state it, and {self.amount} here would be a second copy of a "
                "value its own children already hold. Pass amount=None."
            )

    @classmethod
    def from_settle(
        cls,
        booked: Decimal,
        correction: Optional[Decimal],
        retained: "Settlement | None" = None,
    ) -> "Settlement":
        """Return the record a settle makes, given every figure it may have.

        **A human's figure beats the app's, and the rule is stated ONCE here**
        because both settle verbs make the same choice -- the transaction's
        (``transaction_service._settle.settle_transaction``) and the transfer's
        (``transfer_service._settle.settle``).  Two spellings of one money rule
        is this arc's own root cause 1.

        A figure somebody read off a statement is a FACT; what the app resolved
        is an inference, however good.  So a correction wins, and the record says
        so in its basis -- which is what makes "did a human correct this" a
        stored answer rather than one re-derived by comparing the figure against
        a recomputation that may since have moved.

        *correction* is already the ECHO rule's answer: a submitted figure equal
        to what the row would book anyway is not a correction, and both callers
        resolve that through their own echo predicate before they
        get here (finding **N-231**).  So a ``None`` here means "nobody typed a
        different number", not "nobody typed one".

        **A RETAINED correction outlives the settle that recorded it, and
        honouring it here is what makes a revert non-destructive** (plan step
        X-au-c3, developer 2026-08-17).  A revert releases the assertion --
        ``settled_on`` and the clearing link -- and keeps what moved, so a row
        the user reverted in order to edit still carries the figure they read
        off their statement.  Re-deriving over it would delete that figure one
        step later than the release used to, which is the same data loss with an
        extra hop: the popover TELLS the user to revert in order to edit, so the
        round trip has to be lossless or the instruction is a trap.

        Only a ``corrected`` record is honoured.  A ``derived`` one is the app's
        own inference about a moment that has passed, and re-resolving it is
        strictly better than reusing it -- the plan may legitimately have been
        re-priced meanwhile.  A ``purchases`` record stores no figure at all, so
        there is nothing to retain: its entries still state it.

        Args:
            booked: What the app resolved this row to be worth at the moment of
                the settle.
            correction: The figure a human supplied, when it differs from
                *booked*; ``None`` otherwise.
            retained: What the row already records, when it still carries a
                record from an earlier settle it has since been reverted out of
                (``status_seam.recorded_settlement``); ``None`` when it carries
                none.

        Returns:
            A ``corrected`` record for a figure a human supplied now or supplied
            before and has not withdrawn, else a ``derived`` one.
        """
        if correction is not None:
            return cls(amount=correction, basis=SettlementBasisEnum.CORRECTED)
        if retained is not None and retained.basis is (
            SettlementBasisEnum.CORRECTED
        ):
            return retained
        return cls(amount=booked, basis=SettlementBasisEnum.DERIVED)


def recorded_settlement(row: Transaction) -> Optional[Settlement]:
    """Return the settlement *row* already records, or ``None`` if it has none.

    The read half of the record, for the ONE caller that must repair a row
    without knowing what it should say:
    ``transfer_service._status.apply_status_to_all_three`` resolving a drifted
    shadow's record from its SIBLING's.  That is Transfer Invariant 3 read rather
    than maintained, and it is the exact rule the pair's settle DAY already
    follows -- a repair may not invent either term, so it takes the one the other
    leg already holds.

    Args:
        row: The transaction to read.

    Returns:
        The recorded :class:`Settlement`, or ``None`` when the row has not
        settled.

    Raises:
        KeyError: When ``settled_basis_id`` names no
            :class:`~app.enums.SettlementBasisEnum` member.  Unreachable through
            the FK, which admits only the seeded rows; it is how a member ADDED
            without this map being extended fails loudly.
    """
    if row.settled_basis_id is None:
        return None
    member = {
        ref_cache.settlement_basis_id(basis): basis
        for basis in SettlementBasisEnum
    }[row.settled_basis_id]
    return Settlement(amount=row.settled_amount, basis=member)


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
    is why an honoured row costs no paycheck engine at all.

    Args:
        row: The row about to be offered or settled.

    Returns:
        The retained correction's figure, or ``None`` when the row holds no
        ``corrected`` record.
    """
    retained = recorded_settlement(row)
    if retained is None or retained.basis is not SettlementBasisEnum.CORRECTED:
        return None
    return retained.amount


def correction_record(
    row: Transaction, submitted: Decimal,
) -> Optional[Settlement]:
    """Return the record a human's CORRECTION makes, or ``None`` for an echo.

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
    of.  The basis a real correction gets is always ``corrected`` for the same
    reason: that column's whole meaning is telling a figure somebody read off a
    statement from one the app resolved.

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
        submitted: The figure the human typed.

    Returns:
        A ``corrected`` :class:`Settlement`, or ``None`` when *submitted* equals
        what the row already records.

    Raises:
        AmountUnresolvable: From
            :func:`~app.services.row_valuation.recorded_figure`, for a row whose
            record CONTRADICTS itself -- a basis that stores its figure, storing
            none.  Deliberately not caught: no door can produce that state, so
            reaching it means something wrote around the seam.
    """
    if recorded_figure(row) == submitted:
        return None
    return Settlement(amount=submitted, basis=SettlementBasisEnum.CORRECTED)
