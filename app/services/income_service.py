"""
Shekel Budget App -- Income Service (F-20 / MED-06 / F-032).

Single source of truth for what a salary profile PAYS: the paycheck engine's
own :class:`~app.services.paycheck_calculator.PaycheckBreakdown`, per period,
never the off-engine ``Decimal(str(profile.annual_salary)) / <a paycheck
count>`` recompute that silently dropped any applicable
:class:`~app.models.salary_raise.SalaryRaise` row pre-Commit-17.
:class:`ProfilePaychecks` is the ONE spelling of a profile's projection (ledger
row **N-443**, plan step **salary:R14-a**) and :class:`SalaryPricing` is what
prices a generated row from it.

**It became a FUNCTION of the payday rather than a LIST at plan step
salary:S3-d**, and that is what deletes the horizon question rather than
answering it.  ``project_profile(profile, calendar)`` returned a list over the
owner's SAVED window, so past the last saved payday there is no paycheck and
:class:`~app.services.investment_projection.AccountPayrollFeed` INVENTS one by
holding a figure -- six rules over that fold, each measured wrong (**N-541**,
ruling **R-SAL10**).

**That hold is still LIVE, and the present tense above is deliberate.**  This
step builds the producer that can price a payday past the horizon and moves
every consumer onto it; it does NOT yet ask for one.  Every production caller
of :meth:`ProfilePaychecks.over` still passes ``calendar.saved()``, so
``_year_averages``, ``_complete_years``, ``_held_employee`` and
``_held_gross`` are all still reached.  Plan step **salary:S3-e** is what
widens the ask and deletes them, on the strength of what
``TestThePricerAnswersPastTheSavedHORIZON`` grades here.

Widening the list needs somebody to decide HOW LONG, and
one render asks two lengths of it: the balance seam wants the saved window, the
40-year chart wants forty years, and they share one memo.  Nothing decides,
because a paycheck does not depend on any other paycheck --
:func:`~app.services.paycheck_calculator.calculate_paycheck` rebuilds its own
year's month positions and both cumulatives off ``basis.calendar``, which runs
forward past the horizon at the owner's cadence -- so the series is a MAP from
payday to paycheck, defined wherever the rhythm reaches, and a caller asks for
the paydays it actually reads.

**One pricer per profile per read pass**, :class:`PaycheckPricing`, reached
through :meth:`~app.services.balance_at.BalanceContext.paychecks`.  It replaced
the raw ``BalanceContext.payroll_breakdowns`` dict and the ``breakdowns=None``
argument on :func:`~app.services.projection_inputs.load_payroll_feeds` that
meant "no memo at all" -- the hole
``TestOnePaycheckProjectionPerProfilePerRender`` was written to watch, and which
that test's own docstring records two of four callers falling through.  A
consumer is handed the pricer now, so there is nothing left to forget to pass.

**TWO sources of a paycheck survive this step, and saying so is the point.**
The read pass holds one and :class:`SalaryPricing` derives a second; the
argument, and what it costs, is at :class:`PaycheckPricing` and stated once
there.  It is ledger row **P63**, owned by **C12**, and S3-d does not close
it -- so the runtime gate keeps a subject and is kept, re-pointed from the
deleted ``project_profile`` to :class:`ProfilePaychecks`.

**A SCALAR producer stood beside them until plan step salary:R14-b, and it is
DELETED rather than re-pointed.**  ``get_current_gross_biweekly(user_id,
calendar)`` answered "the current per-period gross" for the savings,
retirement and investment feeds, and the shape was the defect: one figure,
read at ONE moment, standing in for a series that every raise moves.  It
carried two more that fell out of that shape and that ``recurrence:R-F16``'s
adversarial review had to REVERT a fix over -- it chose the owner's profile
with an unordered ``.first()`` (a measured **39%** swing on a two-job owner,
flipping between renders with no data change) and it answered ``$0.00``
whenever no pay period covered its clock, which silently deleted a whole
contribution plan at onboarding and after a horizon lapse.

Ruling **R-SAL2** answers all three at once and none of the answers is a
scalar: the PERIOD is the clock, the gross is that period's own off the
engine's breakdown, and the profile is NAMED (by the deduction, or by
``budget.investment_params.salary_profile_id`` -- **R-SAL5**) rather than
searched.  Its consumers read
:class:`~app.services.investment_projection.AccountPayrollFeed` now, which
:func:`app.services.projection_inputs.load_payroll_feeds` folds out of
:class:`ProfilePaychecks`.  This module's own docstring listed those consumers
for as long as it had them; the list is gone with the function.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data (a user id, a
scenario id, an ORM profile, a calendar).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.payroll_basis import PayrollBasis
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.tax_config_service import configs_by_year, profile_tax_series


class ProfilePaychecks:
    """What ONE salary profile pays, asked a PAYDAY at a time and remembered.

    **The ONE spelling of a profile's projection, and closing that it was
    THREE is ledger row N-443** (plan step **salary:R14-a**).  The pair it
    replaced --
    ``tax_config_service.load_tax_configs_for_periods`` over the calendar's
    saved window (the door plan step salary:S3-d replaced with
    :func:`~app.services.tax_config_service.configs_by_year`, which takes
    YEARS rather than a window), then
    :func:`paycheck_calculator.project_salary` on a
    :class:`~app.services.payroll_basis.PayrollBasis` with the profile's
    calibration -- was written out longhand in three places over the same
    calendar: :class:`SalaryPricing` below, ``routes/salary/views.py`` and
    ``routes/salary/cockpit.py``.

    **The three could not simply call each other, and that is why moving the
    leaf was the remedy rather than picking one.**  The two route sites keep
    the whole :class:`~app.services.paycheck_calculator.PaycheckBreakdown` --
    they render the anatomy, the chart and the ledger table -- while the
    derivation kept only ``net_pay``, so the shared leaf had to be the
    BREAKDOWN and no existing caller produced one.  Ruling **R-IZ** names
    exactly this shape: a second walk is a cache with no column, agreement is
    not the test, and where a layer puts the shared leaf out of reach the
    remedy is to MOVE THE LEAF.

    **It reads the OWNER off the profile** rather than taking a user id,
    because the two must agree and a caller holding ``current_user.id`` beside
    a profile is a second chance to get it wrong.

    **The tax series is loaded ONCE, here, and every year after is arithmetic**
    (:func:`~app.services.tax_config_service.profile_tax_series` is three
    queries whatever the horizon;
    :func:`~app.services.tax_config_service.configs_by_year` is the pure pick).
    That is what lets :meth:`at` answer a payday nobody knew would be asked
    for without re-reading the tax tables, and it is why this is a VALUE built
    per read pass rather than a free function: a free function would have to
    reload the series, or take it, and the second is the caller-supplied input
    the pass exists to remove.

    **This value's OWN database work is all at construction; the PROFILE's
    need not be, and an adversarial review of plan step salary:S3-d corrected
    a sentence here that claimed otherwise.**
    ``SalaryProfile.raises`` and ``.deductions`` are ``lazy="select"`` and
    :func:`~app.services.paycheck_calculator.calculate_paycheck` reads both,
    so whether the FIRST paycheck priced issues two more SELECTs depends on
    how the caller loaded the profile, not on this class.
    :func:`~app.services.projection_inputs.load_payroll_feeds` eager-loads
    them (``subqueryload`` on both); :meth:`SalaryPricing._profile_by_template`
    does not, and that is a pre-existing property of the amount model's
    profile lookup rather than something this step introduced.  It is
    reported, not fixed here: adding eager options to that query changes what
    the amount model reads on every pass that prices a row, which is
    ``balance:X-i1``'s tier and not a producer's to decide.

    **Past the saved schedule it keeps answering, and that is the point of
    plan step salary:S3-d.**  The engine's four calendar judgements -- the
    month position both deduction cadences read, the FICA wage-base cumulative
    and a deduction's annual-cap cumulative -- all come off ``basis.calendar``
    since plan step **balance:X-bh-1**, and that rhythm projects forward at the
    owner's cadence to :data:`~app.utils.dates.CALENDAR_DATE_MAX`
    (:mod:`app.services.pay_calendar._rhythm`).  So a projected payday is
    priced by the same code and the same rules as a saved one; what it lacks is
    a ``budget.pay_periods`` row, which is exactly what
    :attr:`~app.services.paycheck_calculator.PeriodInfo.period_id` being
    ``None`` says.
    """

    def __init__(self, profile, calendar: PayCalendar) -> None:
        """Pin the profile to its owner's calendar and load the tax series.

        Args:
            profile: The :class:`~app.models.salary_profile.SalaryProfile` to
                price.  Read for its owner, its calibration, and everything
                the engine prices a paycheck from.
            calendar: The owner's
                :class:`~app.services.pay_calendar.PayCalendar` -- the payday
                set every calendar question the engine asks is answered from,
                and the cadence it divides the salary by.  Must belong to the
                SAME owner as *profile* (see the raise below).

        Raises:
            ValueError: *profile* and *calendar* belong to different owners.
                **The mispairing is SILENT without this**, which is why it is
                refused here rather than described: the tax series would load
                under one owner while every payday came from the other's
                schedule, and the engine's own cross-owner guard cannot fire
                on it -- :func:`~app.services.paycheck_calculator
                ._month_ordinal` refuses a payday the calendar cannot place,
                and the calendar places its OWN paydays perfectly well.  The
                result is a plausible wrong paycheck.
                :attr:`~app.services.pay_calendar.PayCalendar.user_id` exists
                for exactly this test and says so in its own docstring; plan
                step **salary:S3-d** is the first moment one value holds both
                halves, which is the cheapest this check will ever be.
        """
        if profile.user_id != calendar.user_id:
            raise ValueError(
                f"salary profile {getattr(profile, 'id', '?')} belongs to user "
                f"{profile.user_id} and the pay calendar to user "
                f"{calendar.user_id}: a paycheck is priced against the "
                f"calendar its own owner is paid on, and pairing the two "
                f"answers a plausible figure off the wrong schedule."
            )
        self._profile = profile
        self._basis = PayrollBasis(profile, calendar)
        self._series = profile_tax_series(profile.user_id, profile)
        self._by_payday: "dict[date, paycheck_calculator.PaycheckBreakdown]" = {}

    def over(
        self, periods,
    ) -> list[paycheck_calculator.PaycheckBreakdown]:
        """Return this profile's paycheck for each of *periods*, in that order.

        Prices only the paydays it has not priced already, so two callers
        asking overlapping spans in one pass pay for the union and never for
        the overlap.  That the subset is priced identically to the whole is
        :func:`paycheck_calculator.project_salary`'s own stated contract since
        plan step **balance:X-bh-1**: "this list may be any subset in any order
        and every breakdown is the same as it would be alone", because the
        engine's month and year context comes off ``basis.calendar`` rather
        than off the list it is handed.

        **The priced breakdowns are filed under their OWN payday**, read off
        :attr:`~app.services.paycheck_calculator.PeriodInfo.payday`, rather
        than zipped against *periods* by position.  That is WEAKER than it
        first reads and saying so is the point: it replaces a pairing by
        POSITION -- which would truncate silently if the two sequences ever
        differed in length -- with a pairing by KEY, so what survives is the
        narrower requirement that ``DerivedPeriod.start_date`` and
        ``PeriodInfo.payday`` are the same day.  The engine copies one to the
        other in one statement, so that is a key rather than the two-producer
        contract ``CLAUDE.md`` rule 14 names; it is not nothing.

        **The memo is keyed by PAYDAY alone, so it cannot tell a saved period
        from a projected one that opens on the same day.**  Unreachable: a
        calendar built by :func:`~app.services.pay_calendar.calendar_for`
        reads saved rows only, and every projected payday it yields falls
        strictly after the last saved one, so the two sets are disjoint by
        construction.  It is stated because this method's own signature
        invites "saved, projected, or both" and a caller assembling such a
        list by hand is the one input that could reach it.

        Args:
            periods: The :class:`~app.services.pay_calendar.DerivedPeriod`
                values to price -- saved, projected, or both, in any order.
                Only ``start_date`` and the fields the engine reads are used.
                Materialised on entry, so a generator serves: this walks it
                twice, and the second walk of a spent iterator would answer an
                empty list rather than raising.

        Returns:
            ``list[PaycheckBreakdown]``, one per element of *periods* and in
            its order, so a caller may zip the two.
        """
        periods = tuple(periods)
        # DEDUPED BY PAYDAY, not merely filtered, and the key matters: the
        # memo makes a REPEAT ask free, but two periods sharing a payday
        # INSIDE one call would both miss it and both be priced -- so "a
        # payday is priced once" would hold on the memo-hit path and not on
        # the first one.  Deduping the PERIODS would not do it, because two
        # ``DerivedPeriod`` values can differ (in ``period_id``, say) while
        # naming the same day, and the day is what this memo is keyed by.
        # Insertion order is preserved, so the engine is handed the paydays in
        # the order the caller first named them.
        wanted: "dict[date, object]" = {}
        for period in periods:
            if period.start_date not in self._by_payday:
                wanted.setdefault(period.start_date, period)
        unpriced = list(wanted.values())
        if unpriced:
            self._by_payday.update(
                (breakdown.period.payday, breakdown)
                for breakdown in paycheck_calculator.project_salary(
                    self._basis, unpriced,
                    # Tax configs resolve PER period year (DH-#30), the same
                    # per-year resolution the recurrence engine uses to
                    # GENERATE the stored amount, so the live figure and the
                    # generated one cannot disagree for want of a bracket set.
                    configs_by_year=configs_by_year(
                        self._series,
                        (period.start_date.year for period in unpriced),
                    ),
                    calibration=self._profile.calibration,
                )
            )
        return [self._by_payday[period.start_date] for period in periods]

    def at(self, period) -> paycheck_calculator.PaycheckBreakdown:
        """Return this profile's paycheck for ONE period.

        Args:
            period: The :class:`~app.services.pay_calendar.DerivedPeriod` to
                price.

        Returns:
            That payday's :class:`~app.services.paycheck_calculator
            .PaycheckBreakdown`.
        """
        return self.over([period])[0]


class PaycheckPricing:
    """The read pass's ONE :class:`ProfilePaychecks` per salary profile.

    **Plan step salary:S3-d.**  Within ONE of these, a payday is priced once
    by construction: :meth:`for_profile` memoizes per profile and
    :class:`ProfilePaychecks` memoizes per payday, so two consumers asking
    overlapping spans pay for the union and never for the overlap.  A consumer
    is HANDED one -- there is no ``breakdowns=None`` to forget, which is what
    let two of four callers bypass the dict this replaced.

    **What it does NOT yet make unconstructible is a SECOND one**, and the
    limit is stated because an unstated limit reads as none.  The read pass
    builds one (:meth:`~app.services.balance_at.BalanceContext.paychecks`) and
    :class:`SalaryPricing` builds another, because
    ``cash_ledger.amount_basis`` takes an owner and a scenario and has nothing
    to hand it: ledger row **P63**, owned by **C12**.  So a render that reads
    a salary ROW and a payroll FEED still prices those paydays twice, and
    ``TestOnePaycheckProjectionPerProfilePerRender`` is kept -- re-pointed at
    this class's construction -- to keep a THIRD from appearing meanwhile.

    **More sites price a single period outside any pricer**, by calling
    :func:`~app.services.paycheck_calculator.calculate_paycheck` directly.
    They belong to the same finding as the second source above, and they are
    ENUMERATED WITH THEIR COUNTS in exactly one place -- ``tests/test_arch/
    test_the_calendar_wide_projection_has_one_spelling.py``, whose census
    fails both when a new one appears and when **C12** deletes one.  A copy
    of that list here would be a second home for it, which is the shape rule
    14 names; this paragraph replaced two verbatim copies of it.
    """

    def __init__(self, calendar: PayCalendar) -> None:
        """Pin the calendar every profile in this pass is priced against.

        Args:
            calendar: The owner's
                :class:`~app.services.pay_calendar.PayCalendar`.
        """
        self._calendar = calendar
        self._by_profile: "dict[int, ProfilePaychecks]" = {}

    @property
    def calendar(self) -> PayCalendar:
        """The pass's pay calendar, so a consumer holding this needs no second.

        Published because :func:`~app.services.projection_inputs
        .load_payroll_feeds` needs the payday set as well as the prices, and
        taking the two separately is how one owner's calendar comes to be
        paired with another's paychecks.

        Returns:
            The :class:`~app.services.pay_calendar.PayCalendar` this pricer
            answers against.
        """
        return self._calendar

    def for_profile(self, profile) -> ProfilePaychecks:
        """Return this pass's :class:`ProfilePaychecks` for *profile*.

        Args:
            profile: The :class:`~app.models.salary_profile.SalaryProfile`.
                Its ``id`` is the memo key and its ``user_id`` must be the
                owner the calendar belongs to.

        Returns:
            The profile's pricer -- the same object every time within one
            pass, so nothing it has priced is priced again.
        """
        if profile.id not in self._by_profile:
            self._by_profile[profile.id] = ProfilePaychecks(
                profile, self._calendar,
            )
        return self._by_profile[profile.id]


def paycheck_pricing(calendar: PayCalendar) -> PaycheckPricing:
    """Return a read pass's :class:`PaycheckPricing` over *calendar*.

    The named constructor, so no consumer reaches for the class directly.
    **TWO callers, not one**: :meth:`~app.services.balance_at.BalanceContext
    .paychecks` builds the read pass's, and :meth:`SalaryPricing._pricing`
    below builds the amount model's second one -- which is ledger row
    **P63**, stated at :class:`PaycheckPricing`.  An earlier draft of this
    sentence said the pass was the only builder, and the file it sits in
    refutes that 130 lines down.

    Resolves nothing ITSELF, so a pass that prices no paycheck pays nothing
    for holding one.  The first query lands at
    :meth:`PaycheckPricing.for_profile`, which builds a
    :class:`ProfilePaychecks` and loads that profile's tax series in three
    queries; nothing is issued before a profile is named.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        The empty :class:`PaycheckPricing`.
    """
    return PaycheckPricing(calendar)


class SalaryPricing:
    """What the owner's active salary profiles pay, resolved per profile ASKED.

    **The DERIVATION half of the salary amount rule, split from its per-row
    lookup at plan step X-au-c2b, and the one producer of a ROW's amount since
    plan step X-au-d** (finding **N-443** is the two other spellings of the
    projection itself).  What rows a caller happens to have loaded reaches
    none of it: this is pinned to ``(user_id, scenario_id)``, which is what
    lets one read pass resolve it ONCE however many row sets ask
    (:class:`~app.services.cash_ledger.AmountBasis`).

    **It holds a PRICER since plan step salary:S3-d, where it held a whole
    projection**, and the change is what a row's figure costs.  It memoized
    ``{profile_id: {period_id: PaycheckBreakdown}}`` over the owner's WHOLE
    saved window, so a single row's live amount ran the engine over every
    payday the owner has; it resolves the ONE period the row names now.

    **It derives its own pricing rather than taking the read pass's**, which
    is ledger row **P63** and is argued once, at :class:`PaycheckPricing`.

    It was a ``{transaction_id: Decimal}`` map built per row set until that step,
    and the two consequences are why this type exists.  A request that loaded two
    row sets ran the paycheck engine twice (findings **N-268**, **N-269**), and a
    row outside the set it was built over had no answer -- which forced the basis
    to carry the set as a membership guard so the miss could be REFUSED rather
    than read as "this row has no live figure".  Keyed on the definition and the
    period instead, there is no membership question left to get wrong: the pair
    IS the paycheck's identity.

    **Laziness is in TWO stages, and an adversarial review of this step's own
    build is why.**  The first draft resolved one map for every active profile
    on first read, gated only by ``is_income and template_id is not None`` -- a
    test that cannot tell a paycheck template from any other recurring income.
    Both halves of that were a regression against the row-set producer it
    replaced, which filtered its profile query by the CANDIDATE rows' templates
    and returned ``{}`` without projecting anything:

      * a recurring *Interest* or *Dividend* income row -- templated, projected,
        not a paycheck -- forced the whole paycheck engine, where the old path
        paid one indexed query and stopped; and
      * an owner with TWO active profiles paid ``project_salary`` twice on a
        pass whose rows named one of them.

    So the PROFILE LOOKUP is memoized here: asking about a template no profile
    names costs one indexed query and no engine run, and a profile is priced
    only when a row actually asks about it.  The pricing behind it lives for
    the pass, so the sharing this class exists for is unchanged.
    """

    def __init__(self, user_id: int, scenario_id: int) -> None:
        """Pin the owner and scenario; resolve nothing yet.

        Args:
            user_id: The owner whose active profiles price these rows.
            scenario_id: The scenario to resolve profiles against.
        """
        self._user_id = user_id
        self._scenario_id = scenario_id
        self._profiles: "dict[int, SalaryProfile] | None" = None
        # Derived rather than taken, which is ledger row **P63** (see the
        # class).  LAZY, so a pass that prices no paycheck derives no calendar
        # and builds no pricer -- the property the two-stage laziness above
        # exists for, kept exactly as the calendar memo this replaced had it.
        self._paychecks: "PaycheckPricing | None" = None

    def net_for(
        self, template_id: int, pay_period_id: int,
    ) -> Decimal | None:
        """Return what the profile driving *template_id* pays for that period.

        Args:
            template_id: The recurring definition the row was generated from.
            pay_period_id: The period the row is funded in.

        Returns:
            The live net pay, or ``None`` when no ACTIVE profile in this
            scenario names that template, or when that period is not one the
            owner's saved calendar holds.  Both are the refusals amount rule 2
            raises rather than substituting a stored figure.
        """
        profile = self._profile_by_template().get(template_id)
        if profile is None:
            return None
        paychecks = self._pricing()
        # The period is resolved by ID off the owner's calendar, and that is
        # amount rule 2's second refusal stated where it belongs: a row names
        # a ``budget.pay_periods`` row, so a ``pay_period_id`` the calendar
        # does not hold has no paycheck to price rather than a paycheck worth
        # nothing.  It replaced a membership test against a projected MAP at
        # plan step salary:S3-d, which answered the same question by pricing
        # the owner's whole window first.
        period = paychecks.calendar.period_by_id(pay_period_id)
        if period is None:
            return None
        return paychecks.for_profile(profile).at(period).earnings.net_pay

    def _pricing(self) -> PaycheckPricing:
        """Return this basis's paycheck pricer, deriving it once.

        The owner's saved schedule off the DERIVED calendar (pay-calendar plan
        step C2-f2d-3), so the paydays this prices against carry the ends the
        whole payday set dictates rather than the stored ``end_date`` column
        plan step C4-c dropped.

        **This one still DERIVES**, where every other consumer on the read
        path is given one: the basis is built by ``cash_ledger.amount_basis``
        from an owner and a scenario alone, so there is no pricer to take
        without threading one through that constructor.  Ledger row **P63**
        is that finding and :class:`PaycheckPricing` carries the argument; it
        is repeated here only because this is the line that does it.  LAZY,
        so a pass that prices no paycheck pays nothing for it.

        Returns:
            This basis's :class:`PaycheckPricing`.
        """
        if self._paychecks is None:
            self._paychecks = paycheck_pricing(calendar_for(self._user_id))
        return self._paychecks

    def _profile_by_template(self) -> "dict[int, SalaryProfile]":
        """Return ``{template_id: profile}`` for this owner and scenario.

        One indexed query, memoized -- the CHEAP stage, so a row on a template
        no profile names is answered without projecting anything.

        **The query is ORDERED, and it was not before.**  Two active profiles
        naming ONE template in one scenario is expressible (nothing constrains
        it) and this map keeps the last writer.  Unordered, that was whichever
        row the planner reached first, so one owner could be priced two ways
        across two requests; ordering by id makes the collision resolve the same
        way every time.  The collision itself is finding **N-294**, reported
        rather than fixed here: which profile SHOULD win is a question for the
        salary arc, and answering it inside a reader refactor would be an
        unreviewed ruling.

        Returns:
            ``{template_id: SalaryProfile}``; empty for an owner with no active
            profile in this scenario.
        """
        if self._profiles is None:
            profiles = (
                db.session.query(SalaryProfile)
                .filter(
                    SalaryProfile.user_id == self._user_id,
                    SalaryProfile.scenario_id == self._scenario_id,
                    SalaryProfile.is_active.is_(True),
                )
                .order_by(SalaryProfile.id)
                .all()
            )
            self._profiles = {p.template_id: p for p in profiles}
        return self._profiles


def salary_pricing(user_id: int, scenario_id: int) -> SalaryPricing:
    """Return the read pass's :class:`SalaryPricing` for an owner and scenario.

    The named constructor the amount model calls, so no caller reaches for the
    class directly and the two pins are always supplied together.  Resolves
    nothing: every stage behind it is lazy, so a pass that prices no paycheck
    issues no query.

    Args:
        user_id: The owner whose profiles price these rows.
        scenario_id: The scenario to resolve profiles against.

    Returns:
        The unresolved :class:`SalaryPricing` handle.
    """
    return SalaryPricing(user_id, scenario_id)


def salary_net_for(txn, pricing: SalaryPricing) -> Decimal | None:
    """Return what the salary profile pays for *txn*'s period, or ``None``.

    **The PRICING lookup -- amount rule 2's whole body** (ruling **R-FI**), and
    since plan step **X-au-d** the only producer of a ROW's amount.  It was
    split from a read-time repair (``live_projected_net``) at plan step
    X-au-c2b and stood beside it until that cutover declared salary rows
    DERIVED, at which point there was no stored figure left for a repair to
    supersede and the repair was deleted rather than left as a second walk to
    one answer (``CLAUDE.md`` rule 14, rulings **R-IZ** / **R-JA**).

    *It became the only spelling of the CALENDAR-WIDE projection at plan
    step* **salary:R14-a**, *which is finding* **N-443** *closed.*  The
    qualifier is load bearing and a second adversarial review put it back:
    ``tax_withholding_service`` and ``tax_report_service`` still run
    ``project_salary`` themselves over a single tax YEAR with
    ``tax_configs=``, which is a different question and not a fourth
    spelling -- the arch test's own predicate says so, and prose that
    claims more than the test enforces is the wider claim this paragraph
    was already corrected for once.
    ``routes/salary/views`` and ``routes/salary/cockpit`` each built the same
    ``project_salary`` call over the same calendar, because they render the
    whole breakdown rather than the net -- so the shared leaf had to BE the
    breakdown, which is what :class:`ProfilePaychecks` holds and what
    :meth:`ProfilePaychecks.over` answers.  All three read it, and
    ``tests/test_arch/
    test_the_calendar_wide_projection_has_one_spelling.py`` is what keeps a
    fourth from appearing.

    It asks only what a paycheck IS worth, so it reads nothing about whether
    the row still counts: not ``is_projected``, not ``is_override``, not
    ``is_deleted``.  That is finding **N-262**'s rule applied one tier down --
    those three say whether a row COUNTS and who last touched it, never who
    prices it -- and it is why a Cancelled paycheck resolves like any other
    instead of refusing for a reason that has nothing to do with pricing.  The
    repair that DID read those three is what X-au-d removed, so the split it
    was written to protect is now simply the shape of the module.

    ``is_income`` IS read, and it is a pricing fact rather than a status one: a
    salary profile states a NET PAY, so an expense row on a salary-linked
    template has no figure here to find.  ``amount_rule`` still places such a
    row under rule 2 (it classifies by the definition, which is salary-linked),
    so this returning ``None`` is what turns it into that rule's refusal.

    Args:
        txn: The row being priced.  ``is_income``, ``template_id`` and
            ``pay_period_id`` are read.
        pricing: The read pass's :class:`SalaryPricing`.

    Returns:
        The live net pay for the row's period, or ``None`` when no active
        profile in this scenario names its template, when the projection does
        not cover its period, or when it is not an income row.
    """
    if not txn.is_income or txn.template_id is None:
        return None
    return pricing.net_for(txn.template_id, txn.pay_period_id)
