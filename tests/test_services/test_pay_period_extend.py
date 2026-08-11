"""Tests for pay-period CRUD slice (c): extend + the repopulation helper.

``populate_periods_from_active_templates`` fills newly-created (empty)
periods with each active template's recurring transactions AND transfers;
``extend_pay_periods`` tail-appends periods and repopulates them.

Because a pay period is the spine of every financial number, the extend
happy-path test asserts all four disciplines: structural invariants
(Discipline 1, ``assert_pay_period_invariants``), hand-computed as-of
balances in both the retained and the new window (Discipline 2), and the
production integrity checker passing (Discipline 3).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.pay_schedule import PaySchedule
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    pay_period_admin,
    pay_period_service,
    pay_period_write,
    pay_schedule_service,
    period_population,
)
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    assert_pay_period_invariants,
    create_savings_account,
    seam_cash_balance_at,
    make_expense_template,
    make_transfer_template,
)


def _future_periods(db_session, seed_user, count=4, start=date(2026, 7, 3)):
    """Generate `count` biweekly future periods (indices 1..count)."""
    periods = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=start,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _period_length(period):
    """Inclusive day-span of a period == its cadence."""
    return (period.end_date - period.start_date).days + 1


class TestPopulateFromActiveTemplates:
    """The repopulation helper fills periods with txns and transfers."""

    def test_populates_one_transaction_per_period(self, app, db, seed_user):
        """An active every-period template yields one txn per period."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            created = period_population.populate_periods_from_active_templates(
                seed_user["user"].id, periods,
            )
            db.session.commit()

            assert created == 3
            for period in periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert txns[0].estimated_amount == Decimal("1200.00")

    def test_includes_transfer_templates(self, app, db, seed_user):
        """Active transfer templates generate transfers with both shadows.

        New periods must never silently miss a recurring transfer, so the
        helper runs the transfer engine too -- and each transfer keeps its
        two-shadow invariant.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            created = period_population.populate_periods_from_active_templates(
                seed_user["user"].id, periods,
            )
            db.session.commit()

            transfers = (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .all()
            )
            assert created == 3
            assert len(transfers) == 3
            for transfer in transfers:
                assert len(transfer.shadow_transactions) == 2
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_generates_nothing(self, app, db, seed_user):
        """An inactive (archived) template produces no rows."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, is_active=False)
            created = period_population.populate_periods_from_active_templates(
                seed_user["user"].id, periods,
            )
            db.session.commit()
            assert created == 0

    def test_idempotent_second_run_creates_nothing(self, app, db, seed_user):
        """Re-running over already-populated periods creates nothing.

        ``should_skip_period`` skips any period that already holds a
        template-linked row, so a retried extend / top-up is safe.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            first = period_population.populate_periods_from_active_templates(
                seed_user["user"].id, periods,
            )
            db.session.commit()
            second = period_population.populate_periods_from_active_templates(
                seed_user["user"].id, periods,
            )
            db.session.commit()
            assert first == 3
            assert second == 0

    def test_no_baseline_scenario_returns_zero(self, app, bare_periods):
        """A user with no baseline scenario is a no-op (returns 0)."""
        with app.app_context():
            created = period_population.populate_periods_from_active_templates(
                bare_periods[0].user_id, bare_periods,
            )
            assert created == 0

    def test_empty_period_list_returns_zero(self, app, seed_user):
        """An empty period list short-circuits to 0."""
        with app.app_context():
            assert period_population.populate_periods_from_active_templates(
                seed_user["user"].id, [],
            ) == 0


class TestExtendPayPeriods:
    """``extend_pay_periods`` tail-appends and repopulates."""

    def test_appends_contiguously_after_last_period(self, app, db, seed_user):
        """New periods continue the index sequence and start the next day."""
        with app.app_context():
            existing = _future_periods(db.session, seed_user, count=3)
            last = existing[-1]
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()

            assert [p.period_index for p in new_periods] == [
                last.period_index + 1, last.period_index + 2,
            ]
            assert new_periods[0].start_date == last.end_date + timedelta(days=1)
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_this_door_takes_no_cadence_at_all(self, app, db, seed_user):
        """Finding **P29**: the parameter is GONE, not newly honoured.

        This test asserted the opposite -- that an explicit ``cadence_days``
        beat the stored one -- and that was the defect: the extend card renders
        NO control for it, so the only way to reach it was a direct POST, and
        what it produced was 7-day paychecks beside a ``budget.pay_schedule``
        still saying 14.  ``resolve_cadence``, the derived horizon and the next
        rolling top-up then all continued at 14.

        Extend CONTINUES an existing schedule, so the cadence is not a question
        it gets to ask.  Plan step C3-b deleted the parameter, its Marshmallow
        field and the rolling top-up's redundant pass-through -- which closes
        the finding by making the state unreachable rather than by adding the
        write finding **P30** objected to.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)

            with pytest.raises(TypeError):
                pay_period_admin.extend_pay_periods(
                    seed_user["user"].id, num_periods=1, cadence_days=7,
                )

            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 14
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_stored_schedule_cadence_used_when_unspecified(
        self, app, db, seed_user,
    ):
        """A persisted schedule cadence wins over the inferred one."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            pay_schedule_service.upsert_schedule(
                seed_user["user"].id, cadence_days=7,
            )
            db.session.commit()
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 7

    def test_infers_cadence_for_legacy_user(self, app, db, seed_user):
        """With no schedule row, cadence is inferred from the last period.

        **The row is deleted here, and until plan step C3-b it did not exist
        to delete.**  The cadence rule makes every batch that records a payday
        store one, so the state finding **P8** is about -- paydays with no
        cadence beside them -- can no longer be produced by any door and has to
        be constructed on purpose.  That is the finding narrowing, and this is
        what the extend path does with the legacy data that still carries it.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)  # 14-day periods
            db.session.query(PaySchedule).filter_by(
                user_id=seed_user["user"].id,
            ).delete(synchronize_session=False)
            db.session.commit()
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ) is None
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 14

    def test_new_periods_get_recurring_rows(self, app, db, seed_user):
        """Extended periods are repopulated with active templates' rows."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user)
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert txns[0].estimated_amount == Decimal("1200.00")
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_leaves_new_periods_empty(self, app, db, seed_user):
        """An archived template generates nothing into the new periods."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user, is_active=False)
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                assert (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .count()
                ) == 0

    def test_empty_schedule_raises(self, app, bare_user):
        """Extending a user with no periods raises ValidationError."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Generate your first"):
                pay_period_admin.extend_pay_periods(
                    bare_user["user"].id, num_periods=2,
                )

    def test_balances_correct_after_extend(self, app, db, seed_user):
        """Disciplines 1-3: as-of balances march correctly after extend.

        Anchor $1000 at the bootstrap period (index 0, no expense).  A
        $1200 every-period expense fills indices 1..4, so the projected
        end balance at index N is 1000 - N*1200.  Extending by 2 fills
        indices 5..6 with the same expense, so the projection continues to
        1000 - 6*1200 in the new window while the retained window is
        untouched, and the production integrity checker flags nothing.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)  # idx 1..4
            make_expense_template(db.session, seed_user, amount="1200.00")
            period_population.populate_periods_from_active_templates(
                user_id, periods,
            )
            db.session.commit()

            # Pre-extend: 1000 - N*1200 at index N's end.
            assert seam_cash_balance_at(
                account, scen, periods[3].end_date,  # index 4
            ) == Decimal("-3800.00")  # 1000 - 4*1200
            retained = seam_cash_balance_at(
                account, scen, periods[1].end_date,  # index 2
            )
            assert retained == Decimal("-1400.00")  # 1000 - 2*1200

            # Extend by 2 -> indices 5, 6, each repopulated with the expense.
            new_periods = pay_period_admin.extend_pay_periods(
                user_id, num_periods=2,
            )
            db.session.commit()

            # New window: the projection continues. Index 6 -> 1000 - 6*1200.
            assert seam_cash_balance_at(
                account, scen, new_periods[-1].end_date,  # index 6
            ) == Decimal("-6200.00")  # 1000 - 6*1200
            # Retained window is untouched by the append.
            assert seam_cash_balance_at(
                account, scen, periods[1].end_date,
            ) == retained

            # Discipline 1: structure sound.
            assert_pay_period_invariants(db.session, user_id)
            # Discipline 3: production integrity checker flags nothing.
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))
