"""The backfill in migration ``b7c3d9e41a06``, run as the migration runs it.

Plan step ``bank_import:X-gd-1``.  **This is the one part of that revision that
nothing else grades.**  ``scripts/build_test_template.py`` runs ``upgrade head``
against an EMPTY database, so all three backfill statements execute over zero
rows and prove nothing; the downgrade is never executed at all.  Two adversarial
reviews on 2026-08-25 named the gap, and one of them named why it is the arm
worth covering: ``bank_statement_lines.merchant_id`` stays NULLABLE and the
source column is dropped four statements later, so a row the UPDATE misses
loses its merchant permanently with no error.  ``merchant_rules`` is
self-checking by comparison -- its ``ALTER COLUMN ... SET NOT NULL`` fails
loudly.

**It executes the migration's own strings**, imported from the module, which is
the convention ``efffcf647644``'s ``BACKFILL_SQL`` established here: a test that
re-typed the join would agree with a mistake as readily as with the truth.

**The fixture reproduces the PRE-migration shape** -- the two source columns
back, the two target columns cleared, ``merchant_id`` nullable on the
destination -- because the test database is already at head.  It is the same
construction ``test_c40_account_id_backfill.py`` uses to relax a NOT NULL it
needs to write around, and it restores the schema on teardown.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern,
# and ``unused-argument`` is unavoidable for a fixture requested for its side
# effect -- ``pre_migration_shape`` puts the schema back the way the migration
# found it and the test bodies do not reference what it yields.
# pylint: disable=unused-argument
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_import,
)


_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load(filename: str):
    """Load an Alembic revision as a module, the way alembic itself does.

    ``migrations/versions`` has no ``__init__.py``, so a plain import cannot
    reach it.

    Args:
        filename: The revision file's name.

    Returns:
        The loaded module.
    """
    path = _MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REVISION = _load("b7c3d9e41a06_a_merchant_is_a_row.py")


@pytest.fixture
def pre_migration_shape(db):
    """Put the two tables back into the shape this revision found them in.

    Adds the dropped string columns, relaxes the destination's NOT NULL, and
    empties what the revision fills, so the statements under test start from
    the state they were written for.  Restores all three on teardown.

    Args:
        db: The test database session.

    Yields:
        ``None`` -- it is requested for its side effect.
    """
    db.session.execute(text(
        "ALTER TABLE budget.bank_statement_lines "
        "ADD COLUMN merchant VARCHAR(100)"
    ))
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules "
        "ADD COLUMN merchant VARCHAR(100)"
    ))
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules "
        "ALTER COLUMN merchant_id DROP NOT NULL"
    ))
    yield
    db.session.rollback()


def _to_the_pre_migration_state(db):
    """Move what the builders staged back onto the string columns.

    The builders write through the ORM, so they produce POST-migration rows.
    This copies each row's merchant name onto the column the revision reads and
    clears the column it writes, which is the state a real upgrade begins from.

    Args:
        db: The test database session.
    """
    db.session.flush()
    db.session.execute(text(
        "UPDATE budget.bank_statement_lines AS l SET merchant = m.name "
        "FROM budget.merchants AS m WHERE m.id = l.merchant_id"
    ))
    db.session.execute(text(
        "UPDATE budget.merchant_rules AS d SET merchant = m.name "
        "FROM budget.merchants AS m WHERE m.id = d.merchant_id"
    ))
    db.session.execute(text(
        "UPDATE budget.bank_statement_lines SET merchant_id = NULL"
    ))
    db.session.execute(text(
        "UPDATE budget.merchant_rules SET merchant_id = NULL"
    ))
    db.session.execute(text("DELETE FROM budget.merchants"))
    db.session.expire_all()


@contextlib.contextmanager
def _under_the_revisions_own_name(db):
    """Present the rule table under the name THIS revision knew it by.

    ``budget.merchant_rules`` was ``budget.merchant_destinations`` until
    ``d4a1f8b0c25e`` renamed it, one revision after this one (plan step
    ``bank_import:X-gd-2``).  The strings under test are frozen at the older
    name, which is the whole point of importing them rather than re-typing
    them, so they are executed against a table carrying it.

    **Renamed BACK on the way out**, and that is what makes the window narrow
    rather than a second pre-migration fixture: every assertion in this module
    reads through :class:`~app.models.merchant_rule.MerchantRule`, which is
    mapped at HEAD, and the builders that stage rows do too.  Only the frozen
    strings need the old world, so only they get it.

    Args:
        db: The test database session.

    Yields:
        ``None`` -- it is used for its side effect.
    """
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules RENAME TO merchant_destinations"
    ))
    try:
        yield
    finally:
        db.session.execute(text(
            "ALTER TABLE budget.merchant_destinations RENAME TO merchant_rules"
        ))


def _upgrade(db):
    """Run the revision's three backfill statements, in its own order."""
    with _under_the_revisions_own_name(db):
        db.session.execute(text(_REVISION.MINT_MERCHANTS_SQL))
        db.session.execute(text(_REVISION.POINT_LINES_SQL))
        db.session.execute(text(_REVISION.POINT_DESTINATIONS_SQL))
    db.session.expire_all()


def _downgrade(db):
    """Run the revision's two restore statements, in its own order."""
    with _under_the_revisions_own_name(db):
        db.session.execute(text(_REVISION.RESTORE_DESTINATION_STRINGS_SQL))
        db.session.execute(text(_REVISION.RESTORE_LINE_STRINGS_SQL))
    db.session.expire_all()


class TestTheUpgradeBackfill:
    """Every row that named a merchant comes out naming its own account's."""

    def test_every_named_line_is_pointed_and_a_NAMELESS_one_is_not(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The arm that can lose data silently, and the NULL beside it.

        ``merchant_id`` stays nullable through the whole revision and the
        source column is dropped after this statement, so a line the UPDATE
        misses is a line whose merchant is gone with nothing raising.  The
        NULL-merchant line is what says the miss is not simply universal.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-9.99", merchant="Food Lion",
            sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-4.99", merchant="Food Lion",
            sequence_in_group=1,
        )
        a_bank_line(
            seed_user, statement, amount="-1.99", merchant=None,
            sequence_in_group=2,
        )
        _to_the_pre_migration_state(db)

        _upgrade(db)

        rows = db.session.query(BankStatementLine).order_by(
            BankStatementLine.sequence_in_group,
        ).all()
        assert [row.merchant_id is None for row in rows] == [
            False, False, True,
        ]
        assert rows[0].merchant_id == rows[1].merchant_id
        assert db.session.query(Merchant).count() == 1

    def test_two_accounts_naming_one_merchant_get_a_row_EACH(
        self, app, db, seed_user, seed_second_user, pre_migration_shape,
    ):
        """THE firing control for the join's ``account_id`` term.

        Drop it from :data:`POINT_LINES_SQL` and the join is ambiguous: one
        account's line resolves to whichever row PostgreSQL happens to pair it
        with, which ``fk_bank_statement_lines_merchant_account`` then refuses
        -- so the revision fails on a real two-account database and passes on
        every single-account one, which is every test that existed.
        """
        mine = an_import(seed_user)
        a_bank_line(seed_user, mine, amount="-9.99", merchant="Food Lion")
        theirs = an_import(
            seed_second_user, account=seed_second_user["account"],
        )
        a_bank_line(
            seed_second_user, theirs, amount="-4.99", merchant="Food Lion",
        )
        _to_the_pre_migration_state(db)

        _upgrade(db)

        assert db.session.query(Merchant).count() == 2
        for line in db.session.query(BankStatementLine).all():
            named = db.session.get(Merchant, line.merchant_id)
            assert named.account_id == line.account_id

    def test_a_destination_whose_merchant_has_NO_LINE_is_pointed(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """THE firing control for the UNION's second arm.

        Measured 0 rows on the developer's own database, so nothing anywhere
        else exercises it -- and it is the arm that keeps a stated answer
        readable after the import that named its merchant was deleted.  Drop
        the second SELECT and :data:`POINT_DESTINATIONS_SQL` leaves this row's
        ``merchant_id`` NULL, which the revision's own ``SET NOT NULL`` then
        refuses.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Food Lion")
        db.session.flush()
        ghost = Merchant(
            account_id=seed_user["account"].id, name="Ghost Merchant",
        )
        db.session.add(ghost)
        db.session.flush()
        db.session.add(MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=ghost.id,
            never_a_purchase=True,
        ))
        _to_the_pre_migration_state(db)
        # ...and now the merchant has no line at all, which is the state a
        # deleted import leaves behind.  Read through raw SQL because the
        # re-added ``merchant`` COLUMN is not mapped -- the ORM's attribute of
        # that name is the relationship this revision created.
        assert db.session.execute(text(
            "SELECT count(*) FROM budget.bank_statement_lines "
            "WHERE merchant = 'Ghost Merchant'"
        )).scalar() == 0

        _upgrade(db)

        stored = db.session.query(MerchantRule).one()
        assert stored.merchant_id is not None
        assert db.session.get(Merchant, stored.merchant_id).name == (
            "Ghost Merchant"
        )

    def test_two_SPELLINGS_of_one_payee_stay_two_merchants(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The revision merges nothing, which is the model's stated rule.

        Deciding that ``Amazon`` and ``AMAZON`` are one merchant is a guess,
        and a backfill that folded case would silently re-point one spelling's
        lines at the other's row.  Measured on the developer's own data: 62
        distinct merchants and 62 distinct case-folded, so this costs him
        nothing and is here because the next statement could differ.
        """
        statement = an_import(seed_user)
        for index, spelling in enumerate(("Amazon", "AMAZON")):
            a_bank_line(
                seed_user, statement, amount="-9.99", merchant=spelling,
                sequence_in_group=index,
            )
        _to_the_pre_migration_state(db)

        _upgrade(db)

        assert sorted(
            row.name for row in db.session.query(Merchant).all()
        ) == ["AMAZON", "Amazon"]


class TestTheDowngradeRestoresTheStrings:
    """Value-lossless in the other direction, on rows the upgrade just made."""

    def test_every_pointed_row_gets_its_own_string_back(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The claim the revision's docstring makes, executed.

        A downgrade that missed a row would leave ``merchant`` NULL on a line
        and would fail the destination's ``SET NOT NULL`` -- so the line half
        is the silent one here too.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-9.99", merchant="Food Lion",
            sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-4.99", merchant=None,
            sequence_in_group=1,
        )
        db.session.flush()
        named = db.session.query(Merchant).one()
        db.session.add(MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=named.id,
            never_a_purchase=True,
        ))
        _to_the_pre_migration_state(db)
        _upgrade(db)
        db.session.execute(text(
            "UPDATE budget.bank_statement_lines SET merchant = NULL"
        ))
        db.session.execute(text(
            "UPDATE budget.merchant_rules SET merchant = NULL"
        ))
        db.session.expire_all()

        _downgrade(db)

        restored = db.session.execute(text(
            "SELECT sequence_in_group, merchant "
            "FROM budget.bank_statement_lines ORDER BY sequence_in_group"
        )).all()
        assert restored == [(0, "Food Lion"), (1, None)]
        assert db.session.execute(text(
            "SELECT merchant FROM budget.merchant_rules"
        )).scalar() == "Food Lion"

    def test_a_merchant_NOTHING_references_writes_back_nowhere(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The one fact the round trip cannot keep, stated rather than found.

        A merchant row that no line and no destination names has nothing to
        write back and goes with the table.  It is a fact no reader had before
        this revision, which is why the loss is acceptable -- and it is
        recorded here so the next person to read "value-lossless" knows what it
        does not cover.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Food Lion")
        db.session.flush()
        db.session.add(Merchant(
            account_id=seed_user["account"].id, name="Referenced By Nothing",
        ))
        _to_the_pre_migration_state(db)
        _upgrade(db)

        db.session.execute(text(_REVISION.RESTORE_LINE_STRINGS_SQL))
        db.session.expire_all()

        assert sorted(
            row[0] for row in db.session.execute(text(
                "SELECT DISTINCT merchant FROM budget.bank_statement_lines "
                "WHERE merchant IS NOT NULL"
            )).all()
        ) == ["Food Lion"]
        assert db.session.query(Merchant).filter(
            Merchant.name == "Referenced By Nothing",
        ).count() == 0


class TestTheAmountsAreUntouched:
    """The revision moves no money, asserted rather than asserted about."""

    def test_no_figure_and_no_day_moves_across_the_backfill(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """Every column but the merchant is identical either side."""
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-40.81", merchant="Food Lion",
            posted_on=date(2026, 3, 4), sequence_in_group=0,
        )
        _to_the_pre_migration_state(db)
        before = db.session.execute(text(
            "SELECT id, posted_on, transaction_on, amount, description, "
            "source_category, external_id, sequence_in_group, running_balance "
            "FROM budget.bank_statement_lines ORDER BY id"
        )).all()

        _upgrade(db)

        after = db.session.execute(text(
            "SELECT id, posted_on, transaction_on, amount, description, "
            "source_category, external_id, sequence_in_group, running_balance "
            "FROM budget.bank_statement_lines ORDER BY id"
        )).all()
        assert after == before
        assert before[0][3] == Decimal("-40.81")
