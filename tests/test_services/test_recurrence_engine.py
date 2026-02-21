"""
Shekel Budget App — Recurrence Engine Tests

Tests the auto-generation of transactions from templates with
recurrence rules (§4.7) and the state machine behavior (§4.8).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, TransactionType, Status
from app.services import recurrence_engine


class TestRecurrenceGeneration:
    """Tests for generate_for_template()."""

    def _make_template_with_rule(self, app, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a template + recurrence rule."""
        with app.app_context():
            pattern = (
                db.session.query(RecurrencePattern)
                .filter_by(name=pattern_name)
                .one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )

            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=pattern.id,
                interval_n=rule_kwargs.get("interval_n", 1),
                offset_periods=rule_kwargs.get("offset_periods", 0),
                day_of_month=rule_kwargs.get("day_of_month"),
                month_of_year=rule_kwargs.get("month_of_year"),
            )
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                recurrence_rule_id=rule.id,
                transaction_type_id=expense_type.id,
                name="Test Recurring",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()

            # Load the relationships for the recurrence engine.
            db.session.refresh(template)
            return template

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transaction in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "every_period"
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for txn in created:
                assert txn.estimated_amount == Decimal("100.00")
                assert txn.name == "Test Recurring"

    def test_every_n_periods_with_offset(self, app, db, seed_user, seed_periods):
        """every_n_periods with n=2, offset=1 generates every other period."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "every_n_periods",
                interval_n=2, offset_periods=1,
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            # With 10 periods (indices 0-9), offset=1 matches indices 1,3,5,7,9 → 5.
            assert len(created) == 5
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert (period.period_index - 1) % 2 == 0

    def test_once_pattern_generates_nothing(self, app, db, seed_user, seed_periods):
        """'once' pattern does not auto-generate — user places it manually."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "once",
            )
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "every_period",
            )

            # First generation.
            first_run = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            # Second generation — should create nothing new.
            second_run = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_respects_is_override_flag(self, app, db, seed_user, seed_periods):
        """Overridden entries are not replaced during generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "every_period",
            )

            # Generate entries.
            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            created[0].is_override = True
            created[0].estimated_amount = Decimal("999.99")
            db.session.flush()

            # Regenerate — the overridden entry should be preserved.
            from app.exceptions import RecurrenceConflict

            try:
                recurrence_engine.regenerate_for_template(
                    template, seed_periods, seed_user["scenario"].id,
                )
            except RecurrenceConflict as conflict:
                assert created[0].id in conflict.overridden

            # The overridden amount should still be there.
            db.session.refresh(created[0])
            assert created[0].estimated_amount == Decimal("999.99")

    def test_never_touches_done_transactions(self, app, db, seed_user, seed_periods):
        """Done/received/credit transactions are immutable to the engine."""
        with app.app_context():
            template = self._make_template_with_rule(
                app, seed_user, "every_period",
            )

            created = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done.
            done_status = db.session.query(Status).filter_by(name="done").one()
            created[0].status_id = done_status.id
            created[0].actual_amount = Decimal("95.00")
            db.session.flush()

            # Regenerate — should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged.
            db.session.refresh(created[0])
            assert created[0].actual_amount == Decimal("95.00")
