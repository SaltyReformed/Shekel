"""
Shekel Budget App -- a row of a definition is dated (plan step balance:X-bv-2)

``ck_transactions_template_row_needs_due_date`` is ``template_id IS NULL OR
due_date IS NOT NULL`` on ``budget.transactions`` (ruling **R-BAL6**, finding
**BAL-463**, migration ``4d7123cd9803``).  A template-linked row with no due
date is unpriceable the moment its figure is handed back to its definition --
amount rule 3 resolves the definition's series *as of the row's own date* and
ruling D5 forbids the period's bounds as a substitute -- and plan step X-bv
closed the one producer of that row.  This constraint makes the state
unrepresentable rather than merely unproduced, and these cases grade the
constraint itself, in every direction a writer could reach it from.

**The first case is the one that tells the two-term form from the withdrawn
three-term one.**  ``... OR amount_source_id IS NULL OR due_date IS NOT NULL``
ADMITS an undated row that owns its figure and refuses only the declare that
would derive it -- so a mutation swapping that predicate in passes every case
here except the first, where an OWN-amount linked row with no date must be
refused on INSERT.  That is what R-BAL6 means by *invariant under the
chooser's declare*: the declare touches neither column the CHECK names, so
the CHECK cannot be what turns a button press into a 500.

Every row here is built BARE, for the reason ``test_settle_day_basis`` gives:
the door helpers exist precisely to make the refused states unreachable, so a
control routed through one would grade the helper and never the constraint.

The last class drives the migration's own ``downgrade()`` and ``upgrade()``
over this test's private database clone, so the refusal the upgrade makes on
a violating row is FIRED here rather than left as the one path a green suite
never runs.
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
from tests._test_helpers import load_migration_module, make_expense_template

_CONSTRAINT = "ck_transactions_template_row_needs_due_date"

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


def _constraint_is_bound():
    """Return whether the CHECK exists on ``budget.transactions`` right now."""
    return bool(db.session.execute(sqlalchemy.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint c"
        "  JOIN pg_class t ON t.oid = c.conrelid"
        "  JOIN pg_namespace n ON n.oid = t.relnamespace"
        "  WHERE n.nspname = 'budget' AND t.relname = 'transactions'"
        "    AND c.contype = 'c' AND c.conname = :name"
        ")"
    ), {"name": _CONSTRAINT}).scalar())


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


class TestATemplateRowIsDated:
    """The CHECK, from every side a writer could reach it."""

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
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert _CONSTRAINT in str(exc.value)

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
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert _CONSTRAINT in str(exc.value)

    def test_linking_an_undated_row_to_a_definition_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The other UPDATE direction: the link cannot arrive without a date.

        No writer in ``app/`` sets ``template_id`` on an existing row -- that
        census is why R-BAL6 needs no guard -- and this is the storage tier
        holding the same line for a writer the census could not see.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            txn = _make_transaction(seed_user, seed_periods, due_date=None)
            db.session.add(txn)
            db.session.commit()

            txn.template_id = template.id
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert _CONSTRAINT in str(exc.value)

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


class TestTheMigrationRoundTrips:
    """``4d7123cd9803``'s two steps, driven over this test's own clone."""

    def test_the_downgrade_drops_it_and_the_upgrade_refuses_a_violating_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Down, plant the forbidden row, up REFUSES, remove it, up BINDS.

        Each phase is graded by what the database then ADMITS rather than by
        the constraint's name alone: after the downgrade the undated linked
        row must be storable (so the drop was real), and after the final
        upgrade the same insert must be refused again (so the bind was).
        """
        with app.app_context():
            assert _constraint_is_bound()

            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert not _constraint_is_bound()

            template = make_expense_template(db.session, seed_user)
            undated = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id, due_date=None,
            )
            db.session.add(undated)
            db.session.commit()
            planted_id = undated.id
            assert planted_id is not None

            # The refusal, fired: PostgreSQL validates every existing row as
            # part of the ALTER TABLE, and this one fails it.
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                _run(_MIGRATION.upgrade)
            assert _CONSTRAINT in str(exc.value)
            db.session.rollback()
            assert not _constraint_is_bound(), (
                "a refused upgrade must leave the constraint unbound"
            )

            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.transactions WHERE id = :id"
            ), {"id": planted_id})
            db.session.commit()

            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _constraint_is_bound()

            db.session.add(_make_transaction(
                seed_user, seed_periods,
                template_id=template.id, due_date=None,
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert _CONSTRAINT in str(exc.value)
