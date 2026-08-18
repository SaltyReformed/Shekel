"""
Shekel Budget App -- Status Seam: the MECHANICS

:func:`apply_status_change` -- the ONE place a ``Transaction.status_id`` or a
``Transfer.status_id`` may be assigned -- and the two readings that grade what a
FORM submission means for the status it carries.

**This leaf is what the ``shekel-transaction-status-bypass`` fence allowlists**,
and the narrowing is the point rather than a tidy-up.  The entry read
``app.services.status_seam`` while that was one module; :func:`._common._module_in_allowlist`
matches a package PREFIX, so the split would have extended the exemption over
every leaf here without anybody deciding it should be.  That is precisely what
happened to ``transfer_service`` at plan step X-f2-c3, and its ``_status``
docstring records it.  Only this module writes ``status_id``; the record leaf
and the refusals leaf write nothing at all.

Split out of the single ``status_seam`` module at plan step **X-au-c3**; see
:mod:`._record` for the ground the split was made on.

Mutates the passed row in place; does NOT flush or commit -- the caller owns the
session boundary.  No Flask imports.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction, reject_settle_instant
from app.services import pay_period_service
from app.services.state_machine import verify_transition
from app.services.status_seam._record import Settlement
from app.services.status_seam._refusals import (
    StatusBearingRow,
    reject_figure_without_settled_status,
    reject_future_settle_day,
    reject_settle_day_without_a_record,
    reject_settle_day_without_settled_status,
    reject_settlement_without_settled_status,
)
from app.utils.balance_predicates import (
    enters_settled_band,
    settled_status_ids,
)
from app.utils.dates import display_today


def settle_day_for_status(
    user_id: int, new_status_id: int, submitted_day: Optional[date],
) -> Optional[date]:
    """Return the settle day a FORM submission means, and refuse an impossible one.

    **The edit doors' half of the settled-iff-dated invariant, stated once**
    (plan step X-f1c, ruling **R-EG**).  Both full-edit forms submit the row's
    whole state on Save, and the documented way to unlock a finalised row is to
    set Status to Projected in that same form -- so a revert arrives as
    ``{status_id: Projected, settled_on: <the day the row already carried>}``.
    That day is a stale ECHO of the state being left, not an assertion that
    money moved: the user picked Projected, which says it did not.  The seam's
    own :func:`reject_settle_day_without_settled_status` refuses that pair with
    a 400 -- correctly, for a SERVICE caller, which is asserting both facts on
    purpose -- and applying that refusal to a form submission would make the
    only documented unlock path fail on every settled row.

    So the rule is: **a day counts only when the row is moving INTO (or staying
    in) the settled band.**  Anywhere else it is dropped and the seam clears the
    column, which is what a revert means.

    It lives here, beside the refusal it defers to, rather than in either route:
    the transaction PATCH and the transfer PATCH are two doors onto ONE rule,
    and two spellings of it is this arc's own root cause 1.  It does NOT weaken
    the service guard -- the seam still refuses a day handed to it for an
    unsettled status, so a programmatic caller that means it still fails loud.

    **It also enforces the FLOOR (ruling R-EL), and the floor is a DOOR rule
    rather than a seam invariant -- which is the whole reason it is here and not
    in** :func:`apply_status_change`.  :func:`reject_future_settle_day` refuses a
    day in the future from ANY caller, because no caller can legitimately record
    money that has not moved.  The other end is not like that.  A day at or
    before an assertion is ABSORBED into it by ``cash_ledger._walk``, which then
    resets the running total to the asserted balance -- and for a GENUINE
    pre-schedule settle that is CORRECT (ruling R-EB: an assertion reconciles, so
    anything before it is already inside the asserted balance).  Recording money
    that moved before you started budgeting is a real thing to do, and a bank
    import would do it in bulk.  **What is never legitimate is a HUMAN typing a
    year wrong**, so the bound belongs where a human types.

    Measured: enforcing it at the seam instead refused **six** existing tests
    whose scenario is a payment budgeted to a 2026 pay period whose cash moved in
    December 2025 -- the year-boundary attribution rule, and exactly the shape an
    import produces.  Enforcing it here refuses none of them, because they call
    the service.

    ``fields.Date()`` deserializes ``"0202-08-04"`` to a real ``date``, so the
    typo is an ordinary slip in a box whose own tooltip invites correction.
    Worked on a ``$1,000`` anchor observed three days ago with a ``$100`` settled
    expense: the grid reads ``$900``, and after the typo ``$1,000``, with the
    ``$100`` becoming an unexplained plug at the next anchor re-derive -- the row
    still reading Paid throughout.  The bound is
    :func:`app.services.pay_period_service.earliest_recordable_day`, the SAME
    floor an anchor's ``observed_on`` has used since finding **N-133**, and both
    settle-day inputs carry it as ``min``.

    **Only a FORWARDED day is bounded.**  A day dropped beside a revert writes
    nothing, so refusing it would break the unlock path R-EG exists to keep open.

    Args:
        user_id: The row owner, whose pay-period schedule sets the floor.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's current
            one (an edit that changes only the day is an identity transition).
        submitted_day: The civil day the form submitted, or ``None`` when it
            submitted none.  An empty date input loads as absent rather than as
            an explicit ``None`` (the field is not ``allow_none``), because a
            settled row always carries a day: the way to remove one is to leave
            the settled band, which clears it.

    Returns:
        *submitted_day* when *new_status_id* is settled -- which is
        ``None`` itself when the form submitted none, and that is the same
        answer the unsettled branch gives; ``None`` otherwise.  The seam reads
        ``None`` as "derive the day from the status", i.e. preserve on a
        re-settle and clear on a revert.

    Raises:
        ValidationError: When the day would be FORWARDED and precedes the
            owner's earliest recordable day (ruling R-EL).  A 400: it is
            ordinary user input from the correction box.

    Note:
        There is deliberately no ``if submitted_day is None`` short-circuit.
        It would be unreachable as a DECISION -- both branches already answer
        ``None`` for a ``None`` day -- so no single-line mutation of this
        function could fail a test written against it.  A neutral review found
        one here, in the same step that deleted exactly that shape from
        ``_normalize_empty_inputs`` (finding **N-184**); a guard whose only
        possible test cannot fail is not a guard.
    """
    if new_status_id not in settled_status_ids():
        return None
    if submitted_day is not None:
        floor = pay_period_service.earliest_recordable_day(user_id)
        if submitted_day < floor:
            raise ValidationError(
                f"A settle day of {submitted_day.isoformat()} is before this "
                f"budget's schedule starts ({floor.isoformat()}).  Check the "
                "year -- or generate earlier pay periods first if the money "
                "really moved then."
            )
    return submitted_day


def figure_for_status(
    row: StatusBearingRow,
    new_status_id: int,
    submitted: Optional[Decimal],
    recorded: Optional[Decimal],
) -> Optional[Decimal]:
    """Return the figure a SUBMISSION means, or refuse a real conflict.

    **The figure's half of what :func:`settle_day_for_status` does for the day,
    and it is ECHO-AWARE where that one is status-only** (developer ruling
    2026-08-17; the echo term added 2026-08-18 after a neutral review measured
    what its absence cost).

    Both full-edit forms submit the row's WHOLE state on Save, so a settled row
    reverting to Projected posts the Actual box's current contents alongside
    ``status_id = Projected``.  When the box was NOT touched that figure is a
    stale ECHO of the state being left, and keeping it would make the documented
    unlock path fail on every settled row -- the trap ruling **R-EG** removed
    for the settle day.  So an echo is dropped.

    **A DIFFERENT figure beside an unsettling status is not an echo, and
    dropping it was a measured money defect.**  The first version of this rule
    read the status alone: it took no row, so it could not tell a prefill from a
    number the user had just retyped, and it discarded both.  Measured end to
    end -- a bill settled at a hand-typed ``$245.32``, re-read off the statement
    as ``$214.37``, corrected and reverted in one Save: HTTP 200, no message,
    and the row still recording ``$245.32``, which is what
    :meth:`Settlement.from_settle` then re-books.  Worse than the day's
    analogue, because a revert CLEARS the day and RETAINS the figure, so a
    silent drop there becomes a silently wrong booking here.

    Such a submission asserts two contradictory things -- "this much moved" and
    "it did not move" -- so it is REFUSED, in the sentence
    :func:`reject_figure_without_settled_status` carries, which tells the user
    to correct the figure and revert as two acts.  That refusal was unreachable
    from any HTTP door until this term existed.

    Args:
        row: The row the submission is about, for the refusal's noun.  For a
            transfer that is the PARENT, whose money moves on its legs.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's own (an
            edit that changes only the figure is an identity transition).
        submitted: The figure the form submitted, or ``None`` when it submitted
            none.
        recorded: What the row RECORDS as having moved
            (:func:`app.services.row_valuation.recorded_figure`), which is what
            the box was prefilled from -- so equality here is exactly "the user
            did not touch the box".  For a transfer it is read off the leg,
            because the parent carries no record.

    Returns:
        *submitted* when the row is settling or staying settled; ``None`` when
        nothing was submitted, or when what was submitted is an echo.

    Raises:
        ValidationError: When a figure DIFFERING from the record arrives beside
            a status that settles nothing.  A 400 at either route.
    """
    if submitted is None:
        return None
    if new_status_id not in settled_status_ids():
        if submitted == recorded:
            return None
        reject_figure_without_settled_status(row, new_status_id)
    return submitted


def apply_status_change(
    row: StatusBearingRow,
    new_status_id: int,
    *,
    settled_on: Optional[date] = None,
    settlement: Optional[Settlement] = None,
) -> None:
    """Apply a status transition -- the single status seam, for either row type.

    The ONE place a ``Transaction.status_id`` or a ``Transfer.status_id`` may be
    assigned.  Every status-changing path -- the manual ``mark_done`` branch,
    the inline PATCH, ``cancel``, ``mark_as_credit`` / ``unmark_credit``, the
    envelope ``transaction_service.settle_from_entries``, and
    ``transfer_service.update_transfer``'s mirror onto a transfer and its two
    shadows -- routes through here so the status mechanics are uniform and
    impossible to skip.

    Does the status MECHANICS only, in order:

      1. ``verify_transition`` -- the state-machine legality gate, which picks
         the workflow from *row*'s own model class; raises ``ValidationError``
         on an illegal move (e.g. Settled -> Projected), which the route layer
         surfaces as a 400.
      2. assign ``status_id``.
      3. maintain the SETTLEMENT RECORD -- ``settled_on``, ``settled_amount``,
         ``settled_basis_id`` and the clearing link -- as ONE act (see the
         *settled_on* and *settlement* args).  **Transactions only**, because
         ``Transfer`` carries none of those columns: a transfer's money moves on
         its two shadow rows, and the transfer service applies this seam to those
         shadows, so a transfer settle still records what moved and when.
      4. ``db.session.expire(row, ["status"])`` so a pre-commit reader (a cell
         render, a test assertion) sees the new ``Status`` row, not the stale
         ``lazy="joined"`` one -- the exact trap ``mark_as_credit`` documented
         and handled inline before this seam absorbed it.  Both models declare
         ``status`` as ``lazy="joined"``, so both need it; the transfer path did
         not do this before X-aj1 and held only because every route commits
         before it renders.

    It deliberately does NOT post to the ledger and does NOT flush or commit:
    ledger emission is reconciled once at the END of each handler, after every
    effect field is applied, never at the status flip (Build-Order Step 3,
    Commit 6 -- the same placement ``transfer_service.update_transfer`` uses);
    the caller owns the session boundary.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status changes.  Must
            be session-attached so the ``status`` expire reloads; its
            ``status_id`` is read as the current state for the transition check
            and its CLASS selects the workflow.
        new_status_id: The ``ref.statuses.id`` to move to.
        settled_on: Settle-day policy, read only for a ``Transaction``.
            ``None`` (the default) DERIVES the day from *new_status_id*: stamp
            ``display_today()`` on entering a settled status (Paid / Received /
            Settled) that has none yet, **preserve an existing one on an
            idempotent re-settle**, and clear it on entering a non-settled
            status (so a reverted / cancelled / credited row drops its stale
            settle day).  A non-``None`` ``date`` is written verbatim, and its
            ONE legitimate meaning is "the user typed this day" -- the transfer
            edit door, and the transfer service's pair resolution.

            **A day supplied for a NON-settled status is REFUSED**
            (:func:`reject_settle_day_without_settled_status`, finding
            **N-183**), which is what makes "a row is settled if and only if it
            carries a settle day" a property of this function rather than a
            convention its callers keep.

            **The day is the USER's, not the server's** (ruling R-DH (b)).
            ``display_today()`` reads the display timezone, where
            ``date.today()`` would read the process's UTC day and file an
            8pm-Eastern settle under tomorrow.  This is also why the seam no
            longer assigns ``db.func.now()``: that reached PostgreSQL's clock,
            one of the four database-clock reaches finding N-65 had to build
            ``_freeze_db_clock`` to contain, and a Python ``date`` is a value
            the suite's own clock freeze already governs.

            **The preserving rule is load-bearing and is finding N-146's fix.**
            The transfer service's deleted seam re-stamped every entry into a
            settled status including an identity re-submit, and since plan step
            E1a a settle's civil day IS the ``entry_date`` its postings are
            filed under -- so editing the notes on a paid transfer moved its
            money to today.  **Finding N-178 then showed the seam alone is not
            enough**: both mark-done routes passed an explicit instant, which
            overrode this rule, and a replayed POST re-dated a settled transfer
            by however long ago it really settled.  Neither route passes one
            now, so the preserve rule is the only rule.

            **``Paid -> Settled`` is a RE-ENTRY, not a first entry**, and that
            is why preservation matters beyond the edit forms: archiving a
            payment must not move its money to the day it was archived.  That
            transition has zero production rows today (finding N-177, which
            proposes deleting the status), and the rule is pinned by a test
            regardless, because a status with no rows is not a status with no
            transitions.

        settlement: WHAT moved, when this change RECORDS a settle
            (:class:`Settlement`).  Written to ``settled_amount`` and
            ``settled_basis_id`` in the same act as the day above, so one call
            states everything a settle knows.  The figure and its basis are
            paired by ``ck_transactions_settled_amount_needs_basis``; the settle
            DAY is deliberately not paired with either, because a revert
            withdraws the day and keeps what moved.

            **A row ENTERING the settled band must supply one**, and that
            refusal is what makes "a settled row states what moved" a property of
            this function rather than a convention its callers keep.  Before plan
            step X-au-c3 a settle recorded a figure only when a human had typed a
            correction, so most settled rows recorded nothing and every reader
            fell back to the row's PLAN -- and because a plan is a derivation,
            the plan then had to be FROZEN at settle so a later price change
            could not move a figure the bank had already taken.  A mandatory
            record is what leaves nothing to freeze.

            ``None`` leaves whatever the row already records untouched -- which
            is what an identity re-submit, a settle-day correction, an archive
            and a REVERT each mean.  A revert releases the ASSERTION
            (``settled_on`` and ``reconciled_by_id``, above) and keeps what
            moved, because the two are different facts with different
            lifetimes; the comment at that assignment carries the argument.

    Raises:
        ValidationError: If the transition is illegal for *row*'s workflow
            (propagated from ``verify_transition``), if *settled_on* is
            supplied for a *new_status_id* that is not settled (propagated from
            :func:`reject_settle_day_without_settled_status`), or if
            *settlement* is (propagated from
            :func:`reject_settlement_without_settled_status`).
        ValueError: If a ``Transaction`` ENTERS the settled band with no
            *settlement*.  A programming error at the call site -- no form can
            express it -- so it is not a ``ValidationError``.
        TypeError: If *settled_on* is a ``datetime`` rather than a civil
            ``date`` (finding **N-179**), or if *row* is not a status-bearing
            model (propagated from the state machine -- a programming error at
            the call site).
    """
    # A ``datetime`` is REFUSED rather than accepted and truncated (finding
    # N-179).  The rule and its message live on the column
    # (:func:`app.models.transaction.reject_settle_instant`, wired as an ORM
    # validator) so EVERY write path refuses it, not just this door; it is
    # called again here, ahead of ``verify_transition``, purely for the ordering
    # -- the validator would not fire until the assignment below, by which point
    # ``status_id`` has already moved, and a refused call must leave the row
    # untouched.
    reject_settle_instant(settled_on)

    # The other half of the settled-iff-dated invariant, and it is checked here
    # so EVERY caller inherits it (finding **N-183**).  Without it the explicit
    # arm below writes a day onto whatever status it is handed, which is how
    # ``transfer_service.update_transfer`` could date a Projected transfer; the
    # invariant then held only because no caller happened to do it.  Ordered
    # before ``verify_transition`` for the same reason the ``datetime`` refusal
    # is: a rejected call must not have mutated the row.
    reject_settle_day_without_settled_status(new_status_id, settled_on)

    # A day that has not happened yet is refused (ruling **R-EJ**), ordered with
    # the two refusals above for the same reason: a rejected call must leave the
    # row untouched.  See :func:`reject_future_settle_day` for the measurement --
    # a future-dated settle puts already-spent money back in the balance.
    reject_future_settle_day(settled_on)

    # The settlement record's own half of the same invariant (plan step
    # X-au-c3): a row records what moved only while it is settled.  Ordered with
    # the refusals above, and for the identical reason.
    reject_settlement_without_settled_status(new_status_id, settlement)

    # ``ck_transactions_settle_day_needs_basis`` said in WORDS, and only a
    # ``Transaction`` carries either column.  Without it the legacy row the
    # correction box exists to repair failed as a raw CHECK violation rendered
    # as "invalid reference"; see :func:`reject_settle_day_without_a_record`.
    if isinstance(row, Transaction):
        reject_settle_day_without_a_record(row, settled_on, settlement)

    # Read BEFORE the assignment below, because it is a question about the
    # status the row is LEAVING.  A row entering the settled band owes a record
    # of what moved; a row already in it keeps the one it has, which is what
    # makes an identity re-submit and a settle-day correction legal without one.
    entering = enters_settled_band(row, new_status_id)
    if entering and settlement is None and isinstance(row, Transaction):
        raise ValueError(
            f"Transaction {row.id} is entering the settled band with no "
            "settlement record. A settle states what moved as well as when: "
            "pass settlement=Settlement(...). Writing the status alone would "
            "leave the row settled with no figure, which "
            "row_valuation.settled_figure refuses to value -- and before this "
            "step it was worse than a refusal, because the reader fell back to "
            "the row's PLAN and published a forecast as a fact."
        )

    verify_transition(row, new_status_id)
    row.status_id = new_status_id

    # settled_on maintenance.  An explicit day wins; otherwise derive from the
    # new status: clear when leaving the settled band, stamp the user's today on
    # the first entry into it, and leave an existing day untouched on a
    # re-settle (so editing a Paid row -- which re-submits its unchanged
    # status_id -- never churns the day its money moved).  Skipped whole for a
    # Transfer, which has no such column; its shadows carry the day and get
    # their own call.
    #
    # The three arms are exhaustive and the invariant falls out of them: the
    # guard above has already refused an explicit day on a non-settled status,
    # so arm 1 fires only INTO the settled band, arm 2 clears whenever the row
    # leaves it, and arm 3 fills the band's first entry.  A settled row always
    # leaves here dated and a non-settled row always leaves here undated.
    if isinstance(row, Transaction):
        # **Any MOVE of the settle day releases the clearing fact** (plan step
        # X-f3a-1, ruling **R-FL**), and the three arms below are exactly the
        # three ways it can move.  ``reconciled_by_id`` records that a named
        # statement was seen to show this money ON that day: a revert says the
        # money did not move at all, and a correction says it moved on a
        # different day, and neither leaves the observation standing.
        #
        # **It is not tidiness, it is what keeps the ledger renderable.**  A link
        # whose day the date rule would not pick cannot be folded while an
        # assertion RESETS the balance -- the fold emits the source on its
        # settle day and the correction on the statement's, so the balance stops
        # equalling what the user asserted (``StatementCoverage``'s
        # ``_recorded_anchor_id`` carries the theorem and the production
        # figure).  ``ck_transactions_cleared_needs_settle_day`` catches only the
        # NULL half of that; this catches the move.
        #
        # Re-settling afterwards is a fresh act, which the reconcile panel
        # records against whatever statement is current then.
        released = settled_on is not None and settled_on != row.settled_on
        if settled_on is not None:
            row.settled_on = settled_on
        elif new_status_id not in settled_status_ids():
            row.settled_on = None
            released = True
        elif row.settled_on is None:
            row.settled_on = display_today()
        if released:
            row.reconciled_by_id = None
        # **WHAT MOVED IS RETAINED WHEN THE ASSERTION IS RELEASED**, and that
        # asymmetry with the two lines above is the whole point (plan step
        # X-au-c3).  ``settled_on`` and ``reconciled_by_id`` are the ASSERTION
        # -- "this money moved, on this day, and that statement showed it" --
        # so a revert withdraws them.  ``settled_amount`` and
        # ``settled_basis_id`` are WHAT MOVED, which is a fact about the row and
        # not about the assertion, so nothing here destroys them.
        #
        # A first version of this step released all four together, under a CHECK
        # that paired the day with the basis.  That pairing was the same defect
        # the step exists to remove (finding **N-241**: one column answering two
        # questions) rebuilt as two facts forced to share one lifetime -- and it
        # cost the user real data, because the full-edit popover TELLS them to
        # revert in order to edit, so following the app's own instruction
        # silently deleted a figure they had read off a statement.  Every
        # reconciliation system this was checked against separates the two: an
        # amount belongs to the transaction and cleared-ness is metadata over
        # it, so un-clearing never touches the amount (developer, 2026-08-17).
        #
        # Nothing is lost by retaining it and one thing is gained: a re-settle
        # HONOURS a retained correction (:meth:`Settlement.from_settle`), so the
        # revert / edit / re-settle round trip the popover describes is lossless.
        # What keeps a retained figure out of every balance is not its absence
        # but the STATUS -- ``row_valuation.settled_figure`` answers ``None`` for
        # a row that is not settled, whatever it still carries.
        if settlement is not None:
            row.settled_amount = settlement.amount
            row.settled_basis_id = ref_cache.settlement_basis_id(
                settlement.basis,
            )

    db.session.expire(row, ["status"])
