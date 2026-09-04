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

Pure: no Flask, no ORM import, no clock, no database.  The profile is typed
loosely on purpose (see the class).
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services.pay_calendar import PayCalendar
from app.utils.money import round_money


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

    Attributes:
        profile: The ``SalaryProfile`` -- read for the annual salary, the
            raises, the deductions and the W-4 inputs.  Typed loosely because
            every consumer reads attributes rather than the ORM class, and
            because this module must not import a model (the engine below it is
            pure).  The test suite prices duck-typed profiles through the same
            door for that reason.
        calendar: The owner's whole
            :class:`~app.services.pay_calendar.PayCalendar` -- the payday set
            every calendar question the engine asks is answered from, and the
            cadence :attr:`periods_per_year` divides by.  ONE value because
            they are one fact: the cadence is a field of the calendar, so a
            paycheck cannot be priced at one rhythm and placed in a month
            counted at another.
    """

    profile: object
    calendar: PayCalendar

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks this owner receives in a year.

        Forwarded rather than re-derived so the engine's arithmetic reads as
        one name instead of a three-hop attribute chain, and so there is a
        single place to look when asking where its denominator comes from.

        **Resolved on READ rather than at construction**, which is what let an
        owner with no pay cadence reach a producer that never prices a
        paycheck.  :attr:`~app.services.pay_calendar.PayCalendar.cadence`
        REFUSED such an owner -- there is no honest default for how often
        somebody is paid -- and before plan step **balance:X-bh-1**
        ``tax_report_service.compute_tax_report`` resolved the cadence
        conditionally so the Taxes tab would not 500 for an owner whose report
        is all zeros anyway.

        **That whole class of guard is GONE at plan step pay_calendar:C4-d**
        (ruling **R-PC45**), including the two this paragraph used to name as
        surviving -- ``balance_at/_inputs.py`` and
        ``investment_dashboard_service/_context.py``, which kept
        ``cadence_days is not None`` tests because what they feed takes a raw
        :class:`~app.services.pay_calendar.PayCadence` and not a basis, so
        nothing there resolved lazily.  Both are DELETED rather than satisfied:
        the owner they guarded against holds no ``budget.pay_schedule`` row,
        and ``pay_calendar.calendar_for`` refuses that owner outright instead of
        answering an empty calendar with no cadence.  A calendar in hand
        therefore carries a cadence, this read is TOTAL, and the lazy
        resolution above buys ordering freedom rather than safety.

        Returns:
            The paycheck count as an integral ``Decimal``.
        """
        return self.calendar.cadence.periods_per_year


def gross_per_paycheck(
    annual_salary: Decimal, periods_per_year: Decimal,
) -> Decimal:
    """Return what ONE paycheck pays, for a salary paid *periods_per_year* a year.

    **The one place the per-paycheck division is spelled, for FOUR callers**:
    ``paycheck_calculator.calculate_paycheck`` (and the two cumulatives that
    replay prior periods for it), ``investment_projection``'s percentage
    deductions, ``retirement_projection``'s employer-match salary basis and
    ``retirement_dashboard_service``'s retirement-gap take-home basis.  Stated
    as MEMBERSHIP rather than a count, for the reason
    :mod:`app.services.pay_calendar` states it that way: a count of a census
    goes stale silently, and this one was wrong twice before it was written
    down.

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

    **It does NOT make the two sides agree on a paycheck, and an adversarial
    review corrected a first draft that claimed it did.**  They now share the
    rounding rule and still differ in what they FEED it: the engine passes
    ``apply_raises(...)`` for the period, while
    ``investment_projection.adapt_deductions`` stamps the profile's RAW
    ``annual_salary``.  For an owner with an applicable raise those differ by
    the raise and not by a cent -- that is finding **D45**, measured at
    ``$137.51`` a year of understated employer contribution, and it is owned
    elsewhere.  What this function makes structural is that neither side can
    round differently from the other.

    **The gross is a RATE, not a share of a year** (ruling **balance:R-HW**).
    Every paycheck in one salary segment pays the same figure, and the figure is
    a function of the salary and the cadence ALONE: no period, no period LIST,
    and therefore nothing a schedule extend can move.  That is finding **N-239**
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
    ``investment_projection.AdaptedDeduction`` carries -- stamped from the one
    cadence its adapter is handed -- and widening that namedtuple to hold the
    cadence would reach four services and their fakes to spare one expression.
    The two divisions answer different questions at different precisions
    besides: that one converts a rate and is deliberately NOT quantized (its
    module forbids quantizing at all), where this one is a money boundary.

    **The stored input is the ANNUAL salary, and plan step salary:X-av flips
    it.** Under ruling R-HW the FACT is what one paycheck pays and the annual
    figure is the derivation; until that lands, the annual is what the profile
    holds and this is where it is converted.  So this function is the seam that
    flip edits, and the contract it states -- a constant rate per paycheck,
    independent of the schedule -- is the contract that survives it unchanged.

    Args:
        annual_salary: The salary in effect for the paycheck, post-raise, as
            :func:`~app.services.salary_raises.apply_raises` returns it -- or,
            from ``investment_projection``, the profile's raw annual (D45,
            above).  A ``Decimal`` at full precision, NOT coerced here: a
            ``float`` is refused by the division below with a ``TypeError``
            before :func:`~app.utils.money.round_money` is reached at all, and
            coercing through ``str()`` here would launder exactly the
            imprecision that refusal exists to keep out.
        periods_per_year: How many paychecks the owner receives in a year,
            off :attr:`PayrollBasis.periods_per_year` -- which derives it from
            ``budget.pay_schedule.cadence_days`` and from nothing else.

    Returns:
        The gross for one paycheck, quantized to the cent.
    """
    return round_money(annual_salary / periods_per_year)


__all__ = ["PayrollBasis", "gross_per_paycheck"]
