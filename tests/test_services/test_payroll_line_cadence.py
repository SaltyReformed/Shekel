"""``app.services.payroll_line_cadence`` -- a payroll deduction's cadence, worded.

Plan step **salary:R15-b**.  The salary page's Frequency cell states each
line's cadence through the recurrence package's one phrase producer; a line
with no rule reads *every paycheck* (R-SAL3); a rule that cannot be
described is refused rather than misstated.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind
from app.services import payroll_line_cadence
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceResolutionError
from tests._test_helpers import (
    derived_calendar,
    make_line_cadence_rule,
    make_salary_profile,
)


def _line(seed_user, name="Health"):
    """A flat pre-tax line on its own profile (named after the line), flushed."""
    profile = make_salary_profile(seed_user, db.session, name=f"{name} Job")
    db.session.flush()
    line = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one().id,
        calc_method_id=db.session.query(CalcMethod).filter_by(name="flat").one().id,
        name=name, amount=Decimal("100.00"),
    )
    db.session.add(line)
    db.session.flush()
    return line


@pytest.mark.usefixtures("seed_periods")
class TestThePhrase:
    """One producer, three answers on the developer's data."""

    def test_no_rule_reads_every_paycheck_with_or_without_a_calendar(self, app, db, seed_user):
        """A line with no rule needs no calendar to be worded."""
        with app.app_context():
            line = _line(seed_user)
            assert payroll_line_cadence.cadence_phrase(line, None) == "Every paycheck"
            assert payroll_line_cadence.cadence_phrase(
                line, calendar_for(seed_user["user"].id),
            ) == "Every paycheck"

    def test_the_two_migrated_shapes_read_as_the_recurring_surface_would(
        self, app, db, seed_user,
    ):
        """The 24 and 12 shapes are worded by ``describe``, not by a second vocabulary."""
        with app.app_context():
            twenty_four = _line(seed_user, "Health")
            make_line_cadence_rule(db.session, twenty_four, 24)
            twelve = _line(seed_user, "Transit")
            make_line_cadence_rule(db.session, twelve, 12)
            calendar = calendar_for(seed_user["user"].id)
            assert payroll_line_cadence.cadence_phrase(twenty_four, calendar) == (
                "Every paycheck (at most 2 a month)"
            )
            assert payroll_line_cadence.cadence_phrase(twelve, calendar) == "Monthly (first paycheck)"
            assert payroll_line_cadence.cadence_phrases([twenty_four, twelve], calendar) == {
                twenty_four.id: "Every paycheck (at most 2 a month)",
                twelve.id: "Monthly (first paycheck)",
            }

    def test_no_rule_is_worded_as_the_walk_words_an_every_paycheck_rule(self, app, db, seed_user):
        """``EVERY_PAYCHECK`` is what ``describe`` says of a bare every-paycheck rule.

        Two spellings of one phrase (``payroll_line_cadence.EVERY_PAYCHECK`` and
        ``_describe``'s stem) would drift silently; this pins them to each
        other, so a re-wording of the recurrence package's phrase fails here
        rather than leaving the cell reading differently for a line with no
        rule and a line with the rule that means the same.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum
        from app.services.recurrence import RecurrenceSpec, describe, resolve

        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)
            bare = resolve(
                RecurrenceSpec(
                    user_id=seed_user["user"].id, unit=RecurrenceUnitEnum.PERIOD,
                    starts_on=calendar.opening_bound(),
                ),
                calendar,
            )
            assert payroll_line_cadence.EVERY_PAYCHECK == describe(bare).cadence

    def test_a_rule_with_no_calendar_is_the_callers_error(self, app, db, seed_user):
        """A line with a rule and no calendar to resolve it against is refused, by name."""
        with app.app_context():
            line = _line(seed_user)
            make_line_cadence_rule(db.session, line, 24)
            with pytest.raises(ValueError, match=f"deduction {line.id} carries a recurrence rule"):
                payroll_line_cadence.cadence_phrase(line, None)

    def test_a_rule_against_an_empty_schedule_is_refused_not_misstated(
        self, app, db, seed_user,
    ):
        """An owner whose schedule holds no payday cannot have the line worded as every paycheck."""
        with app.app_context():
            line = _line(seed_user)
            make_line_cadence_rule(db.session, line, 24)
            empty = derived_calendar([], user_id=seed_user["user"].id)
            with pytest.raises(RecurrenceResolutionError, match="holds no pay periods"):
                payroll_line_cadence.cadence_phrase(line, empty)


@pytest.mark.usefixtures("seed_periods")
class TestTheFirstOccurrence:
    """Where a deduction rule starts: the unit's zero at the opening (R-SAL36).

    Plan step **salary:R15-c**.  The form never asks (ruling R-SAL30); the
    route derives it per unit, and the derivation is pinned here against the
    seeded schedule, whose opening payday is 2026-01-02 -- a day that is not
    a 1st, so the three answers are three different dates.
    """

    def test_each_authorable_unit_starts_on_its_own_zero(self, app, db, seed_user):
        """PERIOD: the opening payday; MONTH: its 1st; YEAR: its January 1st."""
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.opening_bound() == date(2026, 1, 2), (
                "the premise: the seeded opening payday is not a 1st"
            )
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.PERIOD, 1, calendar,
            ) == date(2026, 1, 2)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 1, calendar,
            ) == date(2026, 1, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.YEAR, 1, calendar,
            ) == date(2026, 1, 1)

    def test_the_month_zero_is_the_migrated_twelve_shapes_start(self, app, db, seed_user):
        """The MONTH answer IS what migration 542c61e48ee8 wrote for a 12 line.

        The downgrade reads a monthly deduction rule back as a 12 only when
        its ``starts_on`` is a 1st; a form-authored monthly line must be
        that same shape or the downgrade refuses it.  Compared against the
        shared builder the migration's SQL mirrors rather than a literal.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            line = _line(seed_user, "Transit")
            migrated = make_line_cadence_rule(db.session, line, 12)
            calendar = calendar_for(seed_user["user"].id)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 1, calendar,
            ) == migrated.starts_on

    def test_a_month_zero_on_a_mid_month_opening_is_the_first_not_the_opening(
        self, app, db, seed_user,
    ):
        """The rejected reading -- 'monthly on the opening payday's day' -- is not what is derived.

        On a schedule opening 2026-01-15 a monthly line starts 2026-01-01,
        never 2026-01-15: the 15th would make the rule 'the first paycheck
        on or after the 15th', the month's second paycheck most months.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            calendar = derived_calendar(
                [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 12)],
                user_id=seed_user["user"].id,
            )
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 1, calendar,
            ) == date(2026, 1, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.YEAR, 1, calendar,
            ) == date(2026, 1, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.PERIOD, 1, calendar,
            ) == date(2026, 1, 15)

    def test_a_whole_number_of_years_spelled_in_months_takes_the_year_zero(
        self, app, db, seed_user,
    ):
        """'Every 12 months' is stored as (1, YEAR) (R-R17), so its zero is January 1st.

        The adversarial review of this step traced the defect the stated
        unit would cause: on a March opening, 'months, every 12' derived
        March 1st on the create, the edit form read the stored YEAR back,
        and the next amount-only save derived January 1st -- an unrelated
        edit re-phasing the rule (D1's class).  The zero is read off the
        unit the door STORES, so the create and every re-save agree; a
        6-month interval is not a whole year and keeps the month's zero.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            march = derived_calendar(
                [date(2026, 3, 13), date(2026, 3, 27), date(2026, 4, 10)],
                user_id=seed_user["user"].id,
            )
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 12, march,
            ) == date(2026, 1, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 24, march,
            ) == date(2026, 1, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.MONTH, 6, march,
            ) == date(2026, 3, 1)
            assert payroll_line_cadence.first_occurrence(
                RecurrenceUnitEnum.YEAR, 1, march,
            ) == date(2026, 1, 1)

    def test_an_empty_schedule_is_refused_by_name(self, app, db, seed_user):
        """No opening payday, no rule to derive -- the same refusal ``resolve`` makes."""
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            empty = derived_calendar([], user_id=seed_user["user"].id)
            with pytest.raises(RecurrenceResolutionError, match="has no pay periods"):
                payroll_line_cadence.first_occurrence(RecurrenceUnitEnum.MONTH, 1, empty)

    def test_a_unit_the_table_states_no_zero_for_raises_at_the_lookup(
        self, app, db, seed_user,
    ):
        """WEEK is not offered (R8-b); reaching here with it is a new unit owing an entry."""
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)
            with pytest.raises(KeyError):
                payroll_line_cadence.first_occurrence(RecurrenceUnitEnum.WEEK, 1, calendar)


class TestTheEveryPaycheckSpelling:
    """The one cadence that is NO rule (R-SAL29), and nothing near it."""

    def test_only_every_one_paycheck_with_no_ceiling_is_no_rule(self):
        """The truth table: unit, interval and ceiling each break the identity."""
        # pylint: disable=import-outside-toplevel
        from app.enums import RecurrenceUnitEnum

        assert payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.PERIOD, 1, None)
        # A ceiling is a statement, even one that does not bind today.
        assert not payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.PERIOD, 1, 2)
        assert not payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.PERIOD, 1, 3)
        # Every OTHER paycheck is a cadence of its own.
        assert not payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.PERIOD, 2, None)
        # A calendar unit at interval 1 is monthly or yearly, never every paycheck.
        assert not payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.MONTH, 1, None)
        assert not payroll_line_cadence.is_every_paycheck(RecurrenceUnitEnum.YEAR, 1, None)
