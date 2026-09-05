"""
Shekel Budget App -- Transfer Recurrence Engine Tests

Tests the auto-generation of transfers from templates with recurrence
rules, state machine behavior, regeneration, and conflict resolution.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.account import Account, AccountAnchorHistory
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import AmountSourceEnum, SettlementBasisEnum, StatusEnum
from app.services import (
    pay_period_write, transfer_recurrence, transfer_service,
)
from app.services.recurrence_engine import resolve_generation_plan
from app.exceptions import (
    RecurrenceConflict,
)
from app.utils.log_events import (
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    EVT_TRANSFER_UPDATED,
)
from app.utils.dates import display_today
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    rhythm_of,
    an_entered_day,
    create_account_of_type,
    last_covered_day,
    make_cadence_rule,
    settlement_basis_id,
    shadow_amount,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    MONTHLY,
)
from app.services.settle_day import record_settle_day
from app.services.amount_ownership import state_own_amount


def _assert_shadows_valid(xfer):
    """Assert a transfer has exactly 2 correct shadow transactions.

    **Only valid for a transfer that is NOT a loan payment**, and the amount
    assertion is why: a leg reads its parent through amount rule 5, so
    ``shadow_amount(leg) == xfer.amount`` holds by construction -- but rule 4
    prices a loan payment's leg from the LOAN's installment, which is
    deliberately not the parent's figure.  Every caller here generates plain
    transfers; a loan payment reaching this helper would fail on a rule working
    correctly.

    The amount line therefore carries little on its own, so the DECLARATION is
    asserted beside it: since plan step X-au-g-2c-2 a leg stores no figure and
    names ``PARENT_TRANSFER``, and that pair is the invariant this used to check
    by comparing two stored columns.
    """
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer.id)
        .all()
    )
    assert len(shadows) == 2, (
        f"Transfer {xfer.id} has {len(shadows)} shadows (expected 2)"
    )

    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    types = {s.transaction_type_id for s in shadows}
    assert types == {expense_type.id, income_type.id}

    parent_transfer_id = ref_cache.amount_source_id(
        AmountSourceEnum.PARENT_TRANSFER,
    )
    for s in shadows:
        assert s.estimated_amount is None
        assert s.amount_source_id == parent_transfer_id
        assert shadow_amount(s) == xfer.amount
        assert s.status_id == xfer.status_id
        assert s.pay_period_id == xfer.pay_period_id
        # due_date mirrors the parent (Transfer Invariant 3).
        assert s.due_date == xfer.due_date

    expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
    income = [s for s in shadows if s.transaction_type_id == income_type.id][0]
    assert expense.account_id == xfer.from_account_id
    assert income.account_id == xfer.to_account_id
    return expense, income


def make_template_with_rule(seed_user, cadence, **rule_kwargs):
    """Create a savings account, a transfer template and its cadence rule.

    **ONE copy of what was three byte-identical methods.**  Four other classes
    in this file define a `_make_template_with_rule` of their own and keep it:
    each differs genuinely -- a second endpoint, an explicit bound, a loan
    destination -- so collapsing those would be a shared helper with a flag per
    caller, which is the shape being removed rather than a second instance of
    it.

    Args:
        seed_user: The `seed_user` fixture's mapping.
        cadence: The `RecurrenceUnitEnum` the rule fires on.
        **rule_kwargs: `interval_n`, `day_of_month`, `month_of_year`.

    Returns:
        The refreshed `TransferTemplate`.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("500.00"),
    )

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Test Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, cadence,
        interval_n=rule_kwargs.get("interval_n", 1),
        fires_on_day=rule_kwargs.get("day_of_month"),
        fires_in_month=rule_kwargs.get("month_of_year"),
    )

    db.session.refresh(template)
    return template


class TestTransferGeneration:
    """Tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transfer in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for xfer in created:
                assert xfer.amount == Decimal("100.00")
                assert xfer.name == "Test Transfer"
                # Every-period has no day_of_month, so the due date falls
                # back to the pay-period start (payday) -- the prior
                # behaviour is preserved for day-less patterns.
                assert xfer.due_date == xfer.pay_period.start_date
                _assert_shadows_valid(xfer)

    def test_monthly_due_date_placed_on_day_of_month(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly transfers are due on the rule's day_of_month, not payday.

        seed_periods is 10 biweekly periods from 2026-01-02, each spanning
        [start, start+13].  A Monthly rule with day_of_month=15 matches the
        period containing the 15th of each month:
            P0 [01-02..01-15] -> 2026-01-15
            P3 [02-13..02-26] -> 2026-02-15
            P5 [03-13..03-26] -> 2026-03-15
            P7 [04-10..04-23] -> 2026-04-15
            P9 [05-08..05-21] -> 2026-05-15
        so the shadows land on the true monthly due date (matching a loan
        card's monthly_due_date) instead of the pay-period start.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert sorted(x.due_date for x in created) == [
                date(2026, 1, 15),
                date(2026, 2, 15),
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ]
            for xfer in created:
                assert xfer.due_date.day == 15
                # Parent and both shadows carry the same due date.
                _assert_shadows_valid(xfer)

    def test_no_rule_returns_empty(self, app, db, seed_user, seed_periods):
        """Template with recurrence_rule=None returns empty list."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("500.00"),
            )

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="No Rule Transfer",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.flush()
            db.session.refresh(template)

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_a_rule_less_template_returns_empty(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer template that does not repeat auto-generates nothing.

        The transfer-side twin of
        ``test_recurrence_engine.test_a_rule_less_template_generates_nothing``;
        both engines share ``resolve_generation_plan``'s gate.  Named the
        ``Once`` PATTERN until plan step R2e-3 retired it -- and the single
        Transfer such a template stands for is created by the transfer ROUTE,
        not by this engine, which is exactly why the engine must return empty.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            # ONE statement, exactly as
            # ``_recurrence_form_helpers._clear_recurrence_rule`` does since
            # plan step R-F6: dis-associating the rule is what deletes it,
            # because the relationship carries ``delete-orphan`` and the rule
            # holds the owning FK.  It was three statements -- null both sides,
            # then delete the row -- while the FK sat on the template, and an
            # explicit delete after this one now reports
            # ``expected to delete 1 row(s); 0 were matched``, because the
            # dis-association already removed it.
            template.recurrence_rule = None
            db.session.flush()
            assert template.recurrence_rule is None

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            first_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            second_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_skips_overridden_and_deleted(self, app, db, seed_user, seed_periods):
        """Overridden and soft-deleted entries are not duplicated on re-generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Override one entry.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            # Soft-delete another.
            created[1].is_deleted = True
            db.session.flush()

            second_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0


class TestTransferGenerationSharesTheOccurrencePairs:
    """The transfer engine's half of plan step R4b-2, driven rather than assumed.

    Both engines take their ``(occurrence, pay period)`` pairs from the one
    shared preamble (``recurrence_engine.resolve_generation_plan``) and their
    pre-write step from the one shared helper
    (``_recurrence_common.occurrences_to_write``), so the BEHAVIOUR
    cannot drift.  The COVERAGE could: a neutral review found the repeat
    refusal exercised only on the transaction side, which would let a
    transfer-specific regression -- the wrong FK column handed to the shared
    query, say -- ship green.  These drive the transfer path to the same two
    places.
    """

    def test_a_transfer_repeating_inside_one_paycheck_writes_each_occurrence(
        self, app, db, seed_user, seed_periods,
    ):
        """``idx_transfers_template_scenario_occurrence`` is the twin index.

        At a 90-day cadence a monthly transfer legitimately falls inside one
        paycheck three times.  While ``budget.transfers`` held one row per
        ``(template, period, scenario)`` those three could not be stored and
        the pass REFUSED (plan ledger row D19).  Plan step **R17** re-keyed the
        index onto ``(template, scenario, occurs_on)``, so three occurrences
        are three keys and all three transfers are written -- each with its own
        shadow pair, which is the half this engine has and the transaction
        engine does not.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                rhythm=rhythm_of(90),
            )
            db.session.flush()
            template = TestTransferGeneration()._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )

            created = transfer_recurrence.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {p.id for p in long_periods},
                ),
                seed_user["scenario"].id,
            )
            # The flush is the assertion: under the paycheck-keyed index these
            # three transfers were an IntegrityError.
            db.session.flush()

            paycheck = long_periods[0]
            expected = tuple(
                day
                for day in (
                    date(year, month, 15)
                    for year in range(
                        paycheck.start_date.year, last_covered_day(paycheck).year + 1,
                    )
                    for month in range(1, 13)
                )
                if paycheck.start_date <= day <= last_covered_day(paycheck)
            )
            assert len(expected) == 3, (
                "a 90-day paycheck must cover the 15th of three months, or this "
                "fixture no "
                "longer exercises the repeat"
            )
            in_paycheck = [
                row for row in created if row.pay_period_id == paycheck.id
            ]
            assert sorted(
                row.occurs_on for row in in_paycheck
            ) == list(expected)
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
                pay_period_id=paycheck.id,
            ).count() == 3
            # Each transfer keeps its shadow pair -- the invariant this engine
            # owns, and the one a repeat could have broken by writing a parent
            # without its two linked rows.
            for row in in_paycheck:
                _assert_shadows_valid(row)

    def test_a_transfer_occurrence_in_an_absorbed_hole_is_written_too(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The transfer engine takes the absorption identically (row **P27**).

        **This test asserted the opposite until plan step C2-b2.**  A day no
        pay period covered used to produce ``PlacementOutcome.SCHEDULE_GAP``,
        which ``report_schedule_gaps`` logged from the TRANSFER engine's own
        logger, and the occurrence generated nothing.  A calendar now derives
        each period's end from the next payday, so the preceding paycheck
        absorbs those days -- the hole is not a state a reader can see, and the
        report went with it.

        What the absorption leaves is an OVER-LONG paycheck holding the 15th
        twice,
        which the paycheck-keyed index could not hold, so the
        pass refuses and writes nothing.  Identical to the transaction engine's
        answer, which is the point: the two are deliberate parallels and this
        asserts they did not diverge across the cutover.  The transaction twin
        is ``test_recurrence_engine.TestALegacyScheduleHole``.

        **The whole fixture is the real writer's**, and since plan step
        ``pay_calendar:C4-c`` nothing has to be undone afterwards: a batch
        opening 43 days past the horizon is a legal write, and the days between
        belong to the paycheck before them because ``end_date`` is derived
        rather than stored.  *This test re-opened the hole by hand on its last
        fixture line while that column existed, because C3-b's writer closed
        it on every write.*
        """
        with app.app_context():
            last_covered = calendar_for(seed_user["user"].id).horizon()
            later_start = last_covered + timedelta(days=43)
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=later_start,
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            gap_start = last_covered + timedelta(days=1)
            gap_end = later_start - timedelta(days=1)
            template = TestTransferGeneration()._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )

            with caplog.at_level(
                logging.WARNING, logger="app.services.transfer_recurrence",
            ):
                created = transfer_recurrence.generate_for_template(
                    template,
                    GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
                    seed_user["scenario"].id,
                )
                db.session.flush()

            absorbed = [
                date(year, month, 15)
                for year in range(gap_start.year, gap_end.year + 1)
                for month in range(1, 13)
                if gap_start <= date(year, month, 15) <= gap_end
            ]
            assert len(absorbed) == 1, "the fixture built no absorbed occurrence"
            # The absorbed date is ANSWERED by a transfer of its own, beside
            # the one the over-long paycheck already owed -- so nothing is
            # dropped and nothing is refused (plan step R17).
            assert absorbed[0] in {row.occurs_on for row in created}
            absorbing = [
                row for row in created if row.occurs_on == absorbed[0]
            ][0].pay_period_id
            both = [row for row in created if row.pay_period_id == absorbing]
            assert len(both) == 2
            assert len({row.occurs_on for row in both}) == 2
            # And nothing is logged -- the gap report went with plan step C2-b2.
            assert [
                record for record in caplog.records
                if getattr(record, "event", None)
                == "recurrence_occurrence_unplaced"
            ] == []


class TestTransferRegeneration:
    """Tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_regenerate_maintains_its_rows_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """A changed amount reaches the rows the rule already generated.

        **RE-RULED at plan step R10-b.**  This case read the count of rows a
        pass CREATED as the count of rows the rule names, and asserted that
        every id had CHANGED -- both true only while a regeneration rebuilt
        everything it touched.  It now asserts what a maintain pass promises,
        which is strictly more: the same rows, carrying the new figure, with
        their shadows in step and nothing created or destroyed.

        Keeping the id is the point rather than a detail: the delete it replaces
        took the transfer's ``notes`` and any settlement record it had retained
        through a revert with it, and stranded its ledger attribution.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = {xfer.id for xfer in created}
            old_shadow_ids = {
                shadow.id
                for shadow in db.session.query(Transaction).filter(
                    Transaction.transfer_id.in_(old_ids),
                )
            }
            assert len(old_ids) == 10
            assert len(old_shadow_ids) == 20

            # Change the template amount.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            new_created = transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Nothing was created: every period the rule names already had its
            # row, and the pass brought each one into line instead.
            assert new_created == []

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids
            for xfer in live:
                assert xfer.amount == Decimal("200.00")
                _assert_shadows_valid(xfer)

            # Both shadows of every row survived with their own ids -- the
            # CASCADE the old sweep relied on never fires on this path.
            assert {
                shadow.id
                for shadow in db.session.query(Transaction).filter(
                    Transaction.transfer_id.in_(old_ids),
                )
            } == old_shadow_ids

    def test_regenerate_restores_a_drifted_due_date_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """A maintain pass brings a row's DUE DATE back to what the rule says.

        The due date is one of the six columns a definition derives, and a
        delete-and-recreate re-derived it for free.  A maintain pass has to
        apply it, so this drives the one field whose value comes from the RULE
        rather than the template's own columns: the row is moved off its
        computed date through the shadow-safe door, and the next pass puts it
        back -- on the SAME row.

        The edit does not set ``is_override`` (that is the transfers PATCH
        route's doing, not the service's), so the row stays the rule's own and
        the pass may correct it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            drifted = created[0]
            computed = drifted.due_date
            transfer_service.update_transfer(
                drifted.id, seed_user["user"].id, due_date=date(2030, 12, 25),
            )
            db.session.flush()
            assert drifted.due_date == date(2030, 12, 25)

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            restored = db.session.get(Transfer, drifted.id)
            assert restored is not None, "the drifted row was destroyed"
            assert restored.due_date == computed
            _assert_shadows_valid(restored)

    def test_regenerate_produces_computed_due_dates(
        self, app, db, seed_user, seed_periods
    ):
        """Maintained monthly transfers carry the computed day_of_month due date.

        **RE-RULED at plan step R10-b**: it read the pass's return value, which
        was every row while a regeneration rebuilt them and is the rows it
        CREATED now.  The property is unchanged and is asserted over the rows
        the template actually has (here the 15th of each month -- see
        test_monthly_due_date_placed_on_day_of_month for the period math).

        **The due-date list is INHERITED from generation, and an adversarial
        review of R10-b measured that.**  What still requires the pass to have
        run is the amount.  The case that pins the maintain path's own handling
        of ``due_date`` is
        ``test_regenerate_restores_a_drifted_due_date_in_place``; this one pins
        that a maintain pass does not DISTURB a date generation placed
        correctly, which is the complementary half and is worth having beside
        it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            template.default_amount = Decimal("200.00")
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert sorted(x.due_date for x in live) == [
                date(2026, 1, 15),
                date(2026, 2, 15),
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ]
            for xfer in live:
                assert xfer.amount == Decimal("200.00")
                _assert_shadows_valid(xfer)

    def test_regenerate_raises_conflict_for_overridden(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with overridden entry raises RecurrenceConflict."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            overridden_id = created[0].id
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as exc_info:
                transfer_recurrence.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
                )

            assert overridden_id in exc_info.value.overridden

    def test_regenerate_preserves_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfers survive regeneration with original amount."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done (Paid).
            done_id_val = ref_cache.status_id(StatusEnum.DONE)
            created[0].status_id = done_id_val
            original_amount = created[0].amount
            done_id = created[0].id
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer should still exist unchanged.
            done_xfer = db.session.get(Transfer, done_id)
            assert done_xfer is not None
            assert done_xfer.amount == original_amount


class TestTransferResolveConflicts:
    """Tests for resolve_conflicts()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transfer unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
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
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
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

            # Shadows should also be synced.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id
            ).all()
            for s in shadows:
                assert s.is_override is False
                assert s.is_deleted is False
                assert shadow_amount(s) == Decimal("200.00")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transfer."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
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
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
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
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
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
                seed_user, EVERY_PERIOD
            )
            created_a = transfer_recurrence.generate_for_template(
                template_a, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_a.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            xfer_a = created_a[0]
            xfer_a.is_override = True
            state_own_amount(xfer_a, Decimal("999.99"))

            # Create transfer for user B (needs their own periods).
            from app.services import pay_period_service
            periods_b = pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=10, rhythm=rhythm_of(14),
            )
            template_b = self._make_template_with_rule(
                second_user, EVERY_PERIOD
            )
            created_b = transfer_recurrence.generate_for_template(
                template_b, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_b.user_id), {p.id for p in periods_b},
                ), second_user["scenario"].id,
            )
            db.session.flush()
            xfer_b = created_b[0]
            xfer_b.is_override = True
            state_own_amount(xfer_b, Decimal("888.88"))
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

    def _make_template_with_rule(self, seed_user, cadence,
                                  default_amount=Decimal("100.00"),
                                  from_account_id=None, to_account_id=None,
                                  **rule_kwargs):
        """Helper: create rule + template with configurable amount and accounts."""

        # Create savings account for default to_account if not specified.
        if to_account_id is None:
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings NP",
                anchor_balance=Decimal("500.00"),
            )
            to_account_id = savings.id

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=from_account_id or seed_user["account"].id,
            to_account_id=to_account_id,
            name="Test Transfer NP",
            default_amount=default_amount,
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
            fires_on_day=rule_kwargs.get("day_of_month"),
            fires_in_month=rule_kwargs.get("month_of_year"),
        )
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
                    seed_user, EVERY_PERIOD, default_amount=Decimal("0.00")
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
                    seed_user, EVERY_PERIOD,
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
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), set(),
                ), seed_user["scenario"].id,
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
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as done (Paid).
            done_id_val = ref_cache.status_id(StatusEnum.DONE)
            target_xfer = created[3]
            target_id = target_xfer.id
            original_amount = target_xfer.amount
            target_xfer.status_id = done_id_val
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer must still exist unchanged.
            preserved = db.session.get(Transfer, target_id)
            assert preserved is not None, (
                f"Done transfer {target_id} was deleted during regeneration"
            )
            assert preserved.status_id == done_id_val
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
                    seed_user, EVERY_PERIOD,
                    default_amount=Decimal("-100.00"),
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_max_amount_overflow_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Commit 32 / MED-07 / PA-25: amounts past Numeric(12,2) max are rejected.

        Input: Template with default_amount=10_000_000_000.00 (11 integer
        digits, exceeds the column's Numeric(12,2) capacity of
        9,999,999,999.99).
        Expected: DataError (NumericValueOutOfRange) -- the column cannot
        represent the value, so PostgreSQL rejects the INSERT before the
        recurrence engine ever sees it.
        Why: closes the PA-25 transfer-recurrence boundary gap recorded in
        07_test_gaps slice-4 concept-6.  The complementary zero-amount,
        self-transfer, and negative-amount boundaries already have pinned
        rejection tests; max-amount overflow did not.
        """
        from sqlalchemy.exc import DataError as SADataError

        with app.app_context():
            with pytest.raises(SADataError):
                self._make_template_with_rule(
                    seed_user, EVERY_PERIOD,
                    default_amount=Decimal("10000000000.00"),
                )
            db.session.rollback()

    def test_at_max_amount_accepted_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Commit 32 / MED-07 / PA-25: Numeric(12,2) max is accepted.

        Input: Template with default_amount=9_999_999_999.99 (the largest
        value the column can store -- 10 integer digits + 2 decimal
        places).
        Expected: the row inserts and persists the exact Decimal back.
        Pairs with test_max_amount_overflow_rejected_by_db to define the
        boundary precisely (max value accepted, max+1 cent rejected).
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
                default_amount=Decimal("9999999999.99"),
            )
            db.session.flush()
            db.session.refresh(template)
            assert template.default_amount == Decimal("9999999999.99"), (
                f"Expected 9999999999.99, got {template.default_amount}"
            )


# ── Shadow Transaction Verification Tests ──────────────────────────


class TestShadowTransactionCreation:
    """Tests verifying shadow transaction creation through the recurrence engine."""

    def _make_template(self, seed_user, cadence, category_id=None,
                       **rule_kwargs):
        """Helper: create savings account + rule + template with optional category."""

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings Shadow",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Shadow Test Transfer",
            default_amount=Decimal("150.00"),
            category_id=category_id,
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
            fires_on_day=rule_kwargs.get("day_of_month"),
            fires_in_month=rule_kwargs.get("month_of_year"),
        )
        db.session.refresh(template)
        return template, savings

    def test_generated_transfers_have_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Every recurrence-generated transfer has exactly 2 shadows."""
        with app.app_context():
            template, _ = self._make_template(seed_user, EVERY_PERIOD)
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            assert len(created) == len(seed_periods)
            for xfer in created:
                _assert_shadows_valid(xfer)

    def test_category_id_passed_from_template(
        self, app, db, seed_user, seed_periods
    ):
        """Template category_id flows to transfer and expense shadow."""
        with app.app_context():
            rent_cat = seed_user["categories"]["Rent"]
            template, _ = self._make_template(
                seed_user, EVERY_PERIOD, category_id=rent_cat.id
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            for xfer in created:
                assert xfer.category_id == rent_cat.id

                shadows = db.session.query(Transaction).filter_by(
                    transfer_id=xfer.id
                ).all()
                expense = [s for s in shadows
                           if s.transaction_type_id == expense_type.id][0]
                assert expense.category_id == rent_cat.id

    def test_maintaining_keeps_the_shadows_it_already_has(
        self, app, db, seed_user, seed_periods
    ):
        """An amount change reaches both shadows without replacing either.

        **RE-RULED at plan step R10-b.**  It asserted every shadow was GONE
        after a regeneration -- true only while the pass hard-deleted each
        parent and let ``transactions.transfer_id``'s CASCADE take the pair.
        The property worth pinning is the opposite one: the pair the owner
        already has follows the definition, keeping its own rows, because
        ``transfer_service.update_transfer`` mirrors the parent's figure onto
        both legs (Transfer Invariants 3-5).
        """
        with app.app_context():
            template, _ = self._make_template(seed_user, EVERY_PERIOD)
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            old_ids = [xfer.id for xfer in created]
            old_shadow_ids = []
            for xfer in created:
                shadows = db.session.query(Transaction).filter_by(
                    transfer_id=xfer.id
                ).all()
                old_shadow_ids.extend([s.id for s in shadows])

            # Change amount and regenerate.
            template.default_amount = Decimal("300.00")
            db.session.flush()
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.commit()
            db.session.expire_all()

            # Every shadow survived, carrying the new figure.
            for sid in old_shadow_ids:
                shadow = db.session.get(Transaction, sid)
                assert shadow is not None, (
                    f"Shadow {sid} was destroyed by a maintain pass."
                )
                assert shadow_amount(shadow) == Decimal("300.00")

            for xid in old_ids:
                xfer = db.session.get(Transfer, xid)
                assert xfer is not None
                _assert_shadows_valid(xfer)
                assert xfer.amount == Decimal("300.00")

    def test_no_orphaned_shadows_after_regeneration(
        self, app, db, seed_user, seed_periods
    ):
        """No shadow transactions reference non-existent transfers after regen."""
        with app.app_context():
            template, _ = self._make_template(seed_user, EVERY_PERIOD)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            template.default_amount = Decimal("250.00")
            db.session.flush()
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Every shadow transaction should reference an existing transfer.
            all_shadows = db.session.query(Transaction).filter(
                Transaction.transfer_id.isnot(None)
            ).all()
            for shadow in all_shadows:
                parent = db.session.get(Transfer, shadow.transfer_id)
                assert parent is not None, (
                    f"Orphaned shadow {shadow.id} references "
                    f"non-existent transfer {shadow.transfer_id}"
                )

    def test_resolve_update_syncs_shadow_amounts(
        self, app, db, seed_user, seed_periods
    ):
        """resolve_conflicts(update) syncs new_amount to shadow transactions."""
        with app.app_context():
            template, _ = self._make_template(seed_user, EVERY_PERIOD)
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("175.00"),
            )
            db.session.flush()

            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert shadow_amount(s) == Decimal("175.00")
                assert s.is_override is False


# ── Service Routing Tests (L1 fix) ──────────────────────────────


class TestResolveConflictsServiceRouting:
    """Verify that resolve_conflicts routes through the transfer service
    instead of directly manipulating shadow transaction ORM objects.

    Closes L1 from transfer_rework_verification.md.
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + template."""

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings L1",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Test Transfer L1",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
        )
        db.session.refresh(template)
        return template

    def test_update_action_routes_amount_through_service(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts with action='update' routes
        amount updates through transfer_service.update_transfer, ensuring
        both shadow transactions are updated atomically.  Direct ORM
        manipulation of shadows would bypass future service logic.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            # A shadow cannot be drifted to prove the service corrects it:
            # since plan step X-au-g-2c-2 it stores no figure and reads its
            # parent, so ``ck_transactions_amount_ownership`` refuses the
            # write.  What the case still grades is the parent's own figure
            # going through the SERVICE rather than being assigned, and both
            # legs following it -- which the assertions below do.
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("200.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("200.00")
            assert xfer.is_override is False

            # Both shadows must match -- proves service routing, not
            # direct ORM, because the drifted shadow was corrected.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert shadow_amount(s) == Decimal("200.00")
                assert s.is_override is False
                assert s.is_deleted is False

    def test_update_restores_soft_deleted_transfer(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts with action='update' restores a
        soft-deleted transfer by routing through restore_transfer, then
        updates it through update_transfer.  The three-step cascade
        (un-delete, reset override, update amount) maintains all shadow
        invariants.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer_id = xfer.id
            # Soft-delete the transfer and its shadows.
            from app.services import transfer_service as ts
            ts.delete_transfer(xfer_id, seed_user["user"].id, soft=True)
            db.session.flush()

            # Confirm soft-deleted state.
            db.session.refresh(xfer)
            assert xfer.is_deleted is True

            transfer_recurrence.resolve_conflicts(
                [xfer_id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("300.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_deleted is False
            assert xfer.is_override is False
            assert xfer.amount == Decimal("300.00")

            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.is_deleted is False
                assert shadow_amount(s) == Decimal("300.00")

    def test_keep_action_preserves_user_override(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts with action='keep' preserves the
        user's overridden amount and does not reset it to the template
        default.  The user chose to override; the system must respect it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("350.00"))
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep",
                user_id=seed_user["user"].id,
                new_amount=Decimal("200.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("350.00")
            assert xfer.is_override is True

    def test_all_five_invariants_hold_after_resolution(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that after resolve_conflicts routes through the service,
        all five shadow invariants hold: both shadows exist, amounts match,
        statuses match, periods match, and types are one expense / one
        income.  Catches any regression where service routing introduces
        an invariant violation.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("150.00"),
            )
            db.session.flush()

            db.session.refresh(xfer)
            # Use the shared invariant assertion helper.
            _assert_shadows_valid(xfer)

    def test_multiple_transfers_each_routed_through_service(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts correctly processes multiple
        transfers in a single call, routing each through the transfer
        service independently.  Each transfer's shadows must reflect the
        resolved state regardless of processing order.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) >= 3

            # Override two transfers, soft-delete a third.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            created[1].is_override = True
            state_own_amount(created[1], Decimal("888.88"))
            db.session.flush()

            from app.services import transfer_service as ts
            ts.delete_transfer(
                created[2].id, seed_user["user"].id, soft=True
            )
            db.session.flush()

            ids = [created[0].id, created[1].id, created[2].id]
            transfer_recurrence.resolve_conflicts(
                ids, action="update",
                user_id=seed_user["user"].id,
                new_amount=Decimal("250.00"),
            )
            db.session.flush()

            for xfer_id in ids:
                xfer = db.session.get(Transfer, xfer_id)
                assert xfer.is_deleted is False
                assert xfer.is_override is False
                assert xfer.amount == Decimal("250.00")

                shadows = db.session.query(Transaction).filter_by(
                    transfer_id=xfer_id
                ).all()
                assert len(shadows) == 2
                for s in shadows:
                    assert s.is_deleted is False
                    assert s.is_override is False
                    assert shadow_amount(s) == Decimal("250.00")


class _LogCapture:
    """Capture log records emitted by ``logger_name`` with propagation off.

    Mirrors the helper in ``tests/test_services/test_service_log_events.py``;
    duplicated here rather than imported so the Commit 34 test class has
    no cross-file dependency on another test module's private helper.
    """

    def __init__(self, logger_name: str, level: int = logging.DEBUG) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level: int | None = None
        self._prior_propagate: bool | None = None

    def __enter__(self) -> "_LogCapture":
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        self._logger.propagate = False
        return self

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find_all(self, event_name: str) -> list[logging.LogRecord]:
        """Return every captured record whose ``event`` field matches."""
        return [
            r for r in self.records
            if getattr(r, "event", None) == event_name
        ]


class TestTransferMaintain:
    """What a regeneration does to the transfers it ALREADY generated.

    Plan step R10-b, ruling **R-R19**: a pass MAINTAINS the rows the rule still
    names instead of destroying and rebuilding them, retires a row the rule has
    stopped naming, and RETAINS -- untouched, reported -- a row the owner has
    records against.  The transaction engine's twin is
    ``TestRegenerateForTemplate`` in ``test_recurrence_engine.py``.
    """

    def _template_with_rows(self, seed_user, seed_periods, name="Maintained"):
        """Create a recurring transfer template and generate its rows.

        Args:
            seed_user: The seeded user fixture.
            seed_periods: The seeded pay periods.
            name: The template's name, so two templates can coexist.

        Returns:
            ``(template, savings, rows)`` -- the definition, its destination
            account, and the transfers it generated, oldest first.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", f'{name} Savings',
            anchor_balance=Decimal("500.00"),
        )
        db.session.flush()
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name=name,
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        make_cadence_rule(template, EVERY_PERIOD, interval_n=1)
        db.session.refresh(template)
        rows = transfer_recurrence.generate_for_template(
            template, self._schedule(template, seed_periods),
            seed_user["scenario"].id,
        )
        db.session.flush()
        assert len(rows) == 10
        return template, savings, sorted(rows, key=lambda x: x.id)

    def _schedule(self, template, seed_periods):
        """Return the whole-schedule GenerationSchedule for this owner."""
        return GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {p.id for p in seed_periods},
        )

    def _regenerate(self, template, seed_user, seed_periods):
        """Run one maintain pass, returning its conflict or ``None``."""
        try:
            transfer_recurrence.regenerate_for_template(
                template, self._schedule(template, seed_periods),
                seed_user["scenario"].id,
            )
            return None
        except RecurrenceConflict as conflict:
            return conflict
        finally:
            db.session.flush()

    def _settle_then_revert(self, xfer, seed_user, figure):
        """Leave *xfer* Projected while both legs still record what moved.

        The state ruling X-au-c3 creates deliberately: ``status_seam`` releases
        the ASSERTION on the way out of the settled band (``settled_on``,
        ``reconciled_by_id``) and KEEPS what moved (``settled_amount``,
        ``settled_basis_id``), because the full-edit popover instructs the owner
        to revert in order to edit.  So the row is the rule's own again -- and
        it carries a figure read off a bank statement.

        Args:
            xfer: The transfer to settle and revert.
            seed_user: The seeded user fixture.
            figure: The settled amount to record.
        """
        transfer_service.settle_transfer(
            xfer.id, seed_user["user"].id, submitted=figure,
            settle_day=an_entered_day(display_today()),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        legs = db.session.query(Transaction).filter_by(
            transfer_id=xfer.id, is_deleted=False,
        ).all()
        assert [leg.settled_amount for leg in legs] == [figure, figure], (
            "setup: both legs must retain the figure through the revert"
        )
        assert all(leg.settled_on is None for leg in legs), (
            "setup: the revert must release the assertion"
        )

    def test_an_empty_row_the_rule_stops_naming_is_retired(
        self, app, db, seed_user, seed_periods
    ):
        """A row carrying nothing follows the definition out of existence.

        Clearing the recurrence leaves the rule naming no period at all, so
        every generated row is an orphan.  One holding nothing of the owner's
        is removed -- through ``transfer_service.delete_transfer``, so both
        shadows go with it and the ledger is reconciled first.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            shadow_ids = [
                shadow.id for shadow in db.session.query(Transaction).filter(
                    Transaction.transfer_id.in_([x.id for x in rows]),
                )
            ]
            assert len(shadow_ids) == 20

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None, "an empty row raises nothing"
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0
            db.session.expire_all()
            for shadow_id in shadow_ids:
                assert db.session.get(Transaction, shadow_id) is None

    def test_one_pass_updates_creates_and_retires_together(
        self, app, db, seed_user, seed_periods
    ):
        """All three outcomes in one pass, on the rows each one is about.

        The cases above drive one outcome each, which is how a classifier comes
        to be right about each branch and wrong about the WHOLE: ``create_in``
        is computed from the periods no row occupies, and ``retire`` from the
        rows no period names, so the two are complementary and a defect in
        either shows up as the other's set being wrong.  This drives all three
        at once and asserts the resulting row set exactly.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            # Empty period 3: its row is deleted outright, so the rule names a
            # period holding nothing and the pass must CREATE there.
            gap_period_id = rows[3].pay_period_id
            transfer_service.delete_transfer(
                rows[3].id, seed_user["user"].id, soft=False,
            )
            db.session.flush()
            # Narrow to the first SEVEN periods, so periods 7-9 lose their rows
            # and retire, and move the amount so the survivors are updated.
            template.recurrence_rule.end_date = last_covered_day(seed_periods[6])
            template.default_amount = Decimal("155.00")
            db.session.flush()

            with _LogCapture("app.services.transfer_recurrence") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            [event] = cap.find_all(EVT_TRANSFER_RECURRENCE_REGENERATED)
            # Periods 0-6 are named: six hold a row (updated) and period 3 is
            # empty (created).  Periods 7-9 are no longer named and empty
            # (removed).
            assert event.updated_count == 6
            assert event.created_count == 1
            assert event.deleted_count == 3
            assert event.retained_conflict_count == 0

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert len(live) == 7
            assert {x.pay_period_id for x in live} == {
                p.id for p in seed_periods[:7]
            }
            assert all(x.amount == Decimal("155.00") for x in live)
            for xfer in live:
                _assert_shadows_valid(xfer)
            # The created row is a NEW id in the emptied period, and it is the
            # only one: the pass created exactly where nothing was.
            created_rows = [x for x in live if x.pay_period_id == gap_period_id]
            assert len(created_rows) == 1
            assert created_rows[0].id not in {x.id for x in rows}

    def test_a_pass_into_another_user_s_scenario_writes_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods
    ):
        """The cross-user defense still guards the maintain path.

        ``check_scenario_ownership`` runs before the plan is resolved, and it
        also DISAMBIGUATES that plan: a ``None`` plan means "this template no
        longer recurs" and nothing else, because ownership was settled first.
        Get that order wrong and a cross-user probe would look like a cleared
        recurrence and RETIRE every row the template has.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            before = {xfer.id for xfer in rows}

            created = transfer_recurrence.regenerate_for_template(
                template, self._schedule(template, seed_periods),
                seed_second_user["scenario"].id,
            )
            db.session.flush()

            assert created == []
            assert {
                xfer.id for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            } == before, "a cross-user pass retired this owner's rows"


    def test_a_row_holding_a_note_is_retained_when_the_rule_stops_naming_it(
        self, app, db, seed_user, seed_periods
    ):
        """A note the owner typed keeps its row alive and reports it.

        ``Transfer.notes`` is free text no writer derives, and
        ``create_transfer`` is never handed one -- so the delete-and-recreate
        sweep this replaces dropped it silently on every template edit.  The
        row is left EXACTLY as it was found, and named in the raise so the
        route can tell the owner.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            noted = rows[3]
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id,
                notes="ask the credit union about this one",
            )
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [noted.id]
            assert conflict.overridden == []
            assert conflict.deleted == []
            survivor = db.session.get(Transfer, noted.id)
            assert survivor is not None
            assert survivor.notes == "ask the credit union about this one"
            # Every OTHER row was empty, so the same pass retired all nine.
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 1

    def test_a_whitespace_only_note_does_not_retain_its_row(
        self, app, db, seed_user, seed_periods
    ):
        """Blank text is not a record, so it does not block a definition edit.

        The firing control for the case above: if the predicate tested
        ``notes is not None`` rather than what the owner actually typed, a
        stray space would freeze a row against every future edit and report a
        conflict the owner cannot act on.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            transfer_service.update_transfer(
                rows[3].id, seed_user["user"].id, notes="   ",
            )
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0

    def test_a_row_that_kept_its_settlement_record_is_retained(
        self, app, db, seed_user, seed_periods
    ):
        """A figure read off a statement survives a definition edit.

        The money defect this step closes.  A transfer settled and then
        REVERTED -- which is what the full-edit popover instructs, and which
        ``status_seam`` answers by releasing the assertion and KEEPING what
        moved -- is Projected, not overridden, and so was the sweep's to
        delete.  Reproduced on a production clone before this step: `$321.45`
        on both legs, destroyed by a rename with no prompt.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[5]
            self._settle_then_revert(
                recorded, seed_user, Decimal("321.45"),
            )

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            survivor = db.session.get(Transfer, recorded.id)
            assert survivor is not None
            legs = db.session.query(Transaction).filter_by(
                transfer_id=recorded.id, is_deleted=False,
            ).all()
            assert [leg.settled_amount for leg in legs] == [
                Decimal("321.45"), Decimal("321.45"),
            ]

    def test_a_leg_carrying_a_STATEMENT_LINK_is_retained(
        self, app, db, seed_user, seed_periods
    ):
        """A leg that records which statement showed its money holds its row.

        ``reconciled_by_id`` is what a reconcile tick writes on the leg whose
        account's statement was read (``transfer_service.record_clearing``,
        ruling **R-FL**), and it is scoped BY ACCOUNT --
        ``fk_transactions_reconciled_by`` is a composite over
        ``(account_id, reconciled_by_id)``.  So a row carrying one may be
        neither retired nor re-pointed at another account without destroying or
        re-filing an observation the owner made.

        **The retention predicate names no such condition, and it does not have
        to** -- but the PROOF of that is the CHECK-constraint implication, which
        is its own case below
        (``test_a_statement_link_cannot_exist_without_a_settlement_record``), not
        this one.  An adversarial review of R10-b measured that deleting the
        link from this plant leaves the case green, because the settlement
        record the same plant carries is what retains the row: the two facts
        cannot be separated, which is exactly what the implication says.

        What this case pins is that the fullest state a MAINTAINABLE transfer
        can reach -- a leg drifted out of its parent's status, carrying a
        settled day, a figure and a statement link, while the parent is still
        the rule's own row -- is retained rather than retired.  Shadow status
        drift is a state ruling **R-DO** treats as real; the plant writes the
        leg directly because no door produces it.
        """
        with app.app_context():
            template, savings, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            linked = rows[4]
            statement = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .order_by(AccountAnchorHistory.id)
                .first()
            )
            assert statement is not None, (
                "setup: the savings account's opening assertion is the "
                "statement this leg will name"
            )
            income = db.session.query(Transaction).filter_by(
                transfer_id=linked.id, account_id=savings.id,
            ).one()
            income.status_id = ref_cache.status_id(StatusEnum.DONE)
            record_settle_day(income, an_entered_day(display_today()))
            income.settled_amount = linked.amount
            income.settled_basis_id = settlement_basis_id(
                SettlementBasisEnum.DERIVED,
            )
            income.reconciled_by_id = statement.id
            db.session.flush()
            assert linked.status.is_immutable is False, (
                "setup: the PARENT is still the rule's own row"
            )
            assert income.status_id != linked.status_id, (
                "setup: the leg has DRIFTED out of its parent's status"
            )

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [linked.id]
            assert db.session.get(Transfer, linked.id) is not None
            assert db.session.get(Transaction, income.id) is not None

    def test_a_statement_link_cannot_exist_without_a_settlement_record(
        self, app, db, seed_user, seed_periods
    ):
        """The implication the retention predicate rests on, asked of the DB.

        ``_rows_holding_owner_records`` tests a settlement record and NOT a
        statement link, on the ground that two CHECK constraints chain:
        ``ck_transactions_cleared_needs_settle_day`` says a link needs a settle
        day, and ``ck_transactions_settle_day_needs_a_record`` says a settle day
        needs a basis.  So ``reconciled_by_id IS NOT NULL`` implies
        ``settled_basis_id IS NOT NULL`` and the record arm already catches every
        linked row.

        **That argument is what deleted an arm from BOTH engines' predicates at
        plan step R10-b, so it is asked of PostgreSQL rather than reasoned.**  If
        either constraint is ever dropped this case fails, which is the signal to
        put the arm back.
        """
        with app.app_context():
            _, savings, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            statement = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .order_by(AccountAnchorHistory.id)
                .first()
            )
            income = db.session.query(Transaction).filter_by(
                transfer_id=rows[0].id, account_id=savings.id,
            ).one()
            income.status_id = ref_cache.status_id(StatusEnum.DONE)
            record_settle_day(income, an_entered_day(display_today()))
            income.reconciled_by_id = statement.id
            # A link, a day, and NO record: the state the deleted arm would
            # have been the only thing to catch.
            income.settled_amount = None
            income.settled_basis_id = None

            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            assert "ck_transactions_settle_day_needs_a_record" in str(caught.value)
            db.session.rollback()

    def test_an_endpoint_move_applies_to_a_row_holding_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """A definition's account change reaches its rows IN PLACE.

        The old sweep applied it by destroying every row and re-creating it at
        the new endpoints; the pass now moves the pair -- parent, expense leg
        and income leg -- keeping each row's id.  Both shadow display names are
        re-derived, because a leg on the new account still labelled with the old
        one contradicts its own row.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "Elsewhere",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()
            old_ids = {xfer.id for xfer in rows}

            template.to_account_id = elsewhere.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids
            for xfer in live:
                assert xfer.to_account_id == elsewhere.id
                expense, income = _assert_shadows_valid(xfer)
                assert income.account_id == elsewhere.id
                assert expense.name == "Transfer to Elsewhere"
                assert income.name == (
                    f"Transfer from {seed_user['account'].name}"
                )

    def test_an_endpoint_move_is_retained_on_a_row_holding_records(
        self, app, db, seed_user, seed_periods
    ):
        """A recorded figure is not re-filed against accounts nobody asserted.

        A settled leg's ``settled_amount`` is what moved between the OLD pair of
        accounts and its statement link is scoped BY account
        (``fk_transactions_reconciled_by``), so applying an endpoint move to
        such a row would re-attribute both.  The pass leaves it exactly as found
        and asks -- while every row holding nothing follows the definition in
        the same pass.
        """
        with app.app_context():
            template, savings, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[2]
            self._settle_then_revert(recorded, seed_user, Decimal("77.10"))
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "Elsewhere",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()

            template.to_account_id = elsewhere.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            held = db.session.get(Transfer, recorded.id)
            assert held.to_account_id == savings.id, (
                "the retained row must be exactly as the pass found it"
            )
            moved = [
                xfer for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                ) if xfer.id != recorded.id
            ]
            assert len(moved) == 9
            assert all(x.to_account_id == elsewhere.id for x in moved)

    def test_every_derived_column_is_maintained_not_just_the_amount(
        self, app, db, seed_user, seed_periods
    ):
        """All SIX columns a definition states reach the rows it generated.

        **Found missing by an adversarial review of plan step R10-b**, which
        measured it: excluding ``name`` and ``category_id`` from the maintain
        diff left the WHOLE suite green, so a rename of a recurring transfer
        silently not reaching its rows would have shipped.  Only ``amount``,
        ``due_date`` and the endpoint pair were pinned.

        This is the transfer twin of the transaction engine's
        ``test_every_derived_column_is_maintained_not_just_the_amount``: move
        every field at once and read every one back, on the rows the pass kept.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "All Six Destination",
                anchor_balance=Decimal("0.00"),
            )
            other_source = create_account_of_type(
                seed_user, db.session, "Savings", "All Six Source",
                anchor_balance=Decimal("900.00"),
            )
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Rebalance", sort_order=95,
            )
            db.session.add(category)
            db.session.flush()
            old_ids = {xfer.id for xfer in rows}

            template.from_account_id = other_source.id
            template.to_account_id = elsewhere.id
            template.name = "Every Column Moved"
            template.category_id = category.id
            template.default_amount = Decimal("212.12")
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids, (
                "the pass rebuilt rows instead of maintaining them"
            )
            for xfer in live:
                assert xfer.from_account_id == other_source.id
                assert xfer.to_account_id == elsewhere.id
                assert xfer.name == "Every Column Moved"
                assert xfer.category_id == category.id
                assert xfer.amount == Decimal("212.12")
                # The sixth is the rule's, and every-paycheck dates a row from
                # its period's start.
                assert xfer.due_date == db.session.get(
                    PayPeriod, xfer.pay_period_id,
                ).start_date
                expense, income = _assert_shadows_valid(xfer)
                assert expense.category_id == category.id
                assert income.category_id == category.id

    def test_a_SOURCE_move_is_retained_on_a_row_holding_records(
        self, app, db, seed_user, seed_periods
    ):
        """Reattribution is a question about the PAIR, not the destination.

        **Found missing by an adversarial review of plan step R10-b**: every
        endpoint test moved ``to_account_id``, so comparing only the destination
        left the suite green -- and a template whose SOURCE moves would have
        silently re-filed a settled EXPENSE leg's figure onto an account nobody
        asserted it on.  That is the same money defect in the other leg.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[1]
            self._settle_then_revert(recorded, seed_user, Decimal("64.20"))
            new_source = create_account_of_type(
                seed_user, db.session, "Savings", "Moved Source",
                anchor_balance=Decimal("800.00"),
            )
            db.session.flush()
            was_from = recorded.from_account_id

            template.from_account_id = new_source.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            held = db.session.get(Transfer, recorded.id)
            assert held.from_account_id == was_from
            moved = [
                xfer for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                ) if xfer.id != recorded.id
            ]
            assert len(moved) == 9
            assert all(x.from_account_id == new_source.id for x in moved)

    def test_a_NAMED_row_holding_records_still_takes_the_new_amount(
        self, app, db, seed_user, seed_periods
    ):
        """Records retain a row from a RE-ATTRIBUTION, not from every edit.

        The boundary beside this step's headline fix, and an adversarial review
        found no transfer case pinning it.  Retention is
        ``reattributed AND with_records``: a settlement record kept through a
        revert does NOT freeze the row against an ordinary price change, because
        an amount change re-files nothing -- the row stays on the accounts its
        figure was read against.  Freezing it would make a recorded figure stop
        the definition reaching every row it names, which is a different defect
        from the one being fixed.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[6]
            self._settle_then_revert(recorded, seed_user, Decimal("58.00"))

            template.default_amount = Decimal("175.00")
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None, "an amount change re-attributes nothing"
            held = db.session.get(Transfer, recorded.id)
            assert held.amount == Decimal("175.00")
            # The record it kept is untouched by the re-price: what MOVED and
            # what is PLANNED are different facts (plan step X-au-c3).
            legs = db.session.query(Transaction).filter_by(
                transfer_id=recorded.id, is_deleted=False,
            ).all()
            assert [leg.settled_amount for leg in legs] == [
                Decimal("58.00"), Decimal("58.00"),
            ]
            assert all(
                shadow_amount(leg) == Decimal("175.00") for leg in legs
            )

    def test_a_pass_that_changes_nothing_writes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """Re-running a pass over rows already equal to their definition is silent.

        ``update_transfer`` reconciles the posting ledger, re-derives a loan's
        genesis when an endpoint is one, moves the pair's optimistic-lock
        counter and emits an audit row -- so calling it for a row that already
        matches is that whole cost, per row, for nothing.  Measured on a
        production clone: all 99 sweepable rows across the four live recurring
        templates came back with zero fields differing, and the two loan
        templates' passes fell from 1.58 s and 1.14 s to about 4 ms each.

        The version counters are the assertion that matters: a bumped one
        invalidates every open edit form for that transfer.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            versions = {xfer.id: xfer.version_id for xfer in rows}

            with _LogCapture("app.services.transfer_service") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert cap.find_all(EVT_TRANSFER_UPDATED) == []
            assert cap.find_all(EVT_TRANSFER_HARD_DELETED) == []
            db.session.expire_all()
            for xfer_id, version in versions.items():
                assert db.session.get(Transfer, xfer_id).version_id == version

    def test_the_SECOND_pass_after_a_real_change_writes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """A pass that applied a change is a no-op when re-run immediately.

        **Convergence, asked the second time.**  A maintain pass decides what to
        write by comparing each row against what its definition says, so it is
        correct only if applying it makes that comparison come back EMPTY.  A
        field written in a form the comparison cannot recognise -- a Decimal at
        a different scale, a date the door coerces, a value the service
        normalises on the way in -- would leave the pass writing the same row on
        every edit forever, bumping its version counter and re-reconciling its
        ledger each time, while every single-pass test stayed green.

        This project has paid for that shape before: a producer correct on the
        first write and inverted on the repeat.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            template.default_amount = Decimal("133.70")
            template.name = "Renamed Mid-Life"
            db.session.flush()

            with _LogCapture("app.services.transfer_service") as first:
                self._regenerate(template, seed_user, seed_periods)
            assert len(first.find_all(EVT_TRANSFER_UPDATED)) == len(rows), (
                "setup: the first pass must actually have written"
            )
            db.session.expire_all()
            versions = {
                xfer.id: xfer.version_id
                for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            }

            with _LogCapture("app.services.transfer_service") as second:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert second.find_all(EVT_TRANSFER_UPDATED) == [], (
                "the pass did not converge: it rewrites the same rows forever"
            )
            db.session.expire_all()
            for xfer_id, version in versions.items():
                assert db.session.get(Transfer, xfer_id).version_id == version

    def test_a_pass_that_changes_one_field_touches_only_that_field(
        self, app, db, seed_user, seed_periods
    ):
        """The firing control for the silent pass above.

        If the diff were dropped and the whole definition sent every time, the
        test above would still be green on the version counters only by luck --
        so this pins the other side: a real amount change DOES reach the door,
        once per row, naming exactly the field that moved.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )

            template.default_amount = Decimal("140.00")
            db.session.flush()
            with _LogCapture("app.services.transfer_service") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            updates = cap.find_all(EVT_TRANSFER_UPDATED)
            assert len(updates) == len(rows)
            assert {tuple(r.fields_changed) for r in updates} == {("amount",)}
            for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ):
                assert xfer.amount == Decimal("140.00")
                _assert_shadows_valid(xfer)

    def test_the_audit_event_separates_updated_created_and_removed(
        self, app, db, seed_user, seed_periods
    ):
        """One pass, one event, and its counts mean what they say.

        ``deleted_count`` used to count every non-overridden row in the window
        and ``created_count`` counted the same rows again, because the pass
        rebuilt everything it touched.  They now name disjoint sets -- a
        forensic reader comparing across this step must not treat the two as
        the same number -- and ``updated_count`` /
        ``retained_conflict_count`` are new.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            # The note goes on a row the narrowed rule will STOP naming, so the
            # one pass produces all three outcomes at once.
            noted = rows[5]
            assert noted.pay_period_id == seed_periods[5].id
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id, notes="keep me",
            )
            db.session.flush()
            # Narrow the rule to the first FIVE periods and move the amount.
            # Periods 0-4 keep their rows and take the new figure (5 updated);
            # periods 5-9 lose theirs -- four are empty and retire, and the
            # noted one is retained (4 removed, 1 retained).  Nothing is
            # created: every period the narrowed rule names already has a row.
            template.recurrence_rule.end_date = last_covered_day(seed_periods[4])
            template.default_amount = Decimal("111.00")
            db.session.flush()

            with _LogCapture("app.services.transfer_recurrence") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [noted.id]
            [event] = cap.find_all(EVT_TRANSFER_RECURRENCE_REGENERATED)
            assert event.created_count == 0
            assert event.updated_count == 5
            assert event.deleted_count == 4
            assert event.retained_conflict_count == 1
            assert event.overridden_conflict_count == 0
            assert event.deleted_conflict_count == 0
            # The counts describe rows, not just each other.
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert len(live) == 6
            assert sorted(x.amount for x in live) == (
                [Decimal("100.00")] + [Decimal("111.00")] * 5
            )


class TestRegenerateDeletionRoutedThroughService:
    """Commit 34 / LOW-02 / B6-03 -- regen deletes must use the canonical
    transfer_service.delete_transfer path so the orphan-verification
    self-check runs and one EVT_TRANSFER_HARD_DELETED audit event is
    emitted per deleted transfer.  Shadow-pair atomicity is unchanged
    (FK CASCADE already protected it), so this commit asserts forensic
    completeness, not arithmetic correctness.
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + transfer template."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings C34",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Commit 34 Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
            fires_on_day=rule_kwargs.get("day_of_month"),
            fires_in_month=rule_kwargs.get("month_of_year"),
        )
        db.session.refresh(template)
        return template

    def _retire_every_row(self, seed_user, seed_periods, template):
        """Clear the recurrence, then run the pass that retires what it left.

        **The setup these three cases needed from plan step R10-b onwards.**
        Each was written against a regeneration that deleted every
        non-overridden row in the window and rebuilt it, so an AMOUNT change was
        enough to observe a deletion.  A maintain pass deletes only what the
        rule NO LONGER names, so the retirement has to be caused rather than
        assumed -- and clearing the recurrence is the shape that causes the most
        of it, which is the "Does not repeat" edit
        ``regenerate_or_conflict_chooser`` documents.

        The subject of all three is unchanged and is what matters: every
        deletion this engine performs goes through
        ``transfer_service.delete_transfer``.

        Args:
            seed_user: The seeded user fixture.
            seed_periods: The seeded pay periods.
            template: The template whose rows are to be retired.

        Returns:
            ``(retired_ids, capture)`` -- the ids the pass should have removed
            and the :class:`_LogCapture` covering the pass.
        """
        retired_ids = {
            xfer.id for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            )
        }
        template.recurrence_rule = None
        db.session.flush()
        with _LogCapture("app.services.transfer_service") as cap:
            created = transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
        assert created == [], (
            "A cleared recurrence names no period, so it can create nothing."
        )
        return retired_ids, cap

    def test_regen_delete_emits_one_hard_delete_event_per_deletion(
        self, app, db, seed_user, seed_periods
    ):
        """C34-1: every transfer this engine deletes emits exactly one
        ``EVT_TRANSFER_HARD_DELETED`` record on the
        ``app.services.transfer_service`` logger -- proof the bare
        ``db.session.delete(xfer)`` shortcut is gone and the canonical
        service path is the writer.

        **RE-RULED at plan step R10-b**: the deletion is now caused by the rule
        no longer naming the row's period (see :meth:`_retire_every_row`), where
        it used to be caused by any edit at all.  What is asserted is
        unchanged.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10, (
                "Setup expected 10 transfers to retire; got "
                f"{len(created)}."
            )

            deleted_ids, cap = self._retire_every_row(
                seed_user, seed_periods, template,
            )

            hard_delete_records = cap.find_all(EVT_TRANSFER_HARD_DELETED)
            assert len(hard_delete_records) == len(deleted_ids), (
                f"Expected {len(deleted_ids)} {EVT_TRANSFER_HARD_DELETED} "
                f"events (one per deleted transfer); observed "
                f"{len(hard_delete_records)}."
            )

            observed_transfer_ids = {r.transfer_id for r in hard_delete_records}
            assert observed_transfer_ids == deleted_ids

            for record in hard_delete_records:
                assert record.levelno == logging.INFO
                assert record.user_id == seed_user["user"].id
                # orphan_count is the service's self-check result (see C34-2).
                assert record.orphan_count == 0

            # The rows really are gone, not merely reported.
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0

    def test_regen_delete_runs_orphan_verification(
        self, app, db, seed_user, seed_periods
    ):
        """C34-2: the service path runs the orphan-verification
        self-check (``query(Transaction).filter_by(transfer_id=...).count()``)
        and surfaces the result on the audit event.  An ``orphan_count``
        attribute on every hard-delete record proves the check ran;
        ``== 0`` proves the FK CASCADE behaved as expected.

        **RE-RULED at plan step R10-b**: see :meth:`_retire_every_row` for why
        the deletion is now caused rather than assumed.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            _, cap = self._retire_every_row(seed_user, seed_periods, template)

            hard_delete_records = cap.find_all(EVT_TRANSFER_HARD_DELETED)
            assert hard_delete_records, (
                "Orphan check missing: no EVT_TRANSFER_HARD_DELETED "
                "records were emitted by the canonical service path."
            )
            for record in hard_delete_records:
                assert hasattr(record, "orphan_count"), (
                    "EVT_TRANSFER_HARD_DELETED record missing "
                    "orphan_count -- the service's self-check did not run."
                )
                assert record.orphan_count == 0, (
                    "FK CASCADE failed to remove shadows: orphan_count "
                    f"= {record.orphan_count} (expected 0)."
                )

    def test_regen_delete_leaves_no_orphan_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """C34-3: shadow-pair atomicity is unchanged.  Both shadows of
        every RETIRED transfer are gone (FK CASCADE), and no
        ``transactions.transfer_id`` points at a missing transfer.
        Locks the no-balance-drift property the commit promises.

        **RE-RULED at plan step R10-b**: see :meth:`_retire_every_row` for why
        the deletion is now caused rather than assumed.  A row the rule still
        names keeps both of its shadows, which is
        ``test_maintaining_keeps_the_shadows_it_already_has``.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            pre_shadow_ids: list[int] = []
            for xfer in created:
                shadows = (
                    db.session.query(Transaction)
                    .filter_by(transfer_id=xfer.id)
                    .all()
                )
                assert len(shadows) == 2
                pre_shadow_ids.extend(s.id for s in shadows)

            self._retire_every_row(seed_user, seed_periods, template)
            db.session.commit()
            db.session.expire_all()

            for sid in pre_shadow_ids:
                assert db.session.get(Transaction, sid) is None, (
                    f"Shadow {sid} survived regen -- FK CASCADE failed."
                )

            # No live shadow may reference a missing transfer.
            live_shadows = (
                db.session.query(Transaction)
                .filter(Transaction.transfer_id.isnot(None))
                .all()
            )
            for shadow in live_shadows:
                parent = db.session.get(Transfer, shadow.transfer_id)
                assert parent is not None, (
                    f"Orphaned shadow {shadow.id} -> missing "
                    f"transfer {shadow.transfer_id}."
                )

    def test_transfer_recurrence_module_has_no_bare_delete(self):
        """C34-4: lock the structural fix -- the source of
        ``app/services/transfer_recurrence.py`` must contain no bare
        ``db.session.delete(xfer)`` (or ``db.session.delete(transfer)``).
        Pins Transfer Invariant 4 literally: every deletion writer-path
        into ``budget.transfers`` is ``transfer_service.delete_transfer``.
        """
        module_path = (
            Path(__file__).resolve().parents[2]
            / "app" / "services" / "transfer_recurrence.py"
        )
        source = module_path.read_text(encoding="utf-8")
        assert "db.session.delete(xfer)" not in source, (
            "Found db.session.delete(xfer) in transfer_recurrence.py -- "
            "regen must route through transfer_service.delete_transfer "
            "(B6-03 / LOW-02 / Transfer Invariant 4)."
        )
        assert "db.session.delete(transfer)" not in source, (
            "Found db.session.delete(transfer) in transfer_recurrence.py "
            "-- regen must route through transfer_service.delete_transfer."
        )


class TestATransferRecordsItsOccurrence:
    """``Transfer.occurs_on`` -- plan step **R17**, ledger row **D57**.

    The transfer half of the same leaf.  Both engines write the occurrence from
    the same ``PlannedOccurrence`` through the same shared helpers, so what is
    specific here is the pair: a transfer's two SHADOW transactions are created
    by ``transfer_service`` from their parent rather than by the engine from an
    occurrence, and no generate pass asks a shadow whether an occurrence has
    been written.  Mirroring the column onto them would put a second writer on
    it, so the control asserts they stay NULL.
    """

    def _make_template_with_rule(self, seed_user, cadence):
        """Helper: savings account + transfer template with an authored rule."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
            anchor_balance=Decimal("500.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Occurrence Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        make_cadence_rule(template, cadence, interval_n=1)
        db.session.refresh(template)
        return template

    def test_generate_writes_the_occurrence_on_the_transfer(
        self, app, db, seed_user, seed_periods
    ):
        """Each generated transfer carries the date its cadence names."""
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            plan = resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            created = transfer_recurrence.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs the engine to create something"
            assert (
                sorted(row.occurs_on for row in created)
                == sorted(p.occurrence for p in plan.placements)
            )

    def test_the_shadows_carry_no_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A shadow is created from its PARENT, never from an occurrence.

        Transfer Invariant 4 makes ``transfer_service`` the only writer of a
        shadow; this control is what keeps ``occurs_on`` from acquiring a
        second writer through the mirroring the amount, status and period go
        through.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = transfer_recurrence.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            shadows = (
                db.session.query(Transaction)
                .filter(Transaction.transfer_id.in_([x.id for x in created]))
                .all()
            )
            assert len(shadows) == 2 * len(created), (
                "the control needs both shadows of every transfer"
            )
            assert all(shadow.occurs_on is None for shadow in shadows)
