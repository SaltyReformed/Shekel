"""The derivation: an owner's paydays in, their pay periods out (plan step C1).

``budget.pay_periods`` stores three values per row and only ``start_date`` is a
fact -- the day money arrived.  ``end_date`` is ``lead(start_date) - 1`` and
``period_index`` is ``row_number() - 1``, both stored beside the fact they
derive from with nothing reconciling them, which is why five separate runtime
fences exist to police one functional dependency
(``docs/plans/implementation_plan_pay_calendar.md`` section 1).  This module is
that dependency, written once::

    period_index = row_number() over (order by start_date) - 1
    end_date     = coalesce(lead(start_date) - 1,          -- the definition
                            start_date + cadence_days - 1)  -- the open last one

**Its one caller is** :meth:`~._calendar.PayCalendar.__post_init__`, since plan
step C2-a; at C1 it had none at all, deliberately, because the value had to be
proven equal to what was stored -- over a clone of production and over irregular
schedules the live data cannot supply -- before anything read it, wrote it, or
dropped the columns.  **Plan step C4-c dropped them, so that comparison has no
second side and the harness that took it is gone**; the proof it was is in
migration ``b7a41e2c9d63``'s docstring, measured on production itself.  What
holds this function to its values now is
``tests/oracles/pay_calendar_derivation.py``'s hand-computed sweep, driven by
``tests/test_services/test_pay_calendar_derivation.py``.

**Pure, and that is load-bearing.**  No session, no Flask, no clock: the
derivation is a function of two values, so the sweep drives it over a
catalogue of schedule shapes without a database at all.  That is what let the
same code be driven over a production clone while a stored column still
existed to diff it against.
**The only application imports are ``app.exceptions`` and ``app.utils.dates``,
neither of which imports anything from this application**, so the module still
loads with no app stack behind it.  *An earlier draft of this sentence said
``app.utils.dates`` was the ONE non-standard-library import and did not count
``app.exceptions``, which was already there -- a purity claim that miscounted
its own imports* (adversarial review, 2026-08-14).

**That import WEAKENED the no-clock property, and the honest form says which
property survives.**  Before plan step C2-f this module could not reach a clock
through its import graph at all; ``app.utils.dates`` carries
:func:`~app.utils.dates.display_today`, so the guarantee is now "nothing here
CALLS a clock" rather than "no clock is reachable".  It was taken so the period
LABEL is one rule rather than one per type that answers "which paycheck" (see
:attr:`DerivedPeriod.label`), and ``app.utils.dates`` is the only leaf this
package may take on those terms.  *There is one such type now* -- plan step
``pay_calendar:C4-a-5`` deleted the ORM row's accessor -- *and the import
stays for the reason* :func:`~app.utils.dates.pay_period_label`'s *own
docstring gives: the narrow register and the wide one are one decision (ledger
row P47) and live together, and the wide one has a caller outside this
package.*

**Why the last end is a different KIND of value, and says so.**  Every other
end is dictated by a fact -- the next payday.  The last one has no next payday,
so it is projected forward from ``budget.pay_schedule.cadence_days`` (ruling
2026-08-08: "a projection stated as one").  :attr:`DerivedPeriod.end_is_projected`
is that statement, and it cannot be recomputed by a consumer holding one period
out of its calendar.  It is not cosmetic: plan finding P12 -- a
``/pay-periods/generate`` post naming an already-existing payday creates zero
rows and still reaches ``upsert_schedule`` (``routes/pay_periods.py``), so the
stored cadence is rewritten by a batch that wrote nothing -- moves this end and
only this end, and today the stored column hides that.  Once the column is
gone, the flag is the only thing on the value that distinguishes a horizon
derived from a fact from one derived from a setting a no-op post can change.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.exceptions import ShekelError
from app.utils.dates import pay_period_label, pay_period_range_label

#: The cadence bounds, mirroring ``ck_pay_schedule_cadence_range`` on
#: ``budget.pay_schedule.cadence_days``.  Named here rather than inlined
#: because :func:`validate_cadence` states them in its refusal message, and a
#: message that quotes a bound the code does not enforce is how the first cut
#: of this module shipped.
#:
#: **A second copy of the pair lives on the model** as
#: :data:`app.models.pay_schedule.CADENCE_DAYS_MIN` /
#: :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`, where plan step X-ad-a
#: collapsed six literals into one name.  This module does not import it, and
#: the reason is this package's purity: importing a model pulls
#: ``app.extensions`` in and closes an import cycle through
#: ``pay_schedule_service``, which would end the "drive the derivation with no
#: database" property C1's harness rests on.  The two are held equal by
#: ``tests/test_models/test_pay_schedule.py::TestTheCadenceBoundHasOneValue``
#: rather than by whoever edits one remembering the other.
MIN_CADENCE_DAYS: int = 1
MAX_CADENCE_DAYS: int = 365


class PayCalendarError(ShekelError, ValueError):
    """No pay calendar can be derived: there is no schedule, or it is unusable.

    **TWO states reach it and ``app/error_handlers.py`` answers both with one
    page**, deliberately (plan step C4-b-2, the handler ledger row **P35**
    deferred).  One is ORDINARY and repairable: the owner holds no
    ``budget.pay_schedule`` row, so there is no rhythm to derive anything from
    -- :func:`~._loader.calendar_for` and :func:`~._loader.cadence_for` refuse
    them from one place since plan step C4-d (ruling **R-PC45**), where the
    cadence door used to refuse and the calendar door used to answer an empty
    calendar carrying no cadence.  The other is a BROKEN INVARIANT no write
    door produces: a payday set or a cadence that cannot define a calendar.  The
    owner can act on the first and can do nothing about the second, and both
    leave every per-period figure unanswerable, so the page offers the one
    repair that exists and the LOG carries which state it was.

    **Neither state is user INPUT**, which is why no form field is named in
    the message: one is a setup state the owner repairs elsewhere and the other
    is a broken invariant.  *An earlier form of this paragraph also said "no
    route catches it", which was false when written --
    ``routes/accounts/statements`` catches it beside ``BaselineMissingError``
    and degrades -- and would have read as licence to leave the application
    without an answer.  ``app/error_handlers.py`` registers one (plan step
    C4-b-2, ruling R-PC42); a route catching it to DEGRADE a fragment is
    ordinary beside that.*  ``budget.pay_periods`` already enforces the payday
    model's key (``uq_pay_periods_user_start``), so a duplicate payday cannot
    come out of the table; reaching the second state from the application would
    mean a caller assembled a payday set by hand and got it wrong.  Failing
    loud is the only safe disposition for it -- every alternative
    (de-duplicating, clamping a bad cadence) silently produces a calendar whose
    periods do not tile the days the owner's money lives on.

    **A caller's own ``Raises:`` names the cause that matters at its site**
    rather than re-enumerating both; this class is where the pair is stated.

    Also a ``ValueError`` because it is raised for rejected function arguments,
    where that is Python's own contract; a caller catching either name gets it.

    It is NOT the successor of ``recurrence._calendar.RecurrenceScheduleError``,
    and saying so is a correction the review of C1 made.  That class refused an
    overlapping or reversed SCHEDULE at the value boundary, and the plan retired
    it rather than relocating it: plan step **C2-b2** DELETED the class that
    held its only two raise sites, because the states they policed stopped being
    expressible once the periods are DERIVED.  (An earlier draft of this
    paragraph credited that deletion to C5a, which had it on its list until the
    C2-b decomposition measured that the class dies three leaves earlier.)
    What this class refuses is different -- a payday SET or a cadence that
    cannot define a calendar in the first place.

    **One of those refusals WAS reachable from a page, and plan step C4-b-2
    closed that route rather than handling it** (ledger row **P35**).
    :func:`~._loader.calendar_for` resolves the cadence through
    ``pay_schedule_service.resolve_schedule``, which until that step fell back
    to inferring it from the last period's stored length -- bounded below by
    ``ck_pay_periods_date_order`` and NOT bounded above -- so a hand-written
    period spanning more than a year refused the calendar, and since C2-c that
    meant a 500 on every balance page.  ``fk_pay_periods_schedule`` makes the
    schedule-row-less owner unstorable, so the only source of a cadence is the
    column, bounded to 1..365 by ``ck_pay_schedule_cadence_range`` -- the same
    range this class enforces, which is why the two cannot now disagree.  What
    the refusal still covers is a CALLER, not a page.  Failing loud remains
    right (the alternative is projecting a horizon off a value no write
    door could have produced).  *The paragraph this replaces said C4 would
    remove the fallback "with the column it reads"; the key removed it one leaf
    earlier, and ``end_date`` is still C4-c's to drop.*
    """


@dataclass(frozen=True)
class DerivedPeriod:
    """One pay period, derived rather than stored.

    The shape of the two columns plan step C4-c dropped, plus the one thing the
    columns could not say.  Ordered ``start_date`` ascending inside a
    :func:`derive_periods` result, with ``period_index`` matching that order by
    construction -- the disagreement between index order and date order that
    ``uq_pay_periods_user_index`` and three runtime fences existed to catch is
    not expressible here.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the payday was read from, or
            ``None`` when the period is not MATERIALISED -- a projection past
            the owner's horizon, or a candidate the writer has not saved yet.
            The one field here that is carried rather than derived, and it is
            carried because identity is what separates the two questions plan
            step C2 has to keep apart: "which paycheck does this row live in"
            needs a row a foreign key can point at, while "which span does this
            day fall in" is answerable by a projection.  A value that could not
            say which it was would answer the write question wrongly and
            silently.
        period_index: The owner's 0-based ordinal for this period, which is its
            position in payday order.
        start_date: The payday that opens the period.  The only fact in the
            row; everything else here is derived from it and its neighbours.
        end_date: The last day the period covers -- the day before the next
            payday, or, for the last period, ``start_date + cadence_days - 1``.
        end_is_projected: Whether :attr:`end_date` came from the cadence rather
            than from the next payday.  ``True`` for the LAST period of a
            non-empty calendar and ``False`` for every other -- so exactly one
            per calendar, and none at all for the empty one.  A consumer
            holding a single period cannot work this out, which is why it rides
            on the value: a projected end moves when the stored cadence moves,
            and a fact-derived end does not.
    """

    period_id: "int | None"
    period_index: int
    start_date: date
    end_date: date
    end_is_projected: bool

    def covers(self, day: date) -> bool:
        """Return whether *day* falls inside this period's span.

        **The single CONTAINMENT rule for one period**, ruled at **R-PC31**
        and landing at plan step C4-a-3, which retired the sites that
        open-coded ``start_date <= day <= end_date``.  Both bounds are
        INCLUSIVE -- a period covers its payday and it covers the day before
        the next one -- and writing that twice is how a chained comparison
        comes to be spelled once with ``<`` on one end.

        **WHO asks it is a PREDICATE and not a list**, which is this package's
        own hard-won convention rather than a style choice: ``grep -rn
        "\\.covers(" app/`` with the definition struck out.  Three at C4-a-3 --
        the purchase-date warning
        (``entry_service._sums.entry_list_view``), the recurrence engine's
        base-month scan (``recurrence_engine._plan.compute_due_date``) and
        this package's own :func:`~._searches.containing_index` -- and a
        FOURTH is already ranked: ``balance:X-x1`` names this method in its
        own sentence, so a closed list here would go stale the day that step
        ships.  :meth:`~._calendar.PayCalendar.period_by_id`'s docstring is an
        essay on that exact failure, having claimed zero, five, seven and
        eleven callers in turn; writing the grep instead of the names is what
        that essay concludes.  *An adversarial review of this step found the
        closed list, 2026-08-31.*

        **A different ``covers`` exists and is NOT this one**:
        ``cash_ledger._amounts.ReconciledThrough.covers`` asks whether a
        statement's coverage reaches an event day -- one-sided, and TOTAL over
        ``None`` by design.  This one is two-sided and raises on ``None``,
        because a period always has both bounds.

        **It is the predicate the SEARCHES already run**, said once rather
        than a second implementation beside them.
        :func:`~._searches.containing_index` bisects to the last period
        opening on or before *day* and then asks this; that lower bound is
        already established there, so the first comparison below is redundant
        AT THAT ONE CALL SITE and is paid anyway, because a containment rule
        with a branch missing from one caller is the shape ledger row **P6**
        counted six of.

        **What it does NOT ask is whether the end is a FACT or a
        PROJECTION.**  The last period of a calendar carries
        :attr:`end_is_projected`, and its span moves when the owner's stored
        cadence moves; a caller that must distinguish "covered by a paycheck
        that has been banked" from "covered by one the cadence implies" reads
        that flag, exactly as it would to interpret :attr:`end_date` itself.
        No caller today does: a purchase is in or out of its own paycheck's
        span whichever way that span was derived.

        Args:
            day: The calendar day to place.

        Returns:
            ``True`` when ``start_date <= day <= end_date``.
        """
        return self.start_date <= day <= self.end_date

    def attribution_day(self, preferred: "date | None") -> date:
        """Return the day an item filed in THIS period is budgeted to.

        The single BUDGET-attribution rule, shared by the calendar's day-cell
        grouping (``calendar_service._get_display_day``), the balance-at seam's
        PLANNED tier (``balance_at._cash_fold._cash_plan``) and the reconcile
        panel's offer bound (``reconcile_service._rows.attributed_on``), so no
        two of them can come to disagree about which day an item is BUDGETED
        to.  An item lands on *preferred* -- its ``due_date`` -- falling back
        to this period's :attr:`start_date` when it has none; the result is
        then clamped into ``[start_date, end_date]``.

        **It is a METHOD ON THE PERIOD, and that is plan step C4-a-2's whole
        subject.**  It was ``utils.dates.attribution_date(preferred,
        period_start, period_end)``, three positional arguments a caller had to
        pair correctly, and pairing them wrongly is not a crash -- it is a row
        rendered on the wrong day.  Two of its three callers already held a
        :class:`DerivedPeriod` and split it back into two dates to make the
        call; the third read ``txn.pay_period`` and clamped a projected row
        against the STORED ``end_date`` while everything around it sampled the
        DERIVED one.  Asking the period itself makes the pairing structural:
        there is no second span to supply.

        **Clamping is load-bearing for the daily balance**: every one of a
        period's contributing items must fall on or before :attr:`end_date`, so
        the running balance summed through that day equals the period-end
        balance the grid shows (the calendar/grid reconciliation invariant).  A
        ``due_date`` outside the item's own period is possible -- the
        recurrence engine can date an item just outside its period's range,
        which is why the calendar query carries a due-date-in-range OR
        no-due-date path -- so such a stray date is pulled to the nearest
        boundary rather than escaping onto a neighbouring period's day and
        breaking that period's sum.

        **What this does NOT guarantee, and both halves of the paragraph it
        replaces were false** (ledger row **N-97**).  The deleted function's
        docstring named the seam's caller as
        ``balance_resolver.daily_cash_balance_series``, a producer plan step
        X-c2b3 had DELETED a month earlier, so the citation resolved to
        nothing.  And it guaranteed that a flow's calendar cell and the balance
        line's step for it land on the SAME day: that stopped holding at plan
        step X-c2b2, when the balance line became the cash fold, which steps a
        SETTLED row on the day its money moved and a projected one on
        ``max(attribution, as_of + 1)`` (rulings **R-DH (b)** and **R-G**).
        Neither is this day, so a chip and its own step can sit days apart:
        ``|settled_on - due_date|`` over the real Checking account's settled
        rows is **median 2, p75 7, max 25 across 126 rows, re-measured
        2026-08-28**.  It moved here CARRYING NO DATE, quoted as "median 2, p75
        6, max 25" over 130 rows, and a measurement quoted as a REASON decays
        invisibly because nobody re-checks a premise -- so the leaf that
        relocated it re-took it rather than copying it forward.  That
        divergence is finding **N-58**, it is an open fork rather than a
        settled rule, and ``calendar_service._get_display_day`` states it at
        the site.  What survives is the budget attribution itself, which is
        what every reader asks this for.

        Args:
            preferred: The item's preferred landing date (its ``due_date``), or
                ``None`` to fall back to :attr:`start_date`.

        Returns:
            The attributed calendar day, guaranteed within
            ``[start_date, end_date]``.
        """
        if preferred is None or preferred < self.start_date:
            return self.start_date
        if preferred > self.end_date:
            return self.end_date
        return preferred

    @property
    def label(self) -> str:
        """Return this period's human label (``"02/21 - 03/06"``).

        **The ONLY accessor that answers "which paycheck" in words**, since
        plan step ``pay_calendar:C4-a-5``.  It was one of two: the ORM row
        carried ``PayPeriod.label``, reaching the same
        :func:`app.utils.dates.pay_period_label` -- so the FORMAT agreed while
        the DATES did not, because the row fed it the STORED ``end_date`` and
        this value the derived one.  Under the P12 / P28 shape that stored end
        goes stale, and the two rendered one paycheck two ways: C4-a-2 saw it
        across screens, and C4-a-5 measured it inside ONE card, where the
        grid's full-edit popover named the row's period off the row while the
        period ``<select>`` two sections below it named the same period off
        this property.  That step moved both readers here and deleted the
        accessor, so the stored column is no longer reachable for a label at
        all.  The COLUMN itself goes at ``C4-c``.

        Returns:
            The label, carrying the year on both halves only when this period
            straddles one.
        """
        return pay_period_label(self.start_date, self.end_date)

    @property
    def range_label(self) -> str:
        """Return this period's WIDE label (``"Feb 21 - Mar 06, 2026"``).

        :attr:`label`'s sibling register, for a surface with room for month
        names -- today the Income Statement's window ``<select>``, whose
        ``<option>`` sits on screen beside the report heading the same rule
        produces.  That heading reaches
        :func:`~app.utils.dates.pay_period_range_label` through
        ``spending_analysis.window_label``
        (``ledger_report_service._income_statement:111``), which is one call
        away rather than the direct one this sentence claimed until plan step
        ``pay_calendar:C4-a-5`` re-checked it; either way the ``<option>`` and
        the heading beside it come from one rule, which is the property that
        matters.

        It is a PROPERTY rather than a Jinja global for :attr:`label`'s reason:
        the format belongs to ``app.utils.dates``, and a template that called a
        two-argument formatter would be the place a fourth spelling of this
        register next appeared (ledger row **P47**).  Plan step C2-f3a.

        Returns:
            The label.  The four-digit year rides on the END date alone, and
            on BOTH halves where the period straddles one -- ledger row
            **P67**, ruled 2026-08-25.  *This line said "the END date's" flat
            until ``C4-a-5``, which rewrote the paragraph above it and left the
            residue; :func:`~app.utils.dates.pay_period_range_label` has stated
            the ruled rule since P67 landed.*
        """
        return pay_period_range_label(self.start_date, self.end_date)


def derive_periods(
    paydays: "Iterable[tuple[int | None, date]]", cadence_days: int,
) -> tuple[DerivedPeriod, ...]:
    """Derive an owner's whole pay calendar from their paydays and cadence.

    **Takes the owner's COMPLETE payday set, never a window.**  A period's end
    is its successor's payday, so the LAST payday in whatever list arrives here
    falls to the cadence projection -- which means a partial list makes one
    period report a different end depending on which window asked (plan finding
    P14).  **The sibling shape was measured at ``$150,000.00``**: folding a
    ``$100,000.00`` loan true-up against the owner's whole calendar, versus
    against a window that excluded the true-up's own period, moved the balance
    by that much on the days between -- because with the containing period
    absent the old locator missed and its fallback fired.  That measurement
    lived on ``loan_ledger/_visible.owner_pay_periods``' docstring until plan
    step C2-d deleted the function, and it is restated here rather than lost,
    because it is the evidence for this paragraph.  This function cannot detect
    partiality -- a slice of paydays is indistinguishable from a short schedule
    -- so the guarantee has to be structural at the caller.  Plan step C2 makes
    it so: the calendar is built once from the complete set and a window becomes
    a VIEW over it that keeps the real ends.

    **A derived end is not stable against a later write, and that is the one
    way it differs from the column it replaces** (found by adversarial review
    of this step, 2026-08-08).  A stored ``end_date`` cannot move when a
    neighbouring row is written; a derived one can.  Concretely, with paydays
    ``[01-02, 01-16]`` at cadence 14 the second period ends 01-29 (projected);
    append the payday 01-28 -- LATER than every existing one, so a forward-only
    write by the plan's own definition -- and that end moves back to 01-27.
    Only ends inside the last period's PROJECTED span can move this way; an end
    dictated by a following payday is fixed for as long as that payday is.
    A row already dated into the vacated days is not left outside its period --
    :meth:`DerivedPeriod.attribution_day` clamps it -- so the damage is a row
    silently RENDERED on a different day, which is plan finding P10's shape
    reached through a door P10 does not cover.  ``_reject_overlapping_batch``
    blocked that write by comparing against the stored end; plan step **C3-b**
    replaced it with ``pay_period_write._reject_backward_payday``, and **that
    floor is what closes this example** -- corrected 2026-08-11, because this
    paragraph named C3-b's coverage rule until the floor's own correction made
    the citation wrong and the rule was then deleted.  The floor is one FULL
    CADENCE past the latest payday, so on ``[01-02, 01-16]`` at cadence 14 the
    earliest acceptable new payday is 01-30 and the 01-28 above is refused
    outright.

    **What the floor buys is exactly that and no more, and a second adversarial
    review caught a first draft of this paragraph claiming more**: no write can
    place a payday INSIDE an existing paycheck's derived span.  A stored end can
    still be pulled BACK, two ways, and both are exercised by C3-b's own tests
    -- ``retire_paydays`` drops the newly-last survivor from its
    successor-dictated end to the cadence projection, and ``record_paydays``
    shortens the previously-last period whenever the stored cadence is shorter
    than the one the schedule was generated at (row **P28**'s legacy shape).
    The derivation states the property and does not police it: it is a function
    of a payday set, and which sets a user may write is the writer's question.

    Order and duplication are handled HERE rather than trusted, because the
    result's whole value is that index order and date order cannot disagree:
    the input is sorted (so a caller's query order cannot change the answer) and
    a repeated payday is refused (it would place two periods on one opening day
    and give the earlier of them an ``end_date`` before its own
    ``start_date``).

    Args:
        paydays: The owner's complete set of paydays as
            ``(period_id, payday)`` pairs, in any order.  ``period_id`` is the
            ``budget.pay_periods.id`` the payday was read from, or ``None`` for
            a period that is not materialised; it takes no part in the
            derivation and rides through onto
            :attr:`DerivedPeriod.period_id`.  Empty is a legal input and yields
            an empty calendar -- a user who has never generated a schedule, and
            the companion role, which by design holds no paydays of its own.
        cadence_days: Days between paydays, from ``budget.pay_schedule``.  Read
            only for the LAST period's end; every other end is dictated by the
            next payday, so a wrong cadence can move exactly one day in the
            result.  Validated EAGERLY, before the paydays are looked at: a bad
            one is a bad caller whether or not this particular owner has
            paydays yet, and refusing it only when the data reaches the
            projection branch would hide it until the day a user records their
            first payday.
            **Not optional, since plan step C4-d** (ruling **R-PC45**).  It was
            ``int | None``, ``None`` being legal beside an empty payday set and
            REFUSED beside a non-empty one -- a pairing this function policed at
            runtime for every caller, in twenty lines, because the type would
            not.  The absence it stood for was an owner with no
            ``budget.pay_schedule`` row, and that owner now has no CALENDAR:
            :func:`~._loader.calendar_for` refuses them rather than building an
            empty one with no cadence, so nothing constructs the pair and there
            is nothing here to refuse.  An owner with a schedule row and zero
            paydays is unaffected and still ordinary -- they have a real
            cadence and an empty calendar, which is what
            ``pay_period_admin.reset_pay_periods`` passes through.

    Returns:
        The owner's periods, ``start_date`` ascending, ``period_index`` running
        0..n-1 in that order.  Empty for an empty payday set.

    Raises:
        PayCalendarError: ``cadence_days`` is not an ``int`` (``None``
            included) or falls outside 1..365; a ``period_id`` is neither an
            ``int`` nor ``None``; a payday is not a ``datetime.date``, or is a
            ``datetime.datetime`` (which is a ``date`` subclass and would
            silently give every derived end a time component); or a payday
            appears twice.
    """
    validate_cadence(cadence_days)
    # Sorted on the PAYDAY alone.  Sorting the pairs would break on a ``None``
    # id the moment two paydays tied -- and they cannot tie, which is checked
    # next, so keying the sort on the id would only hide that check.
    ordered = sorted(_validated(paydays), key=lambda pair: pair[1])
    for (_earlier_id, earlier), (_later_id, later) in zip(ordered, ordered[1:]):
        if earlier == later:
            raise PayCalendarError(
                f"payday {earlier.isoformat()} appears twice in the same "
                f"calendar.  A pay period is identified one-to-one by the "
                f"payday that opens it (uq_pay_periods_user_start enforces "
                f"that on the table), so two periods cannot share an opening "
                f"day; the first of them would be derived an end_date one day "
                f"before its own start_date."
            )

    if not ordered:
        # No last period, so no projected end, so the cadence is unread.  It
        # was still validated above, deliberately: a bad cadence is a bad
        # caller whether or not this owner has paydays yet.
        return ()

    last_position = len(ordered) - 1
    return tuple(
        DerivedPeriod(
            period_id=period_id,
            period_index=position,
            start_date=payday,
            end_date=(
                payday + timedelta(days=cadence_days - 1)
                if position == last_position
                else ordered[position + 1][1] - timedelta(days=1)
            ),
            end_is_projected=position == last_position,
        )
        for position, (period_id, payday) in enumerate(ordered)
    )


def cadence_steps_to(anchor: date, cadence_days: int, day: date) -> int:
    """Return the whole cadences from *anchor* to the last rhythm day at or before *day*.

    **The one statement of "an owner's paydays are an arithmetic progression at
    their cadence"**, and it is a function because the progression is now read
    from BOTH ends.  :func:`project_period_after` steps it forward from the
    last saved payday; :mod:`._rhythm` steps it backward from the first, below
    which the app used to count nothing at all (ledger row **N-390**, plan step
    **balance:X-bh-2**).  Two copies of ``(day - anchor).days // cadence_days``
    would be two places for the rhythm's own arithmetic to disagree, which is
    exactly the class ledger row **P6** counted seven of for the containment
    question.

    Floor division, so it answers in both directions off one expression: a
    *day* before *anchor* gives a NEGATIVE count, and ``anchor + steps *
    cadence_days`` is the rhythm day at or before *day* either way.  Python's
    ``//`` floors toward negative infinity, which is what makes that true
    rather than a coincidence -- C-style truncation would round a backward step
    toward the anchor and name a day AFTER *day*.

    Args:
        anchor: A day the owner is paid on.  The progression passes through it.
        cadence_days: Days between paydays, a positive ``int``.
        day: The day to place.  May precede, equal or follow *anchor*.

    Returns:
        The signed number of whole cadences: ``0`` when *day* falls in
        ``[anchor, anchor + cadence_days)``, negative below *anchor*, positive
        above.
    """
    return (day - anchor).days // cadence_days


def project_period_after(
    periods: "tuple[DerivedPeriod, ...]", cadence_days: int, day: date,
) -> DerivedPeriod:
    """Return the projected period covering *day*, past the last saved payday.

    **The forward continuation of the rule above**, and it lives beside it
    rather than on a consumer because it IS that rule: the last saved period's
    end is ``start_date + cadence_days - 1``, and every period after it is the
    same span stepped forward by one cadence.  Two consumers ask for it --
    :meth:`~._calendar.PayCalendar.span_containing`, which must answer for any
    day, and :func:`~._views.axis_window`, which walks the projection out to a
    requested horizon -- and a second implementation of "where does the next
    paycheck land" is exactly the class of duplicate ledger row **P6** counted
    seven of.

    Projection is ARITHMETIC on the last saved payday rather than a walk:
    paydays continue at *cadence_days*, so the period covering *day* is the
    ``n``-th one after the last saved payday where ``n`` is the whole number of
    cadences between them.  Computing it directly means the cost does not grow
    with how far past the horizon a caller asks.  **That count is
    :func:`cadence_steps_to`** since plan step balance:X-bh-2, which reads the
    same progression backward for the rhythm's other end.

    Every projected period reports ``end_is_projected`` ``True``, which stays
    faithful to plan step C1's meaning of that flag: the end comes from the
    cadence rather than from a payday anyone has recorded.  It carries
    ``period_id = None``, so a caller needing a foreign key target cannot
    mistake one for a saved row.

    Args:
        periods: The owner's SAVED periods, ``start_date`` ascending and
            non-empty.  Only the last one is read.
        cadence_days: Days between paydays.  An ``int`` rather than
            ``int | None``: a calendar holding a period cannot have been
            constructed without a cadence
            (:func:`derive_periods` refuses that pair), and every caller
            reaches here only after establishing that *periods* is non-empty.
        day: A calendar day strictly after the last saved period's
            ``end_date``.  The caller guarantees it -- both test first.

    Returns:
        The projected :class:`DerivedPeriod`, carrying ``period_id = None`` and
        a ``period_index`` continuing the saved sequence.
    """
    last = periods[-1]
    steps = cadence_steps_to(last.start_date, cadence_days, day)
    start = last.start_date + timedelta(days=steps * cadence_days)
    return DerivedPeriod(
        period_id=None,
        period_index=last.period_index + steps,
        start_date=start,
        end_date=start + timedelta(days=cadence_days - 1),
        end_is_projected=True,
    )


def validate_cadence(cadence_days: int) -> None:
    """Refuse a cadence that is not an in-range plain integer.

    Held to the same standard as :func:`_validated` holds a payday, and for the
    same reason -- the review of C1 measured what the looser check let through.
    ``bool`` is an ``int`` subclass, so ``True`` was accepted as a one-day
    cadence; and a ``float`` was accepted and silently TRUNCATED, because
    ``date.__add__`` reads only ``timedelta.days``, so ``14.9`` produced the
    same calendar as ``14``.

    **Package-internal rather than underscore-private, and plan step R7a-2a is
    why**: :class:`~._cadence.PayCadence` validates through this same function,
    so the bound has one implementation across the two values that hold a
    cadence.  It stays out of the package's public surface -- this module is
    private and ``__init__`` does not re-export it -- so the name is visible to
    siblings and to nothing else.

    **``None`` IS this function's subject since plan step C4-d** (ruling
    **R-PC45**), and that is a reversal of the rule C2-b1 wrote here.  Absence
    used to mean "this owner has no schedule at all", whose legality depended on
    whether they had paydays -- a question only :func:`derive_periods` could
    answer, so it guarded this call and owned a refusal of its own.  A calendar
    now requires a cadence outright: an owner with no ``budget.pay_schedule``
    row has no calendar rather than a cadence-less one, so the pair that needed
    a second opinion cannot be built.  The refusal below is the whole of it, and
    it is the check already written for ``bool`` and ``float`` doing one more
    type's work rather than a new fence.

    The upper bound is the stored column's own
    (``ck_pay_schedule_cadence_range``, 1..365).  Enforcing only the lower half
    while the error message quoted both was the gap; a cadence above 365 cannot
    come from a schedule row, so accepting one would mean projecting a horizon
    off a value no write door could have produced.

    Args:
        cadence_days: The candidate cadence.

    Raises:
        PayCalendarError: The value is not an ``int`` (a ``bool`` and ``None``
            included), or falls outside 1..365.
    """
    if not isinstance(cadence_days, int) or isinstance(cadence_days, bool):
        raise PayCalendarError(
            f"cadence_days must be a plain int, got "
            f"{type(cadence_days).__name__} {cadence_days!r}.  A bool is an "
            f"int subclass and would pass as a one-day cadence, and a float is "
            f"truncated by date arithmetic, which moves a horizon silently.  "
            f"None reaches here as a caller that built a calendar without a "
            f"cadence: since plan step C4-d there is no such calendar, because "
            f"an owner with no budget.pay_schedule row has no calendar at all "
            f"(pay_calendar._loader.calendar_for refuses them)."
        )
    if not MIN_CADENCE_DAYS <= cadence_days <= MAX_CADENCE_DAYS:
        raise PayCalendarError(
            f"cadence_days must be at least {MIN_CADENCE_DAYS} day and at "
            f"most {MAX_CADENCE_DAYS}, got {cadence_days}.  Below the floor "
            f"the last pay period would end before its own payday; above the "
            f"ceiling the value cannot have come from a stored schedule -- "
            f"budget.pay_schedule.cadence_days is constrained to "
            f"{MIN_CADENCE_DAYS}..{MAX_CADENCE_DAYS} by "
            f"ck_pay_schedule_cadence_range."
        )


def _validated(
    paydays: "Iterable[tuple[int | None, date]]",
) -> "list[tuple[int | None, date]]":
    """Return *paydays* as a list, refusing any pair it cannot derive from.

    ``datetime`` is a subclass of ``date``, so a bare ``isinstance`` check would
    accept one -- and every derived end would silently carry a time component,
    comparing unequal to the ``DATE`` column it is meant to reproduce and
    placing a day's money by an accident of the clock.  The stored column is
    ``DATE`` and the app's civil day is ``app.utils.dates.display_today()``, so
    a ``datetime`` reaching here is a caller that skipped the display-timezone
    conversion; refusing it names that at the boundary instead of at the diff.

    The id is checked too, even though nothing here computes with it: it rides
    onto :attr:`DerivedPeriod.period_id`, whose whole purpose is to be the
    thing a foreign key points at, so a value of the wrong type would surface
    as a failed lookup somewhere far from the caller that supplied it.

    Args:
        paydays: The candidate ``(period_id, payday)`` pairs, in any order.

    Returns:
        The same pairs as a list, unsorted.

    Raises:
        PayCalendarError: A payday is not a ``datetime.date`` or is a
            ``datetime.datetime``, or an id is neither an ``int`` nor ``None``.
    """
    checked = []
    for period_id, payday in paydays:
        if not isinstance(payday, date) or isinstance(payday, datetime):
            raise PayCalendarError(
                f"a payday must be a datetime.date, got "
                f"{type(payday).__name__} {payday!r}.  budget.pay_periods."
                f"start_date is a DATE column and the app's civil day is "
                f"app.utils.dates.display_today(); a datetime here would give "
                f"every derived end a time component and place a day's money "
                f"by the process timezone."
            )
        if period_id is not None and (
            not isinstance(period_id, int) or isinstance(period_id, bool)
        ):
            raise PayCalendarError(
                f"a period_id must be an int or None, got "
                f"{type(period_id).__name__} {period_id!r} beside payday "
                f"{payday.isoformat()}.  It is what a foreign key points at, "
                f"and None is how a period that is not materialised says so."
            )
        checked.append((period_id, payday))
    return checked
