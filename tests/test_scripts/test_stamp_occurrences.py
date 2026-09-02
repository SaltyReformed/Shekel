"""Tests for ``scripts/stamp_occurrences.py`` -- the ``occurs_on`` backfill.

Plan step **recurrence:R17**, ledger row **D57**.  The script fills the
occurrence column on rows the recurrence engines wrote before it existed, by
matching each template's rows against the occurrences its rule names.

**Two of these controls exist because the first implementation FAILED them**,
and both failures would have written a wrong date onto a money row:

  * it filtered ROWS to the unstamped ones but not OCCURRENCES to the
    unclaimed ones, so a second run let a leftover row STEAL an occurrence a
    stamped row already held;
  * it matched on the pay period before the due date, so where a period held
    two rows of one template -- which is exactly what a MOVED row produces,
    an ``is_override`` row sitting outside the partial unique index -- the two
    rows were assigned each other's occurrences;
  * it carried a THIRD rule, stamping the last row from the last occurrence
    when one of each remained.  Adversarial review reproduced that rule
    pairing two INDEPENDENT anomalies -- a carry-forward envelope row, which
    answers no occurrence, and an occurrence whose row had been deleted --
    stamping a `$12.34` roll-forward as a car payment nine paychecks away.
    Under the predicate leaf such a row SUPPRESSES the real bill.  The rule is
    gone; ``test_a_row_the_engine_could_not_have_written_is_never_stamped``
    is what keeps it gone.

The script is imported rather than run as a subprocess: it is a pure data pass
with no argv, no environment gate and no credential handling, so the behaviour
under test is the function, and a subprocess would only add a second database
connection to reason about.
"""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.ref import AccountType
from app.services import account_service, transfer_recurrence
from app.models.ref import TransactionType
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import make_cadence_rule
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY_FIRST
from app.models.amount_ownership import AmountOwnership


def _load_script():
    """Import ``scripts/stamp_occurrences.py`` as a module.

    It lives outside the package, so it is loaded by path rather than by name.
    """
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "stamp_occurrences.py"
    )
    spec = importlib.util.spec_from_file_location("stamp_occurrences", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="stamper")
def _stamper():
    """The loaded backfill module."""
    return _load_script()


class TestStampOccurrences:
    """The backfill's three deductions, its ordering and its idempotence."""

    def _template(self, seed_user, name="Stamped"):
        """Build a template with an every-paycheck rule."""
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name=name,
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        make_cadence_rule(template, EVERY_PERIOD)
        db.session.flush()
        db.session.refresh(template)
        return template

    def _generate(self, seed_user, seed_periods, template):
        """Generate this template's rows across the seeded periods."""
        schedule = GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id),
            {p.id for p in seed_periods},
        )
        rows = recurrence_engine.generate_for_template(
            template, schedule, seed_user["scenario"].id,
        )
        db.session.flush()
        return rows

    def test_it_restores_the_occurrence_the_engine_wrote(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """Blanking every occurrence and stamping puts each one back.

        The engine's own answer is the oracle: the backfill has to reproduce
        it exactly, or a row's occurrence is a different fact from the one
        generation would have written.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            truth = {row.id: row.occurs_on for row in rows}
            assert all(truth.values()), "the control needs stamped rows"

            for row in rows:
                row.occurs_on = None
            db.session.flush()

            stamper.stamp_occurrences()

            for row in rows:
                db.session.refresh(row)
                assert row.occurs_on == truth[row.id]

    def test_a_moved_row_is_matched_by_its_due_date_not_its_period(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """Two rows in one paycheck keep their OWN occurrences.

        The regression this pins: matching on the period first assigned the
        two rows each other's occurrence, because an ``is_override`` row may
        share a paycheck with that paycheck's own row and the period does not
        say which is which.  Measured on production as ``Kayla's Spending
        Money`` ids 2388/2389.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            moved, host = rows[0], rows[1]
            moved_occurrence = moved.occurs_on
            host_occurrence = host.occurs_on
            assert moved_occurrence != host_occurrence

            # What the PATCH door does: the row moves paycheck and becomes the
            # owner's, keeping the due_date it was written with.
            moved.pay_period_id = host.pay_period_id
            moved.is_override = True
            moved.occurs_on = None
            host.occurs_on = None
            db.session.flush()

            stamper.stamp_occurrences()
            db.session.refresh(moved)
            db.session.refresh(host)

            assert moved.occurs_on == moved_occurrence, (
                "the moved row took another occurrence's date"
            )
            assert host.occurs_on == host_occurrence

    def test_a_second_run_stamps_nothing_and_steals_nothing(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """Idempotence, and the reason it is not merely cosmetic.

        A row no occurrence claims must STAY unclaimed however many times the
        pass runs.  The first implementation offered it every occurrence again
        on the second run, because only the ROWS were filtered to the unstamped
        ones -- so a cancelled duplicate took the occurrence its live sibling
        already held.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            host = rows[0]

            # An extra row of this template in an occupied paycheck, carrying a
            # due date no occurrence computes: nothing may ever claim it.
            orphan = Transaction(
                account_id=host.account_id,
                template_id=template.id,
                pay_period_id=host.pay_period_id,
                scenario_id=host.scenario_id,
                status_id=host.status_id,
                name=host.name,
                category_id=host.category_id,
                transaction_type_id=host.transaction_type_id,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                due_date=date(1990, 1, 1),
                is_override=True,
                is_deleted=False,
            )
            db.session.add(orphan)
            db.session.flush()

            stamper.stamp_occurrences()
            db.session.refresh(orphan)
            assert orphan.occurs_on is None, (
                "an orphan row was given an occurrence on the first pass"
            )
            claimed = {row.id: row.occurs_on for row in rows}

            stamper.stamp_occurrences()

            db.session.refresh(orphan)
            assert orphan.occurs_on is None, (
                "the second run let an orphan steal a claimed occurrence"
            )
            for row in rows:
                db.session.refresh(row)
                assert row.occurs_on == claimed[row.id]

    def test_it_writes_only_the_occurrence_column(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """No row is created, deleted, re-dated or moved.

        Deliberate: a duplicate this defect already created is a row the
        statement-matching surfaces offer, and changing which rows exist is not
        this pass's business.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            before = {
                row.id: (row.pay_period_id, row.due_date, row.estimated_amount,
                         row.status_id, row.is_deleted, row.is_override)
                for row in rows
            }
            for row in rows:
                row.occurs_on = None
            db.session.flush()
            count_before = db.session.query(Transaction).count()

            stamper.stamp_occurrences()

            assert db.session.query(Transaction).count() == count_before
            for row in rows:
                db.session.refresh(row)
                assert before[row.id] == (
                    row.pay_period_id, row.due_date, row.estimated_amount,
                    row.status_id, row.is_deleted, row.is_override,
                )

    def test_a_row_the_engine_could_not_have_written_is_never_stamped(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """A carry-forward envelope row answers no occurrence, ever.

        The control for the PROVENANCE FILTER.  ``carry_forward_service._execute``
        creates a template-linked row with ``due_date = None`` and
        ``is_override = True`` because there is no cadence to date it from.  The
        engine always dates what it writes, so a NULL due date is proof the row
        came from somewhere else.

        The fixture also frees an occurrence, so the row is the exact shape
        adversarial review paired with one.  The filter keeps it out of the row
        set entirely; the sibling control below covers the rule that did the
        pairing, on a row the filter does NOT exclude.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            host, hole = rows[0], rows[1]
            freed, hole_period_id = hole.occurs_on, hole.pay_period_id
            db.session.delete(hole)
            db.session.flush()

            envelope = Transaction(
                account_id=host.account_id,
                template_id=template.id,
                # The HOLE's period, deliberately: that occurrence is now free,
                # so without the provenance filter ``_match_by_period`` alone
                # stamps this row -- no second defect required.
                pay_period_id=hole_period_id,
                scenario_id=host.scenario_id,
                status_id=host.status_id,
                name=host.name,
                category_id=host.category_id,
                transaction_type_id=host.transaction_type_id,
                amount_ownership=AmountOwnership.own(Decimal("12.34")),
                due_date=None,
                is_override=True,
                is_deleted=False,
            )
            db.session.add(envelope)
            # Blank one legitimately stampable row so the pass actually RUNS.
            # Without it the pre-flight -- which applies the same provenance
            # filter -- counts zero and returns before any rule executes, and
            # this control would pass for the wrong reason.
            host_truth = host.occurs_on
            host.occurs_on = None
            db.session.flush()

            stamper.stamp_occurrences()
            db.session.refresh(envelope)
            db.session.refresh(host)

            assert host.occurs_on == host_truth, (
                "the control needs the pass to have actually run"
            )
            assert envelope.occurs_on is None, (
                f"a carry-forward row was stamped {envelope.occurs_on}; the "
                f"freed occurrence was {freed}"
            )
            still_free = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, occurs_on=freed)
                .count()
            )
            assert still_free == 0, (
                "the deleted row's occurrence was claimed by something else"
            )

    def test_the_occurrence_is_not_a_copy_of_the_due_date(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """The backfill restores a cadence date the row does not carry.

        Every other test here uses an every-paycheck rule, where the occurrence
        and the due date coincide -- so none of them can tell this pass from a
        `SET occurs_on = due_date`, which is the migration the module docstring
        argues against.  A ``Monthly First`` rule occurs on the 1st and is dated
        on the payday, so here the two genuinely differ.
        """
        with app.app_context():
            template = self._template(seed_user, name="Monthly First")
            db.session.delete(template.recurrence_rule)
            db.session.flush()
            make_cadence_rule(template, MONTHLY_FIRST)
            db.session.flush()
            db.session.refresh(template)

            rows = self._generate(seed_user, seed_periods, template)
            assert rows, "the control needs generated rows"
            assert any(r.occurs_on != r.due_date for r in rows), (
                "the control is measuring nothing if the columns agree"
            )
            truth = {r.id: r.occurs_on for r in rows}
            for row in rows:
                row.occurs_on = None
            db.session.flush()

            stamper.stamp_occurrences()

            for row in rows:
                db.session.refresh(row)
                assert row.occurs_on == truth[row.id]
                assert row.occurs_on.day == 1

    def test_an_archived_templates_rows_are_left_alone(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """Only ACTIVE templates are walked, as every generate path does.

        An archived template is never generated into, so its rows cannot be
        duplicated and NULL is the correct answer for them -- and walking its
        rule would drive the engine over cadences no generate path exercises.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            for row in rows:
                row.occurs_on = None
            template.is_active = False
            db.session.flush()

            stamper.stamp_occurrences()

            for row in rows:
                db.session.refresh(row)
                assert row.occurs_on is None

    def test_a_transfer_gets_its_occurrence_too(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """The Transfer arm of the model/FK pairing is exercised.

        Nothing else in this file touches it, so a mis-paired model and foreign
        key column there would pass the whole suite.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("500.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Stamped Transfer",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            make_cadence_rule(template, EVERY_PERIOD)
            db.session.refresh(template)

            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = transfer_recurrence.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "the control needs generated transfers"
            truth = {x.id: x.occurs_on for x in created}
            for xfer in created:
                xfer.occurs_on = None
            db.session.flush()

            stamper.stamp_occurrences()

            for xfer in created:
                db.session.refresh(xfer)
                assert xfer.occurs_on == truth[xfer.id]

    def test_an_unclaimed_row_never_takes_a_rowless_occurrence(
        self, app, db, seed_user, seed_periods, stamper
    ):
        """THE control for the cut third rule, on a row the filter allows.

        Adversarial review showed that stamping "the last row from the last
        occurrence" pairs two INDEPENDENT anomalies of opposite sign rather
        than deducing anything: one extra row that answers nothing, and one
        occurrence whose row is gone.

        Both are built here, and both are reachable in production: the extra
        row has a due date no occurrence computes, which is the shape of the
        five settled rows on this developer's data that predate their rule's
        edited start, and the rowless occurrence is what a hard-deleted retired
        row or a template archived across a period leaves behind.

        Neither may be stamped.  If they are, the predicate leaf reads the
        occurrence as answered and the real bill silently stops generating --
        worse than the duplicate this whole step exists to stop, because
        nothing shows it.
        """
        with app.app_context():
            template = self._template(seed_user)
            rows = self._generate(seed_user, seed_periods, template)
            host, hole = rows[0], rows[1]
            freed = hole.occurs_on
            db.session.delete(hole)
            db.session.flush()

            # A row the ENGINE could have written -- it carries a due date --
            # but that no occurrence of this rule names.
            stranded = Transaction(
                account_id=host.account_id,
                template_id=template.id,
                pay_period_id=host.pay_period_id,
                scenario_id=host.scenario_id,
                status_id=host.status_id,
                name=host.name,
                category_id=host.category_id,
                transaction_type_id=host.transaction_type_id,
                amount_ownership=AmountOwnership.own(Decimal("12.34")),
                due_date=date(1990, 1, 1),
                is_override=True,
                is_deleted=False,
            )
            db.session.add(stranded)
            db.session.flush()

            stamper.stamp_occurrences()
            db.session.refresh(stranded)

            assert stranded.occurs_on is None, (
                f"a stranded row was stamped {stranded.occurs_on}; the rowless "
                f"occurrence was {freed}.  A rule that pairs one leftover row "
                f"with one leftover occurrence has guessed, not deduced"
            )
            assert (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, occurs_on=freed).count() == 0
            ), "the rowless occurrence was claimed by something"
