"""
Shekel Budget App -- a row of a definition is dated (plan step balance:X-bv-2)

One fact on both row tables (ruling **R-BAL17**, finding **BAL-463**,
migration ``4d7123cd9803``)::

    ck_transactions_template_row_needs_due_date
        template_id IS NULL OR due_date IS NOT NULL
    ck_transfers_template_row_needs_due_date
        transfer_template_id IS NULL OR due_date IS NOT NULL

A row that names a recurring definition is unpriceable without a date: amount
rule 3 resolves the definition's series *as of the row's own date*, ruling D5
forbids the period's bounds as a substitute, and plan step X-bv closed the one
producer of the undated row.  These constraints make the state unrepresentable
rather than merely unproduced -- and the refusal arm
``cash_ledger._definition_cash._stated_amount`` kept for that state is deleted
with them, so these cases are the refusal's successor as well as the
constraint's grading.  Each is exercised in every direction a writer could
reach it from, on each table.

**The three refusal cases of each class are what tell the two-term form from
the withdrawn three-term one.**  ``... OR amount_source_id IS NULL OR due_date
IS NOT NULL`` ADMITS an undated row that owns its figure and refuses only the
declare that would derive it -- and every row built here OWNS its figure, so
a mutation swapping that predicate in (model and migration) turns all six
refusal cases and the migration round trip red and leaves only the four
accepted-shape controls green (measured 2026-09-12).  That is what ruling
R-BAL6 means by *invariant under the chooser's declare*: the declare touches
neither column the CHECK names, so the CHECK cannot be what turns a button
press into a 500.

Every row here is built BARE, for the reason ``test_settle_day_basis`` gives:
the door helpers exist precisely to make the refused states unreachable, so a
control routed through one would grade the helper and never the constraint.
A bare ``Transfer`` has no shadow pair, which is fine for a constraint over
the parent's own columns; it is the shape ``test_amount_ownership``'s
Core-insert probe and its ad-hoc arm use, while a transfer OF A DEFINITION
anywhere else in the suite is the engine's (``generate_transfer_of``, plan
step X-ch).  **The two link-arrival cases assign the link onto an existing
row** (``txn.template_id = ...``, ``xfer.transfer_template_id = ...``), which
is the pattern ledger row BAL-480's closing census greps for: they are that
census's one deliberate exception, because the UPDATE direction of a CHECK
over the link cannot be graded any other way.

The last class drives the migration's own ``downgrade()`` and ``upgrade()``
over this test's private database clone, so the refusal the upgrade makes on
a violating row is FIRED here, once per table, rather than left as the one
path a green suite never runs.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    create_savings_account,
    load_migration_module,
    make_expense_template,
    make_transfer_template,
)

_TXN_CONSTRAINT = "ck_transactions_template_row_needs_due_date"
_XFER_CONSTRAINT = "ck_transfers_template_row_needs_due_date"

_MIGRATION = load_migration_module("4d7123cd9803_a_template_row_is_dated.py")


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return an UNFLUSHED Projected expense row that OWNS its figure.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list; the row lands in the
            first.
        **overrides: Column values to set or replace -- ``template_id`` and
            ``due_date`` are what the cases vary.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    fields = {
        "user_id": seed_periods[0].user_id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Dated-row control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "amount_ownership": AmountOwnership.own(Decimal("300.00")),
    }
    fields.update(overrides)
    return Transaction(**fields)


def _savings(seed_user):
    """Return a flushed savings account for the seed user, a transfer's far end."""
    return create_savings_account(
        seed_user, db.session, "Dated-row savings", Decimal("0.00"),
    )


def _make_transfer(seed_user, seed_periods, savings, **overrides):
    """Return an UNFLUSHED Projected transfer that OWNS its figure, no shadows.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list; the transfer lands
            in the first.
        savings: The account the money arrives at
            (``ck_transfers_different_accounts``).
        **overrides: Column values to set or replace --
            ``transfer_template_id`` and ``due_date`` are what the cases vary.

    Returns:
        The unflushed :class:`~app.models.transfer.Transfer`.
    """
    fields = {
        "user_id": seed_periods[0].user_id,
        "from_account_id": seed_user["account"].id,
        "to_account_id": savings.id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Dated-transfer control",
        "category_id": seed_user["categories"]["Rent"].id,
        "amount_ownership": AmountOwnership.own(Decimal("200.00")),
    }
    fields.update(overrides)
    return Transfer(**fields)


def _constraint_is_bound(table, name):
    """Return whether the CHECK *name* exists on ``budget.<table>`` right now."""
    return bool(db.session.execute(sqlalchemy.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint c"
        "  JOIN pg_class t ON t.oid = c.conrelid"
        "  JOIN pg_namespace n ON n.oid = t.relnamespace"
        "  WHERE n.nspname = 'budget' AND t.relname = :table"
        "    AND c.contype = 'c' AND c.conname = :name"
        ")"
    ), {"table": table, "name": name}).scalar())


def _both_bound():
    """Return ``(transactions bound, transfers bound)``."""
    return (
        _constraint_is_bound("transactions", _TXN_CONSTRAINT),
        _constraint_is_bound("transfers", _XFER_CONSTRAINT),
    )


def _run(step):
    """Drive one of the migration's steps over this test's own connection.

    The idiom ``test_c41_baseline_unique_migration`` established: Alembic's
    ``op`` proxy needs an operations context, and ``op.get_bind`` is patched so
    a step that asks for the bind gets the session's connection rather than a
    second one.  The caller commits or rolls back.

    Args:
        step: ``_MIGRATION.upgrade`` or ``_MIGRATION.downgrade``.
    """
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


def _flush_refused_by(constraint):
    """Flush, asserting the database refuses it naming *constraint*."""
    with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
        db.session.flush()
    assert constraint in str(exc.value)


class TestATransactionOfADefinitionIsDated:
    """``ck_transactions_template_row_needs_due_date``, from every side."""

    def test_an_undated_row_that_names_a_definition_is_refused_on_insert(
        self, app, db, seed_user, seed_periods,
    ):
        """The state X-bv stopped producing cannot be stored by anything.

        The row OWNS its figure, so the withdrawn three-term predicate would
        have admitted it -- this is the case that distinguishes the ruling's
        form from the one it replaced.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                template_id=template.id, due_date=None,
            ))
            _flush_refused_by(_TXN_CONSTRAINT)

    def test_clearing_the_date_on_a_row_of_a_definition_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The UPDATE direction, which is the route gate's backstop.

        ``_gates._reject_generated_due_date_edit`` answers a PATCH with a
        designed 400 before any statement is issued; this is what a writer
        that is not the application meets instead.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id, due_date=date(2026, 3, 15),
            )
            db.session.add(txn)
            db.session.commit()

            txn.due_date = None
            _flush_refused_by(_TXN_CONSTRAINT)

    def test_linking_an_undated_row_to_a_definition_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The other UPDATE direction: the link cannot arrive without a date.

        No writer in ``app/`` sets ``template_id`` on an existing row -- that
        census is why the two-term form needs no guard -- and this is the
        storage tier holding the same line for a writer the census could not
        see.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            txn = _make_transaction(seed_user, seed_periods, due_date=None)
            db.session.add(txn)
            db.session.commit()

            txn.template_id = template.id
            _flush_refused_by(_TXN_CONSTRAINT)

    def test_an_undated_row_that_names_no_definition_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The control against over-refusal: an ad-hoc row may carry no date.

        Nothing prices such a row by its date, and the create form offers the
        field as optional.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, due_date=None)
            db.session.add(txn)
            db.session.commit()
            assert txn.id is not None
            assert txn.due_date is None

    def test_a_dated_row_that_names_a_definition_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The shape every generated row has: linked, and dated."""
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id, due_date=date(2026, 3, 15),
            )
            db.session.add(txn)
            db.session.commit()
            assert txn.id is not None


class TestATransferOfADefinitionIsDated:
    """``ck_transfers_template_row_needs_due_date``, from every side.

    The twin table, graded case for case with the transaction one above: the
    transfer recurrence engine splats ``DerivedTransferFields`` (dated by
    ``compute_due_date``) and the one-time branch of
    ``routes/transfers/_instances`` writes the chosen paycheck's start, so
    every producer of a linked transfer dates it, and ``_stated_amount``
    prices a transfer through the same arm it prices a transaction.
    """

    def test_an_undated_transfer_that_names_a_definition_is_refused_on_insert(
        self, app, db, seed_user, seed_periods,
    ):
        """An OWN-figure linked transfer with no date is refused on INSERT.

        ``ck_transfers_adhoc_owns_amount`` admits an own figure beside a link,
        so this row is legal in every respect but its date -- the three-term
        form would have admitted it, and this is the case that tells the two
        apart on this table.
        """
        with app.app_context():
            savings = _savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.add(_make_transfer(
                seed_user, seed_periods, savings,
                transfer_template_id=template.id, due_date=None,
            ))
            _flush_refused_by(_XFER_CONSTRAINT)

    def test_clearing_the_date_on_a_transfer_of_a_definition_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The UPDATE direction, the backstop of the transfer's two PATCH doors.

        Both doors refuse the field through
        ``Transfer.due_date_is_its_definitions`` with a designed 400; this is
        what a writer that is not the application meets instead.
        """
        with app.app_context():
            savings = _savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)
            xfer = _make_transfer(
                seed_user, seed_periods, savings,
                transfer_template_id=template.id, due_date=date(2026, 3, 15),
            )
            db.session.add(xfer)
            db.session.commit()

            xfer.due_date = None
            _flush_refused_by(_XFER_CONSTRAINT)

    def test_linking_an_undated_transfer_to_a_definition_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The other UPDATE direction: the link cannot arrive without a date."""
        with app.app_context():
            savings = _savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)
            xfer = _make_transfer(seed_user, seed_periods, savings, due_date=None)
            db.session.add(xfer)
            db.session.commit()

            xfer.transfer_template_id = template.id
            _flush_refused_by(_XFER_CONSTRAINT)

    def test_an_undated_transfer_that_names_no_definition_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The control against over-refusal: an ad-hoc transfer may carry no date."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _make_transfer(seed_user, seed_periods, savings, due_date=None)
            db.session.add(xfer)
            db.session.commit()
            assert xfer.id is not None
            assert xfer.due_date is None

    def test_a_dated_transfer_that_names_a_definition_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The shape every generated transfer has: linked, and dated."""
        with app.app_context():
            savings = _savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)
            xfer = _make_transfer(
                seed_user, seed_periods, savings,
                transfer_template_id=template.id, due_date=date(2026, 3, 15),
            )
            db.session.add(xfer)
            db.session.commit()
            assert xfer.id is not None


class TestTheMigrationRoundTrips:
    """``4d7123cd9803``'s two steps, driven over this test's own clone."""

    def test_the_downgrade_drops_both_and_the_upgrade_refuses_a_violating_row_on_either_table(
        self, app, db, seed_user, seed_periods,
    ):
        """Down, plant one forbidden row per table in turn, up REFUSES each, then up BINDS.

        Each phase is graded by what the database then ADMITS rather than by
        the constraint's name alone: after the downgrade the undated linked
        row must be storable on each table (so the drop was real), and after
        the final upgrade the same insert must be refused again on each (so
        the bind was).  The two violations are planted ONE AT A TIME so each
        refusal is shown to be its own table's: with a transaction planted the
        upgrade dies on the first ``ALTER TABLE``; with only a transfer
        planted the first succeeds and the second dies, and the rollback must
        take the first back with it.
        """
        with app.app_context():
            assert _both_bound() == (True, True)

            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert _both_bound() == (False, False)

            savings = _savings(seed_user)
            txn_template = make_expense_template(db.session, seed_user)
            xfer_template = make_transfer_template(
                db.session, seed_user, savings,
            )
            db.session.commit()

            # Phase 1: an undated linked TRANSACTION is storable, and the
            # upgrade refuses naming the transactions constraint.
            undated_txn = _make_transaction(
                seed_user, seed_periods,
                template_id=txn_template.id, due_date=None,
            )
            db.session.add(undated_txn)
            db.session.commit()
            planted_txn_id = undated_txn.id
            assert planted_txn_id is not None

            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                _run(_MIGRATION.upgrade)
            assert _TXN_CONSTRAINT in str(exc.value)
            db.session.rollback()
            assert _both_bound() == (False, False), (
                "a refused upgrade must leave both constraints unbound"
            )
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.transactions WHERE id = :id"
            ), {"id": planted_txn_id})
            db.session.commit()

            # Phase 2: an undated linked TRANSFER is storable, and the upgrade
            # refuses naming the transfers constraint -- after the
            # transactions ALTER TABLE has already succeeded inside the same
            # transaction, so the rollback is what un-binds it.
            undated_xfer = _make_transfer(
                seed_user, seed_periods, savings,
                transfer_template_id=xfer_template.id, due_date=None,
            )
            db.session.add(undated_xfer)
            db.session.commit()
            planted_xfer_id = undated_xfer.id
            assert planted_xfer_id is not None

            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                _run(_MIGRATION.upgrade)
            assert _XFER_CONSTRAINT in str(exc.value)
            db.session.rollback()
            assert _both_bound() == (False, False), (
                "a refused upgrade must leave both constraints unbound, "
                "including the one whose ALTER TABLE had already succeeded"
            )
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.transfers WHERE id = :id"
            ), {"id": planted_xfer_id})
            db.session.commit()

            # Clean: the upgrade binds both, and each table refuses again.
            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _both_bound() == (True, True)

            db.session.add(_make_transaction(
                seed_user, seed_periods,
                template_id=txn_template.id, due_date=None,
            ))
            _flush_refused_by(_TXN_CONSTRAINT)
            db.session.rollback()

            db.session.add(_make_transfer(
                seed_user, seed_periods, savings,
                transfer_template_id=xfer_template.id, due_date=None,
            ))
            _flush_refused_by(_XFER_CONSTRAINT)
