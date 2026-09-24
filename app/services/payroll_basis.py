"""The paycheck engine's owner-level input: a salary profile and its calendar.

Plan step **R-F16** (``docs/plans/implementation_plan_recurrence_redesign.md``,
"Carried steps").  Its own module rather than a type inside
:mod:`app.services.paycheck_calculator`, which sits at the 1000-line ceiling
this step's own additions pushed it past -- and because nine modules outside
that one CONSTRUCT this value while only it computes a paycheck, so the input
has more consumers than the producer does.  Every one of the nine imports it
from HERE; an earlier draft of this sentence made the consumer argument while
all nine still reached the type through the engine, which is a claim its own
import graph refuted.

No Flask, no clock, no database access of its own.  What it reads off the
profile it is handed -- the raise rows, and since plan step salary:R15-b each
deduction's recurrence rule -- it reads as ATTRIBUTES the caller loaded (the
engine's loader eager-loads both; ``test_projection_inputs`` counts the
statements a walk issues and finds none), converting each to a value through
the reader that owns its vocabulary: :func:`~app.services.salary_raises
.terms_of` for a raise, :func:`~app.services.recurrence.recurrence_spec` for
a rule.  The profile is typed loosely on purpose (see the class).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from app.services.pay_calendar import PayCadence, PayCalendar, cadence_on
from app.services.recurrence import (
    ResolvedRecurrence,
    projected_occurrence_placements,
    recurrence_spec,
    resolve,
)
from app.services.salary_raises import RaiseTerms, apply_raises, terms_of
from app.utils.money import round_money

#: How far past an ask a cadence is walked before it is asked again: every
#: walk reaches at least a year past the payday asked, and each later
#: extension also doubles the span walked so far, so a projection that asks
#: ascending paydays for twenty years re-walks about five times and never
#: walks past twice its horizon.  A walk to the application's last date was
#: the first shape and it cost 800 ms per basis on the developer's eleven
#: lines (measured 2026-09-13): past the saved schedule the ceiling arm
#: re-projects the month for every occurrence, so the walk must reach only
#: what is asked of it.  **The year past the ask is load-bearing, not
#: slack** (an adversarial review of this step): under ``CONTAINING_DATE`` an
#: occurrence up to a full period AFTER a payday places on that payday, so a
#: reach set exactly at the payday asked stops the walk before the
#: occurrence that admits it and caches the wrong ``False``.
_REACH_PAST_ASK = timedelta(days=366)


@dataclass
class _WalkedCadence:
    """One resolved cadence's admitted paydays, walked as far as it has been asked.

    Mutable, and private to :class:`PayrollBasis`: a memo, not a value.
    Every line that resolves to the same :class:`ResolvedRecurrence` shares
    one of these -- the developer's eleven 24-per-year lines are ONE walk --
    because the occurrence set is a function of the cadence and the calendar
    alone.

    Attributes:
        resolved: The cadence walked.
        through: The last day the walk has reached, or ``None`` before it is
            asked anything.
        admitted: Every payday the walk placed an occurrence on, through
            :attr:`through`.
    """

    resolved: ResolvedRecurrence
    through: date | None = None
    admitted: frozenset[date] = frozenset()

    def admits(self, calendar: PayCalendar, payday: date) -> bool:
        """Whether an occurrence lands on the paycheck of *payday*, walking further if needed.

        Refuses nothing: a payday this calendar cannot place is simply not in
        the set.  The engine's refusal of such a payday is
        :func:`~app.services.paycheck_calculator._calendar_questions
        ._month_ordinal`'s, read before any line is priced.
        """
        if self.through is None or payday > self.through:
            self._walk(calendar, payday)
        return payday in self.admitted

    def _walk(self, calendar: PayCalendar, payday: date) -> None:
        """Re-walk from the cadence's first occurrence to a year past *payday* at least.

        A later walk also doubles the span walked so far, so the reach grows
        geometrically with the asks; the set is REPLACED by the longer walk's,
        which is a superset of the shorter's (the same cadence over a longer
        window).
        """
        through = payday + _REACH_PAST_ASK
        if self.through is not None:
            through = max(through, self.through + (self.through - self.resolved.starts_on))
        self.admitted = frozenset(
            placement.period.start_date
            for placement in projected_occurrence_placements(
                self.resolved, calendar, through=through,
            )
            if placement.period is not None
        )
        self.through = through


@dataclass(frozen=True)
class BasePay:
    """What the salary pays on one payday: the rate, and the two facts it divides.

    Plan step **salary:X-av-2** (ruling **R-SAL66**; ledger row **SAL-569**).
    The value :meth:`PayrollBasis.base_pay_on` returns, so the paycheck engine
    reads a payday's three salary figures from ONE read: the per-paycheck
    rate every paycheck prices from, the annual
    :class:`~app.services.paycheck_calculator.Earnings` reports, and the
    count Pub 15-T annualises the paycheck's wages by.  The count rides here
    rather than being asked again by the withholding because it is a fact of
    the payday, and two reads of it would be two places for a paycheck's
    divisor and its annualiser to part.

    **The rate is a property of the other two, never a third field**: a field
    could be constructed disagreeing with them, and a value holding three
    figures of which one is a function of the others is the stored-derived
    shape rule 14 deletes.

    **It carries the CADENCE, not only its count** (ruling **R-SAL70**): the
    engine hands it on as :attr:`~app.services.paycheck_calculator.PeriodInfo
    .cadence`, so a reader turning the paycheck into a monthly or yearly
    figure converts at the rhythm the paycheck was priced at, through
    :class:`~app.services.pay_calendar.PayCadence`'s own conversions, and
    never at a count read off the calendar a second time.

    Attributes:
        annual_salary: The post-raise annual salary in effect on the payday,
            as :func:`~app.services.salary_raises.apply_raises` returns it.
        cadence: The rhythm in force on the payday --
            :func:`~app.services.pay_calendar.cadence_on`'s answer.
    """

    annual_salary: Decimal
    cadence: PayCadence

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks a year :attr:`cadence` pays, an integral ``Decimal``."""
        return self.cadence.periods_per_year

    @property
    def per_paycheck(self) -> Decimal:
        """Return what one paycheck pays: :func:`gross_per_paycheck` of the two fields."""
        return gross_per_paycheck(self.annual_salary, self.periods_per_year)


@dataclass(frozen=True)
class PayrollBasis:
    """One owner's salary contract bound to the calendar their paychecks arrive on.

    **It carries the whole :class:`~app.services.pay_calendar.PayCalendar`
    since plan step balance:X-bh-1, and that is the same fix applied a second
    time.**  It held a bare
    :class:`~app.services.pay_calendar.PayCadence` until then, while the engine
    took the owner's period SET as a separate argument -- so the paycheck count
    and the paydays it counted arrived by two routes with nothing holding them
    to one owner or one read.  Four of the engine's judgements read that
    argument (third-paycheck detection, the first-paycheck-of-month deduction
    cadence, the FICA wage-base cumulative and a deduction's annual cap), its
    type was ``Sequence[DerivedPeriod]``, and every window, year slice and
    one-period sample satisfied that type: passing one cost a stored salary row
    **$502.45** (ledger row **D25**).  A calendar is constructible only from a
    COMPLETE payday set, so the narrow context is now unrepresentable rather
    than forbidden by a docstring, and the cadence comes off the same
    derivation as the paydays.  That closed finding **N-390**'s first half;
    the second -- what the calendar can answer BELOW its opening payday --
    closed at plan step **balance:X-bh-2**, which gave the calendar the owner's
    stored ``history_opens_on`` and made its rhythm run in both directions.

    **The pair, as one value, is plan step R-F16's fix for finding F-16.**  The
    engine needs two facts to price a paycheck -- what the job pays a year, and
    how many paychecks that year holds -- and until this step they travelled
    separately: the salary profile carried its own ``pay_periods_per_year``
    column (a 12 / 24 / 26 / 52 dropdown) while
    ``budget.pay_schedule.cadence_days`` carried the payday rhythm, and no door
    validated one against the other.  Measured with the real engine on a
    ``$91,675`` salary, a profile saying 26 beside a 7-day cadence modelled
    ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the year's
    paychecks summing to 200% of salary.  Only 5 of the 365 legal cadences had
    a dropdown value that could agree with them at all, so validating the pair
    was not an available remedy: the count had to become a derivation.

    Binding them makes the mismatched pair unrepresentable rather than merely
    discouraged -- the same argument the read-pass ruling makes for
    ``BalanceContext`` -- and there is now ONE derivation of the count,
    :attr:`~app.services.pay_calendar.PayCadence.periods_per_year`, which is
    also what every monthly-equivalent conversion in the application reads.
    A salary profile's paycheck recurs every pay period BY DEFINITION (it is
    what ``routes.salary.profiles._paycheck_template`` authors), so there was
    never a per-profile count for the dropped column to hold.

    **It NAMES the raise set it prices from since plan step salary:S3-f-1**
    (ruling **R-SAL20**), the way it names its calendar.  The engine read
    ``basis.profile.raises`` -- the ORM relationship -- at three sites, so the
    only raise set a paycheck could ever be priced under was the one stored,
    and a what-if over a raise's end year (the ``/retirement`` rail's per-raise
    probe, plan step **salary:S3-f**) had no input to arrive through.  The
    set is an INPUT of this value now: the profile's own rows' terms unless a
    caller supplies :attr:`raise_terms`, and every engine read goes through
    :attr:`raises`, which is always a tuple of
    :class:`~app.services.salary_raises.RaiseTerms` -- the VALUE whose fields
    are the engine's whole contract with a raise.  The stored plan is
    unchanged by construction: a basis built without terms prices exactly the
    figures its rows carry, read through the same walk.

    Attributes:
        profile: The ``SalaryProfile`` -- read for the annual salary, the
            deductions and the W-4 inputs, and for the raises when no
            :attr:`raise_terms` are supplied.  Typed loosely because every
            consumer reads attributes rather than the ORM class, and because
            this module must not import a model (the engine below it is
            pure).  The test suite prices duck-typed profiles through the same
            door for that reason.
        calendar: The owner's whole
            :class:`~app.services.pay_calendar.PayCalendar` -- the payday set
            every calendar question the engine asks is answered from, and the
            eras whose cadence :meth:`base_pay_on` divides each payday's
            salary by.  ONE value because they are one fact: the cadence is a
            field of the calendar, so a paycheck cannot be priced at one
            rhythm and placed in a month counted at another.
        raise_terms: The raise set to price from INSTEAD of the profile's
            rows, as a tuple of :class:`~app.services.salary_raises.RaiseTerms`,
            or ``None`` for the rows' own terms -- the default, and what every
            direct constructor passes; the read pass's pricer
            (:meth:`~app.services.income_service.PaycheckPricing.for_profile`)
            passes the canonical tuple for every set, the stored one included.
            A ``None`` here does NOT stand for a stored number the way the
            plan point's rule forbids: it stands for the rows, which are the
            one home of the stored fact, and this value is an input rather
            than a memo key -- the pricer's memo is keyed on the tuple itself.
    """

    profile: Any
    calendar: PayCalendar
    raise_terms: "tuple[RaiseTerms, ...] | None" = None

    @cached_property
    def raises(self) -> "tuple[RaiseTerms, ...]":
        """The raise set the engine prices this basis from, as values.

        **The one read the engine makes of a raise set.**  Resolved on READ
        rather than at construction: ``SalaryProfile.raises`` is
        ``lazy="select"``, so touching it here would move a caller's SELECT
        from the first paycheck priced to the moment a basis is built, and a
        basis is built at nine sites that did not ask for that.  Cached
        because the engine asks it once per prior payday when it replays a
        year's cumulatives, and converting the rows each time would be work
        for the same answer; a frozen dataclass
        admits ``cached_property`` because it writes the instance dict
        directly rather than through the refused ``__setattr__``.

        **Both arms go through** :func:`~app.services.salary_raises.terms_of`,
        so "always a tuple of ``RaiseTerms``" is a property of this value and
        not of its callers' care: a supplied set spelled with rows or other
        raise-shaped objects is converted too, and the engine below never
        prices anything but the value.  A second adversarial review of this
        step found the first draft converting only the default arm.

        Returns:
            :attr:`raise_terms` canonicalised when a caller supplied one, else
            the profile's rows converted.
        """
        return terms_of(
            self.profile.raises if self.raise_terms is None else self.raise_terms
        )

    @cached_property
    def _line_cadences(self) -> "dict[Any, _WalkedCadence | None]":
        """Each deduction's cadence, resolved ONCE and walked as it is asked.

        The paycheck engine's one read of a deduction's FREQUENCY (plan step
        salary:R15-b, rulings **R-SAL3** and **R-SAL29**): a line's
        :attr:`~app.models.paycheck_line.PaycheckLine
        .recurrence_rule` is read through :func:`~app.services.recurrence
        .recurrence_spec`, resolved against THIS calendar, and its
        occurrences placed on saved and projected paychecks alike through
        :func:`~app.services.recurrence.projected_occurrence_placements` --
        the walk every recurring definition is generated by, so a 24-per-year
        line skipping a month's third paycheck and a bill skipping it are
        the same rule read the same way.  Lines that resolve to the SAME
        cadence share one :class:`_WalkedCadence`, and a walk reaches only as
        far as it is asked (:data:`_REACH_PAST_ASK`): the engine prices any payday
        the calendar can name, in no fixed order -- a projection walks
        forward, a capped line's year-to-date replays backward -- and the
        memo grows to meet it.

        ``None`` for a line with no rule: every paycheck, R-SAL3's NULL.  Keyed
        by the deduction object itself because the engine prices duck-typed
        deductions in its tests (a fake need carry no id), and read through
        ``getattr`` for the same reason the sibling optional columns are.

        Cached on the basis as :attr:`raises` is: one basis prices every
        payday of one profile at one raise set, and a frozen dataclass admits
        ``cached_property`` because it writes the instance dict directly.

        Returns:
            ``{deduction: its walked cadence, or None}`` for every deduction
            on the profile.
        """
        walks: dict[ResolvedRecurrence, _WalkedCadence] = {}
        cadences: dict[Any, _WalkedCadence | None] = {}
        for deduction in self.profile.lines:
            # ``getattr`` for the reason the engine reads ``annual_cap`` and
            # ``target_account_id`` that way: a deduction-like duck type (a
            # test fake) may omit the optional attribute.
            rule = getattr(deduction, "recurrence_rule", None)
            if rule is None:
                cadences[deduction] = None
                continue
            resolved = resolve(recurrence_spec(rule), self.calendar)
            cadences[deduction] = walks.setdefault(resolved, _WalkedCadence(resolved))
        return cadences

    def line_applies_on(self, line, payday: date) -> bool:
        """Whether *line* is taken on the paycheck of *payday*.

        ``deduction_applies_on`` until plan step salary:R18-b, when the
        earning kinds began asking it too (ruling **R-SAL38**); a line of any
        kind is placed by its rule the same way.

        Args:
            line: One of this profile's payroll lines.
            payday: The day the paycheck arrives -- a payday on this calendar,
                saved or projected.

        Returns:
            ``True`` when the line has no rule (every paycheck) or when its
            rule's walk placed an occurrence on this paycheck.
        """
        cadence = self._line_cadences[line]
        return cadence is None or cadence.admits(self.calendar, payday)

    def base_pay_on(self, payday: date) -> BasePay:
        """Return what the salary pays on *payday*, with the two facts it divides.

        **The one spelling of base pay for the engine** (plan step
        **salary:X-av-2**, ruling **R-SAL66**; ledger row **SAL-569**).  The
        paycheck, the FICA wage-base cumulative and a capped line's
        year-to-date each spelled ``gross_per_paycheck(
        basis.annual_salary_on(payday), basis.periods_per_year)`` for
        themselves: three walks of one value, which ``CLAUDE.md`` rule 14
        counts as three homes however long they agree.  This method is the
        walk and all three read it.  It replaced both of those names.  The
        annual half keeps the reason ``annual_salary_on`` was written for
        (plan step salary:S3-f-1): one read of :attr:`raises`, so a supplied
        raise set reaches all three readers.

        **The count is the rhythm in force ON THE PAYDAY**, through
        :func:`~app.services.pay_calendar.cadence_on`, which reads the era
        whose planned payday the day stands for, in cash days.
        ``periods_per_year`` read
        :attr:`~app.services.pay_calendar.PayCalendar.cadence` -- the LATEST
        era's -- for every payday, so a paycheck paid under an earlier rhythm
        was divided, and annualised for withholding, by a count it was never
        paid at.  For an owner holding ONE era that era is both, so every such
        owner's paycheck is unchanged by construction.  The cadence rides on
        to :attr:`~app.services.paycheck_calculator.PeriodInfo.cadence` (ruling
        **R-SAL70**), which is what every reader converting the paycheck to
        a month or a year converts with.

        **Total, as the property it replaced was since plan step
        pay_calendar:C4-d** (ruling **R-PC45**): a calendar in hand carries at
        least one era, because ``pay_calendar.calendar_for`` refuses an owner
        with no ``budget.pay_schedule`` row rather than answering one with no
        rhythm.  Resolved per call and never at construction, so building a
        basis still reads nothing.

        Args:
            payday: The day the paycheck arrives -- saved, projected, or below
                the record.  The raises read only its year and month
                (:func:`~app.services.salary_raises.apply_raises`); the count
                reads the whole day, because an era takes effect on a day.

        Returns:
            The :class:`BasePay` of that payday.
        """
        return BasePay(
            annual_salary=apply_raises(
                self.profile.annual_salary, self.raises, payday,
            ),
            cadence=cadence_on(self.calendar, payday),
        )


def gross_per_paycheck(
    annual_salary: Decimal, periods_per_year: Decimal,
) -> Decimal:
    """Return what ONE paycheck pays, for a salary paid *periods_per_year* a year.

    **The one place the per-paycheck division is spelled, for its callers**:
    :attr:`BasePay.per_paycheck` -- which the paycheck engine reads for the
    paycheck and for the two cumulatives that replay prior paydays, through
    :meth:`PayrollBasis.base_pay_on` since plan step salary:X-av-2 -- and
    ``retirement_dashboard_service.compute_gap_net_biweekly``'s retirement-gap
    take-home basis.  Stated as MEMBERSHIP rather than a count, for the reason
    :mod:`app.services.pay_calendar` states it that way: a count of a census
    goes stale silently, and this one was wrong twice before it was written
    down -- *and a fourth member left at plan step salary:R14-b, which deleted
    ``investment_projection``'s percentage-deduction spelling outright, and a
    fifth at salary:S3-e-2, ``retirement_projection``'s employer-match basis,
    which this list went on naming until X-av-2.*

    **One site is deliberately NOT here and saying so is the point.**
    ``routes.salary.profiles`` sets a template's ``default_amount`` from
    :meth:`~app.services.pay_calendar.PayCadence.annual_to_per_paycheck`
    UNQUANTIZED, letting the ``Numeric(12, 2)`` column round it -- a money
    boundary outside :func:`~app.utils.money.round_money`.  It agrees with this
    function on every value today; it is a pre-existing smell this step did not
    open and does not fix, found by an adversarial review of X-aw.

    The first two callers each spelled the division themselves until plan step
    **balance:X-aw**, and the two RULES were
    measured answering differently on 5 of the owner's 63 saved periods
    (2027-01-14 .. 2027-03-11: ``$3,722.54`` under the engine's residue
    distribution against ``$3,722.53`` under the projection's plain division,
    at the same ``$96,785.88``), because only one of them apportioned a
    residue.  ``docs/audits/pylint-cleanup/deep-quality-hunt.md:721`` had
    recorded that divergence as an open design fork; this is its answer.

    **There WAS a second side, and plan step salary:R14-b deleted it rather
    than reconciling it.**  This paragraph read "it does NOT make the two
    sides agree on a paycheck": they shared the rounding rule and still
    differed in what they FED it, the engine passing ``apply_raises(...)`` for
    the period while ``investment_projection.adapt_deductions`` stamped the
    profile's RAW ``annual_salary`` -- a divergence of the whole raise, which
    is finding **D45**.  The contribution feed reads the engine's own
    breakdown now, so there is one side, and the agreement this function used
    to make partial is total by construction.

    **The gross is a RATE, not a share of a year** (ruling **balance:R-HW**).
    Every paycheck in one salary segment pays the same figure -- a segment that
    also ends where the owner's rhythm changes, since plan step salary:X-av-2
    divides each payday by its own era's count -- and the figure is a function
    of the salary and the cadence ALONE: no period, no period LIST, and
    therefore nothing a schedule extend can move.  That is finding **N-239**
    made unrepresentable rather than guarded -- the defect was that the engine
    decided which paychecks got a
    residue cent by counting the ``budget.pay_periods`` rows that happened to
    exist, so filling 2028 from 16 rows to 26 moved six settled paychecks by a
    cent each.

    **What it gives up, stated because it is a real cost**: a calendar year's
    grosses no longer sum to the annual salary exactly.  The bound is half a
    cent per paycheck -- ``0.005 x periods_per_year``, ``$0.13`` at a biweekly
    cadence and ``$1.83`` at the daily one the schedule legally admits -- and
    on the owner's own salary ``26 x $3,525.96 = $91,674.96``, four cents
    under.  That supersedes audit finding MED-05 / PA-07, which added the
    residue distribution to close exactly that gap.  The module docstring of
    :mod:`app.services.paycheck_calculator` carries the argument and the
    per-year figures; both are stated there once rather than in two places.

    **It takes the COUNT rather than a**
    :class:`~app.services.pay_calendar.PayCadence`, which would let it delegate
    to :meth:`~app.services.pay_calendar.PayCadence.annual_to_per_paycheck` and
    leave ONE division in the codebase.  The count is what
    :attr:`BasePay.periods_per_year` carries, and the two divisions
    answer different questions at different precisions besides: that one
    converts a rate and is deliberately NOT quantized (its module forbids
    quantizing at all), where this one is a money boundary.  *A second reason
    stood here until plan step salary:R14-b -- that widening
    ``investment_projection.AdaptedDeduction`` to hold a cadence would reach
    four services and their fakes -- and that namedtuple no longer exists.*

    **The stored input is the ANNUAL salary, and plan step salary:X-av flips
    it.** Under ruling R-HW the FACT is what one paycheck pays and the annual
    figure is the derivation; until that lands, the annual is what the profile
    holds and this is where it is converted.  So this function is the seam that
    flip edits, and the contract it states -- a constant rate per paycheck,
    independent of the schedule -- is the contract that survives it unchanged.

    Args:
        annual_salary: The salary in effect for the paycheck, post-raise, as
            :func:`~app.services.salary_raises.apply_raises` returns it.
            **Always post-raise since plan step salary:R14-b**, which retired
            the one caller that passed a profile's raw annual (D45, above).
            A ``Decimal`` at full precision, NOT coerced here: a
            ``float`` is refused by the division below with a ``TypeError``
            before :func:`~app.utils.money.round_money` is reached at all, and
            coercing through ``str()`` here would launder exactly the
            imprecision that refusal exists to keep out.
        periods_per_year: How many paychecks a year the paycheck's rhythm
            pays: off :attr:`BasePay.periods_per_year` for the engine -- the
            cadence of the ``budget.pay_eras`` era covering the payday
            (:func:`~app.services.pay_calendar.cadence_on`) -- and off the
            latest era's cadence for the retirement gap, whose paycheck lies
            in a year that era governs.

    Returns:
        The gross for one paycheck, quantized to the cent.
    """
    return round_money(annual_salary / periods_per_year)


__all__ = ["BasePay", "PayrollBasis", "gross_per_paycheck"]
