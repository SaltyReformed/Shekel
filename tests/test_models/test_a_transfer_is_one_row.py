"""Migration ``82f7b7ed4332``: a transfer is one row.

Plan step ``balance:X-bi-6-4d-3``, rulings **R-BAL166** (the twin rows are
deleted in the release that stops their upkeep), **R-BAL258** (the database
refuses a new one, "Database refuses") and **R-BAL260** (the downgrade restores
each twin from a kept copy, "Keep a copy").  The revision keeps a copy of every
twin in ``system.transfer_twin_purge``, deletes them, and adds
``ck_transactions_names_no_transfer``; it REFUSES, writing nothing, while a twin
could not go cleanly, a twin that is no copy of its transfer among them (its
status aside, which the app at ``e616adf7fe22`` writes on the transfer alone),
a twin filed under a category that is not its owner's, and a transfer filed
under one that is not its owner's, whose twins the downgrade would build under
it; its downgrade restores column for column every kept twin whose transfer was not
written since (re-reading what its transfer and its side's record say now),
rebuilds from its transfer (keeping its identity) every twin whose transfer
was, puts a kept value that no longer fits back as the transfer says (ruling
**R-BAL261**), builds two new twins for a transfer created since, and drops
the copy.  Every refusal is driven through the shipped ``upgrade``, so a
refusal it stopped asking fails here.

Driven against the test database, which is built at head, through the shipped
``upgrade`` / ``downgrade``.  At head no twin exists, so the first downgrade in
each case BUILDS two per transfer (the copy holds none) -- the pre-step shape the
upgrade meets.  The schema differs between the two revisions only by the CHECK
and the copy, so the app's doors run at either.  Every case ends at head.
"""

from __future__ import annotations

import contextlib
import io
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.amount_ownership import AmountOwnership
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    load_migration_module,
    restate_account_opening,
    run_migration_callable,
)

_MIGRATION = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: The revision below this one, whose downgrade runs next in a chain.
_BELOW = load_migration_module(
    "e616adf7fe22_a_transfer_side_s_payment_hangs_off_the_transfer.py"
)

#: Every column of a twin row, read back as one tuple per twin.
_TWIN_ROWS_SQL = (
    "SELECT t.* FROM budget.transactions t "
    "WHERE t.transfer_id = ANY(:ids) ORDER BY t.id"
)


def _downgrade():
    """Run the shipped downgrade: every twin restored or built."""
    run_migration_callable(_MIGRATION.downgrade, _db.session)


def _upgrade():
    """Run the shipped upgrade: every twin kept and deleted."""
    run_migration_callable(_MIGRATION.upgrade, _db.session)


#: The downgrade's printed counts, every one of them, in the order it prints
#: them.
_DOWNGRADE_PRINT = re.compile(
    r"(?P<restored>\d+) kept twin\(s\) restored: (?P<rebuilt>\d+) in line "
    r"with a transfer written since; (?P<changed>\d+) of an unwritten "
    r"transfer differ from their kept row, saying what the transfer and its "
    r"side's record say now \((?P<unfit>\d+) of them because a kept value no "
    r"longer fits, ruling R-BAL261\); the rest exactly as kept\.  "
    r"(?P<built>\d+) built new for transfers holding none"
)


def _downgrade_counts():
    """Run the shipped downgrade and return its printed counts, by name.

    The print is read here, around the one call, so a case needs no capture
    fixture of its own.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _downgrade()
    printed = _DOWNGRADE_PRINT.search(out.getvalue())
    assert printed is not None, "the downgrade printed no counts"
    return {name: int(value) for name, value in printed.groupdict().items()}


def _assert_nothing_written(transfer_ids, twins_before):
    """A refused upgrade left the twins, and made no copy and no CHECK.

    Called INSIDE the refused transaction, before the test rolls it back: the
    upgrade runs in the test session's own transaction
    (:func:`run_migration_callable`), so a write made before the refusal is
    visible here and gone once rolled back (cp1d review L-2).
    """
    assert _twin_rows(transfer_ids) == twins_before
    assert _db.session.execute(text(
        "SELECT to_regclass('system.transfer_twin_purge') IS NULL"
    )).scalar_one() is True
    assert _db.session.execute(text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_transactions_names_no_transfer'"
    )).scalar_one() == 0


def _twin_rows(transfer_ids):
    """Return every twin of *transfer_ids*, every column, in id order."""
    return [
        dict(row) for row in _db.session.execute(
            text(_TWIN_ROWS_SQL), {"ids": list(transfer_ids)},
        ).mappings()
    ]


def _twin_of(transfer_id, *, income):
    """Return one side's twin of *transfer_id*, every column."""
    rows = [
        row for row in _twin_rows([transfer_id])
        if (row["account_id"] == _db.session.execute(text(
            "SELECT CASE WHEN :income THEN to_account_id "
            "ELSE from_account_id END FROM budget.transfers WHERE id = :t"
        ), {"income": income, "t": transfer_id}).scalar_one())
    ]
    assert len(rows) == 1, rows
    return rows[0]


def _transfer(transfer_id):
    """Return the transfer's columns."""
    return dict(_db.session.execute(text(
        "SELECT * FROM budget.transfers WHERE id = :t"
    ), {"t": transfer_id}).mappings().one())


def _side_record(transfer_id, *, income):
    """Return the side's payment record's day, basis and link, or ``None``."""
    link = "income_transfer_id" if income else "expense_transfer_id"
    row = _db.session.execute(text(
        "SELECT settled_on, settled_day_basis_id, reconciled_by_id "
        f"FROM budget.transaction_entries WHERE {link} = :t"
    ), {"t": transfer_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


def _status_id(name):
    """Return the ``ref.statuses`` id named *name*."""
    return _db.session.execute(text(
        "SELECT id FROM ref.statuses WHERE name = :n"
    ), {"n": name}).scalar_one()


def _parent_transfer_id():
    """Return the ``parent_transfer`` amount source's id."""
    return _db.session.execute(text(
        "SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer'"
    )).scalar_one()


def _figures(transfer_id):
    """Each twin's ``(estimated_amount, amount_source_id)``, from-side first."""
    return [
        (_twin_of(transfer_id, income=income)["estimated_amount"],
         _twin_of(transfer_id, income=income)["amount_source_id"])
        for income in (False, True)
    ]


def _stale_income_twin(seed_user, xfer):
    """Give *xfer*'s to-side twin a category its transfer does not name.

    Finding BAL-575's shape: older income twins carried "Transfers: Incoming"
    in place of their transfer's own category.  Run at the downgraded state,
    where the twins exist.  Returns the stale category's id.
    """
    stale = seed_user["categories"]["Rent"].id
    _db.session.execute(text(
        "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
    ), {"c": stale, "t": _twin_of(xfer.id, income=True)["id"]})
    _db.session.commit()
    return stale


def _plant_zero_close(xfer, day):
    """Settle *xfer* at ``$0.00`` with *day* on both twins, by SQL.

    A ``$0.00`` close holds no record, so its day lived only on its twins below
    this revision.  The band rule admits a settled transfer with no record on
    either side, and no door below this revision states a close's day apart
    from the seam the twins no longer carry, so it is planted.  Run at the
    downgraded state.
    """
    paid = _status_id("Paid")
    entered = _db.session.execute(text(
        "SELECT id FROM ref.settled_day_bases WHERE name = 'entered'"
    )).scalar_one()
    _db.session.execute(text(
        "UPDATE budget.transfers SET status_id = :p WHERE id = :t"
    ), {"p": paid, "t": xfer.id})
    _db.session.execute(text(
        "UPDATE budget.transactions SET status_id = :p, "
        "settled_on = :d, settled_day_basis_id = :b WHERE transfer_id = :t"
    ), {"p": paid, "d": day, "b": entered, "t": xfer.id})
    _db.session.commit()
    return paid, entered


def _accounts(seed_user):
    """Two savings accounts beside the seed checking account, committed."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "One Row Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "One Row Van Fund",
    )
    _db.session.commit()
    return savings, van


#: The fields the upgrade refuses a twin over, spelled HERE rather than read
#: off the migration, so a field the migration stops refusing fails its own
#: case: every field the app at ``e616adf7fe22`` still writes on both twins
#: with their transfer, save ``category_id``, which it writes there too but
#: which older income twins carry otherwise (finding BAL-575), so it is no copy
#: on production: it is neither refused for differing from its transfer's nor
#: re-read, the upgrade asking only that it be its owner's (ruling R-BAL261
#: answers the rest).
_COPIED = (
    "user_id", "account_id", "pay_period_id", "scenario_id", "due_date",
    "is_override", "is_deleted",
)

#: Every field a twin mirrors from its transfer, which the downgrade re-reads:
#: the copied ones and the status, which the app at ``e616adf7fe22`` writes on
#: the transfer alone.  Spelled here for the same reason, so a field the
#: migration stops re-reading fails its own case.
_MIRRORED = _COPIED + ("status_id",)


def _what_if_scenario(seed_user):
    """A second, non-baseline scenario of the seed user, by SQL; its id."""
    scenario = _db.session.execute(text(
        "INSERT INTO budget.scenarios (user_id, name, is_baseline) "
        "VALUES (:u, 'One Row What-if', FALSE) RETURNING id"
    ), {"u": seed_user["user"].id}).scalar_one()
    _db.session.commit()
    return scenario


def _new_category(user_id, group_name, item_name):
    """A category of *user_id* that no other row uses, by SQL; its id."""
    category = _db.session.execute(text(
        "INSERT INTO budget.categories (user_id, group_name, item_name) "
        "VALUES (:u, :g, :i) RETURNING id"
    ), {"u": user_id, "g": group_name, "i": item_name}).scalar_one()
    _db.session.commit()
    return category


def _second_owner(request):
    """The second user's ids: user, two accounts, a pay period and a scenario.

    A row's ``user_id`` cannot move alone: its accounts and its pay period are
    keyed to their owner with it (``fk_transactions_owner_account`` /
    ``fk_transactions_owner_period``, ``fk_transfers_owner_from_account`` /
    ``fk_transfers_owner_to_account``), so the owner drift moves them all.
    Read through ``request`` so only that case builds the second user.
    """
    second = request.getfixturevalue("seed_second_user")
    savings = create_account_of_type(
        second, _db.session, "Savings", "Second Owner Savings",
    )
    _db.session.commit()
    return {
        "user_id": second["user"].id,
        "checking": second["account"].id,
        "savings": savings.id,
        "pay_period_id": second["bootstrap_period"].id,
        "scenario_id": second["scenario"].id,
    }


def _twin_drift(field, request, seed_user, seed_periods, van):
    """The values that put a twin's copied *field* off its transfer.

    Returns ``(column values, the fields the refusal then names)``.  The owner
    drift names three fields, since its account and pay period move with it.
    """
    if field == "user_id":
        second = _second_owner(request)
        return {
            "user_id": second["user_id"],
            "account_id": second["checking"],
            "pay_period_id": second["pay_period_id"],
        }, ["user_id", "account_id", "pay_period_id"]
    value = {
        "account_id": lambda: van.id,
        "pay_period_id": lambda: seed_periods[1].id,
        "scenario_id": lambda: _what_if_scenario(seed_user),
        "due_date": lambda: seed_periods[0].start_date + timedelta(days=3),
        "is_override": lambda: True,
        "is_deleted": lambda: True,
    }[field]()
    return {field: value}, [field]


def _transfer_drift(field, request, seed_user, seed_periods, van):
    """The values a statement around the version counter writes on the transfer.

    Each changes *field* as a twin mirrors it; the owner drift moves the
    transfer whole to the second user (both endpoints, its pay period and its
    scenario), as its keys require.
    """
    if field == "user_id":
        second = _second_owner(request)
        return {
            "user_id": second["user_id"],
            "from_account_id": second["checking"],
            "to_account_id": second["savings"],
            "pay_period_id": second["pay_period_id"],
            "scenario_id": second["scenario_id"],
        }
    column, value = {
        "account_id": lambda: ("to_account_id", van.id),
        "pay_period_id": lambda: ("pay_period_id", seed_periods[1].id),
        "scenario_id": lambda: ("scenario_id", _what_if_scenario(seed_user)),
        "status_id": lambda: ("status_id", _status_id("Cancelled")),
        "due_date": lambda: (
            "due_date", seed_periods[0].start_date + timedelta(days=3),
        ),
        "is_override": lambda: ("is_override", True),
        "is_deleted": lambda: ("is_deleted", True),
    }[field]()
    return {column: value}


def _set(table, row_id, values):
    """``UPDATE`` *values* onto one row of *table* by SQL, and commit."""
    _db.session.execute(text(
        f"UPDATE budget.{table} SET "
        + ", ".join(f"{column} = :{column}" for column in values)
        + " WHERE id = :row_id"
    ), {**values, "row_id": row_id})
    _db.session.commit()


#: The category refusal's repair advice, spelled HERE rather than read off the
#: migration, so a refusal that stops saying which row to repair fails its
#: case (the cp2b-0 review's L-1).
_REPAIR_ADVICE = (
    "where the transfer names a category whose owner is not the transfer's, "
    "the transfer is refused for its own category as well and is a row to "
    "repair, its twins with it; otherwise the twin alone is"
)


def _cross_user_references():
    """Every transfer and transaction filed under a category not its owner's.

    Returned as ``(table, row id)`` pairs, so an empty list says no row of
    either table names another user's category.
    """
    return [tuple(row) for row in _db.session.execute(text(
        "SELECT 'transfers', x.id FROM budget.transfers x "
        "JOIN budget.categories c ON c.id = x.category_id "
        "WHERE c.user_id <> x.user_id "
        "UNION ALL "
        "SELECT 'transactions', t.id FROM budget.transactions t "
        "JOIN budget.categories c ON c.id = t.category_id "
        "WHERE c.user_id <> t.user_id ORDER BY 1, 2"
    ))]


def _side_value(parent, field, *, income):
    """What *parent* says its *income* (or expense) side's twin holds in *field*."""
    if field == "account_id":
        return parent["to_account_id" if income else "from_account_id"]
    return parent[field]


class TestTheUpgrade:
    """The upgrade keeps each twin whole, deletes it, and refuses a new one."""

    def test_it_keeps_every_twin_whole_and_deletes_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Each twin goes into the copy with its side's record flag, then away."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            planned = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            ids = [planned.id, paid.id]

            _downgrade()
            before = _twin_rows(ids)
            assert len(before) == 4
            _upgrade()

            assert _twin_rows(ids) == []
            kept = db.session.execute(text(
                "SELECT row_id, transfer_id, side_held_record, "
                "transfer_version_id, "
                "(jsonb_populate_record(NULL::budget.transactions, row_data)).* "
                "FROM system.transfer_twin_purge WHERE transfer_id = ANY(:ids) "
                "ORDER BY row_id"
            ), {"ids": ids}).mappings().all()
            assert [row["row_id"] for row in kept] == [
                row["id"] for row in before
            ]
            # The copy is the row, column for column.
            for row, twin in zip(kept, before):
                assert {k: row[k] for k in twin} == twin
            # Only the settled transfer's sides held a record.
            assert {
                (row["transfer_id"], row["side_held_record"]) for row in kept
            } == {(planned.id, False), (paid.id, True)}
            # Each keeps its transfer's version counter, the downgrade's test
            # of "written since".
            assert {
                row["transfer_id"]: row["transfer_version_id"] for row in kept
            } == {i: _transfer(i)["version_id"] for i in ids}

    def test_the_database_refuses_a_twin(self, app, db, seed_user, seed_periods):
        """A row naming a transfer is refused at the write (ruling R-BAL258)."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            twin = _twin_rows([xfer.id])[0]
            _upgrade()

            columns = [name for name in twin if name != "id"]
            with pytest.raises(
                IntegrityError, match="ck_transactions_names_no_transfer",
            ):
                db.session.execute(text(
                    f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + name for name in columns)})"
                ), {name: twin[name] for name in columns})
                db.session.commit()
            db.session.rollback()

    def test_it_refuses_a_twin_another_row_names(
        self, app, db, seed_user, seed_periods,
    ):
        """A card payback naming a twin refuses the upgrade, writing nothing."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=False)
            payback = dict(twin)
            payback.pop("id")
            payback.update(transfer_id=None, credit_payback_for_id=twin["id"])
            columns = list(payback)
            db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)})"
            ), payback)
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert (
                f"({twin['id']}, {xfer.id}, "
                "'budget.transactions.credit_payback_for_id')"
            ) in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transactions "
                "WHERE credit_payback_for_id = :t"
            ), {"t": twin["id"]})
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_transfer_whose_twins_are_not_a_pair(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer missing its income twin refuses the upgrade by name."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            income = _twin_of(xfer.id, income=True)
            db.session.execute(text(
                "DELETE FROM budget.transactions WHERE id = :t"
            ), {"t": income["id"]})
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert f"({xfer.id}, 1, 0)" in str(refused.value)

            # Put a twin back so the transfer is a pair, then return to head.
            income.pop("id")
            columns = list(income)
            db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)})"
            ), income)
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_transfer_holding_two_expense_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer holding a second (deleted) expense twin refuses the upgrade.

        The EXPENSE arm of the pair test; the case above drives the income
        arm.  Let through, both expense twins would come back with
        ``is_deleted`` re-read from the live transfer and the downgrade would
        die on ``uq_transactions_transfer_type_active``.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            second = _twin_of(xfer.id, income=False)
            second.pop("id")
            second["is_deleted"] = True
            columns = list(second)
            extra = db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)}) "
                "RETURNING id"
            ), second).scalar_one()
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert f"({xfer.id}, 2, 1)" in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transactions WHERE id = :e"
            ), {"e": extra})
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_twin_a_movement_names(
        self, app, db, seed_user, seed_periods,
    ):
        """A movement naming a twin refuses the upgrade by its key's name.

        The second arm graded by a plant, on a table other than the twin's
        own: every arm is the same ``EXISTS`` over a key the catalog names, so
        this and the card payback above grade the arm's shape, and the catalog
        test below grades which keys it is asked over.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("60.00"),
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(paid.id, income=False)
            movement = dict(db.session.execute(text(
                "SELECT * FROM budget.transaction_entries "
                "WHERE expense_transfer_id = :t"
            ), {"t": paid.id}).mappings().one())
            movement.pop("id")
            movement.update(expense_transfer_id=None, transaction_id=twin["id"])
            columns = list(movement)
            planted = db.session.execute(text(
                f"INSERT INTO budget.transaction_entries ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)}) "
                "RETURNING id"
            ), movement).scalar_one()
            db.session.commit()
            before = _twin_rows([paid.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([paid.id], before)
            db.session.rollback()
            assert (
                f"({twin['id']}, {paid.id}, "
                "'budget.transaction_entries.transaction_id')"
            ) in str(refused.value)
            # One movement is one reference, though its column carries two
            # keys (the single-column one and the composite owner key).
            assert str(refused.value).count(f"({twin['id']}, {paid.id}, ") == 1
            assert "1 twin reference(s)" in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transaction_entries WHERE id = :e"
            ), {"e": planted})
            db.session.commit()
            _upgrade()

    def test_the_catalog_names_every_key_into_transactions(self, app, db):
        """The refusal is asked over every foreign key into a transaction.

        A catalog read that found nothing would leave the refusal asking
        nothing and passing, so the keys this revision's schema holds are
        named: each would null, cascade or refuse at a twin's delete.
        """
        with app.app_context():
            found = set(_MIGRATION.foreign_keys_into_transactions(
                db.session.connection(),
            ))
            assert found >= {
                ("budget.journal_entries", "transaction_id"),
                ("budget.statement_match_creations", "transaction_id"),
                ("budget.transaction_entries", "transaction_id"),
                ("budget.transaction_entries", "credit_payback_id"),
                ("budget.transactions", "credit_payback_for_id"),
            }, found

    def test_a_transfer_holding_no_twin_passes_and_gets_two(
        self, app, db, seed_user, seed_periods,
    ):
        """A twinless transfer is already one row: no refusal, two on the way down.

        The create door at this revision builds no twin, so a transfer it
        writes to a database still at ``e616adf7fe22`` holds none.  The upgrade
        has nothing of it to keep; the downgrade builds its two.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            _downgrade()
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            assert _twin_rows([xfer.id]) == []

            _MIGRATION.refuse_twins_that_cannot_go(db.session.connection())
            _upgrade()
            _downgrade()

            assert len(_twin_rows([xfer.id])) == 2
            _upgrade()

    @pytest.mark.parametrize(
        "drift",
        [(field, income) for field in _COPIED for income in (False, True)],
        ids=[f"{field}-{side}" for field in _COPIED
             for side in ("from_side", "to_side")],
    )
    def test_it_refuses_a_twin_that_is_no_copy_of_its_transfer(
        self, app, request, seed_user, seed_periods, drift,
    ):
        """A twin whose copied field is not its transfer's refuses the upgrade.

        Named as ``(transfer, twin, [fields])``, writing nothing: the twin is
        no copy of its transfer, so its delete would erase the disagreement
        unseen.  Each field is planted alone (the owner drift with the account
        and pay period its keys move with it), on each side's twin in turn
        (cp1d review L-3), and the refusal must name exactly the fields that
        differ.  *drift* is ``(the field, whether the to-side's twin)``.
        """
        field, income = drift
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            values, named = _twin_drift(
                field, request, seed_user, seed_periods, van,
            )
            assert all(twin[column] != value for column, value in values.items())
            _set("transactions", twin["id"], values)
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert "1 twin(s) are not a copy of their transfer" in str(
                refused.value
            )
            assert f"({xfer.id}, {twin['id']}, {named!r})" in str(refused.value)

            _set("transactions", twin["id"], {
                column: twin[column] for column in values
            })
            _upgrade()

    @pytest.mark.parametrize(
        "shape",
        [(income, deleted) for deleted in (False, True)
         for income in (False, True)],
        ids=[f"{side}-{state}" for state in ("live", "soft_deleted")
             for side in ("from_side", "to_side")],
    )
    def test_it_refuses_a_twin_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods, shape,
    ):
        """A twin under another user's category refuses the upgrade by name.

        No door writes one (both transfer doors refuse another user's
        category, ``_get_owned_category`` and ``_reject_unowned_references``,
        and a category's owner is written only at its create), so it is
        refused, writing nothing, rather than kept and carried back by the
        downgrade.  The transfer is moved under the same category, so the twin
        AGREES with its transfer and is refused for its owner alone, named
        with its transfer's category and that category's owner beside its
        own; that owner is not the transfer's, so the transfer is refused for
        its own category as well: the transfer is a row to repair, that twin
        with it, and the upgrade passes only once both are.  The other twin
        keeps a category its transfer no longer names, which is its owner's
        and passes (the cp1e review's Q1, decided C by the coordinator
        2026-10-11).  Raw statements, on each side's twin in turn, of a live
        transfer and of one the delete door soft-deleted: a hidden transfer's
        twins are kept and deleted like any other, so a refusal that asked
        live twins alone would carry one into the copy (the cp1g review's
        L-2).  *shape* is ``(whether the to-side's twin, whether
        soft-deleted)``.
        """
        income, deleted = shape
        with app.app_context():
            savings = _accounts(seed_user)[0]
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            _db.session.commit()
            if deleted:
                transfer_service.delete_transfer(
                    xfer.id, seed_user["user"].id, soft=True,
                )
                _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            assert twin["category_id"] == rent
            assert twin["is_deleted"] is deleted
            foreign = seed_second_user["categories"]["Rent"].id
            _set("transfers", xfer.id, {"category_id": foreign})
            _set("transactions", twin["id"], {"category_id": foreign})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                "1 twin(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            assert (
                f"({xfer.id}, {twin['id']}, {foreign}, "
                f"{seed_second_user['user'].id}, {foreign}, "
                f"{seed_second_user['user'].id})"
            ) in str(refused.value)
            assert (
                f"({xfer.id}, {foreign}, {seed_second_user['user'].id}, "
                f"{seed_user['user'].id})"
            ) in str(refused.value)

            # The twin alone repaired: the transfer's own refusal stands.
            _set("transactions", twin["id"], {"category_id": rent})
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _db.session.rollback()
            assert "twin(s) are filed under" not in str(refused.value)
            assert (
                "1 transfer(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            _set("transfers", xfer.id, {"category_id": rent})
            _upgrade()

    def test_the_release_path_refuses_a_twin_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """Below ``e616adf7fe22``, a twin under another user's category: the release refuses.

        The release runs both upgrades with no app write between;
        ``e616adf7fe22``'s census asks no category, so this revision's refusal
        is the one that names it (the cp1e review's Q2, decided C by the
        coordinator 2026-10-11).  The transfer names no category, so the
        refusal names the twin as the row to repair.  Not inside a nested
        application context, for the chain test's reason.
        """
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Kept Category Savings",
        )
        _db.session.commit()
        xfer_id = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_periods[0],
        ).id
        _db.session.commit()
        _downgrade()
        run_migration_callable(_BELOW.downgrade, _db.session)
        twin = _twin_of(xfer_id, income=True)
        foreign = seed_second_user["categories"]["Rent"].id
        _set("transactions", twin["id"], {"category_id": foreign})
        run_migration_callable(_BELOW.upgrade, _db.session)
        before = _twin_rows([xfer_id])

        with pytest.raises(RuntimeError) as refused:
            _upgrade()
        _assert_nothing_written([xfer_id], before)
        _db.session.rollback()
        assert _transfer(xfer_id)["category_id"] is None
        assert (
            f"({xfer_id}, {twin['id']}, {foreign}, "
            f"{seed_second_user['user'].id}, None, None)"
        ) in str(refused.value)
        assert "transfer(s) are filed under" not in str(refused.value)

        _set("transactions", twin["id"], {"category_id": twin["category_id"]})
        _upgrade()

    @pytest.mark.parametrize(
        "income", (False, True), ids=("from_side", "to_side"),
    )
    def test_a_twin_moved_to_another_owner_is_the_row_to_repair(
        self, app, request, seed_user, seed_periods, income,
    ):
        """The category ids agree and the transfer is clean: the twin is the row.

        The transfer and both its twins under the owner's Rent, as the create
        and update doors file them; a raw statement then moves ONE twin's
        owner (with the account and pay period its keys move with it).  Its
        category is now not its owner's, while its transfer's category is
        still the transfer's owner's, so the refusal names the transfer's
        category's owner as the transfer's own and does not refuse the
        transfer: the twin alone is the row to repair (the cp2a review's L-1,
        its RV-A1a).  The copied-field refusal names the same twin.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            assert twin["category_id"] == rent
            values, named = _twin_drift(
                "user_id", request, seed_user, seed_periods, van,
            )
            _set("transactions", twin["id"], values)
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert f"({xfer.id}, {twin['id']}, {named!r})" in str(refused.value)
            assert (
                f"({xfer.id}, {twin['id']}, {rent}, {seed_user['user'].id}, "
                f"{rent}, {seed_user['user'].id})"
            ) in str(refused.value)
            assert "transfer(s) are filed under" not in str(refused.value)

            _set("transactions", twin["id"], {
                column: twin[column] for column in values
            })
            _upgrade()

    def test_a_twin_only_repair_leaves_a_foreign_transfer_refused(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """The ids differ and the transfer's category is foreign too: both are named.

        The transfer under the second user's Rent, its income twin under the
        second user's Groceries, by raw statements.  The twin is refused with
        its transfer's category and THAT category's owner beside its own, and
        the transfer is refused for its own category.  Repairing the twin
        alone, the upgrade still refuses the transfer: the downgrade would
        build its twins back under that category (the cp2a review's L-1, its
        RV-A1b, which passed a twin-only repair before the transfer's own
        refusal, option D, decided by the coordinator 2026-10-11).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=True)
            rent = seed_second_user["categories"]["Rent"].id
            groceries = seed_second_user["categories"]["Groceries"].id
            second = seed_second_user["user"].id
            owner = seed_user["user"].id
            _set("transfers", xfer.id, {"category_id": rent})
            _set("transactions", twin["id"], {"category_id": groceries})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                f"({xfer.id}, {twin['id']}, {groceries}, {second}, {rent}, "
                f"{second})"
            ) in str(refused.value)
            assert f"({xfer.id}, {rent}, {second}, {owner})" in str(
                refused.value
            )

            _set("transactions", twin["id"], {
                "category_id": seed_user["categories"]["Groceries"].id,
            })
            before = _twin_rows([xfer.id])
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert "twin(s) are filed under" not in str(refused.value)
            assert f"({xfer.id}, {rent}, {second}, {owner})" in str(
                refused.value
            )

            _set("transfers", xfer.id, {"category_id": None})
            _upgrade()

    @pytest.mark.parametrize(
        "given_away", (False, True),
        ids=("another_users_category", "its_own_category_given_away"),
    )
    def test_a_foreign_twin_of_a_clean_transfer_is_the_row_to_repair(
        self, app, seed_user, seed_second_user, seed_periods, given_away,
    ):
        """The ids differ and the transfer's category is its owner's: the twin alone is the row.

        The transfer under its owner's Rent, as the update door files it; ONE
        twin, by raw statements, under a category that is not its owner's
        and is not its transfer's either: the second user's Groceries on the
        to-side (the ids differ as on finding BAL-575's older income twins,
        here with a foreign category), or on the from-side a category of the
        owner's own that is then given to the second user.  The twin is refused with its
        transfer's category and THAT category's owner, the transfer's own,
        beside its own; the transfer is not refused, and the advice, read
        whole here, sends the operator to the twin alone.  Following it, the
        upgrade passes and no transfer or row is left under another user's
        category, through a round trip after (the cp2b-0 review's L-1, its
        RV-S3 and RV-S6).  *given_away* is whether the twin's category is the
        owner's own, given away since.
        """
        with app.app_context():
            savings = _accounts(seed_user)[0]
            owner = seed_user["user"].id
            second = seed_second_user["user"].id
            rent = seed_user["categories"]["Rent"].id
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            transfer_service.update_transfer(xfer.id, owner, category_id=rent)
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=not given_away)
            if given_away:
                category = _new_category(owner, "One Row", "Given Away")
                _set("transactions", twin["id"], {"category_id": category})
                _set("categories", category, {"user_id": second})
            else:
                category = seed_second_user["categories"]["Groceries"].id
                _set("transactions", twin["id"], {"category_id": category})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                f"({xfer.id}, {twin['id']}, {category}, {second}, {rent}, "
                f"{owner})"
            ) in str(refused.value)
            assert "transfer(s) are filed under" not in str(refused.value)
            assert _REPAIR_ADVICE in str(refused.value)

            # The advice followed: the twin alone repaired.
            _set("transactions", twin["id"], {"category_id": rent})
            _upgrade()
            assert _cross_user_references() == []
            _downgrade()
            assert _cross_user_references() == []
            _upgrade()

    @pytest.mark.parametrize(
        "state", ("live", "soft_deleted", "twinless"),
    )
    def test_it_refuses_a_transfer_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods, state,
    ):
        """A transfer under another user's category refuses the upgrade by name.

        Option D (the cp2a review's L-1, decided by the coordinator
        2026-10-11): no door writes one, and the downgrade's one build rule
        files every twin it builds under its transfer's category, so such a
        transfer's twins would come back under another user's category, which
        the next upgrade refuses.  Here the transfer ALONE is foreign (its
        twins keep their owner's category, so no twin is refused), named as
        ``(transfer, category, that category's owner, the transfer's
        owner)``, writing nothing; once repaired, the upgrade passes.  A live
        transfer, one the delete door soft-deleted (the build rule builds its
        twins too) and one holding no twin (the create door at head, run at
        the downgraded state; the downgrade builds its two).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            owner = seed_user["user"].id
            rent = seed_user["categories"]["Rent"].id
            if state == "twinless":
                _downgrade()
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            transfer_service.update_transfer(xfer.id, owner, category_id=rent)
            _db.session.commit()
            if state == "soft_deleted":
                transfer_service.delete_transfer(xfer.id, owner, soft=True)
                _db.session.commit()
            if state != "twinless":
                _downgrade()
            assert len(_twin_rows([xfer.id])) == (
                0 if state == "twinless" else 2
            )
            foreign = seed_second_user["categories"]["Rent"].id
            second = seed_second_user["user"].id
            _set("transfers", xfer.id, {"category_id": foreign})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                "1 transfer(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            assert f"({xfer.id}, {foreign}, {second}, {owner})" in str(
                refused.value
            )
            assert "twin(s)" not in str(refused.value)

            _set("transfers", xfer.id, {"category_id": rent})
            _upgrade()

    def test_an_ordinary_settled_row_beside_the_twins_is_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The reference census names TWINS: a settled ordinary row's movement passes.

        A movement names every settled ordinary transaction, so a census over
        every referenced row would refuse every database holding one.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("12.00"),
            )
            db.session.commit()
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE transaction_id IS NOT NULL"
            )).scalar_one() >= 1
            _downgrade()
            _upgrade()

            assert _twin_rows([xfer.id]) == []

    def test_an_ordinary_row_under_another_users_category_is_not_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The category refusal names TWINS: an ordinary row is not its to ask.

        Its join to the transfer is what finds the twins, so a row naming no
        transfer is never asked, whatever category it carries; this revision
        deletes no such row.  Planted by a raw statement, as every cross-user
        category is.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            ordinary = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("12.00"),
            )
            db.session.commit()
            ordinary_id = ordinary.id
            _downgrade()
            _set("transactions", ordinary_id, {
                "category_id": seed_second_user["categories"]["Rent"].id,
            })

            _upgrade()

            assert _twin_rows([xfer.id]) == []
            assert db.session.execute(text(
                "SELECT category_id FROM budget.transactions WHERE id = :t"
            ), {"t": ordinary_id}).scalar_one() == (
                seed_second_user["categories"]["Rent"].id
            )


class TestTheDowngrade:
    """The downgrade restores the kept copy exactly, and re-syncs what changed."""

    def test_a_round_trip_restores_every_twin_column_for_column(
        self, app, db, seed_user, seed_periods,
    ):
        """Upgrade then downgrade: every twin's every column, id included.

        The twins are made to carry what a rebuild could NOT reproduce first
        -- a raised version counter, an old update time, and on one income
        twin a category its transfer does not name (finding BAL-575's shape)
        -- so the equality grades the copy, not a rebuild that happens to
        agree.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            planned = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, savings, van, seed_periods[0],
                amount=Decimal("80.00"),
            )
            hidden = create_transfer(
                seed_user, db.session, seed_user["account"], van,
                seed_periods[1], amount=Decimal("40.00"),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                hidden.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            ids = [planned.id, paid.id, hidden.id]

            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET version_id = version_id + 2, "
                "updated_at = TIMESTAMPTZ '2026-01-02 03:04:05.678901+00' "
                "WHERE transfer_id = ANY(:ids)"
            ), {"ids": ids})
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {
                "c": seed_user["categories"]["Rent"].id,
                "t": _twin_of(planned.id, income=True)["id"],
            })
            db.session.commit()
            before = _twin_rows(ids)
            assert len(before) == 6

            _upgrade()
            counts = _downgrade_counts()

            assert _twin_rows(ids) == before
            # Every twin exactly as kept, and printed so.
            assert counts == {
                "restored": 6, "rebuilt": 0, "changed": 0, "unfit": 0,
                "built": 0,
            }
            _upgrade()

    def test_what_changed_since_is_brought_in_line(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer WRITTEN since gets twins built from it, keeping their identity.

        Its version counter moved, so each twin takes the one build rule
        (``_BUILT_SQL``): ``account_id`` from its side's endpoint, the
        transfer's ``pay_period_id``, ``status_id``, ``due_date``,
        ``is_override``, ``user_id``, ``scenario_id``, ``is_deleted`` and
        ``category_id`` on BOTH sides (the kept to-side's stale category goes),
        ``name`` from the endpoints' current names, the day, basis and link
        from each side's record, and the rule's constants (a derived figure, no
        template, notes or payback).  Only the four identity columns (``id``,
        ``created_at``, ``updated_at``, ``version_id``) are the kept copy's.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("120.00"),
            )
            db.session.commit()
            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {
                "c": seed_user["categories"]["Rent"].id,
                "t": _twin_of(xfer.id, income=True)["id"],
            })
            db.session.commit()
            kept_expense = _twin_of(xfer.id, income=False)
            kept_income = _twin_of(xfer.id, income=True)
            _upgrade()

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                to_account_id=van.id,
                category_id=seed_user["categories"]["Groceries"].id,
                due_date=seed_periods[1].start_date,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=_db.session.execute(text(
                    "SELECT id FROM ref.statuses WHERE name = 'Paid'"
                )).scalar_one(),
            )
            db.session.commit()
            _downgrade()

            parent = _transfer(xfer.id)
            resynced = (
                "account_id", "pay_period_id", "status_id", "due_date",
                "is_override", "user_id", "scenario_id", "is_deleted",
                "category_id", "settled_on", "settled_day_basis_id",
                "reconciled_by_id",
            )
            for kept, income in ((kept_expense, False), (kept_income, True)):
                now = _twin_of(xfer.id, income=income)
                record = _side_record(xfer.id, income=income)
                assert record is not None and record["settled_on"] is not None
                assert now["account_id"] == (
                    parent["to_account_id"] if income
                    else parent["from_account_id"]
                )
                for column in (
                    "pay_period_id", "status_id", "due_date", "is_override",
                    "user_id", "scenario_id", "is_deleted", "category_id",
                ):
                    assert now[column] == parent[column], column
                for column in record:
                    assert now[column] == record[column], column
                # The label is built from the endpoints' current names.
                assert now["name"] == (
                    f"Transfer from {seed_user['account'].name}" if income
                    else "Transfer to One Row Van Fund"
                )
                # The rule's constants: a derived figure and nothing else.
                assert now["estimated_amount"] is None
                assert now["amount_source_id"] == _parent_transfer_id()
                for column in (
                    "template_id", "credit_payback_for_id", "notes", "occurs_on",
                ):
                    assert now[column] is None, column
                assert now["transfer_id"] == xfer.id
                # Only the identity is the kept copy's.
                for column in ("id", "created_at", "updated_at", "version_id"):
                    assert now[column] == kept[column], column
                assert set(now) - set(resynced) == {
                    "name", "estimated_amount", "amount_source_id",
                    "template_id", "credit_payback_for_id", "notes",
                    "occurs_on", "transfer_id", "transaction_type_id", "id",
                    "created_at", "updated_at", "version_id",
                }, "a column this case does not grade"
                assert now["transaction_type_id"] == kept["transaction_type_id"]
            assert kept_income["account_id"] == savings.id
            assert kept_expense["name"] == "Transfer to One Row Savings"
            _upgrade()

    def test_a_transfer_not_written_since_keeps_its_stale_income_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A to-side twin keeps a kept category while its transfer is unwritten.

        "Written since" is the transfer's own version counter, so a write to
        ANOTHER transfer leaves this one's twins exactly as kept: the stale
        income category (finding BAL-575's shape) comes back as it was.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            other = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            _downgrade()
            stale = _stale_income_twin(seed_user, xfer)
            kept = _twin_rows([xfer.id])
            _upgrade()
            transfer_service.update_transfer(
                other.id, seed_user["user"].id, notes="another transfer",
            )
            db.session.commit()
            _downgrade()

            assert _twin_rows([xfer.id]) == kept
            assert _twin_of(xfer.id, income=True)["category_id"] == stale
            _upgrade()

    def test_a_transfer_created_since_gets_two_new_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """No kept twin: two are built as the create door below built them."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()
            parent = _transfer(xfer.id)

            _downgrade()

            expense = _twin_of(xfer.id, income=False)
            income = _twin_of(xfer.id, income=True)
            assert expense["name"] == "Transfer to One Row Savings"
            assert income["name"] == f"Transfer from {seed_user['account'].name}"
            # One id drawn per twin, from-side first: a row expansion that
            # re-ran the draw per column would skip the ids between.
            assert income["id"] == expense["id"] + 1
            for twin in (expense, income):
                assert twin["version_id"] == 1
                assert twin["created_at"] == parent["created_at"]
                # Updated at the downgrade, a later transaction than the
                # transfer's creation.
                assert twin["updated_at"] > parent["created_at"]
                assert twin["estimated_amount"] is None
                assert twin["template_id"] is None
                assert twin["occurs_on"] is None
                assert twin["settled_on"] is None
                for column in (
                    "pay_period_id", "scenario_id", "status_id", "category_id",
                    "due_date", "is_override", "is_deleted", "user_id",
                ):
                    assert twin[column] == parent[column], column
            assert db.session.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'system' "
                "AND table_name = 'transfer_twin_purge'"
            )).scalar_one() == 0
            _upgrade()

    def test_a_zero_close_keeps_its_day_and_a_later_one_has_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``$0.00`` close kept unchanged keeps its day; one made after has none.

        A ``$0.00`` close holds no record, so its day lived only on its twins
        below this revision (ruling **R-BAL230** gives none at it).  The kept
        close comes back with its kept day and basis; a transfer closed at
        ``$0.00`` after the upgrade comes back settled with no day, the
        dateless pair the code below calls legacy (finding N-181).  Planted by
        SQL: the band rule admits a settled transfer with no record on either
        side, and no door below this revision states a close's day apart from
        the seam the twins no longer carry.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            early = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            late = create_transfer(
                seed_user, db.session, seed_user["account"], van,
                seed_periods[0],
            )
            db.session.commit()
            paid = _db.session.execute(text(
                "SELECT id FROM ref.statuses WHERE name = 'Paid'"
            )).scalar_one()
            entered = _db.session.execute(text(
                "SELECT id FROM ref.settled_day_bases WHERE name = 'entered'"
            )).scalar_one()
            _downgrade()
            day = seed_periods[0].start_date
            db.session.execute(text(
                "UPDATE budget.transfers SET status_id = :p WHERE id = :t"
            ), {"p": paid, "t": early.id})
            db.session.execute(text(
                "UPDATE budget.transactions SET status_id = :p, "
                "settled_on = :d, settled_day_basis_id = :b "
                "WHERE transfer_id = :t"
            ), {"p": paid, "d": day, "b": entered, "t": early.id})
            db.session.commit()
            _upgrade()
            db.session.execute(text(
                "UPDATE budget.transfers SET status_id = :p WHERE id = :t"
            ), {"p": paid, "t": late.id})
            db.session.commit()
            _downgrade()

            for income in (False, True):
                kept = _twin_of(early.id, income=income)
                assert (kept["status_id"], kept["settled_on"]) == (paid, day)
                assert kept["settled_day_basis_id"] == entered
                later = _twin_of(late.id, income=income)
                assert later["status_id"] == paid
                assert later["settled_on"] is None
                assert later["settled_day_basis_id"] is None
            _upgrade()

    def test_a_twin_whose_transfer_was_deleted_since_is_not_restored(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard delete after the upgrade leaves nothing to restore."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            gone = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            kept = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            gone_id = gone.id
            transfer_service.delete_transfer(gone_id, seed_user["user"].id)
            db.session.commit()
            _downgrade()

            assert _twin_rows([gone_id]) == []
            assert len(_twin_rows([kept.id])) == 2
            _upgrade()

    @pytest.mark.parametrize("field", _MIRRORED)
    def test_a_mirrored_field_written_around_the_counter_is_read_live(
        self, app, request, seed_user, seed_periods, field,
    ):
        """A transfer changed by a statement that left its counter: the twin follows.

        The version counter is raised by the ORM alone, so a raw statement or
        a later revision can change a transfer it reads as unwritten.  Each
        mirrored field is re-read from the transfer, so the twin says what its
        transfer says now; every other column is the kept row's, and the
        print counts each twin that differs from its kept row.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            version = _transfer(xfer.id)["version_id"]
            _set("transfers", xfer.id, _transfer_drift(
                field, request, seed_user, seed_periods, van,
            ))
            assert _transfer(xfer.id)["version_id"] == version

            counts = _downgrade_counts()

            parent = _transfer(xfer.id)
            moved = set()
            for income in (False, True):
                now = _twin_of(xfer.id, income=income)
                for column in _MIRRORED:
                    assert now[column] == _side_value(
                        parent, column, income=income,
                    ), column
                differ = {k for k in now if now[k] != kept[income][k]}
                assert differ <= set(_MIRRORED), differ
                assert field in differ or (field, income) == (
                    "account_id", False,
                ), differ
                if differ:
                    moved.add(income)
            # Only the to-side mirrors the to-account; every other plant
            # moves both twins.
            assert counts == {
                "restored": 2, "rebuilt": 0,
                "changed": 1 if field == "account_id" else 2,
                "unfit": 0, "built": 0,
            }
            assert len(moved) == counts["changed"]
            _upgrade()

    def test_a_database_with_no_copy_gets_two_twins_per_transfer(
        self, app, db, seed_user, seed_periods, capsys,
    ):
        """A database built at head (``create_all``, then a stamp) has no copy.

        ``scripts/init_database.py`` never runs the upgrade, so the copy table
        is missing; the downgrade reads it as empty and finishes (ruling
        **R-BAL261**: the undo always finishes), building two twins for every
        transfer.  Simulated by dropping the copy at head.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("35.00"),
            )
            db.session.commit()
            db.session.execute(text("DROP TABLE system.transfer_twin_purge"))
            db.session.commit()
            _downgrade()

            assert [
                (row["account_id"], row["amount_source_id"])
                for row in _twin_rows([xfer.id])
            ] == [
                (seed_user["account"].id, _parent_transfer_id()),
                (savings.id, _parent_transfer_id()),
            ]
            out = capsys.readouterr().out
            assert "0 kept twin(s) restored" in out
            assert (
                "(no copy table was found: none restored, every twin built new)"
            ) in out
            assert db.session.execute(text(
                "SELECT to_regclass('system.transfer_twin_purge') IS NULL"
            )).scalar_one() is True
            _upgrade()

    @pytest.mark.parametrize("column", (
        "settled_on", "settled_day_basis_id", "reconciled_by_id",
    ))
    def test_a_twin_off_its_sides_record_comes_back_saying_the_records(
        self, app, db, seed_user, seed_periods, column,
    ):
        """A to-side twin whose *column* is not its record's: the record's comes back.

        Not a copied field, so this revision's upgrade lets it through
        (``e616adf7fe22``'s upgrade refuses it on production's path, where no
        app writes between the two), and the downgrade re-reads the side's
        record whatever the kept twin held: the twin comes back saying what
        its record says and the print counts it (cp1d review L-1).  The state
        the app at ``e616adf7fe22`` leaves after a day correction, which writes
        the record alone.  The from-side twin comes back exactly as kept.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(paid.id, income=True)
            record = _side_record(paid.id, income=True)
            off = {
                "settled_on": lambda: record["settled_on"] + timedelta(days=1),
                "settled_day_basis_id": lambda: db.session.execute(text(
                    "SELECT min(id) FROM ref.settled_day_bases WHERE id <> :b"
                ), {"b": record["settled_day_basis_id"]}).scalar_one(),
                "reconciled_by_id": lambda: db.session.execute(text(
                    "SELECT min(id) FROM budget.account_anchor_history "
                    "WHERE account_id = :a"
                ), {"a": savings.id}).scalar_one(),
            }[column]()
            assert off is not None and off != record[column]
            _set("transactions", twin["id"], {column: off})
            kept_from_side = _twin_of(paid.id, income=False)
            _upgrade()

            counts = _downgrade_counts()

            assert _twin_of(paid.id, income=True) == {
                **twin, column: record[column],
            }
            assert _twin_of(paid.id, income=False) == kept_from_side
            assert counts == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 0,
                "built": 0,
            }
            _upgrade()

    def test_built_twins_take_ids_in_transfer_then_side_order(
        self, app, db, seed_user, seed_periods,
    ):
        """Three transfers, the first stored last: the built ids follow the transfers.

        ``_BUILD_NEW_SQL`` draws each id in transfer-then-side order, which
        one transfer cannot tell from the order a scan meets the rows in.  A
        no-op rewrite moves the first transfer's row to the end of the table,
        asserted by its physical position, so a draw in scan order would hand
        it the last ids (cp1d review X3).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            ids = []
            for period in seed_periods[:3]:
                ids.append(create_transfer(
                    seed_user, db.session, seed_user["account"], savings,
                    period,
                ).id)
                db.session.commit()
            db.session.execute(text(
                "UPDATE budget.transfers SET name = name WHERE id = :t"
            ), {"t": ids[0]})
            db.session.commit()
            stored = [row[0] for row in db.session.execute(text(
                "SELECT id FROM budget.transfers WHERE id = ANY(:ids) "
                "ORDER BY ctid"
            ), {"ids": ids})]
            assert stored == [ids[1], ids[2], ids[0]], stored

            _downgrade()

            rows = sorted(_twin_rows(ids), key=lambda row: row["id"])
            first = rows[0]["id"]
            assert [row["id"] for row in rows] == list(range(first, first + 6))
            assert [
                (row["transfer_id"], row["account_id"] == savings.id)
                for row in rows
            ] == [(xfer, income) for xfer in sorted(ids)
                  for income in (False, True)]
            _upgrade()


#: Each write after the upgrade that changes its transfer, by id.  The
#: full-edit form posts the category select on every save, a restore rewrote
#: both twins' categories, and a category changed and changed back leaves the
#: transfer's value as kept -- the three a comparison of values cannot see
#: (cp1 review M-1).  A notes-only save changes nothing a twin mirrors.
_WRITES = ("same_category_save", "restore", "a_to_b_to_a", "notes_only")


def _write(which, xfer, seed_user):
    """Make the write *which* names to *xfer* through the transfer service."""
    user_id = seed_user["user"].id
    if which == "same_category_save":
        transfer_service.update_transfer(
            xfer.id, user_id, category_id=_transfer(xfer.id)["category_id"],
            notes="a save",
        )
    elif which == "restore":
        transfer_service.delete_transfer(xfer.id, user_id, soft=True)
        _db.session.commit()
        transfer_service.restore_transfer(xfer.id, user_id)
    elif which == "a_to_b_to_a":
        kept = _transfer(xfer.id)["category_id"]
        transfer_service.update_transfer(
            xfer.id, user_id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        _db.session.commit()
        transfer_service.update_transfer(xfer.id, user_id, category_id=kept)
    else:
        transfer_service.update_transfer(xfer.id, user_id, notes="a note")
    _db.session.commit()


class TestATransferWrittenSince:
    """A twin of a transfer written since is the ONE build rule's, identity kept."""

    @pytest.mark.parametrize("which", _WRITES)
    def test_its_twins_are_built_from_it_and_keep_their_identity(
        self, app, db, seed_user, seed_periods, which,
    ):
        """Every written transfer's twins: the transfer's values, the kept id.

        The to-side's stale category becomes the transfer's (the code below
        wrote it onto both twins at each of these but the notes save); the
        figure is derived; the id, creation and update times and version
        counter are the kept ones.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("90.00"),
            )
            db.session.commit()
            _downgrade()
            stale = _stale_income_twin(seed_user, xfer)
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            _write(which, xfer, seed_user)
            _downgrade()

            parent = _transfer(xfer.id)
            assert parent["category_id"] != stale
            for income in (False, True):
                now = _twin_of(xfer.id, income=income)
                assert now["category_id"] == parent["category_id"], which
                assert (now["estimated_amount"], now["amount_source_id"]) == (
                    None, _parent_transfer_id(),
                )
                assert now["is_deleted"] is False
                for column in ("id", "created_at", "updated_at", "version_id",
                               "transaction_type_id"):
                    assert now[column] == kept[income][column], column
            _upgrade()

    def test_a_save_that_changes_nothing_leaves_its_twins_as_kept(
        self, app, db, seed_user, seed_periods,
    ):
        """A save restating every value writes no row, so the twins are exact.

        The code below would have written the transfer's category onto the
        stale to-side twin at such a save; nothing in the database records a
        save that changed nothing on its transfer, and ruling R-BAL260 brings a
        twin in line with what CHANGED on its transfer, so it comes back as
        kept -- the one residue of the counter's test, and on a hidden row.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _stale_income_twin(seed_user, xfer)
            kept = _twin_rows([xfer.id])
            version = _transfer(xfer.id)["version_id"]
            _upgrade()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                category_id=_transfer(xfer.id)["category_id"],
            )
            db.session.commit()
            assert _transfer(xfer.id)["version_id"] == version
            _downgrade()

            assert _twin_rows([xfer.id]) == kept
            _upgrade()

    def test_a_figure_typed_since_comes_back_derived(
        self, app, db, seed_user, seed_periods,
    ):
        """Kept legs owning 100.00, the parent re-typed to 400.00: both derive.

        The code below wrote a typed figure onto both legs, so kept legs can
        own the OLD figure (cp1 review H-2).  A derived leg prices its
        parent's 400.00 and cannot disagree with it; an owned leg kept at
        100.00 under a 400.00 parent breaks Transfer Invariant 3.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            typed = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            born = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1], amount=Decimal("100.00"),
            )
            db.session.commit()
            _downgrade()
            # The state the update below leaves after a typed 100.00.
            db.session.execute(text(
                "UPDATE budget.transactions SET estimated_amount = 100.00, "
                "amount_source_id = NULL WHERE transfer_id = :t"
            ), {"t": typed.id})
            db.session.commit()
            _upgrade()
            for xfer in (typed, born):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("400.00")),
                )
                db.session.commit()
            _downgrade()

            derived = [(None, _parent_transfer_id())] * 2
            for xfer in (typed, born):
                assert _transfer(xfer.id)["amount"] == Decimal("400.00")
                assert _figures(xfer.id) == derived
            _upgrade()

    def test_an_unwritten_owned_figure_comes_back_exactly(
        self, app, db, seed_user, seed_periods,
    ):
        """Kept legs owning 100.00 under an unwritten parent: kept, as owned."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            db.session.commit()
            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET estimated_amount = 100.00, "
                "amount_source_id = NULL WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()
            _downgrade()

            assert _figures(xfer.id) == [(Decimal("100.00"), None)] * 2
            _upgrade()

    def test_an_override_flag_set_since_is_on_both_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer flagged ``is_override`` after the upgrade: both twins say so.

        The update below this revision mirrored the flag onto both twins, so
        the build rule takes the transfer's flag rather than the create door's
        unflagged birth.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("20.00"),
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, is_override=True,
            )
            db.session.commit()
            assert _transfer(xfer.id)["is_override"] is True
            _downgrade()

            assert [row["is_override"] for row in _twin_rows([xfer.id])] == [
                True, True,
            ]
            _upgrade()

    def test_a_soft_delete_since_comes_back_hidden(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer hidden after the upgrade gets hidden twins (cp1 review M-2)."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            _downgrade()

            assert [row["is_deleted"] for row in _twin_rows([xfer.id])] == [
                True, True,
            ]
            _upgrade()

    def test_a_zero_close_reverted_and_closed_again_has_no_day(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00`` close re-closed since comes back dateless (cp1 review L-1).

        Its status is the kept one again, so a comparison of status alone
        would put back the kept day; the close made after the upgrade has no
        day anywhere (ruling R-BAL230), and the counter sees the writes.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            paid, _entered = _plant_zero_close(xfer, seed_periods[0].start_date)
            _upgrade()
            for status in (_status_id("Projected"), paid):
                row = db.session.get(type(xfer), xfer.id)
                row.status_id = status
                db.session.commit()
            # The kept day would no longer fit either; a written transfer's
            # twins take none of their kept values, so none counts as unfit.
            restate_account_opening(
                db.session, savings, seed_periods[0].start_date,
            )
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 2, "changed": 0, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert (twin["status_id"], twin["settled_on"]) == (paid, None)
                assert twin["settled_day_basis_id"] is None
            _upgrade()

    def test_a_linked_zero_close_passes_the_revision_below(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00`` close linked to a statement: e616's census lets it by.

        Below ``e616adf7fe22`` such a close's day, basis and link lived on its
        twins alone, so that revision's census reads its twins for status
        alone and its downgrade leaves the link where the code below reads it
        (``X-bi-6-4d-2``'s cp5 review L-3).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _plant_zero_close(xfer, seed_periods[0].start_date)
            for account in (seed_user["account"], savings):
                db.session.execute(text(
                    "UPDATE budget.transactions SET reconciled_by_id = ("
                    "SELECT min(id) FROM budget.account_anchor_history "
                    "WHERE account_id = :a) "
                    "WHERE transfer_id = :t AND account_id = :a"
                ), {"a": account.id, "t": xfer.id})
            db.session.commit()
            linked = _twin_rows([xfer.id])
            _upgrade()
            _downgrade()

            assert _twin_rows([xfer.id]) == linked
            _BELOW.refuse_drifted_twins(db.session.connection())
            db.session.execute(text(
                "UPDATE budget.transactions SET reconciled_by_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_an_endpoint_moved_away_and_back_is_renamed(
        self, app, db, seed_user, seed_periods,
    ):
        """A to B to A after a rename: the labels name the accounts now (L-2)."""
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            savings.name = "One Row Renamed"
            db.session.commit()
            for account in (van, savings):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id, to_account_id=account.id,
                )
                db.session.commit()
            _downgrade()

            assert _twin_of(xfer.id, income=False)["name"] == (
                "Transfer to One Row Renamed"
            )
            assert _twin_of(xfer.id, income=True)["account_id"] == savings.id
            _upgrade()


class TestAKeptValueThatNoLongerFits:
    """A kept value that no longer fits comes back as the transfer says (R-BAL261)."""

    def test_a_category_the_screen_deleted(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The stale category, deleted on its screen: the transfer's comes back.

        Once the twins are gone nothing uses it, so the category screen
        deletes it outright (cp1 review H-1).  Every other kept column stays.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            incoming = _new_category(
                seed_user["user"].id, "Transfers", "Incoming",
            )
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {"c": incoming, "t": _twin_of(xfer.id, income=True)["id"]})
            db.session.commit()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            response = auth_client.post(f"/categories/{incoming}/delete")
            assert response.status_code == 302
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.categories WHERE id = :c"
            ), {"c": incoming}).scalar_one() == 0

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            income = _twin_of(xfer.id, income=True)
            assert income["category_id"] == _transfer(xfer.id)["category_id"]
            assert {k: v for k, v in income.items() if k != "category_id"} == {
                k: v for k, v in kept[True].items() if k != "category_id"
            }
            assert _twin_of(xfer.id, income=False) == kept[False]
            _upgrade()

    def test_a_day_the_books_now_open_after(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00``-close day before a restated opening: no day, that side.

        The opening is restated past the kept day on the to-side's account only
        after the upgrade; that twin comes back settled with no day, basis or
        link, and the from-side keeps its day.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            day = seed_periods[0].start_date
            paid, entered = _plant_zero_close(xfer, day)
            _upgrade()
            restate_account_opening(db.session, savings, day)
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            moved = _twin_of(xfer.id, income=True)
            assert (moved["status_id"], moved["settled_on"]) == (paid, None)
            assert moved["settled_day_basis_id"] is None
            kept = _twin_of(xfer.id, income=False)
            assert (kept["settled_on"], kept["settled_day_basis_id"]) == (
                day, entered,
            )
            _upgrade()

    def test_a_statement_link_whose_statement_is_gone(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept link to a statement since gone comes back as none; one still there stays.

        A statement level goes with its bank import, and nothing holds it once
        the twins are gone.  Planted in the copy, which has no keys: one side's
        kept link names a statement that no longer exists, the other's names
        its account's own.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            day = seed_periods[0].start_date
            _plant_zero_close(xfer, day)
            _upgrade()
            standing = db.session.execute(text(
                "SELECT min(id) FROM budget.account_anchor_history "
                "WHERE account_id = :a"
            ), {"a": seed_user["account"].id}).scalar_one()
            gone = db.session.execute(text(
                "SELECT max(id) + 1000 FROM budget.account_anchor_history"
            )).scalar_one()
            for income, link in ((False, standing), (True, gone)):
                db.session.execute(text(
                    "UPDATE system.transfer_twin_purge SET row_data = "
                    "jsonb_set(row_data, '{reconciled_by_id}', to_jsonb(:l)) "
                    "WHERE transfer_id = :t AND "
                    "(row_data->>'account_id')::integer = :a"
                ), {"l": link, "t": xfer.id,
                    "a": savings.id if income else seed_user["account"].id})
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            linked = _twin_of(xfer.id, income=False)
            assert (linked["settled_on"], linked["reconciled_by_id"]) == (
                day, standing,
            )
            unlinked = _twin_of(xfer.id, income=True)
            assert (unlinked["settled_on"], unlinked["reconciled_by_id"]) == (
                day, None,
            )
            db.session.execute(text(
                "UPDATE budget.transactions SET reconciled_by_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_a_category_that_is_not_its_owners(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A transfer moved to another user by a statement: its owner's category comes back.

        The owner is a mirrored field, re-read from the transfer, so the kept
        category, still the first user's, is not the twin's owner's now; no
        key on a row's category asks its owner, so the downgrade asks it
        (cp1d review L-4).  The upgrade refused a twin whose category was not
        its owner's, so here it can only mean the owner moved.  Reachable only
        by a raw statement: no door moves a transfer between users, and the
        owner keys make it move with its accounts, its period and its
        scenario.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            second_savings = create_account_of_type(
                seed_second_user, db.session, "Savings", "Second Owner Savings",
            )
            db.session.commit()
            _set("transfers", xfer.id, {
                "user_id": seed_second_user["user"].id,
                "from_account_id": seed_second_user["account"].id,
                "to_account_id": second_savings.id,
                "pay_period_id": seed_second_user["bootstrap_period"].id,
                "scenario_id": seed_second_user["scenario"].id,
                "category_id": seed_second_user["categories"]["Rent"].id,
            })

            counts = _downgrade_counts()

            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert twin["user_id"] == seed_second_user["user"].id
                assert twin["category_id"] == (
                    seed_second_user["categories"]["Rent"].id
                )
            assert counts == {
                "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 2,
                "built": 0,
            }
            _upgrade()

    def test_a_transfer_moved_with_its_own_category_left_behind(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The owner moved, the transfer's category did not: changed, not unfit.

        A raw statement moves the transfer to the second user and leaves its
        own category the first user's.  The kept category is not the twin's
        owner's now, so it falls back to the transfer's, which is the very
        category kept: each twin differs from its kept row only by the moved
        owner and what its keys move with it, and the print counts it as
        changed and not as unfit, since no fallback changed a value (the cp1f
        review's R1, L-1).  The transfer itself is then under another user's
        category, so the next upgrade refuses its twins by name, each named
        with the transfer's category and that category's owner beside its
        own, and the transfer for its own category: the transfer is the row
        to repair, its twins with it.  The case below reaches the same
        refusals with the owner unmoved.
        """
        with app.app_context():
            savings = _accounts(seed_user)[0]
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            second_savings = create_account_of_type(
                seed_second_user, db.session, "Savings", "Second Owner Savings",
            )
            db.session.commit()
            _set("transfers", xfer.id, {
                "user_id": seed_second_user["user"].id,
                "from_account_id": seed_second_user["account"].id,
                "to_account_id": second_savings.id,
                "pay_period_id": seed_second_user["bootstrap_period"].id,
                "scenario_id": seed_second_user["scenario"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert twin["category_id"] == rent
                assert {
                    column for column in twin
                    if twin[column] != kept[income][column]
                } == {"user_id", "account_id", "pay_period_id", "scenario_id"}
            # The statement left the TRANSFER under another user's category,
            # and its twins say what it says, so the upgrade names all three.
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            db.session.rollback()
            for income in (False, True):
                assert (
                    f"({xfer.id}, {kept[income]['id']}, {rent}, "
                    f"{seed_user['user'].id}, {rent}, {seed_user['user'].id})"
                ) in str(refused.value)
            assert (
                f"({xfer.id}, {rent}, {seed_user['user'].id}, "
                f"{seed_second_user['user'].id})"
            ) in str(refused.value)
            _set("transfers", xfer.id, {"category_id": None})
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_the_transfers_own_category_given_to_another_user_since(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The create door's shape, its one category given away: back exactly, refused next.

        The transfer and both its twins under one category, as the create and
        update doors file them; after the upgrade a raw statement gives that
        category to the second user, the owner unmoved.  The kept category is
        not the twin's owner's now, so it falls back to the transfer's, which
        is the very category kept: both twins come back exactly as kept and
        the print counts none changed or unfit.  Each is then under another
        user's category, as its transfer is, so the next upgrade refuses both
        by name with the transfer's category and that category's owner beside
        each, and the transfer for its own category: the transfer is the row
        to repair, its twins with it (the cp1g review's L-1, its RVA; the
        cp2a review's L-1).
        Reachable only by a raw statement: no door writes a category's owner
        after its create.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            shared = _new_category(seed_user["user"].id, "Housing", "Shared")
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=shared,
            )
            db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            assert {row["category_id"] for row in kept.values()} == {shared}
            _upgrade()
            _set("categories", shared, {
                "user_id": seed_second_user["user"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 0, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                assert _twin_of(xfer.id, income=income) == kept[income]
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            db.session.rollback()
            assert "2 twin(s) are filed under a category" in str(refused.value)
            for income in (False, True):
                assert (
                    f"({xfer.id}, {kept[income]['id']}, {shared}, "
                    f"{seed_second_user['user'].id}, {shared}, "
                    f"{seed_second_user['user'].id})"
                ) in str(refused.value)
            assert (
                f"({xfer.id}, {shared}, {seed_second_user['user'].id}, "
                f"{seed_user['user'].id})"
            ) in str(refused.value)
            _set("categories", shared, {"user_id": seed_user["user"].id})
            _upgrade()

    def test_a_kept_category_given_to_another_user_since(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A kept category whose OWN owner moved since: the transfer's comes back.

        The to-side twin is kept under its owner's "Incoming" (finding
        BAL-575's shape), its transfer under the owner's Rent.  After the
        upgrade a raw statement gives "Incoming" to the second user, the
        twin's owner unmoved.  Kept, the twin would come back under another
        user's category, a cross-user reference no row held at this revision;
        it is not its owner's now, so it comes back under its transfer's
        category, counted changed and unfit.  The transfer's category is its
        owner's here, so no twin is left under another user's category (the
        cp1f review's R2, L-2); where the transfer's category is the one given
        away, the case above, the fallback fits no better.  Reachable only by
        a raw statement: no door writes a category's owner after its create.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            db.session.commit()
            _downgrade()
            incoming = _new_category(
                seed_user["user"].id, "Transfers", "Incoming",
            )
            _set("transactions", _twin_of(xfer.id, income=True)["id"], {
                "category_id": incoming,
            })
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            _set("categories", incoming, {
                "user_id": seed_second_user["user"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            income = _twin_of(xfer.id, income=True)
            assert income["category_id"] == rent
            assert {k: v for k, v in income.items() if k != "category_id"} == {
                k: v for k, v in kept[True].items() if k != "category_id"
            }
            assert _twin_of(xfer.id, income=False) == kept[False]
            # Every twin's category is its owner's: with the transfer's own
            # category its owner's, the downgrade made no cross-user reference.
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.transactions t "
                "JOIN budget.categories c ON c.id = t.category_id "
                "WHERE t.transfer_id = :t AND c.user_id <> t.user_id"
            ), {"t": xfer.id}).scalar_one() == 0
            _upgrade()


class TestTheCensusBelowStillRefuses:
    """``e616adf7fe22``'s drift census, after this downgrade, still REFUSES.

    This revision's step re-keyed that census (a ``$0.00`` close's twins are
    read for status alone, keyed on the SIDE holding no record); the chain
    test below grades what it passes, these grade what it must still refuse.
    """

    def test_a_basis_off_its_sides_record(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled twin whose day basis is not its side's record's is refused."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            _BELOW.refuse_drifted_twins(db.session.connection())
            twin = _twin_of(paid.id, income=True)
            other = db.session.execute(text(
                "SELECT min(id) FROM ref.settled_day_bases WHERE id <> :b"
            ), {"b": twin["settled_day_basis_id"]}).scalar_one()
            db.session.execute(text(
                "UPDATE budget.transactions SET settled_day_basis_id = :b "
                "WHERE id = :t"
            ), {"b": other, "t": twin["id"]})
            db.session.commit()

            # The tuple, not the fact's name: the message embeds the census
            # SQL, which spells every fact's name.
            with pytest.raises(RuntimeError) as refused:
                _BELOW.refuse_drifted_twins(db.session.connection())
            assert f"({paid.id}, {twin['id']}, ['day basis'])" in str(
                refused.value
            )

            db.session.rollback()
            db.session.execute(text(
                "UPDATE budget.transactions SET settled_day_basis_id = :b "
                "WHERE id = :t"
            ), {"b": twin["settled_day_basis_id"], "t": twin["id"]})
            db.session.commit()
            _upgrade()

    def test_a_deleted_twin_whose_side_holds_a_record(
        self, app, db, seed_user, seed_periods,
    ):
        """A deleted twin is read against its side's record, and refused.

        Keyed on the SIDE holding a record (L-3 of ``X-bi-6-4d-2``'s cp5
        review), a deleted twin of a settled side is graded like a live one:
        its day is none of its side's record's.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            twin_id = _twin_of(paid.id, income=True)["id"]
            db.session.execute(text(
                "UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :t"
            ), {"t": twin_id})
            db.session.commit()

            # Read against no record, so its day and basis both differ; its
            # status is its transfer's.
            with pytest.raises(RuntimeError) as refused:
                _BELOW.refuse_drifted_twins(db.session.connection())
            assert (
                f"({paid.id}, {twin_id}, ['settle day', 'day basis'])"
            ) in str(refused.value)

            db.session.rollback()
            db.session.execute(text(
                "UPDATE budget.transactions SET is_deleted = FALSE WHERE id = :t"
            ), {"t": twin_id})
            db.session.commit()
            _upgrade()


def test_the_revision_below_takes_what_this_downgrade_writes(
    app, seed_user, seed_periods,
):
    """82f7 down, e616 down, e616 up, 82f7 up, over every post-upgrade shape.

    ``e616adf7fe22``'s downgrade, which runs next in a chain, requires every
    twin to say its transfer's status and its side's record's day, basis and
    link (cp1 review M-2).  Six shapes made after the upgrade: an endpoint
    move with records, a soft delete of a Paid transfer, a revert, a hard
    delete, a ``$0.00`` close and a transfer born settled; every record is
    re-attached to its twin on the way down.  Deliberately NOT inside a nested
    application context: the fixture's idle session would hold a lock that
    ``e616adf7fe22``'s ``ALTER TABLE budget.transfers`` waits on.
    """
    del app
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Chain Savings",
    )
    van = create_account_of_type(seed_user, _db.session, "Savings", "Chain Van")
    _db.session.commit()
    user_id = seed_user["user"].id
    moved = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("80.00"),
    )
    hidden = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[1], amount=Decimal("30.00"),
    )
    reverted = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], van,
        seed_periods[1], amount=Decimal("45.00"),
    )
    gone = create_transfer(
        seed_user, _db.session, seed_user["account"], savings, seed_periods[2],
    )
    zero = create_transfer(
        seed_user, _db.session, seed_user["account"], van, seed_periods[2],
    )
    _db.session.commit()
    ids = {"moved": moved.id, "hidden": hidden.id, "reverted": reverted.id,
           "gone": gone.id, "zero": zero.id}
    _downgrade()
    _upgrade()
    transfer_service.update_transfer(ids["moved"], user_id, to_account_id=van.id)
    _db.session.commit()
    transfer_service.delete_transfer(ids["hidden"], user_id, soft=True)
    _db.session.commit()
    transfer_service.update_transfer(
        ids["reverted"], user_id, status_id=_status_id("Projected"),
    )
    _db.session.commit()
    transfer_service.delete_transfer(ids["gone"], user_id)
    _db.session.commit()
    _db.session.execute(text(
        "UPDATE budget.transfers SET status_id = :p WHERE id = :t"
    ), {"p": _status_id("Paid"), "t": ids["zero"]})
    _db.session.commit()
    born = create_settled_transfer(
        seed_user, _db.session, savings, van, seed_periods[3],
        amount=Decimal("12.00"),
    )
    _db.session.commit()
    ids["born"] = born.id
    _db.session.expunge_all()
    on_sides = dict(_db.session.execute(text(
        "SELECT coalesce(expense_transfer_id, income_transfer_id), count(*) "
        "FROM budget.transaction_entries "
        "WHERE expense_transfer_id IS NOT NULL "
        "OR income_transfer_id IS NOT NULL GROUP BY 1"
    )).all())

    _downgrade()
    run_migration_callable(_BELOW.downgrade, _db.session)
    records = dict(_db.session.execute(text(
        "SELECT t.transfer_id, count(e.id) FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "WHERE t.transfer_id IS NOT NULL GROUP BY 1"
    )).all())
    run_migration_callable(_BELOW.upgrade, _db.session)
    _upgrade()

    # Every record a side held is back on its side's twin, the moved and the
    # born-since transfers' both sides among them.
    assert records == on_sides
    assert records[ids["moved"]] == records[ids["born"]] == 2


#: Each status act the app makes at ``e616adf7fe22``, which writes the transfer
#: and its sides' records and never a twin: ``(act, whether the transfer is
#: born settled, the status the act states)``.
_STATUS_ACTS = (
    ("settle", False, "Paid"),
    ("revert", True, "Projected"),
    ("cancel", False, "Cancelled"),
)


@pytest.mark.parametrize(
    "born_settled, status", [act[1:] for act in _STATUS_ACTS],
    ids=[act[0] for act in _STATUS_ACTS],
)
def test_a_database_the_app_wrote_below_takes_that_revisions_way_down(
    app, seed_user, seed_periods, born_settled, status,
):
    """A status act at ``e616adf7fe22``: up through this revision, then down through both.

    From ``e616adf7fe22`` the app writes a settle, revert or cancel on the
    transfer and its sides' records and never on a twin, so afterwards the
    twins' status is not their transfer's, by design (cp1d review M-1).  This
    revision's upgrade does not refuse that (the status is not a copied
    field), and its downgrade re-reads each twin's status from the transfer
    and its day, basis and link from its side's record, which is what
    ``e616adf7fe22``'s downgrade census requires: the route that revision's
    own refusal prescribes, back to head.  Not inside a nested application
    context, for the chain test's reason.
    """
    del app
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Status Act Savings",
    )
    _db.session.commit()
    make = create_settled_transfer if born_settled else create_transfer
    xfer_id = make(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("50.00"),
    ).id
    _db.session.commit()
    _downgrade()
    born = {row["status_id"] for row in _twin_rows([xfer_id])}
    transfer_service.update_transfer(
        xfer_id, seed_user["user"].id, status_id=_status_id(status),
    )
    _db.session.commit()
    # The premise: the act wrote the transfer and never a twin.
    assert _transfer(xfer_id)["status_id"] == _status_id(status)
    assert {row["status_id"] for row in _twin_rows([xfer_id])} == born
    assert born != {_status_id(status)}
    records = {
        income: _side_record(xfer_id, income=income) for income in (False, True)
    }
    none = dict.fromkeys(
        ("settled_on", "settled_day_basis_id", "reconciled_by_id"),
    )

    _upgrade()
    counts = _downgrade_counts()

    assert counts == {
        "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 0, "built": 0,
    }
    for income in (False, True):
        twin = _twin_of(xfer_id, income=income)
        assert twin["status_id"] == _status_id(status)
        for column, value in (records[income] or none).items():
            assert twin[column] == value, column
    run_migration_callable(_BELOW.downgrade, _db.session)
    run_migration_callable(_BELOW.upgrade, _db.session)
    _upgrade()

    assert _twin_rows([xfer_id]) == []
    assert {
        income: _side_record(xfer_id, income=income) for income in (False, True)
    } == records
