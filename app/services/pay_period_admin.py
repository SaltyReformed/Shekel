"""
Shekel Budget App -- Pay Period Admin Service

The structural / destructive pay-period operations -- the lock
classifier and extend / truncate / regenerate -- kept out of the heavily
imported read/generate ``pay_period_service`` so the destructive paths
live in one isolated place.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``; flushes / bulk-deletes,
never commits (the route owns the transaction).

**It DECIDES; it does not write** (plan step C3-b).  Every row this
module adds or removes goes through
:mod:`app.services.pay_period_write`, the one place in ``app/`` that
changes ``budget.pay_periods``.  That single home is why plan step
``pay_calendar:C4-c`` could drop ``end_date`` and ``period_index`` in one
place: while they were stored, the rule that they equalled the derivation
over the owner's paydays lived there and nowhere else.  What stays here is
the orchestration -- the four doors, and which reconciles a wipe owes; the two
gates they consult (which periods may go: the lock classifier and the discard
count) moved to :mod:`app.services.pay_period_gates` at plan step
``pay_calendar:C14-f``.

**Nothing here REPOPULATES any more, and that is ruling R-R38** (plan step
R7d-c-1).  Each door recorded its paydays and then, in the SAME call,
generated every active template's recurring rows into them -- a write followed
by a READ-DEPENDENT write, so no caller could get between the two to open the
read pass that generation resolves in, and the module that ran it had to open
its own.  The doors now record and RETURN; the caller runs
:func:`app.routes._period_population.populate_new_periods` next, which opens
the pass AFTER the periods exist and BEFORE the rows do.  What a pass opened
any earlier answers is measured in that module's docstring.

The gates' foundation is the reusable **lock classifier** in
``pay_period_locks``: the one place that decides whether a pay period
may be deleted or rebuilt.  Truncate and regenerate consult it before
touching anything; the settings UI renders its result as a per-period
lock badge.

**The ROLLING TOP-UP left at plan step C4** for
:mod:`app.services.pay_period_rolling`, and the seam is the one this docstring
already drew: the four doors here are DESTRUCTIVE and user-initiated, while the
top-up is an opportunistic appender ``/grid`` and ``/dashboard`` run on every
render.  Finding **P31** is what forced it -- this module reached 991 of
pylint's 1,000-line ceiling, so the next correction would have had to delete
prose to fit, which is that finding's own sentence.

**Nothing here reads ``end_date`` or ``period_index``, the two columns plan step
C4-c dropped -- and since C4's FIRST commit that is the WHOLE module rather than the
narrow claim it was** (finding **P70**).  Every door decides on the owner's
schedule read once through ``pay_calendar``, in
:class:`~app.services.pay_calendar.DerivedPeriod` values.  Three of the four
doors still RETURN ``list[PayPeriod]`` from ``pay_period_write`` to their own
caller, which populates them -- the writer's OUTPUT, not an input to any
decision here.  What the doors hand the writer is the set of
``budget.pay_periods.id`` to retire.

**Each door resolves "today" ONCE, as the OWNER's civil day**
(``utils.dates.display_today``), and both halves of that are plan step C2-f3b's.
``regenerate_pay_periods`` read the wall clock THREE times for one decision --
the not-yet-started test plus two independent lock classifies -- which was
benign only because a period cannot become historical between two statements of
one transaction.  And the day it read was ``date.today()``, the PROCESS clock,
where these decisions are made against the OWNER's calendar: finding
**balance:N-191** named this module's two sites, and the developer ruled the
owner's day on 2026-08-19.  **TWO of the repository's THREE compose files pin
``TZ: America/New_York``** -- ``deploy/docker-compose.prod.yml`` (what is
deployed) and ``docker-compose.dev.yml`` -- so the two clocks are equal there;
the repo-root ``docker-compose.yml``, whose own first line calls it the
production compose, pins nothing, and neither does CI, a script or a bare
``flask run``.  Where they differ the owner's day is the EARLIER one, so exactly
two decisions flip and both admit MORE: a paycheck ending yesterday-in-UTC is
still current, and a paycheck opening tomorrow-in-UTC is still unstarted.
Settled money and posted ledger entries lock either way.

**A no-op truncate costs four queries more than it did, and that is stated
rather than absorbed** (adversarial review, 2026-08-19).  The lock map is an
ARGUMENT to :func:`_gate_deletable_tail` so that one door can read it once, so
its two set queries run ahead of that function's "nothing to delete" early
return, and ``retire_paydays`` reads the owner's rows before returning 0.  The
alternative is truncate and regenerate taking different shapes, which is what
the shared gate exists to prevent; these are user-action doors rather than
render paths, and one clock and one map is the trade.
"""

import logging
from app.exceptions import PayPeriodResetBlocked, PayPeriodUnresolved
from app.services import (
    account_posting_service,
    loan_posting_service,
    pay_period_gates,
    pay_period_write,
    user_write_lock,
)
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import classify_schedule_locks
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


def extend_pay_periods(user_id, num_periods):
    """Append ``num_periods`` pay periods to the end of the user's schedule.

    Tail-append only: the new paydays fall after every existing one.  While
    the ordinal was a COLUMN that mattered -- only tail-append and
    tail-truncate preserved ``period_index == calendar-order``, which the
    balance resolver walks; plan step ``pay_calendar:C4-c`` dropped it, and an
    ordinal that IS the position in payday order cannot disagree with one.
    :func:`~app.services.pay_period_write.continue_paydays` creates the new
    periods EMPTY -- it does not run the recurrence engine -- and this door
    LEAVES them empty (ruling **R-R38**): the caller repopulates them through
    :func:`app.routes._period_population.populate_new_periods`, which opens the
    read pass the generation resolves in.  The module docstring carries why
    that call cannot be made from here.

    **It takes no cadence, and that is finding P29's fix** (plan step C3-b).
    ``cadence_days`` was an accepted parameter, forwarded from a Marshmallow
    field the extend card renders NO control for, so a direct POST could
    generate 7-day paychecks while ``budget.pay_schedule`` still said 14.
    Extend CONTINUES an existing schedule, so the cadence is not a question it
    gets to ask.  The finding closes by the state becoming unreachable rather
    than by adding a write, which is what finding **P30** objected to.

    **It STATES nothing, since plan step ``pay_calendar:C17-c-2b``** (rulings
    **R-PC75**, **R-PC78**; ledger rows **PC-509** and **N-494** closed).
    This door is the lock and one call: the writer's CONTINUE door records
    the next paydays the owner's own plan projects
    (:func:`~app.services.pay_calendar.planned_paydays_after`), reading the
    eras and the record itself.  Until then this door computed the batch --
    the LATEST era's grid day past the horizon, handed to
    :func:`~app.services.pay_period_write.record_paydays` with that era's
    rhythm as a stated batch -- and the two shapes were one answer only for
    an owner whose latest era covers their record.  The history of how the
    computed day was chosen (ledger rows **N-495**, **PC-497** fault 2,
    ruling **R-PC61**: the grid stepped from a stored phase rather than from
    the last recorded payday, because a batch anchored on a displaced cash
    day re-phased the rhythm permanently) is the calendar package's to carry
    now: ``pay_calendar/_grid.py``'s module docstring holds the measurement,
    and :func:`~app.services.pay_calendar.projected_payday`'s the anchor's
    move to the era's phase.

    Args:
        user_id: The owning user's id.
        num_periods: How many periods to append (>= 1; the route's
            schema validates the range).

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects, flushed and EMPTY -- no recurring row has been generated into
        them yet.

    Raises:
        ValidationError: When the user has no existing periods to extend
            from (they must generate first), or ``num_periods`` is outside
            the batch bound.
        PayCalendarError: The owner holds no ``budget.pay_schedule`` row or
            no era (:func:`~app.services.pay_calendar.schedule_for`); or their
            eras reach no payday past the record within two cadences, which
            needs a displacement a whole cadence long and is ledger row
            **N-493**'s reported hole rather than a state a door admits.
            **Neither is the ``ValidationError`` the route catches**: both
            reach ``app/error_handlers.py``'s recovery page for this
            exception rather than the extend card's flash, which is the right
            surface for "this owner has no derivable calendar" and the wrong
            one for "that date is not allowed".
    """
    # Serialize against concurrent structural mutations for this user so the
    # record is read under the lock and the append cannot race another
    # extend / top-up into a duplicate payday.  ``uq_pay_periods_user_start``
    # is the hard guard; the lock keeps the racing loser from hitting it as a
    # 500.
    user_write_lock.lock_user_writes(user_id)
    return pay_period_write.continue_paydays(user_id, num_periods)


def truncate_pay_periods(
    user_id: int, keep_through_period_id: int, confirm_discard: bool = False,
) -> int:
    """Delete the schedule tail beyond the period *keep_through_period_id* names.

    **The public wire door, and it takes an ``id`` rather than an ordinal**
    (plan step C3-a, finding **P13**).  This parameter was
    ``keep_through_index``, a ``period_index`` the form posted as an
    ``<option value>`` and the discard-confirm 422 echoed into a re-submittable
    hidden field -- a user-supplied POSITION selecting which periods a CASCADE
    destroyed, across a browser round trip.  Safe only while nothing renumbers:
    from plan step C3-b (both columns materialised from the payday list) or C6
    (mid-schedule insert), an ordinal read in an earlier request names a
    DIFFERENT period than the user reviewed and takes its transactions, its
    transfers with both shadows, and its journal entries.  ``user_write_lock``
    cannot help -- the stale value crossed a REQUEST boundary, not a concurrent
    one.  Identity is ``id``, so the wire key is ``id``.

    Removes every pay period whose PAYDAY falls after the named period's
    (tail-truncate preserves the index==calendar invariant; only tail ops do).
    Two gates protect real data, checked in order before anything is deleted:

      1. **Hard locks (not overridable).** If any to-delete period is
         historical, holds a settled transaction, carries an unbalanced
         ledger account, raise
         :class:`PayPeriodLocked` and delete nothing.
      2. **Discard gate (overridable).** If any to-delete period holds a
         row regeneration cannot reproduce -- hand-entered, override, or
         Credit/Cancelled -- and ``confirm_discard`` is False, raise
         :class:`PayPeriodDiscardRequired` and delete nothing.

    Deletion is a single bulk ``DELETE`` so PostgreSQL performs the whole
    cascade in one pass: transactions and transfers (and both shadows,
    preserving the transfer invariant) go; DB-level audit triggers
    still fire.  **RECURRENCE RULES are untouched since plan step R7b-4** --
    a rule's opening bound is a DATE (``start_date``) rather than a pay-period
    FK, so there is no longer anything on that table for a period delete to
    cascade into.  **Balance ASSERTIONS do NOT go** -- ruling R-EO deleted
    ``account_anchor_history.pay_period_id``, so a schedule operation can
    no longer destroy the record of what the bank said.  The statement lives in
    :func:`~app.services.pay_period_write.retire_paydays` since plan step C3-b;
    the two gates below are what THIS door contributes.

    **Shortening the schedule can leave a settled row's cash day outside it,
    and that is ACCEPTED** (developer ruling 2026-08-11, which deleted the rule
    that refused it).  Removing the tail drops the new last period back to its
    cadence projection, so a row filed in a surviving period but settled in the
    days that projection gives up keeps counting against its paycheck while no
    column holds the day its money moved.  ``_cash_periods`` reports that as the
    ``period_timing`` remainder ruling R-DH split out for it, every column's
    identity stays exact, and the balance is right either way -- on the new last
    ``end_date`` the bank genuinely had not taken the money yet.

    **The named period is always KEPT, so THIS DOOR can never empty a
    schedule**, which three docstrings elsewhere rest on.  "This door" rather
    than "truncate", precisely: the ordinal form protected the ROUTE with a
    Marshmallow floor of zero, while the service beneath it emptied schedules
    routinely -- ``_regenerate_keep_through_index`` answered ``-1``, and
    :func:`_gate_deletable_tail` still selects every period when regenerate
    hands it
    ``None``.  The guarantee now rests on the resolve below instead: an id must
    name one of this owner's periods, and that period is on the KEEP side of
    the comparison by construction.

    Args:
        user_id: The owning user's id.
        keep_through_period_id: The ``budget.pay_periods.id`` of the last
            period to KEEP.  Must name one of *user_id*'s own periods.
        confirm_discard: When True, proceed past the discard gate (the
            user has acknowledged the loss).  Hard locks are never
            bypassed.

    Returns:
        The number of pay periods deleted (0 when the named period is
        already the last one -- an idempotent no-op).

    Raises:
        PayPeriodUnresolved: *keep_through_period_id* names no pay period of
            *user_id*'s.  **"No such period" and "not your period" raise the
            SAME message deliberately**, so a caller cannot use this door to
            learn whether another owner's id exists.  A stale id -- one the
            browser held from before a concurrent truncate -- lands here too,
            and refusing it is the point: the alternative is deleting a tail
            the user never reviewed.
        PayPeriodLocked: A to-delete period is hard-locked.
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    # Serialize against concurrent structural mutations so the resolve, the
    # classify and the bulk DELETE see one consistent set -- closes the
    # classify-then-DELETE TOCTOU against another extend / top-up /
    # truncate for this user.
    user_write_lock.lock_user_writes(user_id)

    # The OWNER's calendar, so the resolve below is owner-scoped by
    # construction rather than by a comparison this function has to remember to
    # make: ``period_by_id`` searches only the periods derived from this
    # owner's paydays, so another owner's id can only ever answer ``None``.
    calendar = calendar_for(user_id)
    kept = calendar.period_by_id(keep_through_period_id)
    if kept is None:
        pay_period_gates.log_unresolved_period(user_id, keep_through_period_id)
        raise PayPeriodUnresolved(keep_through_period_id)
    doomed = pay_period_gates.gate_deletable_tail(
        calendar.saved(), kept, confirm_discard,
        classify_schedule_locks(calendar, as_of=display_today()),
    )
    return pay_period_write.retire_paydays(
        user_id, {period.period_id for period in doomed},
    )


def regenerate_pay_periods(
    user_id, new_start_date, num_periods, rhythm, confirm_discard=False,
):
    """Rebuild the not-yet-started, unlocked tail from a corrected start.

    "Fix a mistake" without per-period date editing: truncate the
    rebuildable future tail (the first not-yet-started unlocked period
    onward), then generate a fresh ``num_periods``-long schedule from
    ``new_start_date`` at ``cadence_days``.  The new periods come back EMPTY
    and the caller repopulates them (ruling **R-R38**; see the module
    docstring).  Periods that have already started,
    are historical, hold settled money or posted ledger entries, or anchor a
    recurrence rule are KEPT; if any such locked period sits inside the rebuildable tail the
    truncate step refuses (history cannot be rewritten under a settled
    paycheck).  A new rhythm becomes a new ERA from the corrected start
    (plan step ``pay_calendar:C17-a``) so later extends continue at it, and
    the kept paydays keep the era they ran on.

    The whole operation is one transaction the route commits: if the writer
    rejects ``new_start_date`` after the truncate has run, the route's rollback
    undoes the truncate too -- nothing partial ships.  *That rollback was a
    claim this docstring made and no route performed until plan step C3-b; an
    adversarial review found it.*  It is the route's and not this function's
    because the caller owns the transaction boundary (the module docstring's
    rule), and a service that rolled back would be deciding for a caller that
    may have staged work of its own.

    **The rhythm is persisted by the writer, not here** (plan step C3-b).
    This function used to upsert the schedule row itself, unconditionally,
    which is one half of finding **P12**: a batch that created nothing still
    rewrote the forecast cadence.  ``record_paydays`` now applies the one rule
    -- a batch that RECORDED a payday on a rhythm the covering era does not
    hold mints an era -- so the three doors that had a copy of this line have
    none.

    **This is the ERA-MINT door** (ruling **R-PC64**; plan step
    ``pay_calendar:C17-c-2a``).  An owner whose rhythm, convention or phase
    changed states here where their next paycheck really lands and how they
    are paid from it; the writer mints an era there when the stated rhythm
    is one the covering era does not hold.  What it may state is bounded at
    both ends by the writer, and the bounds are the OWNER'S PLAN rather than
    this door's arithmetic: the first payday may not land inside a paycheck
    they already have (the floor), and it may not skip a whole paycheck of
    the plan (the ceiling, ruling **R-PC67**) -- so a corrected first payday
    lands anywhere from the plan's next payday up to, not including, the one
    after it.  *Until ``C17-c-2a`` the second bound was a CONFIRMATION --
    ``Confirmations.gap``, threaded through here to
    ``pay_period_gates.reject_unconfirmed_gap`` -- and a confirmed gap was
    still a gap (ledger row **P80**); it is a refusal in
    ``pay_period_batch`` now, asked of every batch, and this door threads
    nothing for it.*

    Args:
        user_id: The owning user's id.
        new_start_date: First payday of the rebuilt tail, read as a day on
            the owner's NOMINAL grid.  Must fall on or after the plan's next
            payday past the last RETAINED period's (the writer's floor) and
            before the one after that (its ceiling); both re-check it.  It
            may fall INSIDE the retained schedule's projected coverage, which
            is what makes "correct my cadence going forward" expressible: the
            old guard bounded on the retained ``end_date`` -- a column plan
            step ``pay_calendar:C4-c`` dropped -- and so accepted only the
            single day after it.
        num_periods: How many periods to generate.
        rhythm: How often the rebuilt tail is paid and what payroll does
            when one of its paydays lands on a closed day
            (:class:`~app.services.pay_rhythm.Rhythm`); also
            persisted as a new era from *new_start_date* by the writer, when
            it differs from the era covering that day.  A PAIR rather than a
            bare cadence since plan step **C14-b**, because the two carry a
            joint rule the writer judges together.
        confirm_discard: When True, proceed past the discard gate -- the
            owner has acknowledged that the rebuildable tail holds rows
            regeneration cannot reproduce.  Forwarded to the truncate step;
            when False and the tail holds such rows, raise
            :class:`PayPeriodDiscardRequired` and change nothing.  Hard locks
            are never bypassed.
    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        PayPeriodLocked: A locked period sits inside the rebuildable tail.
        PayPeriodDiscardRequired: The tail holds unrecoverable rows and
            ``confirm_discard`` is False.
        ValidationError: ``new_start_date`` falls before the forward-only
            floor or at or past the plan's second projected payday
            (``record_paydays``' two bounds).
    """
    # Serialize the whole rebuild -- boundary computation through the
    # truncate + regenerate -- for this user; re-entrant with the lock
    # ``truncate_pay_periods`` used to take before plan step C3-a split the
    # resolve off the delete, and which the generate below still relies on.
    user_write_lock.lock_user_writes(user_id)

    # The schedule is read ONCE, under the lock, and threaded into both the
    # boundary computation and the delete.  Before plan step C3-a each of
    # those issued its own query, so the boundary was computed against one
    # snapshot and applied against another.
    #
    # The LOCKS are read once too, and the clock once, both at plan step
    # C2-f3b.  This door used to classify twice -- once over the whole schedule
    # to find where the rebuildable tail opens, once over the tail to refuse a
    # locked period inside it -- each call defaulting ``as_of`` to its own
    # ``date.today()``, and the boundary test read a third.  Three reads of one
    # fact that a fourth line then compares against each other is a state that
    # can disagree; one is not.
    as_of = display_today()
    calendar = calendar_for(user_id)
    saved = calendar.saved()
    locks = classify_schedule_locks(calendar, as_of=as_of)
    kept = pay_period_gates.regenerate_keep_through_period(saved, locks, as_of)
    # ONE write, and an adversarial review of plan step C3-b is why.  The
    # truncate and the rebuild used to be two calls, so everything downstream
    # saw the schedule BETWEEN them -- an interval this door then widens again.
    # The rule that measured it (the coverage rule) was deleted 2026-08-11,
    # and the second reason went with the derived columns at plan step
    # ``pay_calendar:C4-c`` -- the writer used to materialise that intermediate
    # shape, shortening the newly-last survivor to a cadence projection and
    # logging it as a repair before undoing both.  The composition stays on the
    # remaining reason: the gate below decides WHICH periods may go, and the
    # writer carries the delete out beside the create so every refusal is asked
    # of the payday set the operation leaves behind.
    doomed = pay_period_gates.gate_deletable_tail(
        saved, kept, confirm_discard, locks,
    )
    return pay_period_write.record_paydays(
        user_id, new_start_date, num_periods, rhythm,
        retiring_ids={period.period_id for period in doomed},
    )


def reset_pay_periods(user_id, new_start_date, num_periods, rhythm):
    """Wipe and rebuild the user's WHOLE schedule, re-anchoring accounts.

    The bounded first-time-setup correction.  Unlike regenerate -- which
    rebuilds only the not-yet-started, unlocked future tail and can never
    touch the anchor period or historical periods -- reset deletes EVERY
    pay period (anchor, historical, current, future) and generates a fresh
    schedule from ``new_start_date``, then re-anchors each account onto it
    with its balance preserved.

    Bounded for safety: it refuses if the user has ANY settled
    transaction.  Once a paycheck has settled, rewriting the schedule
    under it would corrupt history, so those users use regenerate instead.

    The whole operation is ONE transaction the route commits.

    **It used to need an obstacle cleared, and the obstacle is gone.**  An
    account carried a ``NOT NULL`` FK to its anchor pay period, so a reset had
    to delete the old anchor period before it could re-point the anchor,
    leaving the FK dangling mid-transaction; the FK was declared
    ``NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` (Phase 0) purely so this --
    its only caller -- could issue ``SET CONSTRAINTS ... DEFERRED``.  Ruling
    R-EH deleted the columns and ruling R-EO deleted the assertion's own pay
    period, so nothing an account owns points at a period any more: no
    deferral, no re-anchoring, and no window in which the schema is
    inconsistent.

    Steps, all in one transaction:

      1. Refuse if any settled transaction exists (delete nothing).
      2. Take the per-user advisory lock (a structural mutation, like
         extend / truncate / regenerate).
      3. Bulk-DELETE every pay period.  PostgreSQL cascades it in one
         pass: transactions and transfers (+ both shadows, preserving the
         transfer invariant) go; audit triggers still fire.  Anchor history is
         NOT in that cascade any more (ruling R-EO), and neither are the
         recurrence rules (plan step R7b-4).  Every era goes too, by the
         writer's own rule rather than a cascade (plan step C17-a).
      4. Generate the fresh schedule from ``new_start_date``, minting its
         one era there.
      5. Re-sync each of the user's loans' genesis postings onto the
         rebuilt schedule (:func:`loan_posting_service.resync_user_loan_postings`).
         A loan's opening / true-up ledger entries carry a ``pay_period_id``
         and so CASCADE-delete with the wiped periods, but they exist
         independently of any settled transaction, so the zero-settled gate
         does NOT keep them; their SOURCE facts (``LoanParams`` and the
         ``user_trueup`` ``LoanAnchorEvent`` rows) survive, so this re-derives
         and re-posts them attributed to the new periods.  Then the same for
         the non-loan accounts' anchor corrections (Build-Order Step 5,
         :func:`account_posting_service.resync_user_account_anchor_postings`):
         the wipe took their correction ENTRIES, which are keyed on a pay
         period, but not the assertions those entries derive from -- so this
         re-derives every one of the user's real assertions onto the rebuilt
         schedule rather than one fabricated opening per account.

    **The REPOPULATION left this list at plan step R7d-c-1** (ruling
    **R-R38**), and it moved to the CALLER rather than merely to the end: it
    used to run between steps 4 and 5, and it runs after both re-syncs now.

    **Neither re-sync can see what the repopulation writes, and the reason is
    ONE property rather than a list of readers.**  Both walk the POSTED
    LEDGER: every read either of them makes of ``budget.transactions`` or
    ``budget.transfers`` is keyed on a set of ids taken from
    ``budget.journal_entries`` -- the linked ledger's nonzero per-row nets on
    the account side (``account_posting_service._walk._source_net_days``), the
    stale lineage transfers and stale payment shadows on the loan side, and
    the loan walk's own ``settled_income_shadows``.  A freshly generated row is
    ``Projected`` and posts nothing, so it is in none of those sets.  *A first
    draft of this paragraph said the account half "reads no transaction or
    transfer at all" and enumerated two readers on the loan side; an
    adversarial review MEASURED the first false (``ACCOUNT RESYNC TABLES:
    ['budget.transactions']``) and found two more loan-side readers.  The
    conclusion survived both, and the enumeration is what was wrong -- so the
    property is stated instead of the roll-call.*  The pre-split order was
    measured equal on 82 journal entries (2026-08-27); this is why it is equal.

    **The other direction is what R7d-c-2 makes load-bearing.**  The wipe
    CASCADE-deletes the loan's genesis entries, so the OLD order generated
    against an EMPTIED loan ledger and the new one generates against the
    re-posted ledger.  Nothing on today's generation path reads a loan: its
    reads off the schedule are FOUR of ``schedule.calendar`` and TWO of
    ``schedule.write_period_ids``, which is the whole set, so the change is
    invisible now.  From R7d-c-2 the pass folds the loan to bound a
    payment, and then generating before the re-sync would fold a ledger the
    wipe had emptied.  The new order is the one that survives that step.
    The new rhythm is persisted by step 4's writer rather than by a line of
    this function's own (plan step C3-b): ``record_paydays`` applies the one
    rule -- a batch that RECORDED a payday on a rhythm no era covers mints an
    era -- so the three doors that each held a copy of that call now hold
    none.  **Every era goes with the periods** (plan step ``C17-a``): the
    writer retires them all when no payday survives, so the rebuilt schedule
    holds exactly one era, minted from *new_start_date*.

    **A capture-and-re-point step LEFT this list at plan step R7b-4**, and it
    left because its subject stopped existing.  The wipe used to SET NULL
    every rule's ``start_period_id``, which made a rule the cascade nulled
    indistinguishable from one that legitimately had no explicit start -- so
    the ids had to be captured before the delete and re-pointed at the new
    first period afterwards.  A rule's opening bound is a DATE now, which no
    cascade touches, and ``resolve`` measures it against whatever schedule the
    owner has: ``max(new_opening_payday, start_date)``.  A rule whose stated
    start precedes the rebuilt schedule opens with the schedule, exactly as
    the re-point produced; a rule whose stated start falls INSIDE it now keeps
    that date, where the re-point silently moved it to the new first period.

    Args:
        user_id: The owning user's id.
        new_start_date: First payday of the rebuilt schedule.
        num_periods: How many periods to generate.
        rhythm: How often the new schedule is paid and what payroll does
            when one of its paydays lands on a closed day
            (:class:`~app.services.pay_rhythm.Rhythm`); also
            persisted as the schedule's one era, by the writer.  See
            :func:`regenerate_pay_periods` for why it is a pair.

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        PayPeriodResetBlocked: The user has at least one settled
            transaction; nothing is changed.
        ValidationError: ``record_paydays`` rejects the batch (an invalid
            start date or cadence).
    """
    # Reset is gated on zero settled transactions.  Build-Order Step 3 note:
    # this same gate keeps the CASH double-entry postings consistent across a
    # reset -- the wipe below deletes the user's pay periods, and
    # journal_entries.pay_period_id is ON DELETE CASCADE, so a period holding
    # settled (posted) transactions would dispose its cash journal entries + legs
    # at the DB tier (outside the ORM, where the balanced-journal trigger never
    # fires on DELETE).  Because any settled row blocks the reset entirely, no
    # SETTLED-transaction posting is ever wiped.  Whoever relaxes this gate MUST
    # first reverse those transactions' postings
    # (posting_service.reverse_postings_before_delete).
    #
    # The gate does NOT protect a LOAN's genesis postings: a loan's opening /
    # true-up entries exist without any settled transaction (a payment-less
    # configured loan posts its opening at params-create), so the wipe DOES
    # CASCADE-delete them.  That is safe because their source facts (LoanParams,
    # user_trueup LoanAnchorEvent) survive the wipe, and
    # ``resync_user_loan_postings`` below re-posts them onto the rebuilt
    # schedule in this same transaction (review M2 / R7).
    settled = pay_period_gates.settled_transaction_count(user_id)
    if settled > 0:
        raise PayPeriodResetBlocked(settled)

    # Serialize against concurrent structural mutations for this user.
    user_write_lock.lock_user_writes(user_id)

    # Wipe ALL the user's periods (the cascade handles the dependents) and
    # build the new schedule in ONE write, so the writer derives and
    # materialises the end state rather than the period-less moment between
    # them.
    new_periods = pay_period_write.record_paydays(
        user_id, new_start_date, num_periods, rhythm,
        retiring_ids=pay_period_write.owner_period_ids(user_id),
    )
    # Re-post the loan genesis (opening / true-up) corrections the period
    # CASCADE wiped: their source facts survived, so this re-derives them
    # onto the rebuilt schedule inside this transaction (review M2 / R7).
    loan_posting_service.resync_user_loan_postings(user_id)
    # Same for the NON-loan accounts' anchor corrections (Build-Order Step
    # 5): the wipe CASCADEd their opening / true-up ENTRIES with the old
    # periods, but no longer their assertions (ruling R-EO), so this re-derives
    # every real assertion's correction onto the rebuilt schedule.  Post-reset
    # is clean by construction, and since plan step X-f3b the reason is the
    # CASCADE rather than the gate alone: a PURCHASE whose bank posting day is
    # recorded posts its own cash leg even under a Projected envelope (ruling
    # **R-FM**), so the zero-settled gate no longer implies "nothing has
    # posted".  What it does still imply, and what matters here, is that every
    # journal entry the wipe reaches carries a ``pay_period_id`` and goes WHOLE
    # -- both legs of each balanced pair -- along with the transactions and
    # purchases that sourced them.  So each account walks to exactly the
    # balance its latest assertion declares.
    account_posting_service.resync_user_account_anchor_postings(user_id)
    return new_periods
