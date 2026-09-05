"""The occurrence index covers a RE-PRICED row (plan step balance:X-au-h).

Migration ``e7c3a1f9b482`` dropped ``is_override = FALSE`` from the
occurrence-keyed unique index on both row tables and kept it on the
paycheck-keyed undated one.  Three things need grading and only the first is
visible in a schema dump:

* the predicates actually differ, in the right direction, on both tables;
* the guarantee the ruling BOUGHT -- a second live row for an occurrence is
  refused even when one of them is an override, which is exactly what the old
  exemption allowed;
* the migration's PRE-FLIGHT, which is the half that decides whether the
  upgrade may run at all and which a green deploy never exercises.

**The pre-flight is graded by executing its own SQL**, not by re-deriving the
predicate here -- the migration exposes ``COLLIDING_OCCURRENCES_SQL`` as a
standalone constant for that reason, and a test that rewrote the query would be
grading a second spelling rather than the shipped one.

Each test clones a fresh worker database (see ``conftest``), so the case that
DROPS the index to plant an otherwise-unrepresentable row cannot leak.
"""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy.exc
from sqlalchemy import text

from app.models.amount_ownership import AmountOwnership
from app.extensions import db as _db
from app.models.transaction import Transaction

from tests._test_helpers import make_expense_template


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions"
    / "e7c3a1f9b482_a_repriced_row_still_answers_one_occurrence.py"
)


def _migration_module():
    """Import the migration file directly, for its published SQL constant.

    Alembic revisions are not an importable package, so this loads the file by
    path.  Reading the constant from the shipped file is the point: a copy of
    the query in this test would pass while the migration's own broke.
    """
    spec = importlib.util.spec_from_file_location("_xauh_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DATED = "idx_{t}_template_scenario_occurrence"
_UNDATED = "idx_{t}_template_scenario_undated"


def _predicate(name):
    """Return the partial predicate PostgreSQL holds for *name*."""
    return _db.session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='budget' AND indexname=:n"
        ),
        {"n": name},
    ).scalar()


@pytest.mark.parametrize("table", ["transactions", "transfers"])
class TestThePredicatesDiverged:
    """The dated index dropped the exemption; the undated one kept it."""

    def test_the_dated_index_covers_override_rows(self, app, table):
        """A re-priced row is INSIDE the occurrence-keyed index.

        This is the whole of what the migration bought: X-au-h raises
        ``is_override`` on a re-price, and before the change that dropped the
        row out of the guarantee even though it never moved and still answers
        exactly one occurrence.
        """
        with app.app_context():
            definition = _predicate(_DATED.format(t=table))
            assert definition is not None, "the dated index exists"
            assert "is_override" not in definition, definition
            assert "occurs_on IS NOT NULL" in definition, definition

    def test_the_undated_index_keeps_the_exemption(self, app, table):
        """The paycheck-keyed index still exempts overrides, and must.

        ``carry_forward_service._execute._create_target_override_row`` writes an
        override row with NO ``occurs_on``, which lands here and has to sit
        beside the canonical undated row.  Dropping the term from BOTH indexes
        would make a routine leftover roll-forward raise.
        """
        with app.app_context():
            definition = _predicate(_UNDATED.format(t=table))
            assert definition is not None, "the undated index exists"
            assert "is_override = false" in definition.lower(), definition
            assert "occurs_on IS NULL" in definition, definition


class TestTheGuaranteeItBought:
    """A second live row for one occurrence is refused, override or not."""

    def test_an_override_row_cannot_duplicate_an_occurrence(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The behaviour, not the schema text.

        Before the migration this insert SUCCEEDED -- the exemption let an
        override row answer an occurrence a live row already answered, and the
        duplicate was then counted twice on every balance surface.  R17
        measured that class at 8 rows / `$1,482.93` on a production clone.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            common = {
                "user_id": seed_user["user"].id,
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": seed_user["account"].id,
                "status_id": _db.session.execute(
                    text("SELECT id FROM ref.statuses WHERE name='Projected'"),
                ).scalar(),
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": _db.session.execute(
                    text("SELECT id FROM ref.transaction_types LIMIT 1"),
                ).scalar(),
                "template_id": template.id,
                "occurs_on": date(2026, 10, 15),
            }
            db.session.add(Transaction(
                name="the rule's own", amount_ownership=AmountOwnership.own(Decimal("10.00")),
                is_override=False, **common,
            ))
            db.session.flush()

            db.session.add(Transaction(
                name="a re-priced sibling", amount_ownership=AmountOwnership.own(Decimal("12.00")),
                is_override=True, **common,
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()


class TestTheMigrationsPreFlight:
    """The half that decides whether the upgrade may run at all."""

    def test_it_finds_a_collision_the_loose_index_allowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Run the migration's OWN query against a state only it can see.

        The tightened index makes the offending pair unrepresentable, so the
        index is dropped first -- which is safe because every test clones a
        fresh worker database.  Without this the pre-flight would be a control
        that has never once been shown to fire, which is the shape a green
        deploy cannot distinguish from a correct one.
        """
        with app.app_context():
            db.session.execute(text(
                "DROP INDEX budget.idx_transactions_template_scenario_occurrence"
            ))
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            common = {
                "user_id": seed_user["user"].id,
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": seed_user["account"].id,
                "status_id": _db.session.execute(
                    text("SELECT id FROM ref.statuses WHERE name='Projected'"),
                ).scalar(),
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": _db.session.execute(
                    text("SELECT id FROM ref.transaction_types LIMIT 1"),
                ).scalar(),
                "template_id": template.id,
                "occurs_on": date(2026, 10, 15),
            }
            db.session.add(Transaction(
                name="the rule's own", amount_ownership=AmountOwnership.own(Decimal("10.00")),
                is_override=False, **common,
            ))
            db.session.add(Transaction(
                name="a re-priced sibling", amount_ownership=AmountOwnership.own(Decimal("12.00")),
                is_override=True, **common,
            ))
            db.session.flush()

            sql = _migration_module().COLLIDING_OCCURRENCES_SQL.format(
                table="transactions", fk="template_id",
            )
            rows = db.session.execute(text(sql)).fetchall()

            assert len(rows) == 1, rows
            assert rows[0][0] == template.id
            assert rows[0][2] == date(2026, 10, 15)
            assert rows[0][3] == 2, "it counts both live rows"

    def test_it_is_silent_on_clean_data(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The other direction, so the query is not simply always positive.

        A control that fires on everything is as useless as one that fires on
        nothing, and only running both directions tells them apart.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            db.session.add(Transaction(
                user_id=seed_user["user"].id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=_db.session.execute(
                    text("SELECT id FROM ref.statuses WHERE name='Projected'"),
                ).scalar(),
                name="the rule's own",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=_db.session.execute(
                    text("SELECT id FROM ref.transaction_types LIMIT 1"),
                ).scalar(),
                amount_ownership=AmountOwnership.own(Decimal("10.00")),
                template_id=template.id,
                occurs_on=date(2026, 10, 15),
                is_override=True,
            ))
            db.session.flush()

            sql = _migration_module().COLLIDING_OCCURRENCES_SQL.format(
                table="transactions", fk="template_id",
            )
            assert db.session.execute(text(sql)).fetchall() == []
