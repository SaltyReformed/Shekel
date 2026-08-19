"""The paycheck count derives from the owner's cadence, and from nothing else.

Plan step **R-F16**, closing finding **F-16**.  Until it,
``salary.salary_profiles.pay_periods_per_year`` was a SECOND stored answer to
"how often am I paid" beside ``budget.pay_schedule.cadence_days``, and no door
validated one against the other.  Measured with the real engine on the
developer's own ``$91,675`` salary, a profile reading 26 beside a 7-day cadence
modelled ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the
year's paychecks summing to 200% of salary.

**What these tests hold, and why each is here rather than folded into another
file.**  The engine's own suite
(``test_paycheck_calculator.py``) prices ONE paycheck and pins its arithmetic;
none of it varies the CADENCE, so none of it could see the defect.  The
property F-16 is about is a year-scale one -- the count the engine divides by
and the number of paychecks the owner actually receives are the same number --
and it is only visible across a whole year at more than one rhythm.  That is
the axis every case below varies, which is the shape
``docs/plans/lessons.md`` records as the one a baseline cannot see when no
case varies it.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app.extensions import db as _db
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.pay_calendar import PayCadence, PayCalendar
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year

#: Every cadence whose derived count the dropped dropdown could express, plus
#: the two it could not.  ``(cadence_days, paychecks_a_year)``; the second
#: value is ``round(365.2425 / cadence_days)`` stated independently, so a
#: change to the derivation fails here rather than agreeing with itself.
_CADENCES = [
    (7, 52),     # weekly
    (14, 26),    # biweekly -- production, and the old column's default
    (15, 24),    # the semi-monthly COUNT, on nominal paydays (the R-F16 ruling)
    (30, 12),    # monthly
    (365, 1),    # annual (a contractor)
]


def _calendar(cadence_days, count, user_id=1, first=date(2026, 1, 1)):
    """A derived calendar of *count* paydays spaced *cadence_days* apart."""
    return PayCalendar.from_paydays(
        paydays=[
            (i + 1, first + timedelta(days=cadence_days * i))
            for i in range(count)
        ],
        cadence_days=cadence_days,
        user_id=user_id,
    )


class TestTheCountIsTheSchedule:
    """The engine's divisor equals the paydays the owner actually receives."""

    @pytest.mark.parametrize("cadence_days,expected", _CADENCES)
    def test_the_derived_count_is_the_paydays_in_a_year(
        self, cadence_days, expected,
    ):
        """``periods_per_year`` equals the paydays a year of that cadence holds.

        Input: each authorable rhythm.
        Expected: the derived count, and a calendar of that many paydays that
        does not overrun the year.
        Why: this is the identity F-16 broke.  The engine divides an annual
        salary by the count and the schedule pays it out once per payday, so
        the two being the same number is what makes a year's paychecks add up
        to a year's salary.  It was two independently writable columns.
        """
        assert PayCadence(cadence_days=cadence_days).periods_per_year == expected
        calendar = _calendar(cadence_days, expected)
        # The last payday of the run still opens inside the year it started in
        # (or, for the 15-day walk, within a day of it) -- a count that
        # overshot would put a paycheck the owner never receives in the
        # denominator.
        span = (calendar.periods[-1].start_date - date(2026, 1, 1)).days
        assert span <= 365

    @pytest.mark.parametrize("cadence_days,count", _CADENCES)
    def test_a_years_paychecks_sum_to_a_years_salary(
        self, app, db, seed_user, cadence_days, count,
    ):
        """The year's grosses total the annual salary EXACTLY, at every rhythm.

        Input: a $91,675 raise-free profile -- the developer's own salary --
        projected over one full year at each authorable cadence.
        Expected: the grosses sum to $91,675.00 to the cent, whatever the
        rhythm.
        Why: **this is the money property finding F-16 destroyed**, and it is
        an identity rather than a figure, so it holds at every cadence without
        a per-cadence expected value to get wrong.  Before R-F16 the engine
        divided by a stored 26 while the schedule paid out ``count`` times, so
        the year totalled ``count / 26`` of the salary: 200% at a 7-day
        cadence, 46% at 30 days.  Measured on this exact salary at plan step
        R-F16.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name=f"Cadence {cadence_days}",
                annual_salary=Decimal("91675.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.flush()

            calendar = _calendar(cadence_days, count, user_id=user_id)
            periods = list(calendar.saved())
            configs = load_tax_configs_for_year(user_id, profile, 2026)

            breakdowns = paycheck_calculator.project_salary(
                PayrollBasis(profile, calendar.cadence), periods, configs,
            )

            total = sum(b.earnings.gross_biweekly for b in breakdowns)
            assert total == Decimal("91675.00"), (
                f"a {cadence_days}-day cadence paid {total} of a "
                f"$91,675.00 salary over {len(breakdowns)} paychecks"
            )

    def test_the_same_profile_prices_differently_at_two_rhythms(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: the cadence actually moves the answer.

        Input: ONE profile, priced at 7 days and at 14 days.
        Expected: the weekly gross is half the biweekly one, exactly.
        Why: every assertion above would still pass if the engine ignored the
        cadence and hardcoded 26 for a 14-day-cadence fixture -- the suite
        would be green and the defect back.  This is the case that fails if
        the divisor stops being a function of the argument, and it is the
        assertion the whole pre-R-F16 suite lacked: nothing varied this axis,
        so nothing could see a count that was not the schedule's.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name="One profile, two rhythms",
                annual_salary=Decimal("91675.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.flush()
            configs = load_tax_configs_for_year(user_id, profile, 2026)

            weekly = _calendar(7, 52, user_id=user_id)
            biweekly = _calendar(14, 26, user_id=user_id)

            weekly_gross = paycheck_calculator.calculate_paycheck(
                PayrollBasis(profile, weekly.cadence),
                weekly.periods[0], list(weekly.saved()), configs,
            ).earnings.gross_biweekly
            biweekly_gross = paycheck_calculator.calculate_paycheck(
                PayrollBasis(profile, biweekly.cadence),
                biweekly.periods[0], list(biweekly.saved()), configs,
            ).earnings.gross_biweekly

            # $91,675 / 52 = $1,763.0 vs / 26 = $3,526.0 (residue-reconciled,
            # so each is its group's floor or floor + a cent).
            assert biweekly_gross - weekly_gross * 2 <= Decimal("0.02")
            assert weekly_gross * 2 - biweekly_gross <= Decimal("0.02")
            assert weekly_gross < biweekly_gross


class TestTheSecondCountIsGone:
    """No door can state a paycheck count beside the owner's cadence."""

    def test_the_column_does_not_exist(self, app):
        """``salary_profiles`` holds no ``pay_periods_per_year``.

        Input: the live test database's schema, which is built by running the
        migrations.
        Expected: the column is absent.
        Why: the ORM model is not the oracle here -- ``build_test_template.py``
        runs MIGRATIONS, so a model edit with no migration leaves the schema
        untouched and every ORM-driven test still passes.  This asks the
        database (recorded as a trap in this project's own operating notes).
        """
        with app.app_context():
            columns = {
                c["name"] for c in
                inspect(_db.engine).get_columns(
                    "salary_profiles", schema="salary",
                )
            }
            assert "pay_periods_per_year" not in columns
            assert "annual_salary" in columns  # the census reached the table

    def test_the_columns_check_constraint_is_gone_too(self, app):
        """``ck_salary_profiles_positive_periods`` is dropped with its column.

        Why: a CHECK left behind on a dropped column is not merely untidy --
        it is the residue that makes a downgrade fail halfway, and the
        migration's own downgrade re-creates it by name.  Asked of
        ``pg_constraint`` so it is the database answering.
        """
        with app.app_context():
            found = _db.session.execute(text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'ck_salary_profiles_positive_periods'"
            )).fetchall()
            assert found == []

    def test_the_engine_cannot_be_asked_without_a_cadence(self, app, seed_user):
        """``calculate_paycheck`` refuses a bare profile.

        Input: a ``SalaryProfile`` passed where a :class:`PayrollBasis` belongs.
        Expected: it raises rather than pricing anything.
        Why: the count is REQUIRED and undefaultable by construction -- a
        missing rhythm fails at the call, where a defaulted one would model a
        weekly-paid owner's income at half its true value and say nothing.
        This is the same argument the read-pass ruling makes for
        ``BalanceContext``.
        """
        with app.app_context():
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1, name="Bare",
                annual_salary=Decimal("50000.00"), state_code="NC",
            )
            calendar = _calendar(14, 26, user_id=seed_user["user"].id)
            with pytest.raises(AttributeError):
                paycheck_calculator.calculate_paycheck(
                    profile, calendar.periods[0], list(calendar.saved()), {},
                )
