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
)
from app.services import pay_schedule_service
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


class TestTheFourConversions:
    """Each names the units it moves between, and none of them rounds."""

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


class TestBothDoorsReachOneDerivation:
    """A calendar's cadence and the loader's cadence are the same value."""

    def test_the_calendar_answers_its_own_cadence(self):
        """``PayCalendar.cadence`` is built from the calendar's own days."""
        calendar = PayCalendar.from_paydays(
            paydays=(), cadence_days=7, user_id=1,
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
            from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
                calendar_for as _calendar_for,
            )
            assert cadence_for(user_id) == _calendar_for(user_id).cadence

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
    """

    def test_the_calendar_refuses_when_it_holds_no_cadence(self):
        """An empty calendar has no cadence, and says so rather than guessing."""
        empty = PayCalendar.from_paydays(
            paydays=(), cadence_days=None, user_id=42,
        )
        with pytest.raises(PayCalendarError, match="no pay cadence"):
            _ = empty.cadence

    def test_the_loader_refuses_an_owner_with_no_schedule_and_no_periods(
        self, app, bare_user,
    ):
        """The companion shape: nothing to read a cadence from at all.

        ``bare_user`` rather than ``seed_user``, and the difference is the
        whole case: ``seed_user`` carries a bootstrap pay period (its default
        account's NOT NULL anchor needs one), so ``resolve_cadence``'s legacy
        fallback measures that period's length and answers 14 -- this test
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

            with pytest.raises(PayCalendarError, match="no pay cadence"):
                cadence_for(user_id)
