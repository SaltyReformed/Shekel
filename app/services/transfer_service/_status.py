"""
Shekel Budget App -- Transfer Service status and settle-day appliers

The two writers that move a transfer's THREE rows together: the status applier
(parent + both shadow :class:`~app.models.transaction.Transaction` rows to one
status, through the one seam) and the settle-day corrector (the pair's recorded
civil day, when the user says the bank moved the money on a different day than
was assumed).

Extracted from ``transfer_service`` at plan step **X-f1b**, on the same ground
every earlier split from that module used -- it was at the 1000-line ceiling and
finding **N-152** recorded that the next change to it would hit the gate again.
This is that change.  The split is by responsibility rather than by line count:
these two functions are the ONLY place the three rows' shared status and shared
settle day are resolved, and Transfer Invariant 3 (shadow statuses and settle
days always equal the parent's) is a property of this module.  Plan step
X-f2-c3 then made ``transfer_service`` a PACKAGE (**N-152** / **N-156**) and
this module a leaf of it; the responsibility is unchanged and the file moved
from ``app/services/_transfer_status.py``.

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
applied in place, no ``request`` / ``session`` imports, no flush, no commit --
the caller owns the session boundary.
"""

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import status_seam
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.state_machine import verify_transition
from app.services.transfer_service._validation import TransferRows
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today


def apply_status_to_all_three(
    rows: TransferRows,
    new_status_id: int,
    *,
    settle_day: SettleDay | None = None,
    settlement: "status_seam.Settlement | None" = None,
) -> None:
    """Move a transfer and both shadows to one status, through the ONE seam.

    Replaces ``transfer_service``'s own copy of the status seam (plan step
    X-aj1, ruling **R-DN**); see
    :func:`app.services.status_seam.apply_status_change` for the mechanics and
    for the three defects the duplicate carried.

    **Verified in FULL before anything is assigned.**  That preserves the
    atomicity the deleted version promised -- an illegal request leaves the
    transfer AND both shadows untouched (F-047 / commit C-21) -- and it is
    what makes ruling **R-DO** safe here: a shadow whose status has drifted
    somewhere the parent's status is not legally reachable from now REFUSES,
    so assigning as we went would strand the transfer ahead of its shadows.
    The seam re-verifies as it writes and stays the enforcement point; this
    pass is for atomicity, mirroring how the transaction PATCH handler's
    ``_resolve_status_change`` pre-check relates to the same seam.

    The shadow checks pass by construction for any transfer whose own
    transition was legal: a shadow's status equals the parent's pre-update
    status (Transfer Invariant 4), and every transfer-legal move is also
    transaction-legal (measured over both maps at X-aj1's trace, 0
    exceptions, reverse control firing).

    Args:
        rows: The transfer and both shadows being moved.
        new_status_id: The ``ref.statuses.id`` all three rows move to.
        settle_day: The civil day the money moved and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), when the CALLER knows
            it -- the reconcile tick's statement date on the ``asserted``
            basis, threaded from
            :func:`app.services.transfer_service._settle.settle`.  ``None``
            derives the day, which is what every door that does not know one
            means.  **Taken here rather than corrected afterwards** and that is
            a defect fixed rather than a convenience: a settle carrying a
            statement day used to stamp the derived day first and then rewrite
            it through :func:`apply_settle_day_correction`, so every reconcile
            tick wrote the column twice, the first value was a day the money
            did not move, and the second went through the door ruling **R-ED**
            built for a user CORRECTING one.  Ignored for a non-settled status,
            where the seam clears the day.
        settlement: WHAT moved, when this change records a settle
            (:class:`app.services.status_seam.Settlement`).  Applied to BOTH
            shadows and to neither the parent -- a transfer's money moves on its
            legs, so each leg records its own, and the two are equal by Transfer
            Invariant 3 exactly as their settle day is.  **A move INTO the
            settled band must carry one**, which the seam refuses otherwise;
            that is what keeps "a settled row states what moved" true of a
            transfer's rows as well as a plain one's.  ``None`` on any other move
            leaves each shadow's existing record alone, and on the way OUT of the
            band the seam releases both.

    Raises:
        ValidationError: If the transition is illegal for the transfer or for
            either shadow (propagated from the state machine), or if
            *settled_on* is refused by the seam (a future day, ruling R-EJ).
        ValueError: If the move enters the settled band with no *settlement*
            (propagated from the seam).
    """
    for row in (rows.transfer, *rows.shadows):
        verify_transition(row, new_status_id)

    # ONE settle DAY for the PAIR (Transfer Invariant 3), resolved before
    # either shadow is written.  The seam's per-row rule is "preserve an
    # existing day, else stamp today", which is right for a lone transaction and
    # WRONG for a pair: two shadows whose days already differ would each keep
    # their own, and a pair where only one carries a day would have the other
    # stamped with today.  Both outcomes break the equality
    # ``posting_service._entry_date`` depends on -- it reads the INCOME shadow's
    # ``settled_on`` and its docstring records that the two are always equal
    # because the transfer service mirrors the day to both shadows.
    # Preferring an EXISTING day over today is what stops a repair from
    # inventing a settle day: the sibling already knows when the money moved,
    # and that day is the ``entry_date`` the postings are filed under.
    settled_ids = settled_status_ids()
    settles = new_status_id in settled_ids
    # **A leg still IN the settled band is asked FIRST, and the two repairs
    # below share that one ordering** (plan step X-au-c3).  A settled leg's
    # facts are what a balance is counting right now; an unsettled leg's are
    # only what it remembers, and since a revert RETAINS the record while
    # RELEASING the day, the pair can hold a live leg and a stale one at once.
    # Reading the shadows in their declared order instead -- ``TransferRows.
    # shadows`` is ``(expense, income)`` -- would always ask the expense leg,
    # so a repair on a transfer whose expense leg had drifted out of the band
    # would write that leg's retained figure over the income leg's live one,
    # pricing the pair at a number one of them had already stopped claiming.
    # ``sorted`` is stable, so two legs in the same band keep the declared
    # order and a repair remains deterministic.
    legs = sorted(rows.shadows, key=lambda s: s.status_id not in settled_ids)
    pair_day = None
    if settles:
        # The CALLER's day wins over both legs, because it is the only one of
        # the three that is EVIDENCE: a statement said the money moved that day.
        # The rest are repairs -- a sibling's record, then the user's today --
        # and a repair may not overrule a fact.
        pair_day = settle_day
        for leg in legs:
            # ``is not None`` rather than truthiness: the coding standard
            # forbids relying on falsiness for a business value, and while no
            # ``SettleDay`` is falsy today, an ``or`` chain read as if one could
            # be.
            #
            # **The sibling's BASIS travels with its day** (plan step X-az).  A
            # repair may not invent either term, and the KIND of day is a term:
            # taking the day and re-stamping it ``entered`` would report a bank
            # observation the sibling holds as the owner's own typing, which is
            # the same class of laundering finding **N-332** is about.
            if pair_day is None:
                pair_day = recorded_settle_day(leg)
        if pair_day is None:
            # Neither leg knows and no caller said: the pair settles today on
            # the owner's own word, which is what ``entered`` names.  The same
            # answer :func:`app.services.status_seam.apply_status_change` gives
            # a lone transaction in the same position, stated here because the
            # PAIR needs one day for both legs and the seam's per-row rule would
            # give each its own.
            pair_day = SettleDay(
                day=display_today(),
                basis=SettledDayBasisEnum.ENTERED,
            )
    # ONE settlement RECORD for the PAIR, resolved by the same rule and for the
    # same reason as the day above: a REPAIR may not invent either term.
    # ``restore_transfer`` moves a shadow that drifted out of its parent's
    # settled status back INTO the band, and it has no figure of its own to state
    # -- so it takes the one its SIBLING already recorded, which is Transfer
    # Invariant 3 read rather than maintained.  A caller's own record wins,
    # because it is the only one of the two that is EVIDENCE; where neither
    # exists the seam refuses, which is correct -- there is nothing to record.
    pair_settlement = settlement
    if pair_settlement is None and settles:
        for leg in legs:
            pair_settlement = status_seam.recorded_settlement(leg)
            if pair_settlement is not None:
                break
    for shadow in rows.shadows:
        status_seam.apply_status_change(
            shadow, new_status_id,
            settle_day=pair_day, settlement=pair_settlement,
        )
    # The parent carries neither a ``settled_on`` column nor a settlement
    # record, so it takes neither: a transfer's money moves on its two shadow
    # rows and each records its own leg.
    status_seam.apply_status_change(rows.transfer, new_status_id)


def apply_settle_day_to_pair(
    expense_shadow: Transaction,
    income_shadow: Transaction,
    day: SettleDay | None,
    *,
    settlement: "status_seam.Settlement | None" = None,
) -> None:
    """Record ONE settle day on both shadows, at the status they already hold.

    The pair's day writer for the two callers that are NOT moving the rows
    anywhere: ``transfer_service.create_transfer``'s born-settled branch (the
    shadows are constructed in the parent's settled status and need the day)
    and :func:`apply_settle_day_correction` (the user corrected the day).  Each
    write is an IDENTITY status change through the seam, which is what makes
    :mod:`app.services.status_seam` the only writer of the column.

    **It is deliberately not :func:`apply_status_to_all_three` with the current
    status.**  That function verifies the transition of the PARENT as well, and
    a transfer's workflow map has no ``Received`` entry (income is a
    display convention for regular rows; transfers settle with ``Done``), so
    routing a born-``Received`` create through it would refuse a state
    ``create_transfer`` has always accepted.  Rejecting that state may well be
    right, but it is a create-path rule and not this step's to decide -- so the
    day write is narrowed to the two rows that carry the column.

    Args:
        expense_shadow: The expense-side shadow :class:`Transaction`.
        income_shadow: The income-side shadow :class:`Transaction`.
        day: The civil day both shadows record and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`; Transfer Invariant 3 --
            the two legs are always equal, which
            ``posting_service._entry_date`` depends on: it reads the INCOME
            shadow's day for the pair).  ``None`` means the user's today on the
            ``entered`` basis, which is the F-048 / C-22 rule for a transfer
            created already settled: it settled at creation, on nobody's word
            but the owner's.  The default is resolved HERE rather than at the
            call site so there is one statement of it.
        settlement: WHAT moved, for the BORN-SETTLED create alone
            (:class:`app.services.status_seam.Settlement`).  Its shadows are
            constructed already in the settled status, so the seam sees an
            IDENTITY transition and cannot ask them for a record -- but a
            settled row that records nothing is one
            ``row_valuation.settled_figure`` refuses to value, so the create
            must supply one.
            :func:`apply_settle_day_correction`, the other caller, passes
            ``None``: it corrects the day of a pair whose record already exists,
            and re-stating the figure there would re-price a settled row from
            whatever the app thinks today.

    Raises:
        ValidationError: If either shadow is not in a settled status (a day
            belongs only to a settled row), or if a shadow's CURRENT status is
            not a recognised transaction status -- the identity transition is
            still verified by the state machine, so a drifted shadow refuses
            here rather than being silently re-dated.  Both propagate from the
            seam.
        TypeError: If *day*'s own day is a ``datetime`` rather than a civil
            ``date`` -- refused by :class:`~app.services.settle_day.SettleDay`'s
            constructor at whichever call site built it.
    """
    pair_day = day if day is not None else SettleDay(
        day=display_today(), basis=SettledDayBasisEnum.ENTERED,
    )
    for shadow in (expense_shadow, income_shadow):
        status_seam.apply_status_change(
            shadow, shadow.status_id,
            settle_day=pair_day, settlement=settlement,
        )


def apply_settle_day_correction(
    rows: TransferRows, day: SettleDay | None,
) -> None:
    """Correct the civil day a SETTLED transfer's money moved, through the seam.

    The ``settled_on`` edit door (ruling **R-ED**): the user read their
    statement and the money moved on a different day than the settle was
    recorded on.  Since plan step E1a that day IS the ``entry_date`` the
    transfer's postings are filed under, so this is a money-moving edit and
    ``transfer_service._update._reconcile_postings_after_update`` re-dates the
    ledger after it.

    **It routes through the status seam rather than assigning the column, and
    that is finding N-183's fix.**  ``update_transfer`` used to write
    ``expense_shadow.settled_on = value`` and the same for the income shadow,
    which made it a SECOND write door for the column the seam owns -- so it
    could date a Projected transfer (breaking the settled-iff-dated invariant
    the whole step establishes) and could clear a settled one (leaving a row
    the balance walk REFUSES, i.e. a 500 on the grid).  Applying an identity
    status transition carrying the new day gives the seam both facts in one
    call, exactly as a settle does, and inherits its ``datetime`` refusal
    (finding **N-179**) for free.  ``Transaction.settled_on`` now has ONE
    writer in ``app/``: :mod:`app.services.status_seam`.  (The column NAME is
    shared with ``TransactionEntry.settled_on``, which is a different fact --
    the day a PURCHASE cleared, plan step S1-c -- and is written by
    ``entry_service``; the two are not related and neither owns the other.)

    Both shadows take the SAME day (Transfer Invariant 3); the parent carries
    no such column.

    Args:
        rows: The transfer and both shadows.  The parent already carries the
            status this edit leaves it in, and since plan step X-f2-c3 a
            correction arriving WITH a settle no longer reaches here at all --
            the settle takes the day at the status flip, so what this door sees
            is a correction to a row whose money had already moved.
        day: The corrected civil day and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None``.  A day
            that reached here from the transfer PATCH carries the ``entered``
            basis, stamped by
            :func:`app.services.status_seam.settle_day_for_status`; a day the
            statement matcher corrects carries ``observed``.

    Raises:
        ValidationError: When *day* is supplied for an unsettled transfer (no
            money has moved, so there is no day to record -- propagated from
            :func:`~app.services.status_seam.reject_settle_day_without_settled_status`),
            or when *day* is ``None`` for a settled one (every settled row
            carries the day its money moved; the way to remove the day is to
            revert the transfer out of the settled band, which clears it).
        TypeError: If *day*'s own day is a ``datetime`` rather than a civil
            ``date`` -- refused by :class:`~app.services.settle_day.SettleDay`'s
            constructor at whichever call site built it.
    """
    if rows.transfer.status_id not in settled_status_ids():
        status_seam.reject_settle_day_without_settled_status(
            rows.transfer.status_id, day,
        )
        return

    if day is None:
        raise ValidationError(
            "A settled transfer must carry the day its money moved, so the "
            "settle day cannot be cleared.  Correct it to the day the bank "
            "showed, or move the transfer back to Projected -- which removes "
            "the day because the money has not moved."
        )

    apply_settle_day_to_pair(rows.expense, rows.income, day)
