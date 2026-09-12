"""
Shekel Budget App -- Pay Period Batch

**What a batch of paydays IS, and what refuses one** -- split out of
:mod:`app.services.pay_period_write` at plan step ``pay_calendar:C17-c-1``
(developer ruling **R-PC74**, 2026-09-12, closing ledger row **PC-507**).  A
batch has a size this app will materialise in one call
(:data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`,
:func:`reject_out_of_range_batch_size`), a first payday that must be a civil
day (:func:`reject_undatable_payday`), the days it lands on
(:func:`requested_paydays`) and a floor it may not start under
(:func:`reject_backward_payday`).  None of that touches a row: the writer asks
every one of these before it issues a statement, which is what lets it promise
that a refused batch leaves nothing behind.

**Why a module of its own.**  The writer stood at 998 of pylint's 1,000 lines
after ``C17-a`` and ``C17-b-2``, and ``C17-c-2`` -- the leaf that rewrites its
doors -- could not land in a file at the ceiling.  **PC-507 named the remedy a
SPLIT and not a trim**, on the terms ledger row **PC-498** records (answering a
ceiling by deleting argument is how a module loses the reasoning that keeps it
correct), and named the PLACEMENT the developer's rather than the next
session's to assume, as **R-PC60** placed ``_grid`` and **R-PC69** placed
``_projection``.  He placed it here: the whole batch shape, leaving the writer
its two doors and its statements.  What stays there is what
``TestThereIsOneWriter`` pins to that file -- the one ``PayPeriod(...)``
construction and the one ``DELETE`` in ``app/`` -- and this module imports no
model and issues no statement, so that census reads exactly as it did.

**Three of these names are PUBLIC and were private before the move**, for the
reason :mod:`app.services.pay_period_gates` gives: a function another module
calls is part of this module's surface, and keeping the underscore would have
made every call site read as a privacy breach.  Nothing here computes
differently than it did inside the writer -- a move and a rename, ``$0.00``.

**The dependency runs ONE WAY**: the writer imports this module, and this
module imports nothing from the writer, ``pay_period_admin`` or the routes.
Its imports are the derivation (:mod:`app.services.pay_calendar`, whose
producers the batch's days and its floor are answered by), the rhythm value
and the form error it raises.

The one refusal, and why the second was DELETED
==============================================

Plan ruling **R-PC1** stated ONE rule -- "the last paycheck must hold no row
dated on or after the new payday".  Tracing it against ``shekel-prod-db`` found
it wrong in both directions, so the developer ruled it into two (2026-08-10): a
structural floor and a financial coverage rule.  What survives is the floor.

:func:`reject_backward_payday` -- **structural, and TEMPORARY.**  A new payday
may not land inside a paycheck the owner already has.  Its only job is keeping
plan step **C6**'s mid-schedule insert closed, and **C6 removes it.**

**The coverage rule was DELETED (developer ruling 2026-08-11), and the argument
is recorded because this module could re-derive it.**  It refused any write
that moved a day from COVERED to UNCOVERED underneath a SETTLED row filed in a
surviving period, and it was approved on the claim that stranding such a day
reproduces ``balance:N-128`` -- the two halves of the cash period view
disagreeing.  **That claim was false, and it was the whole of the case for the
rule.**  ``_cash_periods._assemble_figures`` values each column at that
period's OWN ``end_date`` and computes ``period_timing`` as ``moved - net``, so
a settle day past the last reported end is absent from BOTH sides of ruling
R-K's identity and cancels.  The money reports as a timing remainder -- the row
ruling R-DH split out to carry precisely this -- and the balance is right
either way: on that end date the bank had genuinely not taken it.  Pinned by
``test_cash_period_view.py``'s
``test_a_settle_day_past_the_window_keeps_every_column_exact``, and driven on a
production CLONE: retiring 58 of 61 periods strands three real rows totalling
``$177.47`` and every surviving column reconciles to the cent.

**Two things that are NOT evidence for it, stated because the first draft of
this paragraph offered both.**  Production has never been in the refused state
-- **0** settled rows fall outside its schedule's coverage -- so production is
silent on this rule rather than supporting its removal.  What production shows
is the DESIGN it rests on: 21 of 160 settled rows settle outside their OWN
paycheck (measured 2026-08-11), carried by the remainder with nothing refusing.
And the refusal's message offered THREE remedies, not one -- re-date the row,
move it, or choose a schedule that still covers the day.  The first two falsify
when money moved; the third is declining the edit.  **The measured COST is what
decided it**: 5 of the owner's 61 truncation points refused, one over three
rows that cleared the bank ONE day late.

**"Outside the reported window" is not a windowing nicety on the load-bearing
surface**: ``routes/grid/page.py`` passes the owner's COMPLETE period set, so there
the phrase means "outside every paycheck they have".  The identity holds all
the same -- it is a property of where each column is valued, not of how the
window was chosen -- but the reassurance must not be read as "only a partial
view sees this".

Flask-isolated: takes and returns plain data, never imports ``request`` /
``session``.  Raises :class:`~app.exceptions.ValidationError`, the form error
each refusal actually is, so the route that took the input renders it.
"""

from datetime import date, datetime

from app.exceptions import ValidationError
from app.services import pay_calendar, pay_rhythm

#: Inclusive bounds on how many pay periods ONE call may create.
#:
#: A generation policy rather than a column constraint, which is why it lives
#: beside the writer rather than on a model: 260 is ten years of fortnightly
#: paydays (five of weekly), past any horizon the app renders and well inside
#: what one transaction can generate and populate.  The floor is 1 because a
#: batch that creates nothing is a caller mistake, not a no-op -- at
#: registration it used to surface several statements later as
#: ``create_account`` complaining that the owner had no pay periods.
#:
#: Read by :func:`reject_out_of_range_batch_size` and by the Marshmallow fields
#: in ``app.schemas.validation.pay_periods``, which import them from here: the
#: bound belongs with the batch it bounds, and a schema that stated its own
#: copy is how four form fields came to hold four literals.
PERIOD_BATCH_MIN = 1
PERIOD_BATCH_MAX = 260


def reject_out_of_range_batch_size(num_periods: int) -> None:
    """Refuse a batch size outside what one call may create.

    **The batch's OWN precondition, stated once and asked by the writer before
    anything is written** (plan step X-ad-a).  It was held by each caller's
    Marshmallow field until registration became a fifth caller, and a bound
    held by remembering is a bound the next door does without: ``num_periods``
    was refused by no service at all, so a non-form caller could ask for zero
    periods (which then failed several statements later, in ``create_account``,
    under a message about accounts) or for a hundred thousand (383 years of
    fortnights in one transaction).

    **It used to carry a cadence FLOOR beside this, and plan step C4-c deleted
    it with its subject.**  That floor refused a cadence of 1 because a STORED
    ``end_date`` cannot express a one-day period -- ``ck_pay_periods_date_order``
    required ``start_date < end_date`` and the writer stored
    ``start_date + (cadence_days - 1)`` -- so the INSERT died as an unhandled
    ``CheckViolation`` 500 on both the settings form and the registration form.
    Nothing is stored now, two paydays a day apart simply define a one-day
    period, and pay-calendar findings **P9** and **P33** close with the column.

    The CADENCE bound that remains is ``budget.pay_schedule``'s CHECK, asked by
    that column's own writer,
    :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`.
    Two bounds, two owners, because they answer different questions -- what may
    be STORED as a schedule, and how much of one the writer will materialise
    in a single call.

    Args:
        num_periods: How many periods the batch would create.

    Raises:
        ValidationError: *num_periods* is outside
            :data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`.  The message
            names the offending value and both bounds.
    """
    if not PERIOD_BATCH_MIN <= num_periods <= PERIOD_BATCH_MAX:
        raise ValidationError(
            f"Number of pay periods must be between {PERIOD_BATCH_MIN} and "
            f"{PERIOD_BATCH_MAX}; got {num_periods}."
        )


def reject_undatable_payday(payday: date) -> None:
    """Refuse a payday that is not a plain ``datetime.date``.

    ``datetime`` is a subclass of ``date``, so the bare ``isinstance`` check
    this replaced accepted one -- and every derived end would then carry a time
    component, comparing unequal to the ``DATE`` column it is stored in and
    placing a day's money by an accident of the process clock.
    ``pay_calendar._validated`` refuses the same value, but it raises
    ``PayCalendarError``, which no route catches; refusing here makes it the
    form error it actually is.

    Args:
        payday: The candidate first payday.

    Raises:
        ValidationError: *payday* is not a ``date``, or is a ``datetime``.
    """
    if not isinstance(payday, date) or isinstance(payday, datetime):
        raise ValidationError(
            f"first_payday must be a date object, got "
            f"{type(payday).__name__}.  budget.pay_periods.start_date is a "
            f"DATE column and the app's civil day is display_today(); a "
            f"datetime here would place a day's money by the process timezone."
        )


def requested_paydays(
    first_payday: date, num_periods: int, rhythm: pay_rhythm.Rhythm,
) -> "list[date]":
    """Return the paydays a batch asks for, whether or not they already exist.

    **It asks the PRODUCER rather than restating its arithmetic** (plan step
    C14-d).  ``first_payday + timedelta(days=cadence_days * step)`` was the
    fourth spelling of the payday rhythm, in the module whose OTHER spelling
    that step deleted, and an adversarial review of ``C14-d`` found it missing
    from the census that step corrected.

    **It RECORDS THE DISPLACED DAY since plan step ``C14-e-3``, which is
    ledger row PC-497 fault 1** -- and this is where the ``$0.00`` stops.  It
    ran the batch on the GRID and recorded what it spaced, so under a
    displacing convention it wrote NOMINAL paydays where the projection showed
    CASH ones.  The two disagreed at the door next to it: ``derive_periods``
    closes the last saved paycheck on the DISPLACED day, so
    :func:`reject_backward_payday`'s floor is a cash day, and an extend
    offering the nominal one was refused -- on a READ path, since
    ``top_up_rolling_window`` reaches this door from ``/grid`` and
    ``/dashboard`` and nothing registers a handler for ``ValidationError``.
    The batch still runs on the GRID: every element is
    ``projected_payday(first_payday, rhythm, step)``, which is the grid day
    ``step`` cadences on DISPLACED, never the previous element displaced and
    stepped from.  That is what keeps the progression from compounding, and it
    is why this takes the whole :class:`~app.services.pay_rhythm.Rhythm` rather
    than the cadence alone.

    **``first_payday`` is READ as a point on the nominal grid, and at the four
    form doors that is a reading rather than a guarantee.**  The extend door
    hands a grid day by construction (``pay_calendar.nominal_payday_after``),
    and :class:`~app.services.pay_period_write._PaydayChange` stores whatever
    arrives as the phase, so the day this spaces from and the day the next
    extend continues from are one value.
    What no door establishes is that the day the OWNER typed is on payroll's
    grid.  *An adversarial review of ``C14-e-3`` struck a sentence resting that
    on the typed day being a business day and so its own displacement: being a
    fixed point of the displacement does not make a day a grid point.*  Worked:
    an owner really paid 2025-12-31, because payroll moved the 2026-01-01
    nominal day back, types 2025-12-31 -- which is what the sign-up form asks
    for -- and the batch records 2025-12-31, 2026-01-14, 2026-01-28 against a
    truth of 2026-01-15 and 2026-01-29.  Every element after the first is a day
    early, permanently.  That is ledger row **pay_calendar:PC-504**, owned by
    ``C17``: an ERA carries the anchor the owner would have to state, and no
    form asks for it today.

    Args:
        first_payday: The batch's first NOMINAL payday.
        num_periods: How many paydays the batch covers.
        rhythm: The owner's cadence and payday convention
            (:class:`~app.services.pay_rhythm.Rhythm`).  The pair rather than
            the cadence, because the days recorded are displaced under the
            convention.

    Returns:
        *num_periods* days, ascending: the grid days ``rhythm.cadence_days``
        apart from *first_payday*, each displaced onto a business day.  Under
        :attr:`~app.enums.BusinessDayShiftEnum.NONE` that is the grid itself.
    """
    return [
        pay_calendar.projected_payday(first_payday, rhythm, step)
        for step in range(num_periods)
    ]


def reject_backward_payday(
    surviving_paydays: "set[date]",
    new_paydays: "list[date]",
    stored_eras: "tuple[pay_rhythm.Era, ...] | None",
) -> None:
    """Refuse a batch whose earliest new payday would land inside a paycheck.

    **The forward-only rule, keyed on the PAYDAY** (ruling **R-PC1** as split
    2026-08-10).  It replaces ``pay_period_service._reject_overlapping_batch``,
    which bounded a batch on ``max(end_date)`` -- a derived column plan step
    C4-c dropped, and one that made the guard do a second job nothing credited
    it with.

    That second job is this function's ONLY job: **keeping plan step C6 closed.**
    Under the derivation a gap and an overlap are not expressible -- consecutive
    paydays define adjacent intervals -- so there is nothing left here to refuse
    except a payday landing INSIDE an existing paycheck, which splits it.  C6
    owns that, behind two questions ledger row **P10** records as unruled: what
    happens to a row ``DerivedPeriod.attribution_day`` would now clamp into the
    wrong half,
    and whether the split-off payday is repopulated (a monthly billed twice) or
    left empty (income understated for the whole horizon).  **When C6 answers
    them, this function is what it deletes.**

    **The floor is WHERE THE LAST PAYCHECK ENDS, and since plan step C14-d it
    asks the derivation rather than restating it.**  It is
    ``payday_after(stored_eras, latest_payday)`` -- the same call
    :func:`~app.services.pay_calendar.derive_periods` makes to close the last
    saved period, whose ``end_date`` is that day minus one.  So the first day
    NOT inside a paycheck the owner already has is the floor by construction,
    and the two cannot come apart.  Since plan step ``C17-b-2`` that day is
    the next payday of the ERA grid after the one the latest record stands
    for, anchored on the era's phase rather than stepped from the record.

    *That was a maintained agreement until C14-d, and the docstring said so:
    "on any schedule this app can write those two spellings select the same
    set, because the last period's end IS ``payday + cadence - 1``".  A rule
    that two places must always agree is rule 14's tell -- one value with two
    homes -- and the remedy is to delete a home rather than keep them in step.
    The home deleted here is this function's own ``latest_payday +
    cadence_days``, one of the five spellings*
    :func:`~app.services.pay_calendar.projected_payday` *censuses -- and this
    module held TWO of them:* :func:`requested_paydays` *routed to the grid
    producer in the same step, and to the PROJECTION at ``C14-e-3``, so two of
    the five went and the census now names one.*

    **``C14-d`` moved ``$0.00`` and this fence now MOVES MONEY, which is
    ``C14-e-3``.**  That step's own ``$0.00`` was structural rather than a fact
    about stored data -- an adversarial review corrected a first draft resting
    it on every row holding ``none``, which any owner can falsify in one POST
    through the four doors ``C14-b`` shipped -- because
    :func:`~app.services.pay_calendar.projected_payday` took no convention and
    nothing in the pay-calendar package read one.  It reads one now, so the
    floor below is a DISPLACED day for any owner who has answered the question.

    **What the change buys is measured, not asserted** (probe over production's
    own schedule, 1,951 paydays from 2026-03-26 at cadence 14 out to
    ``CALENDAR_DATE_MAX``, 2026-09-05).  Both spellings refuse the same **0**
    paydays with the convention at ``none``.  Under ``prior`` the open-coded
    floor refuses **58** of the owner's own future paydays -- the R-PC47 case
    exactly: a payday nominally
    2026-01-01 is really paid 2025-12-31, and ``latest + cadence`` puts the
    floor on the nominal day and refuses the real one -- and the producer call
    refuses **0**.

    **The floor reads the STORED eras, an obligation written down by an
    adversarial review of ``C14-d``, MOVED by ``C14-e-1`` without being
    gradable, and GRADED at ``C14-e-3``.**  The floor reads the stored eras
    and not the batch's own
    :attr:`Rhythm.shift <app.services.pay_rhythm.Rhythm.shift>`.  The
    argument's rhythm is what the operation LEAVES BEHIND, and a batch that
    changes the convention would otherwise compute its floor under the new one
    while :func:`~app.services.pay_calendar.derive_periods` still closes the
    existing calendar under the old -- the disagreement between fence and
    boundary ``C14-d`` exists to end, reintroduced through the argument list.

    **Nothing could grade that until ``C14-e-3``, which is why it stayed an
    obligation for two steps.**  While
    :func:`~app.services.pay_calendar.projected_payday` returned the nominal
    grid day the stored convention and the batch's own selected the SAME floor,
    so no test could tell this function from one reading the wrong half -- and
    an obligation marked discharged with nothing grading it is worse than one
    left open, because the next reader stops looking.  With the displacement
    live the two part company, and
    ``TestTheFloorReadsTheSTOREDConventionAndNotTheBatchS`` lands both
    directions: stored ``prior`` with an incoming ``next`` must ACCEPT a payday
    on 2030-11-27 (reading the batch's half refuses a day the owner was really
    paid), and stored ``next`` with an incoming ``prior`` must REFUSE it
    (reading the batch's half splits a paycheck they already hold).

    **Under ``next`` it still refused those 58 until ``C17-b-2``**, ledger
    row **N-495**: the ANCHOR was the last RECORDED payday, so a payday
    payroll moved forward carried its whole projection forward with it, and
    the floor inherited exactly the error the derived end had -- the point of
    asking the producer being that the fence cannot be wrong in a way the
    calendar is not.  The anchor is the era's phase now, at both.

    **Why it is not two days, and an adversarial review of C3-b is why.**  That
    step's first cut bounded at ``latest_payday +
    MIN_MATERIALISABLE_CADENCE_DAYS``, on the reasoning that the only insert
    worth refusing is one before an existing payday.  That is wrong by the
    length of a paycheck, and P10's BOTH damage arms are then reachable through
    a door P10 says is closed.  Measured on the two-period fortnightly
    schedule: recording 2026-01-23 shrank the 2026-01-16 paycheck from 01-29 to
    01-22 and moved a row due 01-25 from rendering on 01-25 to rendering on
    01-22, while ``/pay-periods/generate`` left the split-off half EMPTY and
    ``regenerate`` repopulated it beside the row the shrunk half kept -- one
    monthly billed twice in what had been one paycheck.

    Args:
        surviving_paydays: The paydays the owner keeps once this operation's
            retirements are applied, empty for a first-time schedule.
        new_paydays: The paydays this batch would create -- already filtered of
            any that exist, so a re-run naming existing days is bounded on what
            it would actually add -- with the minted era's first payday in
            front when the batch mints one, whether or not the record holds
            that day: the seam it opens is bounded like the paydays it adds.
        stored_eras: The owner's STORED eras
            (:class:`~app.services.pay_rhythm.Era`), which set how far the
            last paycheck reaches.  **Stored rather than the batch's own**,
            which is the whole of the paragraph above: the question is how far
            the existing calendar already reaches.  ``None`` only beside an
            empty payday set -- an owner with no era (every batch that records
            a payday mints one when none covers it) -- where there is no floor
            to apply.  The early return below is what makes that safe, and it
            has to be, because the producer takes the eras.

    Raises:
        ValidationError: The earliest new payday falls before the floor.
    """
    if not surviving_paydays or not new_paydays:
        return
    latest_payday = max(surviving_paydays)
    floor = pay_calendar.payday_after(stored_eras, latest_payday)
    earliest_new = min(new_paydays)
    if earliest_new < floor:
        era = stored_eras[pay_calendar.era_index_at(stored_eras, floor)]
        raise ValidationError(
            f"A new payday, or the first payday of a new pay rhythm, must "
            f"fall on or after {floor.isoformat()} -- the "
            f"day the next paycheck opens after your latest recorded payday "
            f"({latest_payday.isoformat()}, at a "
            f"{era.rhythm.cadence_days}-day cycle); "
            f"got {earliest_new.isoformat()}.  An earlier date lands inside a "
            f"paycheck you already have and would split it in half, which this "
            f"app cannot yet do safely.  Choose a later date, or rebuild the "
            f"tail from the payday you want."
        )
