"""How often an owner is paid, and the unit conversions that rest on it.

Plan step **R7a-2a** (``docs/plans/implementation_plan_recurrence_redesign.md``
section 4a).  ``app.utils.money.PAY_PERIODS_PER_YEAR = Decimal("26")`` was a
module constant while ``budget.pay_schedule.cadence_days`` is user-selectable
1..365, so every monthly-equivalent figure on ``/savings``, the Recurring
surface and ``/retirement`` was wrong for any owner who is not paid biweekly --
a weekly-paid owner's ``$100`` per-paycheck bill reported ``$216.67`` a month
against a true ``$433.33``.

**This is THE producer of "how many paychecks in a year", for every side of
the application.**  The fact here is ``cadence_days``, and the count is derived
from it in this module and nowhere else -- it is not a column, because a
derivation stored beside its own input is a cache and a cache drifts the moment
one writer moves one side alone (the argument
:mod:`app.services.recurrence._resolution` makes for the two-axis recurrence
values, applied to this one).

**It became the only one at plan step R-F16, and until then it was not.**
``salary.salary_profiles.pay_periods_per_year`` was a SECOND, stored,
user-selected count -- a 12 / 24 / 26 / 52 dropdown on the salary form -- and
nothing tied it to ``cadence_days``.  It was the DIVISOR the paycheck engine
turned an annual salary into one paycheck with, while this module's conversions
multiplied that paycheck back up.  While both read 26 the two errors CANCELLED;
plan step R7a-2a fixed this side and made the mismatch visible, and finding
**F-16** is what it exposed: measured with the real engine on a ``$91,675``
salary, a profile reading 26 beside a 7-day cadence gave ``$15,279.20`` of
monthly gross against a true ``$7,639.60``, and the year's paychecks summed to
200% of salary.  R-F16 dropped the column; the engine takes a
:class:`PayCadence` (bound to its profile as
:class:`app.services.payroll_basis.PayrollBasis`) and divides by the
count derived here.  A salary profile's paycheck recurs every pay period by
definition, so there was never a second count to hold.

Why the ROUNDED integer, and not the exact rate
-----------------------------------------------

``365.2425 / 14`` is ``26.0888``, and the exact rate was proposed first.  It
was measured against the developer's own rhythm (anchor ``period_index 0`` =
2026-03-26) and ruled against on 2026-08-05::

    paydays per CALENDAR year:  2016-2025 all 26,  2026 = 27,  2027-2036 all 26
    rolling-12-month count, every day of 2026-2027:  26 on 655 of 731 days
                                                     27 on  75 of 731 days

The 27-paycheck year is real -- 26 paychecks span 364 days, so the payday
calendar slips ~1.25 days a year and catches an extra payday every ~11 years
-- but it is a calendar-BOUNDARY artifact, and a forward-looking rolling year
holds 26 about 90% of the time.  The exact rate is asymptotically correct,
matches no window the developer budgets against, and would have shifted every
displayed figure ``+0.341%`` on migration day (``+$25.91/mo`` on the live
every-period set) for no gained truth.  Ledger rows **F-4** and **F-5** carry
what the rounded integer gives up.

**Counting the owner's actual paydays was the third option and is the worst.**
It is the truest answer and the least stable one: the figure would then move as
the pay schedule is extended, which is finding **N-239**'s defect (a period's
gross moving by a cent as 2028 fills from 14 periods to 26) and recurrence row
**D10**'s class.  This derivation is a function of a fact the owner STATED, not
of how far the schedule happens to have been generated.

The rule also covers plan step R8's WEEK unit with no second rule:
``round(365.2425 / 7) = 52``.

**And it covers the forward HORIZONS at plan step R-F17.**  Three surfaces
label a window in months and then resolve it in pay periods -- the account
pages' balance chips, their "Interest, next 12 mo" chip and the grid's range
buttons -- and each held its own hardcoded ``months x 26 / 12``.  They read
:meth:`PayCadence.paychecks_within` now, so the count of paychecks in a span is
derived here for the horizons exactly as it already was for the money.  Ledger
row **F-17**, ruling **R-R31**.

Why the conversions live on the value
-------------------------------------

Six call sites spelled the biweekly-to-monthly factor inline as
``x * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``, which
``app.utils.money``'s own docstring names as the thing to avoid ("any future
26/12 inlining is a regression of D6-05").  Naming each conversion once puts
the factor in one place per DIRECTION.

**One direction is still spelled inline and it is deliberate**:
``obligations_aggregator`` writes
``amount * units_per_year / (interval_n * MONTHS_PER_YEAR)`` rather than
calling a method here, and plan step R7a-2b measured why: that form divides
ONCE, by an exact integer, where dividing the amount by the interval FIRST --
``per_paycheck_to_monthly(amount / n)`` -- rounds twice.  (At ``n = 1`` the two
are byte-identical; the cost is only in the interval.)  It is a genuine fifth
direction rather than an oversight, and the one that must not be wrapped.

**Each keeps the sequential order, and that is an accuracy claim rather than a
style one.**  ``periods_per_year`` is an integral ``Decimal``, so ``x * ppy``
is exact and ``(x * ppy) / MONTHS_PER_YEAR`` rounds ONCE.  Pre-computing the
ratio -- ``x * (ppy / MONTHS_PER_YEAR)`` -- rounds twice, because ``26 / 12``
is inexact at any precision.  ``recurring_view`` held the two-rounding form
until this step.

**Nothing here quantizes**, which is the contract every consumer already
states: :func:`app.services.savings_goal_service.resolve_goal_target`
("intermediate results are NOT quantized") and
``obligations_aggregator.committed_monthly`` (full precision until one
``round_money`` at the boundary).  A conversion that rounded would put a second
rounding point inside a chain built to have one.

Pure: no Flask, no ORM, no clock, no database.  The one door that reads an
owner's cadence out of the table is :func:`~._loader.cadence_for`.
"""
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.utils.money import MONTHS_PER_YEAR

from ._derive import validate_cadence

#: Days in the mean Gregorian year -- 365 + 1/4 - 1/100 + 1/400.
#:
#: The numerator of the periods-per-year derivation.  Named rather than
#: inlined because it is the one place the leap-year rule enters this
#: calculation, and a reader has to be able to tell it from a plain 365.
DAYS_PER_YEAR = Decimal("365.2425")

#: The quantum :attr:`PayCadence.periods_per_year` rounds to: whole paychecks.
#:
#: ``ROUND_HALF_UP`` rather than Decimal's banker's default, for auditability
#: rather than for arithmetic: no cadence in 1..365 produces an exact half, so
#: the two modes agree on every reachable input.  (``DAYS_PER_YEAR / c = k +
#: 0.5`` requires ``c = 730485 / (1000 * (2k + 1))``, and ``730485`` is odd, so
#: it is divisible by neither 1000 nor 2 and no integer ``c`` satisfies it.)
#: The margin is thin enough to be worth a number: the closest any cadence
#: comes is 146 days, whose fraction is ``0.50166``.  Stating the mode means
#: the answer does not depend on the ambient decimal context.
_WHOLE_PAYCHECKS = Decimal("1")


@dataclass(frozen=True)
class PayCadence:
    """How often one owner is paid, and the conversions that follow from it.

    Frozen and tiny on purpose: it is a SMALLER fact than
    :class:`~._calendar.PayCalendar`, and most of its consumers need nothing
    else.  Making it a property of the calendar instead would force
    ``retirement_gap_calculator`` -- a pure function with no owner in scope --
    to load 61 payday rows to divide by a number.  The calendar exposes
    :attr:`~._calendar.PayCalendar.cadence`, so a caller that already holds one
    never builds a second, and there is still exactly one derivation.

    Attributes:
        cadence_days: Days between the owner's paydays, from
            ``budget.pay_schedule.cadence_days``.  The ONLY fact here;
            everything else is derived from it.  Validated at construction
            against the same 1..365 bound
            :func:`~._derive.derive_periods` holds a calendar's cadence to --
            through :func:`~._derive.validate_cadence` itself, so the two
            cannot part company.
    """

    cadence_days: int

    def __post_init__(self) -> None:
        """Refuse a cadence ``ck_pay_schedule_cadence_range`` would refuse.

        The check is load-bearing HERE and not merely repeated from
        :func:`~._derive.derive_periods`, because this value has a door that
        does not go through a calendar: ``pay_schedule_service.resolve_cadence``
        falls back for a schedule-row-less owner to
        ``(end_date - start_date).days + 1`` off the last period, which
        ``ck_pay_periods_date_order`` bounds below and NOTHING bounds above
        (plan finding **P8**).  A hand-written period spanning two years would
        otherwise answer "half a paycheck a year" and misstate every monthly
        equivalent on the page.

        Raises:
            PayCalendarError: The value is not a plain ``int`` (a ``bool``
                included) or falls outside 1..365.
        """
        validate_cadence(self.cadence_days)

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks a year this cadence produces.

        ``round(365.2425 / cadence_days)``, the derivation this module's
        docstring argues for: biweekly -> 26, weekly -> 52, a monthly cadence
        -> 12, and plan step R8's WEEK unit for free.

        Recomputed per call rather than pinned at construction.  It is one
        division and one quantize over a value that cannot change (the
        dataclass is frozen), so a stored copy would be a second representation
        of one fact for no measurable gain -- and it would have to be excluded
        from equality, the way
        :attr:`~._calendar.PayCalendar.periods` is, to stop two equal cadences
        comparing unequal.

        Returns:
            The paycheck count as an integral ``Decimal`` -- a ``Decimal``
            rather than an ``int`` because every consumer divides money by it,
            and an ``int`` there would invite a ``float`` division somewhere
            downstream.  At least 1 for every cadence in the domain
            (``365.2425 / 365`` rounds to 1).
        """
        return (DAYS_PER_YEAR / self.cadence_days).quantize(
            _WHOLE_PAYCHECKS, rounding=ROUND_HALF_UP,
        )

    def per_paycheck_to_monthly(self, per_paycheck: Decimal) -> Decimal:
        """Re-express a per-PAYCHECK rate as a per-MONTH rate.

        Args:
            per_paycheck: An amount attributable to one paycheck, at full
                precision.

        Returns:
            The same commitment per month, NOT quantized -- the caller rounds
            at its own aggregation boundary.
        """
        return self._times_paychecks_per_month(per_paycheck)

    def monthly_to_per_paycheck(self, monthly: Decimal) -> Decimal:
        """Re-express a per-MONTH rate as a per-PAYCHECK rate.

        The exact inverse of :meth:`per_paycheck_to_monthly`, and the Recurring
        surface's Monthly / Per-paycheck toggle is its one caller: the toggle
        re-expresses one committed figure in a second unit and must never open
        a second money path.

        Args:
            monthly: A monthly commitment, at full precision.

        Returns:
            The share attributable to one paycheck, NOT quantized.
        """
        return monthly * MONTHS_PER_YEAR / self.periods_per_year

    def annual_to_per_paycheck(self, annual: Decimal) -> Decimal:
        """Spread an ANNUAL figure evenly across the year's paychecks.

        What a contribution limit becomes as a per-paycheck transfer, and what
        an account's remaining limit headroom is worth per paycheck.

        Args:
            annual: A yearly total, at full precision.

        Returns:
            The per-paycheck share, NOT quantized.
        """
        return annual / self.periods_per_year

    def months_to_paychecks(self, months: Decimal) -> Decimal:
        """Re-express a SPAN of months as a span of paychecks.

        Shares its arithmetic with :meth:`per_paycheck_to_monthly` and answers
        a different question, which is why it has its own name: that one
        converts a RATE between units, this one converts a DURATION.  Calling
        the rate conversion here would read as though a number of months were
        an amount of money.  Two honest names, ONE expression -- both delegate
        to :meth:`_times_paychecks_per_month`, so the shared arithmetic is
        shared rather than written twice.

        Two callers, and they want the ratio at different precisions.  The
        emergency-fund footer's "paychecks covered" reads it RAW, because it is
        measured from the same ratio as its months and years so each of the
        three is quantized exactly once (ruling **R-CS**, finding **N-120**).
        :meth:`paychecks_within` floors it to a whole paycheck, because a
        forward horizon resolves to a pay period and there is no half period to
        land on.  Neither rounds here: the caller's question decides.

        Args:
            months: A duration in months, unrounded.

        Returns:
            The same duration in paychecks, NOT quantized.
        """
        return self._times_paychecks_per_month(months)

    def paychecks_within(self, months: int) -> int:
        """Return how many of this owner's paychecks arrive within *months*.

        The rule behind every forward window this application labels in MONTHS
        and then resolves in PAY PERIODS.  There are FIVE, and an accurate
        census took two adversarial reviews: the balance chips' "3 months" /
        "6 months" / "1 year", the "Interest, next 12 mo" chip, the grid's
        6M / 1Y / 2Y range buttons, the mobile Plan tab's window, and the
        dashboard pulse chart -- whose canvas announces "the next six months"
        to a screen reader while a ``_CHART_HORIZON_PERIODS = 13`` decided what
        it drew.  Ledger row **F-17** names the first two; the developer
        widened the step to the grid, and BOTH reviews found the last two by
        re-grepping the row's own predicate rather than the diff.  All five
        counted a hardcoded 6 / 13 / 26 / 52 until plan step **R-F17**, which
        is ledger row **F-17**: those numbers are ``months x 26 / 12``, so the
        label told the truth for a biweekly owner and for nobody else.  At a
        weekly cadence "1 year" reached 26 x 7 = 182 days and said a year; at a
        monthly one it reached 780 days and said the same.

        **Ruling R-R31 (developer, 2026-08-19): the horizon is the LAST WHOLE
        PAYCHECK that ARRIVES within the span.**  Paydays are exactly
        ``cadence_days`` apart, so that count is
        ``floor(months x DAYS_PER_YEAR / (MONTHS_PER_YEAR x cadence_days))`` --
        a division against the CADENCE ITSELF, not against
        :attr:`periods_per_year`.

        **Deriving it through the rounded annual count instead was the first
        implementation, and an adversarial review measured it wrong.**
        ``floor(months x periods_per_year / 12)`` rounds twice, and the two
        forms disagree on 384 of the 1,460 cadence-by-horizon cases -- always
        overshooting by one period, never undershooting.  In **74** of those it
        OFFERS a horizon the owner has no paycheck inside at all, which is the
        state this ruling's second half exists to refuse: at ``cadence_days =
        243`` it offered "6 months" pointing at a paycheck arriving 60 days
        AFTER the day that label names, which is row F-17's own defect at
        greater magnitude than the one it replaced.  Every common cadence is
        untouched by the correction -- 7, 14, 15, 28 and 30 all answer
        identically, including the developer's own 6 / 13 / 26 / 52.

        **Why the fraction FLOORS rather than rounds**, which is the rest of
        the ruling and was chosen on measurement.  Over every legal cadence
        (1..365) crossed with the 3 / 6 / 12 / 24-month spans the two disagree
        on 388 of 1,460 cases; scoring each by the MEAN ABSOLUTE distance, in
        days, between the resolved period's END and the day the label names --
        swept over all twelve opening months and every phase within a period --
        the floored answer is closer in 387 and the two are equal in the last
        (``cadence_days = 1`` at six months, 14 days of error each way).  The
        metric is stated because it decides the tie: counted per case rather
        than aggregated, that last one splits 6-6 instead.

        **Zero is a real answer and callers must not clamp it.**  An owner paid
        less often than every 92 days receives no paycheck at all inside three
        months, and one paid less often than every 183 days receives none
        inside six.  The surfaces then OFFER no such horizon (ruling R-R31's
        second half): the pay period is this application's finest forward
        resolution, so a chip that resolved to offset zero would publish the
        balance at the end of a months-long current period under a label naming
        a shorter span.  Twelve months can never be zero -- ``365.2425 / 365``
        still clears 1 -- so the "1 year" chip and the "Interest, next 12 mo"
        window it shares are always answerable.

        **It is NOT equal to** :attr:`periods_per_year` **at twelve months**,
        and the first draft of this class claimed it was: at ``cadence_days =
        31`` eleven paychecks arrive within a year (``11 x 31 = 341``) where
        the rounded annual count is 12, because the twelfth lands seven days
        late.  The two chips that share a twelve-month span agree because they
        BOTH call this method with the same constant, not because either equals
        that attribute.

        Args:
            months: A forward span in whole months.  Non-negative.

        Returns:
            The number of this owner's paychecks that fall within that span, as
            a plain ``int`` -- a period-INDEX offset rather than money, which is
            why it is not a ``Decimal`` like every conversion above it.  Read as
            an OFFSET from the current period by the chips (the last paycheck
            within the span) and as a COUNT of columns by the grid and the Plan
            tab (that many paychecks starting at the current one); the two
            readings differ by one pay period and each call site says which it
            takes.
        """
        return int(
            (
                Decimal(months) * DAYS_PER_YEAR
                / (MONTHS_PER_YEAR * self.cadence_days)
            ).to_integral_value(rounding=ROUND_FLOOR)
        )

    def _times_paychecks_per_month(self, value: Decimal) -> Decimal:
        """Multiply *value* by paychecks-per-month, in the sequential order.

        The one expression behind :meth:`per_paycheck_to_monthly` and
        :meth:`months_to_paychecks`, which are the same multiplication applied
        to a rate and to a duration.  Private because the two PUBLIC names say
        which of those a caller means, and this one cannot.

        ``(value * ppy) / MONTHS_PER_YEAR`` rather than
        ``value * (ppy / MONTHS_PER_YEAR)``: the multiply is exact for any
        realistic figure, so the sequential form rounds once where the
        pre-computed ratio rounds twice.

        Args:
            value: A rate per paycheck, or a duration in months.

        Returns:
            *value* times paychecks-per-month, NOT quantized.
        """
        return value * self.periods_per_year / MONTHS_PER_YEAR


__all__ = ["DAYS_PER_YEAR", "PayCadence"]
