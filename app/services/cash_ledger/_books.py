"""The boundary between an account's OPENING and its RECORDS, both directions.

**An account's opening equity is the balance at the CLOSE of its ``opened_on``**
(ruling **balance:R-HG**), exactly the rule
:attr:`~._events.CashAnchorFact.observed_on` states for a balance assertion
(ruling R-DH (a)).  One boundary follows from that, and it can be crossed from
either side:

* a MOVEMENT may not be dated on or before the day its account's books open --
  :func:`reject_movement_before_books_open`, the sentence a settle-day box gets;
* an OPENING may not be restated onto or past a day the account already records
  money moving -- :func:`reject_books_open_on_or_after_movements`, the sentence
  the restatement door gets (plan step **X-f3c-2b-2a**);
* an OPENING may not be restated past a day the owner has ASSERTED a balance
  for -- :func:`reject_books_open_after_an_assertion`.  The third question, and
  the one the first two did not see: an account with no settled movement is
  unbounded by the rule above, which is every investment, retirement and
  property account in production.  Reproduced on the developer's own Roth IRA,
  it fabricated ``$22,809.02`` of investment return and discarded the opening
  the owner had just stated.

**They are two questions about one rule, and they live in one module because
the alternative already bit this arc.**  Each reads a different table to answer
the same comparison, so nothing about them can be collapsed into a shared
function -- which is precisely why they have to be READ together: "two
statements of one rule that differ silently is the failure this arc names as
its own root cause" (:mod:`app.opening_infrastructure`).  The movement half
lived in :mod:`._events` beside the opening RECORD it reads while it was the
only half; with a second half it belongs beside its twin instead, and
:mod:`._events` goes back to being what its own docstring says it is -- the
loaders that build the event stream.

**The DATABASE is the structural half of both, and neither of these is.**
:mod:`app.opening_infrastructure` installs deferrable constraint triggers over
``budget.transactions``, ``budget.transaction_entries`` AND
``budget.account_openings``, so the state is unstorable by any single
transaction from any client -- a bulk ``UPDATE``, a raw statement, a psql
session, a writer nobody enumerated.  These two functions exist so an ordinary
date box gets a sentence instead of a ``psycopg2`` exception at COMMIT: the
same pairing ``ck_transactions_settle_day_needs_a_record`` has with
:func:`app.services.status_seam.reject_settle_day_without_a_record`.

**Which is why the ROW SET below is imported rather than re-spelled.**  The
opening-side predicate has to refuse exactly what the trigger refuses, or a
submission passes here and aborts at COMMIT with a message no surface can
render -- and the trigger's row set is deliberately WIDER than the balance
fold's: it counts SOFT-DELETED rows and the Credit / Cancelled statuses, so a
row the owner cannot see still bounds how far back their books may be restated
(see :data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL` for why narrowing
it opens a hole on RESTORE).  Interpolating that one SQL statement is what
makes "the service refuses what the database refuses" true by construction
rather than by two authors agreeing.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, plain
values out; no Flask symbol, no writes, no clock read.  The three REFUSALS
raise :class:`~app.exceptions.ValidationError` and do nothing else; the two
day readers beside them return a date and raise nothing.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, text

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.opening_infrastructure import SETTLED_MOVEMENTS_SQL

from ._events import account_opening_fact


def reject_movement_before_books_open(account_id: int, day: date) -> None:
    """Refuse a cash movement dated on or before *account_id*'s opening day.

    The MOVEMENT side of the boundary (plan step X-f3c-2b, finding **N-378**).
    An account's opening equity is what it held at the CLOSE of
    :attr:`~._events.CashOpeningFact.opened_on`, so a movement dated on or
    before that day is ALREADY INSIDE the figure, and recording it counts the
    money twice.

    **What the double count costs, and why the balance healing is not a
    defence.**  The fold seeds at the opening equity and
    :func:`~._walk.dated_deltas` emits every source at its own day, so between
    the movement's day and the next assertion the running total carries it a
    second time.  The next assertion RESETS to what the owner declared, so the
    rendered balance heals -- but the correction that heals it is booked to the
    general ledger, and on a MODELLED account (ruling **R-FO**) its counter leg
    is ``unrealized_change``, not ``anchor_equity``.  A transfer therefore
    becomes market performance that never unwinds.  Measured on a fixture: a
    Roth declared ``$1,000.00`` with a ``$1,000.00`` pre-opening transfer
    reports ``$850.00`` of unrealized change against a real ``$150.00``.

    It is asked by the TWO writers of a settle day -- the ORM one
    (:func:`app.services.settle_day.record_settle_day`, which every door for
    both ``budget.transactions`` and ``budget.transaction_entries`` goes
    through) and the bulk one (``reconcile_service.record_settled_days``, a
    ``query.update()`` with no ORM instance to hand that function).  Two
    callers, one predicate.

    **The CALLER owns the ownership scoping, and the message is why that
    matters.**  The refusal names the account's opening equity so a date box
    can render it verbatim, and this function applies no ``user_id`` filter of
    its own.  Every caller today reaches it behind an ownership check -- the
    routes resolve the row by owner before any settle door is entered -- so
    the figure only ever reaches the owner.  A future caller that took an
    account id straight from a request would turn this message into a balance
    oracle.

    Args:
        account_id: The account the movement belongs to.  Assumed already
            scoped to the acting user by the caller.
        day: The civil day the movement's cash moved.

    Raises:
        ValidationError: When *day* is on or before the account's opening day.
            A 400 rather than a programming error: the day arrives from a date
            box, and the message names both the offending value and the bound
            it broke so a surface can render it verbatim.
        RuntimeError: When the account carries no opening record, propagated
            from :func:`~._events.account_opening_fact` -- a broken invariant,
            and deliberately not softened here into "then anything is allowed".
    """
    # **Under ``no_autoflush``, and that is a defect plan step X-f3c-2b's own
    # suite caught rather than a precaution.**  This is the only READ on a
    # write path, and SQLAlchemy autoflushes pending mutations before a query:
    # the caller has already assigned part of the row it is midway through
    # writing -- ``apply_status_change`` sets ``status_id`` before it reaches
    # :func:`app.services.settle_day.record_settle_day` -- so the flush lands a
    # half-written row against constraints that describe the finished one.
    # Measured: a row already carrying a settle day and no settlement record,
    # which is the LEGACY shape ``ck_transactions_settle_day_needs_a_record``
    # exists to let an owner repair, failed with a raw ``CheckViolation``
    # raised "as a result of Query-invoked autoflush".  Suppressing the flush
    # cannot hide a pending opening from this read, and the reason is stated
    # precisely because the whole paragraph is about autoflush: the ONE writer
    # (``opening_service.stage_account_opening``) only ever stages, and BOTH
    # of its entrances emit the row before returning -- a restatement through
    # the posting reconcile's first query, an origination through
    # ``stage_anchor_true_up``'s advisory-lock statement (measured 2026-08-31:
    # the opening INSERT lands at statement 6 of ``create_account``).  Neither
    # leaves an unflushed opening for a settle in the same transaction to
    # race, and the migration writes through ``op.execute`` outside the ORM
    # entirely.
    with db.session.no_autoflush:
        opening = account_opening_fact(account_id)
    if day > opening.opened_on:
        return
    raise ValidationError(
        f"Money cannot have moved on {day.isoformat()}: this account's books "
        f"open on {opening.opened_on.isoformat()} holding "
        f"${opening.opening_equity}, and that figure is the closing balance "
        "for its own day -- so anything that moved by then is already inside "
        "it.  Restate the account's opening to an earlier day if the records "
        "really do start before it."
    )


def earliest_recorded_movement_day(account_id: int) -> date | None:
    """Return the first day *account_id* records cash moving, or ``None``.

    ``MIN(settled_on)`` over BOTH movement tables, and it is the SAME row set
    the database constraint counts -- one SQL statement
    (:data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL`), interpolated
    here and into ``budget.assert_account_books_hold_its_movements`` rather
    than written twice.  That is what makes
    :func:`reject_books_open_on_or_after_movements` refuse exactly what the
    trigger refuses, instead of two authors agreeing to keep two queries in
    step.

    **The row set is deliberately WIDER than the balance fold's**, so this
    answers a day the fold ignores.  ``balance_contributing_clause`` excludes
    ``is_deleted`` rows and the Credit / Cancelled statuses; this counts them,
    because un-deleting a row is an ``UPDATE`` of ``is_deleted`` alone and the
    movement trigger fires ``UPDATE OF settled_on, account_id`` -- so a
    restored pre-books row would pass every tier untouched.  The cost is
    over-refusal, which is the safe direction: it refuses a legal act loudly
    rather than admitting an illegal one silently.

    **Raw SQL rather than a two-model union, deliberately.**  The point of the
    function is to ask the constraint's own question, and the constraint's
    question exists as a SQL string; rebuilding it out of
    :class:`~app.models.transaction.Transaction` and
    :class:`~app.models.transaction_entry.TransactionEntry` would be a second
    spelling that no gate could grade against the first -- ``duplicate-code``
    does not see SQL inside a string literal.

    Args:
        account_id: The account whose movements to bound.

    Returns:
        The earliest ``settled_on`` recorded against the account on either
        movement table, or ``None`` when it records none.
    """
    return db.session.execute(
        text(
            f"SELECT MIN(settled_on) FROM ({SETTLED_MOVEMENTS_SQL}) "
            "AS movements WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).scalar()


def reject_books_open_on_or_after_movements(
    account_id: int, day: date,
) -> None:
    """Refuse opening *account_id*'s books on or after a recorded movement.

    The OPENING side of the boundary (plan step **X-f3c-2b-2a**), and the exact
    mirror of :func:`reject_movement_before_books_open`: the movement door is
    handed a day and asks where the books open, this door is handed a day and
    asks when the records start.  Both refuse the same state; which one an
    owner meets is decided by which act they are performing.

    **It is asked under ``no_autoflush`` for the reason its twin is**, and the
    reason is sharper here: the restatement door calls this BEFORE it stages
    the new ``budget.account_openings`` row, so an autoflush at this query
    could only ever emit some other pending mutation of the caller's -- never
    the opening being judged, which does not exist yet.  Letting a half-written
    unrelated row flush against the constraints that describe the finished one
    is the defect measured one door over, and suppressing it costs nothing:
    this function reads only the two movement tables, and no writer of those
    reaches a restatement.

    Args:
        account_id: The account whose books are being opened or restated.
            Assumed already scoped to the acting user by the caller, exactly as
            :func:`reject_movement_before_books_open` assumes it -- the message
            names the account's own earliest recorded day, which is a fact
            about the owner's records.
        day: The candidate ``opened_on``.

    Raises:
        ValidationError: When the account already records money moving on or
            before *day*.  A 400 rather than a programming error: the day
            arrives from a date box, and the message names both the offending
            value and the bound it broke so a surface can render it verbatim.
    """
    with db.session.no_autoflush:
        earliest = earliest_recorded_movement_day(account_id)
    if earliest is None or day < earliest:
        return
    raise ValidationError(
        f"These books cannot open on {day.isoformat()}: this account already "
        f"records money moving on {earliest.isoformat()}, and an opening "
        "equity is the closing balance for its own day -- so that movement "
        "would be counted twice.  Open the books on a day before it, or "
        "re-date the movement first."
    )


def earliest_assertion_day(account_id: int) -> "date | None":
    """Return the first day *account_id* was asserted to hold a balance.

    ``MIN(observed_on)`` over ``budget.account_anchor_history``, the bound
    :func:`reject_books_open_after_an_assertion` compares against.  Public for
    the same reason :func:`earliest_recorded_movement_day` is: the restatement
    form renders it as its date input's ceiling, so the browser refuses what
    the service would refuse rather than round-tripping a rejection.

    **Every assertion, not the governing one.**  The rule is about the EARLIEST
    balance the owner has stated, because that is the first one the fold resets
    at -- a later assertion cannot be the one an opening jumps over first.

    Args:
        account_id: The account whose assertions to bound against.

    Returns:
        The earliest ``observed_on`` on the account, or ``None`` when it
        carries no assertion at all -- a broken invariant elsewhere
        (``account_service.create_account`` writes one), and an honest empty
        answer here.
    """
    return db.session.query(
        func.min(AccountAnchorHistory.observed_on)
    ).filter(AccountAnchorHistory.account_id == account_id).scalar()


def reject_books_open_after_an_assertion(account_id: int, day: date) -> None:
    """Refuse opening *account_id*'s books after a balance it has asserted.

    **The THIRD bound, and it closes a money defect the first two did not
    see** (code review, 2026-08-31).  The movement rule bounds an opening
    against recorded CASH; nothing bounded it against what the owner has
    SAID the account held.  An account with no settled movement -- which is
    every investment, retirement and property account in production -- could
    therefore have its books restated to any day up to today, past every
    assertion it carries.

    **What that costs, reproduced on the developer's own Roth IRA.**  Restating
    it to 2026-08-01 at ``$100.00``, over six assertions running from
    2026-03-31 to 2026-07-16, was ACCEPTED: ``unrealized_change`` moved from
    ``-$4,523.33`` to ``-$27,332.35``, so the app reported ``$22,809.02`` of
    investment return that never happened.  The stated opening was discarded in
    the same act -- the earliest assertion RESETS the fold, so the balance went
    on reading ``$27,432.35`` and the ``$100.00`` the owner typed was never
    read by anything.  Both halves are finding **N-378**'s shape, reached from
    the opening side rather than the movement side.

    **The comparison is ``>``, not ``>=``, and equality is the ORDINARY case.**
    ``account_service.create_account`` writes the origination opening and the
    origination assertion for the same day, so an opening EQUAL to the earliest
    assertion is what every account starts life with -- three of the
    developer's nine still sit exactly there.  Refusing equality would make the
    factory's own output unrestatable.

    Args:
        account_id: The account whose books are being restated.  Assumed
            already scoped to the acting user by the caller.
        day: The candidate ``opened_on``.

    Raises:
        ValidationError: When the account has asserted a balance for a day
            before *day*.  A 400: the day arrives from a date box, and the
            message names the offending value and the bound it broke.
    """
    with db.session.no_autoflush:
        first = earliest_assertion_day(account_id)
    if first is None or day <= first:
        return
    raise ValidationError(
        f"These books cannot open on {day.isoformat()}: you have already "
        f"recorded a balance for this account on {first.isoformat()}, and a "
        "balance you recorded is what the app folds forward from -- so an "
        "opening after it would be ignored, and the gap would be reported as "
        "a gain. Open the books on or before that day."
    )
