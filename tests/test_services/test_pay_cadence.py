"""How often an owner is paid, as a value (plan step R7a-2a).

``app.utils.money.PAY_PERIODS_PER_YEAR = Decimal("26")`` was a module constant
standing in for a fact that varies per owner: ``pay_schedule.cadence_days`` is
user-selectable 1..365.  Nine files read it, so every monthly-equivalent figure
on ``/savings``, the Recurring surface and ``/retirement`` was wrong for
anyone not paid biweekly.  This proves the value that
replaced it.

**Written at the edges, because the middle is where the constant was right.**
A test at 14 days cannot tell the derivation from the constant -- both answer
26 -- so every case here either uses a cadence the constant would have got
wrong, or pins a boundary of the derivation's own domain.

**The one derivation, reachable through two doors.**  A caller holding a whole
:class:`~app.services.pay_calendar.PayCalendar` reads
:attr:`~app.services.pay_calendar.PayCalendar.cadence`; one that needs the
cadence alone calls :func:`~app.services.pay_calendar.cadence_for` and never
loads a payday.  ``TestBothDoorsReachOneDerivation`` is what stops those two
becoming the seventh and eighth answers to a question this arc exists to give
one answer to (ledger row **P6**).

Clock discipline (``.claude/rules/testing.md``): nothing here reads a clock.
Every input is a literal, so these pass identically under
``TZ=Pacific/Kiritimati``.
"""

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.pay_calendar import (
    DAYS_PER_YEAR,
    MAX_CADENCE_DAYS,
    MIN_CADENCE_DAYS,
    PayCadence,
    PayCalendar,
    PayCalendarError,
    cadence_for,
    calendar_for,
)
from app.services import pay_schedule_service
from app.utils.dates import add_months
from app.utils.money import MONTHS_PER_YEAR, round_money


class TestThePaycheckCountIsDerivedFromTheCadence:
    """``round(365.2425 / cadence_days)``, ruled 2026-08-05."""

    @pytest.mark.parametrize(
        "cadence_days,expected,why",
        [
            (7, "52", "weekly -- 365.2425 / 7 = 52.1775"),
            (14, "26", "biweekly -- 365.2425 / 14 = 26.0889, the old constant"),
            (15, "24", "semi-monthly-ish -- 365.2425 / 15 = 24.3495"),
            (30, "12", "a monthly cadence -- 365.2425 / 30 = 12.1748"),
            (1, "365", "the floor: paid daily"),
            (365, "1", "the ceiling: 365.2425 / 365 = 1.0007, one a year"),
        ],
    )
    def test_the_named_cadences(self, cadence_days, expected, why):
        """Each cadence a real schedule uses answers its hand-computed count.

        Only ONE of these six agrees with the retired constant, which is the
        measure of what the constant cost: at every other cadence the app was
        stating a paycheck count the owner does not receive.
        """
        assert PayCadence(cadence_days=cadence_days).periods_per_year == (
            Decimal(expected)
        ), why

    def test_the_derivation_rounds_rather_than_truncates(self):
        """13.0444 and 12.5945 both answer 13.

        Named for what it can actually show.  It does NOT separate half-up
        from banker's rounding -- ``test_no_cadence_in_the_domain_lands_on_an
        _exact_half`` proves no input can -- but it does separate rounding
        from TRUNCATION, which would answer 12 for the 29-day case.  The pair
        straddles the .5 boundary from below (28 days) and above (29 days).
        """
        assert PayCadence(cadence_days=28).periods_per_year == Decimal("13")
        assert PayCadence(cadence_days=29).periods_per_year == Decimal("13")

    def test_no_cadence_in_the_domain_lands_on_an_exact_half(self):
        """The rounding MODE can never decide an answer, and that is provable.

        ``365.2425 / c = k + 0.5`` requires ``c = 730485 / (1000 * (2k + 1))``,
        and 730,485 is odd, so no integer ``c`` satisfies it.  Asserted by
        exhaustion over the whole 1..365 domain rather than by the argument
        alone -- an adversarial review found the first statement of that
        algebra off by 100x, with the conclusion intact, which is exactly why
        the exhaustive form is the assertion and the argument is the comment.
        The closest any cadence comes is 146 days, at ``0.50166``.
        """
        halves = [
            days for days in range(MIN_CADENCE_DAYS, MAX_CADENCE_DAYS + 1)
            if (DAYS_PER_YEAR / days) % 1 == Decimal("0.5")
        ]
        assert halves == []

    def test_the_count_is_positive_across_the_whole_domain(self):
        """Every legal cadence yields at least one paycheck a year.

        A zero would make every ``annual_to_per_paycheck`` a division by zero
        and every monthly equivalent infinite; the ceiling (365 days) is the
        closest the domain comes, at exactly 1.
        """
        counts = [
            PayCadence(cadence_days=days).periods_per_year
            for days in range(MIN_CADENCE_DAYS, MAX_CADENCE_DAYS + 1)
        ]
        assert min(counts) == Decimal("1")
        assert max(counts) == Decimal("365")

    def test_the_count_is_a_decimal_not_an_int(self):
        """Every consumer divides money by it, so it must be a ``Decimal``.

        An ``int`` would work until the first ``float`` crept into a division
        downstream, which is the leak ``app.utils.money`` exists to close.
        """
        assert isinstance(
            PayCadence(cadence_days=14).periods_per_year, Decimal,
        )


class TestTheCadenceBoundIsEnforcedHere:
    """The value refuses what ``ck_pay_schedule_cadence_range`` refuses.

    Load-bearing rather than duplicated: ``resolve_cadence`` falls back for a
    schedule-row-less owner to the last period's stored LENGTH, which
    ``ck_pay_periods_date_order`` bounds below and nothing bounds above (plan
    finding **P8**).  A hand-written two-year period would otherwise answer
    "half a paycheck a year" and misstate every monthly figure on the page.
    """

    @pytest.mark.parametrize("bad", [0, -1, MAX_CADENCE_DAYS + 1, 10_000])
    def test_a_cadence_outside_the_domain_is_refused(self, bad):
        """Outside 1..365 there is no honest paycheck count to derive."""
        with pytest.raises(PayCalendarError, match="cadence_days must be"):
            PayCadence(cadence_days=bad)

    def test_a_bool_is_refused_even_though_it_is_an_int(self):
        """``True`` would otherwise pass as a one-day cadence, 365 a year."""
        with pytest.raises(PayCalendarError, match="plain int"):
            PayCadence(cadence_days=True)

    def test_a_float_is_refused(self):
        """``14.0`` reads as biweekly and is not an ``int``.

        The derivation would happily divide by it and return a plausible 26,
        which is worse than an error: it would mean the type discipline the
        rest of the package holds stops at this value.
        """
        with pytest.raises(PayCalendarError, match="plain int"):
            PayCadence(cadence_days=14.0)

    def test_the_value_is_frozen(self):
        """A cadence resolved once per request cannot be moved mid-pass."""
        cadence = PayCadence(cadence_days=14)
        with pytest.raises(FrozenInstanceError):
            cadence.cadence_days = 7


class TestTheRateAndSpanConversions:
    """Each names the units it moves between, and none of them rounds.

    ``paychecks_within`` is the one member of the family that DOES round, and
    it lives in its own class below for that reason.
    """

    def test_per_paycheck_to_monthly_doubles_between_biweekly_and_weekly(self):
        """$100 a paycheck is $216.67/mo biweekly and $433.33/mo weekly.

        Hand-computed: 100 * 26 / 12 = 216.666... and 100 * 52 / 12 =
        433.333...  The pair is the control -- a single assertion would pass
        against a hardcoded 26 or a hardcoded 52.
        """
        assert PayCadence(cadence_days=14).per_paycheck_to_monthly(
            Decimal("100"),
        ).quantize(Decimal("0.01")) == Decimal("216.67")
        assert PayCadence(cadence_days=7).per_paycheck_to_monthly(
            Decimal("100"),
        ).quantize(Decimal("0.01")) == Decimal("433.33")

    def test_monthly_to_per_paycheck_is_the_exact_inverse(self):
        """Round-tripping a figure through both conversions returns it.

        Not an identity for free: the two are separate expressions, and one
        written with the reciprocal factor instead of the reciprocal ORDER
        would drift here.  Checked at three cadences so a coincidence at one
        cannot carry it.
        """
        for days in (7, 14, 30):
            cadence = PayCadence(cadence_days=days)
            monthly = cadence.per_paycheck_to_monthly(Decimal("1234.56"))
            assert cadence.monthly_to_per_paycheck(monthly) == Decimal(
                "1234.56",
            )

    def test_annual_to_per_paycheck_spreads_a_limit_over_the_year(self):
        """A $7,800 annual limit is $300 a paycheck biweekly, $150 weekly.

        Hand-computed: 7800 / 26 = 300 exactly, 7800 / 52 = 150 exactly.  The
        weekly owner making 52 contributions at the biweekly figure would
        overshoot the cap by $7,800 over the year, which is what the retired
        constant told the investment route to suggest.
        """
        assert PayCadence(cadence_days=14).annual_to_per_paycheck(
            Decimal("7800"),
        ) == Decimal("300")
        assert PayCadence(cadence_days=7).annual_to_per_paycheck(
            Decimal("7800"),
        ) == Decimal("150")

    def test_months_to_paychecks_converts_a_SPAN_not_a_rate(self):
        """6 months of runway is 13 paychecks biweekly, 26 weekly.

        Hand-computed: 6 * 26 / 12 = 13 and 6 * 52 / 12 = 26.  It shares its
        arithmetic with ``per_paycheck_to_monthly`` and answers a different
        question, which is why it has its own name; this pins that the
        emergency-fund footer's "paychecks covered" uses the owner's rhythm.
        """
        assert PayCadence(cadence_days=14).months_to_paychecks(
            Decimal("6"),
        ) == Decimal("13")
        assert PayCadence(cadence_days=7).months_to_paychecks(
            Decimal("6"),
        ) == Decimal("26")

    def test_nothing_here_quantizes(self):
        """Full precision out, so a caller keeps its single rounding point.

        ``obligations_aggregator.committed_monthly`` sums per-template figures
        and rounds ONCE at the boundary; a conversion that rounded would put a
        second rounding inside that sum and reintroduce the per-penny drift
        the contract exists to prevent.  $100 biweekly is 216.666..., not
        216.67.
        """
        monthly = PayCadence(cadence_days=14).per_paycheck_to_monthly(
            Decimal("100"),
        )
        assert monthly != Decimal("216.67")
        assert monthly.quantize(Decimal("0.01")) == Decimal("216.67")

    def test_the_conversion_rounds_once_where_a_ratio_would_round_twice(self):
        """``(x * ppy) / 12`` is exact where ``x * (ppy / 12)`` is not.

        The multiply is exact for any realistic amount, so the sequential form
        has ONE inexact step; pre-computing ``26 / 12`` makes it two, because
        that ratio does not terminate in base 10 at any precision.
        ``recurring_view`` held the two-rounding form until plan step R7a-2a.

        Three cents a paycheck is $0.065 a month EXACTLY, and the pre-computed
        ratio cannot say so -- it answers ``0.06500000000000000000000000001``.
        A contrived amount on purpose: the divergence is one unit in the last
        place, so a realistic figure hides it and the next test measures what
        that means for a displayed cent.
        """
        cadence = PayCadence(cadence_days=14)
        assert cadence.per_paycheck_to_monthly(Decimal("0.03")) == Decimal(
            "0.065",
        )
        assert Decimal("0.03") * (
            cadence.periods_per_year / MONTHS_PER_YEAR
        ) != Decimal("0.065")

    def test_no_displayed_cent_moves_from_the_reordering(self):
        """Measured, not argued: the two forms agree at every cent to $20,000.

        Plan step R7a-2a moved ``recurring_view``'s per-paycheck column from
        the two-rounding form to the one-rounding form.  That is a change to
        published money, so the claim "nothing moves" is measured rather than
        argued.  **16,000,000 comparisons, 0 moved cents**: every two-decimal
        amount from ``$0.01`` to ``$20,000.00``, in both directions, at
        cadences 7 / 14 / 15 / 30.  What the reordering buys is exactness, not
        a different answer.

        Swept at a coarse step here so the suite stays fast; the exhaustive
        run is recorded in the commit message and reproducible from this
        docstring.
        """
        for days in (7, 14, 15, 30):
            cadence = PayCadence(cadence_days=days)
            ratio = cadence.periods_per_year / MONTHS_PER_YEAR
            inverse = MONTHS_PER_YEAR / cadence.periods_per_year
            for cents in range(1, 200_000, 37):
                amount = Decimal(cents) / 100
                assert round_money(
                    cadence.per_paycheck_to_monthly(amount),
                ) == round_money(amount * ratio), (
                    f"the reordering moved a cent at {amount}, cadence {days}"
                )
                assert round_money(
                    cadence.monthly_to_per_paycheck(amount),
                ) == round_money(amount * inverse), (
                    f"the inverse moved a cent at {amount}, cadence {days}"
                )


class TestAHorizonNamedInMonthsResolvesToPaychecks:
    """``paychecks_within``: plan step R-F17, ledger row F-17, ruling R-R31.

    Every forward window this application LABELS in months -- the account
    pages' "3 months" / "6 months" / "1 year" balance chips, their "Interest,
    next 12 mo" chip, and the grid's 6M / 1Y / 2Y range buttons -- resolved to
    a hardcoded 6 / 13 / 26 / 52 pay periods, which is ``months x 26 / 12``.
    Written at the edges for the reason this module's docstring gives: at 14
    days the derivation and the constants agree, so only another cadence can
    tell them apart.
    """

    def test_biweekly_returns_the_constants_it_replaced(self):
        """6 / 13 / 26 / 52 -- so the cutover moved no displayed figure.

        The developer is paid biweekly, and these four numbers are what
        ``HORIZON_OFFSETS``, ``_ONE_YEAR_PERIODS``, ``PLAN_WINDOW_PERIODS``,
        ``_CHART_HORIZON_PERIODS`` and the grid's ``range_options`` literal all
        held before plan step R-F17.  This is the regression pin behind the
        step's "no figure moved" claim.  (A first draft added "and was also
        measured account by account against a production clone"; an adversarial
        review pointed out a reader cannot check a measurement with no artifact
        in the tree, so that claim lives in the commit message with its output.)
        """
        cadence = PayCadence(cadence_days=14)

        assert [cadence.paychecks_within(m) for m in (3, 6, 12, 24)] == [
            6, 13, 26, 52,
        ]

    @pytest.mark.parametrize("cadence_days, months, expected, why", [
        (7, 12, 52, "weekly: a year is 52 paychecks, where 26 was six months"),
        (7, 6, 26, "weekly: six months is 26, where 13 was three"),
        (30, 12, 12, "monthly: a year is 12, where 26 was over two years"),
        (30, 3, 3, "monthly: a quarter is 3 paychecks"),
        (15, 12, 24, "semi-monthly: 24 paychecks a year"),
        (1, 12, 365, "daily: the count IS the derived paycheck count"),
    ])
    def test_the_label_stops_lying_at_other_cadences(
        self, cadence_days, months, expected, why,
    ):
        """Hand-computed ``months * round(365.2425 / cadence) / 12``.

        Each case is a cadence the replaced constant got wrong.  ``why``
        names what the old hardcoded number claimed instead.
        """
        assert PayCadence(cadence_days=cadence_days).paychecks_within(
            months,
        ) == expected, why

    def test_it_floors_the_fraction_rather_than_rounding_it(self):
        """The biweekly 3-month horizon is 6.5 paychecks and resolves to 6.

        The ONE place the rule is visible on the developer's own cadence, and
        the whole content of ruling **R-R31**: ``3 * 26 / 12`` is exactly 6.5,
        so flooring and rounding disagree, and rounding would have moved the
        chip out one pay period.  The exact value is asserted beside the
        floored one so the tie is on the record rather than implied.
        """
        cadence = PayCadence(cadence_days=14)

        assert cadence.months_to_paychecks(Decimal("3")) == Decimal("6.5")
        assert cadence.paychecks_within(3) == 6

    def test_flooring_lands_closer_to_the_day_the_label_names(self):
        """Measured over every phase of the year: 6 beats 7, and by how much.

        Ruling **R-R31** chose the floor on this measurement rather than on
        taste.  The chip shows the balance at the END of the resolved pay
        period, so the honest error is that end minus the day three months
        after the reader's own day.  Swept over all twelve start months and
        all fourteen positions within a biweekly period (168 cases):

        * offset 6 lands within 8 days either side of the mark;
        * offset 7 is NEVER early and is up to 22 days late.

        A period end after the day the label names overstates how far the
        projection reached, which is row F-17's own defect wearing a smaller
        number.
        """
        cadence = PayCadence(cadence_days=14)
        # **DERIVED from the value under test, not hardcoded.**  A first draft
        # wrote the literals 6 and 7 here and an adversarial review measured
        # that it then exercised no production code at all: it survived both
        # reverting the whole derivation to the biweekly constants AND
        # inverting ROUND_FLOOR to ROUND_HALF_UP -- the very rule it claims to
        # justify.  Taking the offsets from the method means a rounding change
        # moves the swept offsets and this case moves with it.
        floor_offset = cadence.paychecks_within(3)
        round_offset = floor_offset + 1
        assert (floor_offset, round_offset) == (6, 7), (
            "the biweekly 3-month horizon is the one case where flooring and "
            "rounding disagree on the developer's own cadence; if it no longer "
            "is, this measurement is about something else"
        )

        floored, rounded = [], []
        for start_month in range(1, 13):
            opening = date(2026, start_month, 1)
            for phase in range(cadence.cadence_days):
                as_of = opening + timedelta(days=phase)
                label_day = add_months(as_of, 3)
                for offset, errors in (
                    (floor_offset, floored), (round_offset, rounded),
                ):
                    period_end = opening + timedelta(
                        days=cadence.cadence_days * (offset + 1) - 1,
                    )
                    errors.append((period_end - label_day).days)

        assert max(abs(e) for e in floored) == 8
        assert min(rounded) == 6
        assert max(rounded) == 22

    @pytest.mark.parametrize("cadence_days, months, why", [
        (92, 3, "the shortest cadence reaching no paycheck in a quarter"),
        (365, 3, "an annually paid owner has no paycheck in a quarter"),
        (183, 6, "the shortest cadence reaching none in half a year"),
        (365, 6, "nor in half a year"),
    ])
    def test_zero_is_a_real_answer_and_is_not_clamped(
        self, cadence_days, months, why,
    ):
        """A span no paycheck reaches answers 0, for the caller to interpret.

        Ruling **R-R31**'s second half: the surfaces then OFFER no such
        horizon.  Clamping to 1 here would hand every caller a pay period the
        span does not contain, which is exactly the overstatement row F-17
        records -- so the honest zero is produced and each caller decides
        (the chips and the grid buttons omit it; the mobile Plan tab, which
        must render something, shows the paycheck the owner is in, because
        that one already spans the whole window).
        """
        assert PayCadence(cadence_days=cadence_days).paychecks_within(
            months,
        ) == 0, why

    def test_a_twelve_month_span_is_never_zero(self):
        """No cadence in 1..365 makes a year hold zero paychecks.

        ``365.2425 / 365`` still clears 1, so the "1 year" balance chip and the
        "Interest, next 12 mo" window it shares are answerable for every owner
        -- which is what lets both read this method with one constant and no
        absent-horizon branch.
        """
        for cadence_days in range(MIN_CADENCE_DAYS, MAX_CADENCE_DAYS + 1):
            assert PayCadence(cadence_days=cadence_days).paychecks_within(12) >= 1

    def test_a_year_of_paychecks_is_NOT_the_rounded_annual_count(self):
        """``paychecks_within(12)`` is not :attr:`periods_per_year`, by design.

        The first implementation derived the count THROUGH that attribute --
        ``floor(months * periods_per_year / 12)`` -- and an adversarial review
        measured the double rounding wrong: it disagrees with the exact arrival
        test on 384 of 1,460 cases, always overshooting, and in 74 of them it
        OFFERS a horizon the owner has no paycheck inside at all.  The
        developer ruled for the exact test on 2026-08-19 (**R-R31**) knowing
        this identity is what it costs.

        At a 31-day cadence eleven paychecks arrive within a year -- ``11 * 31
        = 341`` days, where the twelfth lands on day 372, seven days late --
        while the ROUNDED annual count is 12.  The two chips that share a
        twelve-month span still agree, because both call this method with the
        same constant rather than because either equals that attribute.
        """
        cadence = PayCadence(cadence_days=31)

        assert cadence.paychecks_within(12) == 11
        assert cadence.periods_per_year == Decimal("12")

    def test_it_answers_an_int_because_it_is_an_index_offset(self):
        """A period-INDEX offset, not money -- unlike every conversion above.

        ``periods_per_year`` is deliberately a ``Decimal`` because its
        consumers divide money by it.  This one is added to a
        ``period_index``, so an ``int`` is what a caller can use without
        converting, and a ``Decimal`` here would invite one.
        """
        answer = PayCadence(cadence_days=14).paychecks_within(12)

        assert isinstance(answer, int)
        # It is usable as an index offset WITHOUT conversion, which is the
        # whole claim.  (`assert not isinstance(answer, bool)` stood here and
        # an adversarial review pointed out it cannot fail: the body is
        # `int(...)`, which never returns a bool.)
        assert list(range(40))[answer] == 26


class TestBothDoorsReachOneDerivation:
    """A calendar's cadence and the loader's cadence are the same value."""

    def test_the_calendar_answers_its_own_cadence(self):
        """``PayCalendar.cadence`` is built from the calendar's own days."""
        calendar = PayCalendar.from_paydays(
            paydays=(), cadence_days=7, user_id=1,
            history_opens_on=None,
        )
        assert calendar.cadence == PayCadence(cadence_days=7)
        assert calendar.cadence.periods_per_year == Decimal("52")

    def test_the_loader_and_the_calendar_agree(self, app, seed_user, seed_periods):
        """Both doors answer the same value for one owner, from one column.

        The control against the two becoming a seventh and eighth answer to
        "how often is this owner paid" -- the shape ledger row **P6** counts
        for "which period contains this date".
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert cadence_for(user_id) == calendar_for(user_id).cadence

    def test_the_loader_reads_the_stored_schedule_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Changing the stored cadence changes the answer, with no period touched.

        Proves the loader reads ``budget.pay_schedule.cadence_days`` rather
        than inferring from the periods: the payday rows are untouched here and
        the answer still moves from 26 to 52.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert cadence_for(user_id).periods_per_year == Decimal("26")

            pay_schedule_service.upsert_schedule(user_id, 7)
            db.session.flush()

            assert cadence_for(user_id).periods_per_year == Decimal("52")


class TestAnAbsentCadenceIsRefusedRatherThanDefaulted:
    """No stated cadence -> no answer.  Never an assumed 26.

    The disposition ruled for this step: assuming biweekly is precisely the
    retired constant, and it would render a weekly-paid owner every monthly
    figure at half its true value with nothing on the page saying so.
    Unreachable for a registered owner since plan step X-ad-a, which made
    registration write the ``budget.pay_schedule`` row.

    **Where the refusal LIVES moved at plan step ``pay_calendar:C4-d``**
    (ruling **R-PC45**), and only that.  It was ``PayCalendar.cadence``, which
    raised when the calendar carried no cadence; a calendar cannot carry that
    now, so the refusal sits at the two loader doors -- the only place that
    knows whether the owner has a ``budget.pay_schedule`` row at all -- and
    ``PayCalendar.cadence`` is total.  The disposition above is unchanged: the
    owner is refused, never defaulted.
    """

    def test_an_EMPTY_calendar_still_answers_its_cadence(self):
        """The case that inverted at plan step C4-d, kept so the inversion is graded.

        This asserted ``PayCalendar.cadence`` REFUSED an empty calendar, which
        was true while an empty one could carry ``cadence_days=None``.  An
        empty calendar is now an owner who HAS a schedule row and zero paydays
        -- ``pay_period_admin.reset_pay_periods`` passes through exactly that
        -- and their cadence is a stated fact, so refusing it would be refusing
        something the owner has told the application.

        A ``cadence_days`` that is not 14 is deliberate: 14 is what most of
        this file's fixtures carry, so a totality check at 14 could pass
        against a producer reading somebody else's schedule.
        """
        empty = PayCalendar.from_paydays(
            paydays=(), cadence_days=7, user_id=42,
            history_opens_on=None,
        )

        assert empty.periods == ()
        assert empty.cadence == PayCadence(cadence_days=7)

    def test_the_loader_refuses_an_owner_with_no_schedule_and_no_periods(
        self, app, bare_user,
    ):
        """The companion shape: nothing to read a cadence from at all.

        ``bare_user`` rather than ``seed_user``, and the difference is the
        whole case: ``seed_user`` carries a bootstrap pay period (its default
        account's NOT NULL anchor needs one), so ``resolve_cadence``'s legacy
        fallback measured that period's length and answered 14 -- this test
        would then pass its ``raises`` for no reason at all.  Production's
        user 2, the companion role, is the live instance of the state actually
        under test: zero periods, no ``budget.pay_schedule`` row.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            # The precondition this case rests on, asserted rather than
            # assumed -- an earlier draft used ``seed_user`` and the
            # assertion is what caught it.
            assert pay_schedule_service.resolve_cadence(user_id) is None

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                cadence_for(user_id)

    def test_the_CALENDAR_door_refuses_that_owner_TOO(
        self, app, bare_user,
    ):
        """The other door, and the disagreement plan step C4-d closed.

        ``cadence_for`` has refused this owner since plan step R7a-2a.
        ``calendar_for`` ANSWERED them -- an empty ``PayCalendar`` carrying
        ``cadence_days=None`` -- so the refusal was not avoided, it was
        DEFERRED to whichever method first read the cadence.  That is why
        ``/savings`` showed the repair page for this owner while ``/grid`` and
        the account detail page each showed a blank render of their own: three
        answers to one state.

        Graded on the two doors together rather than on ``calendar_for``
        alone: what the ruling decided is that they AGREE, and a case that
        watched only the door that changed would still pass if the other one
        stopped refusing.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            assert pay_schedule_service.resolve_schedule(user_id) is None

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                calendar_for(user_id)
            with pytest.raises(PayCalendarError, match="no pay calendar"):
                cadence_for(user_id)
