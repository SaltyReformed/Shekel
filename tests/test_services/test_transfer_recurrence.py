"""
Shekel Budget App -- Transfer Recurrence Engine Tests

Tests the auto-generation of transfers from templates with recurrence
rules, state machine behavior, regeneration, and conflict resolution.
"""

import pytest
from datetime import date
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

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep", user_id=seed_user["user"].id,
            )
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
                [xfer.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("200.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is False
            assert xfer.is_deleted is False
            assert xfer.amount == Decimal("200.00")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transfer."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            xfer.amount = Decimal("999.99")
            db.session.flush()

            # Attempt resolve as second_user -- should be blocked.
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                user_id=second_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert xfer.amount == Decimal("999.99")

    def test_cross_user_keep_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """keep with wrong user_id leaves transfer unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            xfer.amount = Decimal("999.99")
            db.session.flush()

            # 'keep' with wrong user -- no-op by design (keep never modifies).
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep",
                user_id=second_user["user"].id,
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert xfer.amount == Decimal("999.99")

    def test_same_user_update_succeeds(
        self, app, db, seed_user, seed_periods
    ):
        """update with correct user_id modifies the transfer."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            xfer.amount = Decimal("999.99")
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is False
            assert xfer.amount == Decimal("50.00")

    def test_mixed_ownership_list(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Only owned transfers are modified in a mixed-ownership list."""
        with app.app_context():
            # Create transfer for user A.
            template_a = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created_a = transfer_recurrence.generate_for_template(
                template_a, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            xfer_a = created_a[0]
            xfer_a.is_override = True
            xfer_a.amount = Decimal("999.99")

            # Create transfer for user B (needs their own periods).
            from app.services import pay_period_service
            periods_b = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=seed_periods[0].start_date,
                num_periods=10,
            )
            template_b = self._make_template_with_rule(
                second_user, "every_period"
            )
            created_b = transfer_recurrence.generate_for_template(
                template_b, periods_b, second_user["scenario"].id,
            )
            db.session.flush()
            xfer_b = created_b[0]
            xfer_b.is_override = True
            xfer_b.amount = Decimal("888.88")
            db.session.flush()

            # Resolve as user A -- only xfer_a should be modified.
            transfer_recurrence.resolve_conflicts(
                [xfer_a.id, xfer_b.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("50.00"),
            )
            db.session.flush()

            db.session.refresh(xfer_a)
            db.session.refresh(xfer_b)
            assert xfer_a.is_override is False
            assert xfer_a.amount == Decimal("50.00")
            assert xfer_b.is_override is True
            assert xfer_b.amount == Decimal("888.88")


# --- Negative-Path Tests ---------------------------------------------------


class TestNegativePaths:
    """Negative-path and boundary-condition tests for transfer recurrence.

    Verifies behavior with zero/negative amounts, self-transfers, empty
    periods, and immutable status preservation during regeneration.
    """

    def _make_template_with_rule(self, seed_user, pattern_name,
                                  default_amount=Decimal("100.00"),
                                  from_account_id=None, to_account_id=None,
                                  **rule_kwargs):
        """Helper: create rule + template with configurable amount and accounts."""
        pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name=pattern_name)
            .one()
        )

        # Create savings account for default to_account if not specified.
        if to_account_id is None:
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="savings")
                .one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings NP",
                current_anchor_balance=Decimal("500.00"),
            )
            db.session.add(savings)
            db.session.flush()
            to_account_id = savings.id

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
            from_account_id=from_account_id or seed_user["account"].id,
            to_account_id=to_account_id,
            recurrence_rule_id=rule.id,
            name="Test Transfer NP",
            default_amount=default_amount,
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        return template

    def test_zero_amount_transfer_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Zero-amount transfer template is rejected by the DB CHECK constraint.

        Input: Template with default_amount=0.00.
        Expected: IntegrityError from ck_transfer_templates_positive_amount.
        The DB enforces that default_amount > 0 at the schema level.
        Why: A zero-amount transfer is financially meaningless. The DB constraint
        catches this before the recurrence engine ever runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, "every_period", default_amount=Decimal("0.00")
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_self_transfer_same_account_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Self-transfers (same from and to account) are rejected by DB constraint.

        Input: Template with from_account_id == to_account_id.
        Expected: IntegrityError from ck_transfer_templates_different_accounts.
        The DB enforces from_account_id != to_account_id at the schema level.
        Why: A self-transfer is logically meaningless and could corrupt balance
        calculations. The DB constraint prevents it before the service runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            same_account_id = seed_user["account"].id
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, "every_period",
                    from_account_id=same_account_id,
                    to_account_id=same_account_id,
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_generate_with_empty_periods_returns_empty(
        self, app, db, seed_user, seed_periods
    ):
        """Empty periods list returns empty without error.

        Input: Template with valid rule, periods=[].
        Expected: Returns []. No crash.
        Why: Edge case when the user has no pay periods generated yet.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )
            created = transfer_recurrence.generate_for_template(
                template, [], seed_user["scenario"].id,
                effective_from=date(2026, 1, 1),
            )

            assert created == []

    def test_immutable_status_preserved_on_regeneration(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfers must be preserved on regeneration.

        Input: Generate for all periods, mark one as done, change template
        amount, regenerate.
        Expected: The done transfer persists with same ID, status, and
        original amount. Other periods get the new amount.
        Why: Settled transfers are financial history that must not be overwritten.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, "every_period"
            )

            created = transfer_recurrence.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as done.
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )
            target_xfer = created[3]
            target_id = target_xfer.id
            original_amount = target_xfer.amount
            target_xfer.status_id = done_status.id
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer must still exist unchanged.
            preserved = db.session.get(Transfer, target_id)
            assert preserved is not None, (
                f"Done transfer {target_id} was deleted during regeneration"
            )
            assert preserved.status.name == "done"
            assert preserved.id == target_id
            assert preserved.amount == original_amount

    def test_negative_amount_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Negative transfer amount is rejected by the DB CHECK constraint.

        Input: Template with default_amount=-100.00.
        Expected: IntegrityError from ck_transfer_templates_positive_amount.
        The DB enforces that default_amount > 0 at the schema level.
        Why: A negative transfer amount could reverse the direction of money
        flow, causing incorrect account balances. The DB constraint prevents
        it before the service ever runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, "every_period",
                    default_amount=Decimal("-100.00"),
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()
