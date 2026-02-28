"""
Shekel Budget App — Transfer Recurrence Engine Tests

Tests the auto-generation of transfers from templates with recurrence
rules, state machine behavior, regeneration, and conflict resolution.
"""

import pytest
from decimal import Decimal

from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, AccountType, Status
from app.services import transfer_recurrence
from app.exceptions import RecurrenceConflict


class TestTransferGeneration:
    """Tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + transfer template."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        savings_type = (
            db.session.query(AccountType)
            .filter_by(name="savings")
            .one()
        )

        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("500.00"),
        )
        db.session.add(savings)
        db.session.flush()

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

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            recurrence_rule_id=rule.id,
            name="Test Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transfer in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for xfer in created:
                assert xfer.amount == Decimal("100.00")
                assert xfer.name == "Test Transfer"

    def test_no_rule_returns_empty(self, app, db, seed_user, seed_periods):
        """Template with recurrence_rule=None returns empty list."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="savings")
                .one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("500.00"),
            )
            db.session.add(savings)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                recurrence_rule_id=None,
                name="No Rule Transfer",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_once_pattern_returns_empty(self, app, db, seed_user, seed_periods):
        """'once' pattern does not auto-generate."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "once"
            )
            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            first_run = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            second_run = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_skips_overridden_and_deleted(self, app, db, seed_user, seed_periods):
        """Overridden and soft-deleted entries are not duplicated on re-generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Override one entry.
            created[0].is_override = True
            created[0].amount = Decimal("999.99")
            # Soft-delete another.
            created[1].is_deleted = True
            db.session.flush()

            second_run = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            assert len(second_run) == 0


class TestTransferRegeneration:
    """Tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + transfer template."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        savings_type = (
            db.session.query(AccountType)
            .filter_by(name="savings")
            .one()
        )

        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("500.00"),
        )
        db.session.add(savings)
        db.session.flush()

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

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            recurrence_rule_id=rule.id,
            name="Test Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_regenerate_deletes_unmodified_and_recreates(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with changed amount deletes old entries and creates new."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = [xfer.id for xfer in created]
            assert len(old_ids) == 10

            # Change the template amount.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            new_created = transfer_recurrence.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            assert len(new_created) == 10
            for xfer in new_created:
                assert xfer.amount == Decimal("200.00")
                assert xfer.id not in old_ids

    def test_regenerate_raises_conflict_for_overridden(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with overridden entry raises RecurrenceConflict."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            overridden_id = created[0].id
            created[0].is_override = True
            created[0].amount = Decimal("999.99")
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as exc_info:
                transfer_recurrence.regenerate_for_template(
                    template, seed_periods, seed_user["scenario"].id,
                )

            assert overridden_id in exc_info.value.overridden

    def test_regenerate_preserves_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfers survive regeneration with original amount."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done.
            done_status = db.session.query(Status).filter_by(name="done").one()
            created[0].status_id = done_status.id
            original_amount = created[0].amount
            done_id = created[0].id
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer should still exist unchanged.
            done_xfer = db.session.get(Transfer, done_id)
            assert done_xfer is not None
            assert done_xfer.amount == original_amount


class TestTransferResolveConflicts:
    """Tests for resolve_conflicts()."""

    def _make_template_with_rule(self, seed_user, pattern_name, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + transfer template."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )
        savings_type = (
            db.session.query(AccountType)
            .filter_by(name="savings")
            .one()
        )

        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("500.00"),
        )
        db.session.add(savings)
        db.session.flush()

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

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            recurrence_rule_id=rule.id,
            name="Test Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()

        db.session.refresh(template)
        return template

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transfer unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            xfer.amount = Decimal("999.99")
            db.session.flush()

            transfer_recurrence.resolve_conflicts([xfer.id], action="keep")
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert xfer.amount == Decimal("999.99")

    def test_resolve_update_clears_flags_and_applies_amount(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' clears flags and applies new_amount."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            xfer.amount = Decimal("999.99")
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update", new_amount=Decimal("200.00")
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is False
            assert xfer.is_deleted is False
            assert xfer.amount == Decimal("200.00")
