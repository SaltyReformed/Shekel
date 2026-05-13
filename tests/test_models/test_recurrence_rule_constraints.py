"""Database CHECK constraint regression tests for budget.recurrence_rules.

Locks the storage-tier guarantee that day_of_month and month_of_year
fall within real calendar ranges (1..31 and 1..12 respectively) and
that due_day_of_month does the same when populated.  The constraints
are declared inline on the model columns
(``app/models/recurrence_rule.py``) and materialised by:

  * ck_recurrence_rules_due_dom -- migration f15a72a3da6c
  * ck_recurrence_rules_dom -- migration 1702cadcae54 (H-3 fix)
  * ck_recurrence_rules_moy -- migration 1702cadcae54 (H-3 fix)

Without these constraints, the recurrence engine in
``app/services/recurrence_engine.py`` would translate values like
day_of_month=99 into impossible calendar dates, silently generating
transactions on dates that do not exist and corrupting balance
projections downstream.

Audit reference: H-3 of
docs/audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern


def _monthly_pattern_id():
    """Return the ref.recurrence_patterns id for the Monthly pattern."""
    return (
        db.session.query(RecurrencePattern)
        .filter_by(name="Monthly")
        .one()
        .id
    )


def _annual_pattern_id():
    """Return the ref.recurrence_patterns id for the Annual pattern."""
    return (
        db.session.query(RecurrencePattern)
        .filter_by(name="Annual")
        .one()
        .id
    )


class TestRecurrenceRuleRangeConstraints:
    """Out-of-range day/month values rejected at flush time."""

    def test_day_of_month_above_31_rejected(self, app, db, seed_user):
        """day_of_month=99 raises IntegrityError on insert.

        A future caller that allowed an out-of-range day to slip past
        the schema layer would otherwise corrupt the recurrence
        engine's date arithmetic.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_monthly_pattern_id(),
                day_of_month=99,
            )
            db.session.add(rule)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_recurrence_rules_dom" in str(exc_info.value)
            db.session.rollback()

    def test_day_of_month_zero_rejected(self, app, db, seed_user):
        """day_of_month=0 raises IntegrityError on insert.

        Zero would map to "the day before the 1st", which the engine
        would silently shift into the previous month.  Pinning the
        lower bound at 1 makes the rejection explicit.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_monthly_pattern_id(),
                day_of_month=0,
            )
            db.session.add(rule)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_recurrence_rules_dom" in str(exc_info.value)
            db.session.rollback()

    def test_due_day_of_month_above_31_rejected(self, app, db, seed_user):
        """due_day_of_month=99 raises IntegrityError on insert.

        Mirrors the day_of_month bound; this constraint already
        existed in production before the H-3 fix (added by migration
        f15a72a3da6c) and the test is here as a complementary backstop
        so all three recurrence-rule range checks are exercised in
        one file.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_monthly_pattern_id(),
                day_of_month=15,
                due_day_of_month=99,
            )
            db.session.add(rule)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_recurrence_rules_due_dom" in str(exc_info.value)
            db.session.rollback()

    def test_month_of_year_above_12_rejected(self, app, db, seed_user):
        """month_of_year=15 raises IntegrityError on insert.

        Without this constraint the annual recurrence pattern would
        treat month=15 as "December plus three months" thanks to
        Python's date-overflow arithmetic, generating transactions in
        a year the user did not specify.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_annual_pattern_id(),
                month_of_year=15,
                day_of_month=1,
            )
            db.session.add(rule)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_recurrence_rules_moy" in str(exc_info.value)
            db.session.rollback()

    def test_month_of_year_zero_rejected(self, app, db, seed_user):
        """month_of_year=0 raises IntegrityError on insert.

        Zero would shift the annual recurrence into the previous
        December.  Pinning the lower bound at 1 makes the rejection
        explicit at the storage tier.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_annual_pattern_id(),
                month_of_year=0,
                day_of_month=1,
            )
            db.session.add(rule)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_recurrence_rules_moy" in str(exc_info.value)
            db.session.rollback()

    def test_null_day_and_month_allowed(self, app, db, seed_user):
        """A RecurrenceRule with day_of_month=NULL and month_of_year=NULL inserts.

        Patterns like 'every_n_periods' do not need either field.
        Asserts the CHECK predicates' NULL branches admit the common
        case so a future regression that tightens the predicates
        (drops the IS NULL branch) breaks here loudly instead of
        breaking the every-period pattern silently.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=_monthly_pattern_id(),
            )
            db.session.add(rule)
            db.session.flush()
            assert rule.id is not None
            db.session.rollback()
