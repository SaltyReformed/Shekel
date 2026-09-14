"""``app.services.deduction_cadence`` -- a payroll deduction's cadence, worded.

Plan step **salary:R15-b**.  The salary page's Frequency cell states each
line's cadence through the recurrence package's one phrase producer; a line
with no rule reads *every paycheck* (R-SAL3); a rule that cannot be
described is refused rather than misstated.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import CalcMethod, DeductionTiming
from app.services import deduction_cadence
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceResolutionError
from tests._test_helpers import (
    derived_calendar,
    make_deduction_cadence_rule,
    make_salary_profile,
)


def _line(seed_user, name="Health"):
    """A flat pre-tax line on its own profile (named after the line), flushed."""
    profile = make_salary_profile(seed_user, db.session, name=f"{name} Job")
    db.session.flush()
    line = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=db.session.query(DeductionTiming).filter_by(name="pre_tax").one().id,
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
            assert deduction_cadence.cadence_phrase(line, None) == "Every paycheck"
            assert deduction_cadence.cadence_phrase(
                line, calendar_for(seed_user["user"].id),
            ) == "Every paycheck"

    def test_the_two_migrated_shapes_read_as_the_recurring_surface_would(
        self, app, db, seed_user,
    ):
        """The 24 and 12 shapes are worded by ``describe``, not by a second vocabulary."""
        with app.app_context():
            twenty_four = _line(seed_user, "Health")
            make_deduction_cadence_rule(db.session, twenty_four, 24)
            twelve = _line(seed_user, "Transit")
            make_deduction_cadence_rule(db.session, twelve, 12)
            calendar = calendar_for(seed_user["user"].id)
            assert deduction_cadence.cadence_phrase(twenty_four, calendar) == (
                "Every paycheck (at most 2 a month)"
            )
            assert deduction_cadence.cadence_phrase(twelve, calendar) == "Monthly (first paycheck)"
            assert deduction_cadence.cadence_phrases([twenty_four, twelve], calendar) == {
                twenty_four.id: "Every paycheck (at most 2 a month)",
                twelve.id: "Monthly (first paycheck)",
            }

    def test_no_rule_is_worded_as_the_walk_words_an_every_paycheck_rule(self, app, db, seed_user):
        """``EVERY_PAYCHECK`` is what ``describe`` says of a bare every-paycheck rule.

        Two spellings of one phrase (``deduction_cadence.EVERY_PAYCHECK`` and
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
            assert deduction_cadence.EVERY_PAYCHECK == describe(bare).cadence

    def test_a_rule_with_no_calendar_is_the_callers_error(self, app, db, seed_user):
        """A line with a rule and no calendar to resolve it against is refused, by name."""
        with app.app_context():
            line = _line(seed_user)
            make_deduction_cadence_rule(db.session, line, 24)
            with pytest.raises(ValueError, match=f"deduction {line.id} carries a recurrence rule"):
                deduction_cadence.cadence_phrase(line, None)

    def test_a_rule_against_an_empty_schedule_is_refused_not_misstated(
        self, app, db, seed_user,
    ):
        """An owner whose schedule holds no payday cannot have the line worded as every paycheck."""
        with app.app_context():
            line = _line(seed_user)
            make_deduction_cadence_rule(db.session, line, 24)
            empty = derived_calendar([], user_id=seed_user["user"].id)
            with pytest.raises(RecurrenceResolutionError, match="holds no pay periods"):
                deduction_cadence.cadence_phrase(line, empty)
