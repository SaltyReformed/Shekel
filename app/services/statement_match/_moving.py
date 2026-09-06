"""How a matched row is MOVED onto the bank's day and its figure.

Plan step ``bank_import:X-gj-3a``, split out of :mod:`._accept` when that
module reached pylint's 1,000-line ceiling.

**WHY it was split is ruling balance:R-IR; WHERE the cut goes is not.**  That
ruling says the ceiling stays and *the session that breaks a module is the one
that splits it* -- it deliberately names no module and blesses no seam, so
citing it for the seam would be a category error.  What it settles is that the
split happened here rather than being left to whoever committed next.

**The seam is by SUBJECT, and it is NEW rather than pre-drawn.**  Before this
step :mod:`._accept`'s own opening paragraph said that module *does both* --
records the correspondence AND moves the days -- and its taxonomy paragraph
said three subjects in three files.  Both were true and both were edited with
this split rather than cited for it.  The subject here is what applying a
match does to ONE member, which has different collaborators from recording the
correspondence: three settle doors, the day rule, and the figure the bank
leaves that row.  Nothing here reads a match, a member table or a claim;
nothing in :mod:`._accept` calls a settle verb.

**It writes and does NOT commit** -- the route owns the unit of work, exactly
as every door in this package does.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import (
    entry_service,
    transaction_service,
    transfer_service,
)
from app.services.settle_day import SettleDay

from ._offers import (
    CandidateRow,
    MatchDays,
    RowKind,
    corrected_purchase_day,
)
from ._scope import ReviewScope
from ._variance import DifferenceLanding


def _apply_day(
    row: CandidateRow, owner_id: int, days: "MatchDays",
    figure: Decimal | None = None,
) -> str:
    """Move one member row onto the bank's days AND figure through its own door.

    The dispatch, and every arm is an existing verb rather than a column write:

    * a PURCHASE stamps its posting day through ``entry_service.update_entry``,
      which refuses a future day and releases the row's clearing link -- and
      takes the bank's own transaction day in the SAME call where the app's
      recorded purchase day is refuted (ruling **R-FW**, see
      :func:`~._offers.corrected_purchase_day`);
    * a transfer SHADOW goes through ``transfer_service`` -- ``settle_transfer``
      when it is still Projected, ``update_transfer`` when only the day moves,
      because a settled transfer is an idempotent no-op for the first;
    * every other transaction goes through
      ``transaction_service.apply_requested_status``, with the row's OWN status
      when it is already settled (an edit that changes only the day is an
      identity transition) and its type's settled status when it is not.

    Args:
        row: The member being moved.
        owner_id: The user the route proved owns the account.
        days: The days the bank states for this match.
        figure: What the bank says this row is worth
            (:meth:`~._variance.DifferenceLanding.figure_for`), or ``None``
            where this match's difference is not this row's, or is nobody's,
            or already agrees with what the row holds.  **It rides the SAME
            call as the day** for the reason the purchase's two dates already do:
            each settle door validates the state it is asked to produce, so
            submitting the figure separately would offer it an intermediate
            row the door would rightly refuse.

    Returns:
        ``"settled"`` when the row entered the settled band, ``"corrected"``
        when it was already settled on a different day, ``"unchanged"`` when it
        already carried the bank's own day.  **``"unchanged"`` is about the DAY
        and not about whether anything was written** (plan step X-az): a row
        already carrying the bank's day on a weaker basis has that basis raised
        to ``observed``, which moves no day and so is not a correction.

    Raises:
        ValidationError: From a settle door -- a future day, a posting day
            before its purchase, an illegal transition.  Surfaced to the owner.
        PostingError: From a ledger reconcile.  Fails loud.
    """
    # **"unchanged" requires the row to be SETTLED as well as correctly
    # dated.**  Deciding on the day alone would let a Projected row carrying
    # the bank's own day be recorded as matched and left Projected -- the bank
    # line would read explained while the money was never booked.  The status
    # seam should make that state unreachable (it refuses a day on a
    # non-settled status and clears the column on the way out), but no CHECK
    # pairs the two columns, so the arm does not rest on that discipline.
    posts_on = days.posts_on
    purchase_day = corrected_purchase_day(row, days)
    outcome = (
        "unchanged" if row.is_settled and row.settled_on == posts_on
        and purchase_day is None
        else "corrected" if row.is_settled
        else "settled"
    )
    # **An "unchanged" row is still written when the bank CONFIRMS a day the app
    # only had a BOUND for** (plan step **X-az**, finding **N-332**).  The
    # reconcile panel records the day a BALANCE was asserted for -- the money
    # moved on or BEFORE it -- and a bank line posted on exactly that day turns
    # the bound into an observation.  Nothing else in the app can make that
    # write: no settle door fires when the day does not move, so before this
    # step such a row kept reporting itself a bound forever.  The DAY is
    # unchanged, so the outcome the caller counts stays ``"unchanged"`` and
    # neither the settled nor the corrected tally moves; what changes is the
    # stored answer to "how is this day known".
    #
    # It writes through the row's own settle door rather than assigning the
    # column, exactly as the other arms do, so the basis keeps the single writer
    # ``settled_on`` has.  Each door compares the resulting DAY with the stored
    # one to decide whether to release the clearing link, and the day is equal
    # here -- so a confirmation strengthens the observation the link records
    # instead of dropping it.
    if (
        outcome == "unchanged"
        and row.settle_day_basis is SettledDayBasisEnum.OBSERVED
        and figure is None
    ):
        return outcome

    settle_day = SettleDay(day=posts_on, basis=SettledDayBasisEnum.OBSERVED)

    if row.kind is RowKind.PURCHASE:
        # ONE call, both days, because ``update_entry`` checks the RESULTING
        # pair: submitting them separately would offer the door an intermediate
        # state where the posting day precedes the purchase day, and it would
        # rightly refuse the very correction that fixes it.
        moves = {"settle_day": settle_day}
        if purchase_day is not None:
            moves["purchased_on"] = purchase_day
        if figure is not None:
            moves["amount"] = figure
        entry_service.update_entry(row.row_id, owner_id, **moves)
        return outcome

    if row.transfer_id is not None:
        if row.is_settled:
            transfer_service.update_transfer(
                row.transfer_id, owner_id, settle_day=settle_day,
            )
        else:
            transfer_service.settle_transfer(
                row.transfer_id, owner_id, settle_day=settle_day,
            )
        return outcome

    txn = db.session.get(Transaction, row.row_id)
    target_status_id = (
        txn.status_id if row.is_settled
        else transaction_service.settled_status_id(txn)
    )
    transaction_service.apply_requested_status(
        txn, target_status_id, settle_day=settle_day, submitted=figure,
    )
    return outcome


@dataclass(frozen=True)
class Moved:
    """What applying a match's days and figures to its member rows did.

    Three counts derived in one pass over the members, because each of them
    has to be read BEFORE the writes that make it false and reading them apart
    would be three passes over one question.

    Attributes:
        outcomes: One of ``"settled"`` / ``"corrected"`` / ``"unchanged"`` per
            member, in the order they were moved -- see :func:`_apply_day`.
        redated_count: How many member purchases had their PURCHASE day
            corrected (ruling **R-FW**).
        repriced_count: How many members took the bank's own figure (ruling
            **R-GD(a)**).
    """

    outcomes: "list[str]"
    redated_count: int
    repriced_count: int


def move_members(
    scope: ReviewScope,
    rows: "list[CandidateRow]",
    landing: DifferenceLanding,
    days: MatchDays,
) -> Moved:
    """Move every member row onto the bank's days and figure, and count it.

    **The purchases move first**, for the reason
    ``reconcile_service.record_reconciliation`` states for its own order: a
    purchase's posting day changes what its parent envelope's cash leg is worth
    (ruling **R-FM**), so settling a parent first and stamping its purchase
    afterwards would book the parent at a figure the purchase then moves.
    :func:`~._accept._reject_parent_and_its_own_purchase` makes that pairing unreachable
    in ONE match and across matches alike, so no submission this door accepts
    can actually hit the interaction today.  **The order is kept anyway and the
    reason is stated rather than invented**: a first draft justified it by "the
    parent is in a different match accepted in the same request", which cannot
    happen -- one POST accepts exactly one match.  What the order really buys
    is that the rule survives the guard: if a later step widens what a match
    may name, the sequence is already the safe one rather than something that
    has to be rediscovered.

    Args:
        scope: The pass, for the owner every settle door is scoped by.
        rows: The submitted app rows, already priced.
        landing: Where this match's difference goes
            (:class:`~._variance.DifferenceLanding`).  **Taken rather than
            derived, because the caller decides the two remedies by the same
            answer**: the value that says a member absorbs the gap is the
            value that says no row has to be minted for it, so the two cannot
            be answered differently.
        days: The days the match writes.

    Returns:
        Its :class:`Moved`.

    Raises:
        ValidationError: From a settle door -- a future day, a posting day
            before its purchase, an illegal transition.
        PostingError: From a ledger reconcile.  Fails loud.
    """
    ordered = sorted(
        rows, key=lambda row: (row.kind is not RowKind.PURCHASE, row.row_id),
    )
    # Read BEFORE the writes: once `_apply_day` has moved a purchase onto the
    # bank's day the predicate no longer holds, so counting afterwards would
    # report zero every time.
    redated_count = sum(
        1 for row in ordered if corrected_purchase_day(row, days) is not None
    )
    # Read BEFORE the writes, exactly as ``redated_count`` is and for the same
    # reason: once a settle door has taken the bank's figure the row agrees
    # with it, so counting afterwards would report zero every time.
    #
    # **Asked of every member and answered by ONE value** (plan step
    # ``bank_import:X-gj-3a``).  This read ``corrected_figure(row, bank_cash)``
    # while the bank's figure could only ever name a lone row, so passing the
    # same figure to every member was harmless -- there was only one.  A group
    # that attributes its difference to one of several members makes that
    # false, and the fix is not a condition here: ``figure_for`` answers
    # ``None`` for a member the difference does not belong to, so the rule
    # about WHICH row moves stays in the value that owns it.
    figures = [landing.figure_for(row) for row in ordered]
    return Moved(
        outcomes=[
            _apply_day(row, scope.owner_id, days, figure)
            for row, figure in zip(ordered, figures, strict=True)
        ],
        redated_count=redated_count,
        repriced_count=sum(1 for figure in figures if figure is not None),
    )
