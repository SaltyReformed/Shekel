"""
Shekel Budget App -- a paycheck is priced at the rhythm IN FORCE on its payday.

Plan step **salary:X-av-2** (ruling **R-SAL66**; ledger row **SAL-569**).  An
owner's pay history is a list of ERAS -- "paid monthly from January, then
biweekly from July" -- and until this step the paycheck engine read the LATEST
era's paychecks-a-year for every payday: a paycheck paid under the monthly era
was divided by 26, and its withholding annualised by 26, as though it had been
a biweekly one.  The count is now read per payday through
:func:`~app.services.pay_calendar.cadence_on`, and the engine reads base pay
through ONE door, :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`,
for the paycheck and for both year-to-date replays.

Every figure here is MADE UP (ruling **balance:R-BAL132**): a $60,000 salary,
no raises, and the engine suite's own two-bracket tax fakes, so each expected
value is hand-computed in the case that asserts it.

Two schedules, both stated in full so the paydays are read rather than
derived by the code under test:

* **Monthly then biweekly** -- the 1st of each month January-June 2026, then
  every 14 days from Thursday 2026-07-02.  The biweekly era's first payday
  replaces the monthly era's 2026-07-01 (ruling **R-PC75**).
* **The displaced first payday** -- monthly January-August 2026; biweekly under
  the ``prior`` convention from Monday 2026-09-07, which is Labor Day, so that
  era's first paycheck is paid on Friday 2026-09-04, BEFORE its own
  ``effective_from``; then weekly from 2027-01-11.  Three eras so that the old
  reader (the latest era, weekly), the nominal-day reader
  (:func:`~app.services.pay_rhythm.era_covering`, monthly) and the cash-day
  reader this step uses (biweekly) give three different answers.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    PayCalendar,
    cadence_on,
    first_payday_of,
)
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, era_covering
from app.services.paycheck_calculator import calculate_paycheck
from app.services.paycheck_calculator._calendar_questions import (
    _get_cumulative_wages,
)
from app.services.payroll_basis import PayrollBasis
from tests.test_services.test_paycheck_calculator import (
    FakeBracket,
    FakeBracketSet,
    FakeDeduction,
    FakeFicaConfig,
    FakeProfile,
    FakeStateTaxConfig,
)

_NONE = BusinessDayShiftEnum.NONE

#: The monthly era: the 1st of every month, from 2026-01-01.
_MONTHLY = Era(effective_from=date(2026, 1, 1), rhythm=Rhythm(Monthly(1), _NONE))

#: The biweekly era that follows it, from Thursday 2026-07-02.
_BIWEEKLY = Era(
    effective_from=date(2026, 7, 2), rhythm=Rhythm(FixedDays(14), _NONE),
)

#: A biweekly era taking effect on Labor Day 2026 under ``prior``: its first
#: paycheck is paid the business day before, Friday 2026-09-04.
_BIWEEKLY_FROM_LABOR_DAY = Era(
    effective_from=date(2026, 9, 7),
    rhythm=Rhythm(FixedDays(14), BusinessDayShiftEnum.PRIOR),
)

#: A weekly era after it, on the biweekly era's own grid day 2027-01-11.
_WEEKLY = Era(effective_from=date(2027, 1, 11), rhythm=Rhythm(FixedDays(7), _NONE))


def _calendar(paydays, eras):
    """A calendar over exactly *paydays*, numbered 1.. in order."""
    return PayCalendar.from_paydays(
        [(index + 1, payday) for index, payday in enumerate(paydays)],
        eras,
        user_id=1,
        history_opens_on=None,
    )


def _monthly_then_biweekly():
    """Six monthly paydays, then thirteen biweekly ones through 2026-12-17."""
    return _calendar(
        [date(2026, month, 1) for month in range(1, 7)]
        + [date(2026, 7, 2) + timedelta(days=14 * step) for step in range(13)],
        (_MONTHLY, _BIWEEKLY),
    )


def _with_a_displaced_first_payday():
    """Monthly through August, biweekly from 2026-09-04, weekly from 2027-01-11."""
    return _calendar(
        [date(2026, month, 1) for month in range(1, 9)]
        + [date(2026, 9, 4)]
        + [date(2026, 9, 21) + timedelta(days=14 * step) for step in range(8)]
        + [date(2027, 1, 11) + timedelta(days=7 * step) for step in range(3)],
        (_MONTHLY, _BIWEEKLY_FROM_LABOR_DAY, _WEEKLY),
    )


def _period_on(calendar, payday):
    """The calendar's own period opening on *payday*."""
    matches = [period for period in calendar.periods if period.start_date == payday]
    assert len(matches) == 1, f"{payday} is not a payday of this calendar"
    return matches[0]


def _tax_configs(ss_wage_base="168600"):
    """The engine suite's two-bracket fakes: every figure below is hand-computable.

    Federal: a $15,000 standard deduction, 10% to $50,000, 22% above.  State:
    a flat 4.5% with no deduction.  FICA: 6.2% Social Security to
    *ss_wage_base*, 1.45% Medicare, the surtax far above every figure here.
    """
    return {
        "bracket_set": FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=[
                FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
                FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
            ],
        ),
        "state_config": FakeStateTaxConfig(flat_rate="0.045"),
        "fica_config": FakeFicaConfig(ss_wage_base=ss_wage_base),
    }


def _profile(lines=None):
    """A made-up $60,000 salary with no raises."""
    return FakeProfile(
        annual_salary=60000, lines=lines, created_at=date(2026, 1, 1),
    )


class TestCadenceOn:
    """The pay calendar answers the rhythm in force ON A DAY, not only the latest."""

    def test_one_era_answers_its_own_cadence_on_every_day(self):
        """An owner with one era reads the same count everywhere.

        Input: a single biweekly era; days below the record, on it, and
        projected two years past it.
        Expected: the latest-era cadence, at every day.
        Why: this is the property that makes the step change nothing for an
        owner who has never changed rhythm -- production's case.  The
        covering era IS the latest era, so the engine's divisor is the one it
        always was.
        """
        calendar = _calendar(
            [date(2026, 1, 2) + timedelta(days=14 * step) for step in range(26)],
            (Era(effective_from=date(2026, 1, 2), rhythm=Rhythm(FixedDays(14), _NONE)),),
        )
        for day in (date(2025, 6, 6), date(2026, 1, 2), date(2026, 7, 3),
                    date(2028, 12, 29)):
            assert cadence_on(calendar, day) == calendar.cadence, day
            assert cadence_on(calendar, day).periods_per_year == Decimal("26")

    def test_each_payday_reads_its_own_eras_count(self):
        """A monthly-era payday answers 12 under a later biweekly era.

        Input: monthly January-June 2026, biweekly from 2026-07-02.
        Expected: 12 on every monthly payday and on a day below the record
        (the earliest era runs backward, R-PC66); 26 on the biweekly era's
        first payday, on a later one, and on a projected day past the record.
        Why: the latest era's count -- what ``PayCalendar.cadence`` answers,
        asserted here as the control -- is 26, so a reader that ignored the
        day would answer 26 on the monthly paydays and fail this case.
        """
        calendar = _monthly_then_biweekly()
        assert calendar.cadence.periods_per_year == Decimal("26")

        expected = {
            date(2025, 12, 1): Decimal("12"),
            date(2026, 3, 1): Decimal("12"),
            date(2026, 6, 1): Decimal("12"),
            date(2026, 7, 2): Decimal("26"),
            date(2026, 12, 17): Decimal("26"),
            date(2027, 6, 3): Decimal("26"),
        }
        assert {
            day: cadence_on(calendar, day).periods_per_year for day in expected
        } == expected

    def test_a_displaced_first_payday_belongs_to_the_era_it_opens(self):
        """The paycheck an era pays BEFORE its effective day is that era's.

        Input: the three-era schedule; its biweekly era takes effect on Labor
        Day 2026-09-07 under ``prior``, so its first paycheck is 2026-09-04.
        Expected: 26 on 2026-09-04.
        Why: the calendar derives that payday under the biweekly era (its
        period runs 09-04 to 09-20), so its pay is a biweekly paycheck.  The
        two other readers are asserted beside it as the controls: the
        nominal-day reader ``era_covering`` answers the MONTHLY era for that
        day, and the latest era is WEEKLY -- so reading either one fails this
        case, and neither can pass it by coinciding with the right answer.
        """
        calendar = _with_a_displaced_first_payday()
        payday = date(2026, 9, 4)
        assert first_payday_of(_BIWEEKLY_FROM_LABOR_DAY) == payday

        assert cadence_on(calendar, payday).periods_per_year == Decimal("26")
        assert era_covering(calendar.eras, payday) == _MONTHLY
        assert calendar.cadence.periods_per_year == Decimal("52")


class TestAnEarlierRhythmsPaycheck:
    """The engine prices a payday at its OWN era's paychecks a year."""

    def test_a_monthly_paycheck_divides_and_annualises_by_twelve(self):
        """The monthly-era paycheck, line by line.

        Input: $60,000, the 2026-03-01 payday of the monthly-then-biweekly
        schedule, no lines.
        Expected, by hand:

        * base = gross = 60,000 / 12 = $5,000.00;
        * federal (Pub 15-T): 5,000 x 12 = 60,000, less the 15,000 standard
          deduction = 45,000 at 10% = 4,500 a year, / 12 = $375.00;
        * state: 5,000 x 12 x 4.5% = 2,700 a year, / 12 = $225.00;
        * Social Security 5,000 x 6.2% = $310.00; Medicare 1.45% = $72.50;
        * net 5,000 - 375 - 225 - 310 - 72.50 = $4,017.50.

        Why: the base alone would not catch a withholding path left on the
        latest count.  Annualised by 26 the same $5,000.00 is 130,000 a year,
        115,000 taxable, 50,000 x 10% + 65,000 x 22% = 19,300, / 26 =
        $742.31 federal -- so ``$375.00`` pins the annualiser independently
        of the divisor.  The state line is asserted but cannot tell the
        counts apart: a flat rate annualised and divided back by the same
        count is the same figure at any count.
        """
        calendar = _monthly_then_biweekly()
        paycheck = calculate_paycheck(
            PayrollBasis(_profile(), calendar),
            _period_on(calendar, date(2026, 3, 1)),
            _tax_configs(),
        )

        assert paycheck.earnings.annual_salary == Decimal("60000")
        assert paycheck.earnings.base_biweekly == Decimal("5000.00")
        assert paycheck.earnings.gross_biweekly == Decimal("5000.00")
        assert paycheck.taxes.federal == Decimal("375.00")
        assert paycheck.taxes.state == Decimal("225.00")
        assert paycheck.taxes.social_security == Decimal("310.00")
        assert paycheck.taxes.medicare == Decimal("72.50")
        assert paycheck.earnings.net_pay == Decimal("4017.50")

    def test_the_later_rhythms_paycheck_is_the_biweekly_one(self):
        """The biweekly era's paycheck is priced at 26, as it always was.

        Input: $60,000, the 2026-07-16 payday.
        Expected: base 60,000 / 26 = 2,307.6923 -> $2,307.69; federal
        2,307.69 x 26 = 59,999.94, less 15,000 = 44,999.94 at 10% =
        4,499.994, / 26 = 173.0767 -> $173.08.
        Why: the other half of the firing pair -- the monthly case above
        would pass for an engine that divided EVERY payday by 12.
        """
        calendar = _monthly_then_biweekly()
        paycheck = calculate_paycheck(
            PayrollBasis(_profile(), calendar),
            _period_on(calendar, date(2026, 7, 16)),
            _tax_configs(),
        )

        assert paycheck.earnings.base_biweekly == Decimal("2307.69")
        assert paycheck.taxes.federal == Decimal("173.08")

    def test_the_wage_cumulative_sums_each_payday_at_its_own_pay(self):
        """The FICA year-to-date counts the monthly paychecks at $5,000.00.

        Input: $60,000; the year-to-date before 2026-07-16, and the Social
        Security on 2026-07-02 under a made-up $31,000 wage base.
        Expected: six monthly paychecks at $5,000.00 plus the 2026-07-02 one
        at $2,307.69 = $32,307.69.  On 2026-07-02 the year-to-date is
        $30,000.00, so $1,000.00 of wage base remains and the tax is
        1,000 x 6.2% = $62.00, not 2,307.69 x 6.2% = $143.08.
        Why: the year-to-date REPLAYS each earlier payday's base, and the
        replay is its own reader of the base: at the latest count it summed
        seven paychecks at $2,307.69 = $16,153.83 and reached the wage base
        months late, which overstates net.
        """
        calendar = _monthly_then_biweekly()
        basis = PayrollBasis(_profile(), calendar)

        assert _get_cumulative_wages(
            basis, _period_on(calendar, date(2026, 7, 16)),
        ) == Decimal("32307.69")

        paycheck = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 7, 2)),
            _tax_configs(ss_wage_base="31000"),
        )
        assert paycheck.taxes.social_security == Decimal("62.00")

    def test_a_capped_lines_replay_counts_each_payday_at_its_own_pay(self):
        """A percentage line's cap is reached on the paychecks that reached it.

        Input: $60,000 with a 10% pre-tax line capped at a made-up $3,000 a
        year.
        Expected: $500.00 on each monthly paycheck (10% of $5,000.00), so the
        June paycheck brings the year to exactly $3,000.00 and the 2026-07-02
        line is $0.00.
        Why: the cap's year-to-date replays each earlier payday's line
        through its own read of the base.  At the latest count it replayed
        six paychecks of 10% x $2,307.69 = $230.77, $1,384.62 in all, and
        charged the line again in July -- $230.77 over a cap the owner had
        already met.
        """
        line = FakeDeduction(
            name="Retirement", amount="0.10", calc_method="percentage",
            annual_cap="3000",
        )
        calendar = _monthly_then_biweekly()
        basis = PayrollBasis(_profile(lines=[line]), calendar)

        june = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 6, 1)), _tax_configs(),
        )
        july = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 7, 2)), _tax_configs(),
        )

        assert [d.amount for d in june.deductions.pre_tax] == [Decimal("500.00")]
        assert [d.amount for d in july.deductions.pre_tax] == [Decimal("0.00")]

    def test_a_displaced_first_payday_is_priced_at_its_new_rhythm(self):
        """The paycheck an era pays before its effective day is a paycheck of that era.

        Input: $60,000 on the three-era schedule.
        Expected: 2026-08-01 (monthly) $5,000.00; 2026-09-04 (the biweekly
        era's displaced first payday) 60,000 / 26 = $2,307.69; 2027-01-11
        (weekly) 60,000 / 52 = 1,153.846 -> $1,153.85.
        Why: 2026-09-04 is where the three readers part (see
        :class:`TestCadenceOn`): the nominal-day reader would price it at
        $5,000.00 and the latest era at $1,153.85.
        """
        calendar = _with_a_displaced_first_payday()
        basis = PayrollBasis(_profile(), calendar)

        bases = {
            payday: calculate_paycheck(
                basis, _period_on(calendar, payday), _tax_configs(),
            ).earnings.base_biweekly
            for payday in (date(2026, 8, 1), date(2026, 9, 4), date(2027, 1, 11))
        }
        assert bases == {
            date(2026, 8, 1): Decimal("5000.00"),
            date(2026, 9, 4): Decimal("2307.69"),
            date(2027, 1, 11): Decimal("1153.85"),
        }
