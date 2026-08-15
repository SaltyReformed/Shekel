"""
Shekel Budget App -- Status Seam

The single status-mechanics primitive for BOTH status-bearing rows.
``apply_status_change`` is the ONE place a ``Transaction.status_id`` or a
``Transfer.status_id`` is assigned.  Every status-changing path -- the manual
``mark_done`` branch, the inline PATCH, ``cancel``, ``mark_as_credit`` /
``unmark_credit``, the envelope ``settle_from_entries``, and the transfer
service's mirror onto a transfer and its two shadow rows -- routes through it so
the status mechanics are uniform and impossible to skip.

**It became the only seam at plan step X-aj1** (ruling **R-DN**,
``docs/audits/balance_architecture/README.md``).  ``transfer_service`` used to
carry a SECOND implementation of this same seam for a transfer's rows, and that
duplication is why the ``shekel-transaction-status-bypass`` checker (W9907)
needed a two-module allowlist at all.  **Merging the seams does not by itself
shrink that allowlist**, and saying so here matters because the obvious
inference is wrong: ``transfer_service`` still writes a status through two
CONSTRUCTORS (``_build_shadow`` and ``create_transfer``), which W9907's
born-Projected rule refuses, so its entry survives until plan step X-aj2 replaces
the write door.  What the merge did remove is the duplicate ATTRIBUTE writes --
and three defects the duplicate had and this one did not:

* it stamped the settle instant UNCONDITIONALLY on entering a settled status
  rather than preserving an existing one, so an identity re-submit of an
  unchanged status re-dated a settled transfer -- and since plan step E1a that
  day IS the posted ``entry_date``, so it moved the money (finding **N-146**).
  **The seam alone did not close that class**: plan step X-f1b0 found both
  mark-done routes passing an explicit instant that overrode this seam's
  preservation, re-dating a replayed settle by however long ago it happened
  (finding **N-178**), and removed both;
* it never expired the ``status`` relationship, though both models declare it
  ``lazy="joined"`` -- latent rather than live, since every route commits before
  rendering, but true by accident rather than by construction;
* it mirrored a drifted shadow's status with no transition check at all, which
  ruling **R-DO** replaced with a refusal.

Architecture:
  - A LOW-LEVEL primitive: it depends only on the state machine, the
    settled-status predicate, the session, and the models -- never on the
    higher-level services that call it (``transaction_service``,
    ``credit_workflow``, ``transfer_service``, the route layer, and the loan /
    paycheck settle paths).  Living below its callers is what keeps it free of
    the ``transaction_service <- entry_service <- entry_credit_workflow <-
    credit_workflow`` import cycle: were the seam in ``transaction_service``
    (which imports ``entry_service``), ``credit_workflow`` could not import it
    without closing that cycle.
  - **It also owns the DOOR-SIDE reading of a submitted settle day**
    (:func:`settle_day_for_status`, plan step X-f1c / ruling R-EG), which is
    form-submission policy rather than a primitive.  It lives here anyway, and
    the narrower "primitive only" claim this paragraph used to make alone was
    corrected by a neutral review: the rule has THREE route doors (the
    transaction PATCH, the transfer PATCH, and the transaction PATCH's
    shadow branch) spread over two route packages, so the alternatives were a
    shared route helper -- a cross-package private import, which W9910 fences --
    or three spellings of one rule, which is this arc's own root cause 1.  It
    sits beside the refusal it defers to
    (:func:`reject_settle_day_without_settled_status`) so the forgiving door
    rule and the fail-loud service rule are read together.
  - The dependency claim above is unchanged by that: the function is pure, and
    reads only the settled-status predicate.
  - No Flask imports.  Mutates the passed row in place; does NOT flush or
    commit -- the caller owns the session boundary.
"""

from datetime import date
from typing import Optional, Union

from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction, reject_settle_instant
from app.models.transfer import Transfer
from app.services import pay_period_service
from app.services.state_machine import verify_transition
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today

#: The rows this seam accepts.  ``Transfer`` carries no ``settled_on`` column --
#: a transfer's settle day lives on its two shadow ``Transaction`` rows --
#: so the dating half of the mechanics is skipped for one of the two.  The
#: branch is on the MODEL, never on ``hasattr``: a probe would silently skip the
#: maintenance for any future row that merely spelled the column differently,
#: and this arc has already paid for a ``hasattr``-shaped test -- plan step
#: X-aa's, whose lesson is Section 8's "``hasattr`` on a dataclass is not a
#: test".  (An earlier draft cited ruling R-CQ for that; R-CQ is the classifier
#: RENAME and carries no such lesson.)
StatusBearingRow = Union[Transaction, Transfer]


def reject_settle_day_without_settled_status(
    status_id: int, settled_on: Optional[date],
) -> None:
    """Refuse a settle day supplied for a status that is not settled.

    **One half of the settled-iff-dated invariant, stated once** (plan step
    X-f1, finding **N-183**).  A row carries the civil day its money moved if
    and only if it is in a settled status (Paid / Received / Settled), so a day
    handed in beside a Projected / Credit / Cancelled status is not a value to
    store -- it is a request to record a payment that has not happened.

    It is a module-level function rather than an inline check inside
    :func:`apply_status_change` because ONE caller has to ask the question
    BEFORE the seam can: ``transfer_service.create_transfer`` validates
    ``spec.settled_on`` against ``spec.status_id`` before any row exists, and
    for an unsettled create it never reaches the seam at all (its settle branch
    is gated on the status being settled), so the day would be silently
    dropped.  Two moments, one rule -- the alternative is the same sentence
    written twice, which is this arc's own root cause 1.

    Args:
        status_id: The ``ref.statuses.id`` the row is (or would be) in.
        settled_on: The settle day supplied beside it, or ``None`` when the
            caller supplied none.  ``None`` is always accepted -- it means "no
            day was offered", which is legal for either kind of status.

    Raises:
        ValidationError: When *settled_on* is not ``None`` and *status_id* is
            not one of :func:`~app.utils.balance_predicates.settled_status_ids`.

            **A ``ValidationError`` (a 400) rather than a programming error,
            and NO form can reach it yet.**  Measured: no schema declares a
            settle day, no template renders one, and no ``app/`` caller passes
            ``settled_on`` to ``transfer_service.update_transfer`` -- so today
            the only way here is a service-layer mistake, for which a 400 is
            generous.  Plan step **X-f1c** is what makes it a user mistake with
            a correction: it puts the field on the full-edit door, and
            submitting a day while moving the row back to Projected becomes an
            ordinary form error.  The class is chosen for the door that is
            coming rather than re-picked when it lands; saying so beats a
            rationale in the present tense that is not yet true.
    """
    if settled_on is None:
        return
    if status_id in settled_status_ids():
        return
    raise ValidationError(
        f"A settle day ({settled_on.isoformat()}) was supplied for status "
        f"{status_id}, which is not a settled status.  A row records the day "
        "its money moved only while it is settled (Paid / Received / "
        "Settled); mark it settled to give it a day, or clear the day to "
        "leave it projected."
    )


def reject_future_settle_day(settled_on: Optional[date]) -> None:
    """Refuse a settle day that has not happened yet (ruling **R-EJ**).

    A row carries a settle day if and only if it is settled, and settled means
    the money HAS moved -- so a day in the future is not a fact about money, it
    is a forecast in a fact column.  ``Transaction``'s class docstring specified
    this rule before any door could reach it: *"a 'not in the future' rule is
    not expressible in a CHECK (it is not immutable) and lives at the write door
    instead, exactly as ruling R-M's purchase-date guard does for an entry."*
    Plan step X-f1c is that write door, and the first one where a USER supplies
    the day.

    **What it costs to omit, MEASURED end to end through the live routes** (two
    independent derivations, one number -- the step's own trace and a neutral
    adversarial review).  A settled source counts from its own ``settled_on``
    (``cash_ledger.dated_deltas``), and
    :func:`app.services.cash_ledger.walk_cash_ledger` absorbs one into an
    assertion only when the assertion is dated ON OR AFTER it -- so a
    future-dated settle rides on top of every assertion until that day arrives.
    On a ``$1,000`` anchor a ``$100`` expense settled three days ago reads
    ``$900``; PATCH its day forward and the route answers ``200`` with the
    balance back at ``$1,000``.  **Already-spent money, back in the projection.**

    **And it is the LIKELY input, not an exotic one.**  The correction box tells
    the user to correct the day against their statement, and a statement's most
    common disagreement is a PENDING item carrying a FUTURE posting date.

    **The opposite rule on ``TransactionEntry.settled_on`` is not a
    contradiction.**  A future ENTRY posting day is the CONSERVATIVE direction --
    no assertion closes over it, so the debit stays reserved and the balance
    stays low -- so that door bounds only from below and says so.  A future
    ``Transaction.settled_on`` points the other way: it takes settled money OUT
    of the balance.  The two fields' rationales do not transfer.

    **The recorded clearing fact does not undo that** (plan step X-f3a-1, ruling
    **R-FL**), and an adversarial review was right to ask: a LINKED purchase is
    cleared whatever its day says, so a linked entry moved to a future day would
    release its reservation and put already-reserved money back in the
    projection -- the very failure this refusal exists to prevent, arriving
    through the exempt door.  It cannot happen, because
    ``entry_service.update_entry`` RELEASES the link whenever the posting day
    moves: a future-dated entry is therefore always unlinked, and the day rule
    answers it exactly as this paragraph describes.

    It lives here, beside :func:`reject_settle_day_without_settled_status` and
    the ``datetime`` refusal, for the reason those are here: ONE door, every
    write path, a value the seam does not accept rather than a check each caller
    has to remember.  Both date inputs also carry ``max`` = today, so the browser
    refuses first and this is the backstop -- the same layering
    ``accounts/form.html`` uses for an anchor's ``observed_on``.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse a settle the user is making right now.

    Args:
        settled_on: The candidate settle day, or ``None`` when none was
            supplied.  ``None`` is always accepted -- it means "derive the day
            from the status", which is the everyday path.

    Raises:
        ValidationError: When *settled_on* is later than the user's today.  A
            400 rather than a programming error, because plan step X-f1c makes
            it reachable by an ordinary user typing in the correction box; the
            route layer renders it as a designed error fragment.
    """
    if settled_on is None:
        return
    today = display_today()
    if settled_on <= today:
        return
    raise ValidationError(
        f"A settle day of {settled_on.isoformat()} has not happened yet "
        f"(today is {today.isoformat()}).  A row records the day its money "
        "moved, so the day cannot be in the future -- if the payment is "
        "scheduled rather than made, leave it Projected."
    )


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


def apply_status_change(
    row: StatusBearingRow,
    new_status_id: int,
    *,
    settled_on: Optional[date] = None,
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
      3. maintain ``settled_on`` (see the *settled_on* arg) -- **transactions
         only**, because ``Transfer`` has no such column: a transfer's settle
         day lives on its two shadow rows, and the transfer service applies
         this seam to those shadows, so a transfer settle still records its day.
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

    Raises:
        ValidationError: If the transition is illegal for *row*'s workflow
            (propagated from ``verify_transition``), or if *settled_on* is
            supplied for a *new_status_id* that is not settled (propagated from
            :func:`reject_settle_day_without_settled_status`).
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

    db.session.expire(row, ["status"])
