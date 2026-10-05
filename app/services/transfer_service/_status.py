"""
Shekel Budget App -- Transfer Service status and settle-day writers

The writers that move a transfer's THREE rows together: the status applier
(parent + both shadow :class:`~app.models.transaction.Transaction` rows to one
status, through the one seam, each shadow dated with ITS OWN side's day) and the
born-settled create's identity writer beside it.

Extracted from ``transfer_service`` at plan step **X-f1b**, on the same ground
every earlier split from that module used -- it was at the 1000-line ceiling and
finding **N-152** recorded that the next change to it would hit the gate again.
This is that change.  The split is by responsibility rather than by line count:
these functions are the ONLY place the three rows' shared status and each
side's settle day are written, and Transfer Invariant 3's status half is a
property of this module.  Plan step X-f2-c3 then made ``transfer_service`` a
PACKAGE (**N-152** / **N-156**) and this module a leaf of it; the
responsibility is unchanged and the file moved from
``app/services/_transfer_status.py``.

**The two sides' DAYS part here since plan step ``balance:X-bi-6-4c-3``**
(ruling **R-BAL142**): each side keeps its own day and basis, a side with no
evidence of its own borrows the other's, and the derivation is
:mod:`._side_days`' -- pure, and asked by both writers below through
:func:`_resolved_days`.  The day CORRECTION door (``apply_settle_day_correction``,
ruling **R-ED**) is gone: a correction states a day for a side and goes through
:func:`apply_status_to_all_three` at the pair's current status, which is how a
figure correction already travelled.

**Neither function writes ``status_id`` and neither constructs a status-bearing
model**, so this module stays clear of the W9907 status fence: both go through
:func:`app.services.status_seam.apply_status_change`, which is the sanctioned
door.  **The package move is where that sentence could have quietly stopped
being true**: the fence's allowlist read ``app.services.transfer_service`` and
:func:`_module_in_allowlist` matches a package PREFIX, so this leaf would have
become exempt without anybody deciding it should be.  The entry now names
:mod:`app.services.transfer_service._create` -- the one leaf holding the two
CONSTRUCTORS that hold it open (``_build_shadow`` and ``create_transfer``),
which plan step X-aj2 replaces.

Flask-isolated like the parent service: plain data and ORM rows in, mutations
applied in place, no ``request`` / ``session`` imports, no flush or commit of
its own -- the caller owns the session boundary.  (The seam this module calls
reads each shadow's ``entries`` to keep its covering movement in step, a lazy
load that may autoflush pending writes; ``_update`` reads the aggregate's lock
through the rows' counters as well as their dirty state for that reason.)
"""

from app.models.transaction import Transaction
from app.services import status_seam
from app.services.match_withdrawal import NOTHING_SHOWN, Shown, Silent
from app.services.planned_rows_books import reject_revert_below_the_books
from app.services.settle_day import recorded_settle_day
from app.services.state_machine import verify_transition
from app.services.transfer_service._side_days import (
    NO_DAYS,
    PairDays,
    repair_fallback,
    resolve_pair_days,
)
from app.services.transfer_service._validation import TransferRows
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today


def _resolved_days(
    expense: Transaction, income: Transaction, stated: PairDays,
) -> PairDays:
    """Return each shadow's day once this act lands -- the one derivation.

    Reads each side's CURRENT day off its shadow -- believed only while the
    shadow is in the settled band, because a leg that drifted out of it is
    being repaired, not consulted -- and the repair order's fallback day, then
    hands both to :func:`~._side_days.resolve_pair_days`.  Both writers in this
    module call it, so a borrowed day has one derivation in ``app/`` (the
    REC-552 study's seam S6).

    **A leg still IN the settled band is asked FIRST for the fallback**, and
    that ordering is plan step X-au-c3's, kept.  A settled leg's facts are
    what a balance is counting right now; an unsettled leg's are only what it
    remembers, and since a revert RETAINS the record while RELEASING the day,
    the pair can hold a live leg and a stale one at once.  ``sorted`` is
    stable, so two legs in the same band keep the declared ``(expense,
    income)`` order and a repair remains deterministic.

    Args:
        expense: The expense-side shadow, at its pre-act status.
        income: The income-side shadow, at its pre-act status.
        stated: What the act states, already admitted by its verb.

    Returns:
        Both sides' days, for a pair in or entering the settled band.
    """
    settled_ids = settled_status_ids()
    current = PairDays(
        expense=(
            recorded_settle_day(expense)
            if expense.status_id in settled_ids else None
        ),
        income=(
            recorded_settle_day(income)
            if income.status_id in settled_ids else None
        ),
    )
    legs = sorted((expense, income), key=lambda s: s.status_id not in settled_ids)
    fallback = repair_fallback(
        (recorded_settle_day(leg) for leg in legs), display_today(),
    )
    return resolve_pair_days(current, stated, fallback)


def reject_stated_days_without_settle(status_id: int, stated: PairDays) -> None:
    """Refuse a stated day beside a status that settles nothing.

    No money has moved, so there is no day to record: the seam's own refusal
    (:func:`app.services.status_seam.reject_settle_day_without_settled_status`),
    asked of each side's stated day.  One rule at three moments -- the create
    before any row exists, the update before its first write, and this
    module's writer before either shadow is written.

    Args:
        status_id: The status the rows are, or are about to be, in.
        stated: The days stated by side.

    Raises:
        ValidationError: When a day is stated and *status_id* is not settled.
    """
    for day in stated:
        status_seam.reject_settle_day_without_settled_status(status_id, day)


def drifted_sides_only(rows: TransferRows, stated: PairDays) -> PairDays:
    """Return the stated days a settle over an ALREADY-settled parent admits.

    Only a side whose shadow is OUT of the settled band: the repair of a
    drifted side is dated on the day its door states (ledger row **BAL-578**:
    that day was dropped and the repair dated the side on the owner's today),
    while a side already in the band keeps what it recorded -- a settle is
    idempotent over money already recorded.

    Args:
        rows: The transfer and both shadows, at their pre-act status.
        stated: What the settle stated, by side.

    Returns:
        *stated* with each in-band side's day dropped.
    """
    settled_ids = settled_status_ids()
    return PairDays(
        expense=None if rows.expense.status_id in settled_ids else stated.expense,
        income=None if rows.income.status_id in settled_ids else stated.income,
    )


def apply_status_to_all_three(
    rows: TransferRows,
    new_status_id: int,
    *,
    stated: PairDays = NO_DAYS,
    settlement: "status_seam.Settlement | None" = None,
    shown: Shown | Silent = NOTHING_SHOWN,
) -> None:
    """Move a transfer and both shadows to one status, each side on its own day.

    Replaces ``transfer_service``'s own copy of the status seam (plan step
    X-aj1, ruling **R-DN**); see
    :func:`app.services.status_seam.apply_status_change` for the mechanics and
    for the three defects the duplicate carried.

    **The ONE writer of a transfer side's day** (plan step
    ``balance:X-bi-6-4c-3``): a settle, a revert, a cancel, a figure
    correction, a day correction and the drift repair all come through here,
    and whenever the pair is in or entering the settled band each shadow takes
    ITS OWN side's day (:func:`_resolved_days`, ruling **R-BAL142**).  A day
    correction is an identity status change carrying a stated day -- the shape
    a figure correction already had -- and a Save that states nothing moves no
    side that holds evidence (finding **N-304**, closed).

    **Verified in FULL before anything is assigned.**  That preserves the
    atomicity the deleted version promised -- an illegal request leaves the
    transfer AND both shadows untouched (F-047 / commit C-21) -- and it is
    what makes ruling **R-DO** safe here: a shadow whose status has drifted
    somewhere the parent's status is not legally reachable from now REFUSES,
    so assigning as we went would strand the transfer ahead of its shadows.
    The seam re-verifies as it writes and stays the enforcement point; this
    pass is for atomicity, mirroring how the transaction PATCH handler's
    ``_resolve_status_change`` pre-check relates to the same seam.  A day
    stated beside a status that settles nothing is refused in the same pass,
    before any write (it ran last, after the status arm, while the correction
    had a door of its own).

    The shadow checks pass by construction for any transfer whose own
    transition was legal: a shadow's status equals the parent's pre-update
    status (Transfer Invariant 4), and every transfer-legal move is also
    transaction-legal (measured over both maps at X-aj1's trace, 0
    exceptions, reverse control firing).

    Args:
        rows: The transfer and both shadows being moved.
        new_status_id: The ``ref.statuses.id`` all three rows move to.  The
            pair's CURRENT status for a correction.
        stated: The days the act STATES, by side
            (:class:`~app.services.transfer_service._side_days.PairDays`),
            already admitted by the calling verb -- a settle entering the band
            admits every stated day (a statement's day beats whatever a leg
            remembers), a correction admits every stated day, and a settle
            over an already-settled pair admits one only for a side out of
            the band (``_update``).  Empty derives every day, which is what
            every door that knows none means.
        settlement: WHAT moved, when this change records a settle
            (:class:`app.services.status_seam.Settlement`).  Applied to BOTH
            shadows and to neither the parent -- a transfer's money moves on its
            legs, so each leg records its own, and the two are equal by Transfer
            Invariant 3.  **A move INTO the settled band must carry one**, which
            the seam refuses otherwise; that is what keeps "a settled row states
            what moved" true of a transfer's rows as well as a plain one's.
            ``None`` on any other move leaves each shadow's existing record
            alone, and on the way OUT of the band the seam releases both.
        shown: The bank lines the door's page named before the press, or
            what lets it stay silent -- handed to BOTH shadows' seam calls
            whole (plan step ``credit_card:CC-5-4a-5``, ruling **R-CC127**).
            A ``$0.00`` record takes each side's payment off the books, and
            the act grades each call against the named lines on that side's
            own account, so one posted set grades the pair exactly.

    Raises:
        ValidationError: If the transition is illegal for the transfer or for
            either shadow (propagated from the state machine), if a day is
            stated beside a status that settles nothing, if a day is refused
            by the seam (a future day, ruling R-EJ, or one the account's books
            do not reach), or if a revert to Projected lands inside the
            transfer's books
            (:func:`~app.services.planned_rows_books.reject_revert_below_the_books`,
            ruling **R-PC97**, asked before either shadow is written).
        ValueError: If the move enters the settled band with no *settlement*
            (propagated from the seam).
    """
    for row in (rows.transfer, *rows.shadows):
        verify_transition(row, new_status_id)
    # A revert to Projected that the books hold is refused here, the
    # transfer's one status door, before anything is assigned (ruling
    # **R-PC97**); the seam asks it of a transaction only, so one revert walks
    # the definition once.
    reject_revert_below_the_books(rows.transfer, new_status_id)

    reject_stated_days_without_settle(new_status_id, stated)
    settled_ids = settled_status_ids()
    settles = new_status_id in settled_ids
    # Each SIDE's day, resolved before either shadow is written; out of the
    # band the seam clears both, so nothing is resolved there.
    days = _resolved_days(rows.expense, rows.income, stated) if settles else NO_DAYS
    # ONE settlement RECORD for the PAIR.  A REPAIR may not invent a figure:
    # ``restore_transfer`` moves a shadow that drifted out of its parent's
    # settled status back INTO the band, and it has no figure of its own to
    # state -- so it takes the one its SIBLING already recorded, which is
    # Transfer Invariant 3 read rather than maintained.  A caller's own record
    # wins, because it is the only one of the two that is EVIDENCE; where
    # neither exists the seam refuses, which is correct -- there is nothing to
    # record.  The legs are asked in the repair order :func:`_resolved_days`
    # states: a leg still in the band first.
    pair_settlement = settlement
    if pair_settlement is None and settles:
        for leg in sorted(
            rows.shadows, key=lambda s: s.status_id not in settled_ids,
        ):
            pair_settlement = status_seam.recorded_settlement(leg)
            if pair_settlement is not None:
                break
    for shadow, day in ((rows.expense, days.expense), (rows.income, days.income)):
        status_seam.apply_status_change(
            shadow, new_status_id,
            settle_day=day, settlement=pair_settlement, shown=shown,
        )
    # The parent carries neither a ``settled_on`` column nor a settlement
    # record, so it takes neither: a transfer's money moves on its two shadow
    # rows and each records its own leg.
    status_seam.apply_status_change(rows.transfer, new_status_id)


def date_born_settled_pair(
    expense_shadow: Transaction,
    income_shadow: Transaction,
    stated: PairDays,
    *,
    settlement: "status_seam.Settlement",
) -> None:
    """Date a transfer CREATED settled, each side on its own day.

    The born-settled create's writer (``transfer_service.create_transfer``):
    the shadows are constructed in the parent's settled status and need their
    days, so each write is an IDENTITY status change through the seam, which
    keeps :mod:`app.services.status_seam` the only writer of the column.  The
    days are :func:`_resolved_days`' -- the derivation
    :func:`apply_status_to_all_three` asks -- so with nothing stated both sides
    borrow the owner's today, as a Paid press does.

    **It is deliberately not :func:`apply_status_to_all_three` with the current
    status.**  That function verifies the transition of the PARENT as well, and
    a transfer's workflow map has no ``Received`` entry (income is a
    display convention for regular rows; transfers settle with ``Done``), so
    routing a born-``Received`` create through it would refuse a state
    ``create_transfer`` has always accepted.  Rejecting that state may well be
    right, but it is a create-path rule and not this step's to decide -- so the
    write is narrowed to the two rows that carry the column.  It was also the
    day CORRECTION's writer until plan step ``balance:X-bi-6-4c-3``, which
    routes a correction through :func:`apply_status_to_all_three` (a settled
    parent is ``Done``, and ``Done -> Done`` is legal).

    Args:
        expense_shadow: The expense-side shadow :class:`Transaction`.
        income_shadow: The income-side shadow :class:`Transaction`.
        stated: The days the create states, by side.
        settlement: WHAT moved (:class:`app.services.status_seam.Settlement`).
            The shadows are constructed already in the settled status, so the
            seam sees an IDENTITY transition and cannot ask them for a record
            -- but a settled row with no covering movement is the ``$0.00``
            record (ruling **R-BAL82**), a transfer that moved nothing, so the
            create must supply one.

    Raises:
        ValidationError: If either shadow is not in a settled status (a day
            belongs only to a settled row), or if a shadow's CURRENT status is
            not a recognised transaction status -- the identity transition is
            still verified by the state machine.  Both propagate from the seam.
    """
    days = _resolved_days(expense_shadow, income_shadow, stated)
    for shadow, day in (
        (expense_shadow, days.expense), (income_shadow, days.income),
    ):
        status_seam.apply_status_change(
            shadow, shadow.status_id,
            settle_day=day, settlement=settlement,
        )
