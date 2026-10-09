"""Migration ``82f7b7ed4332``: a transfer is one row.

Plan step ``balance:X-bi-6-4d-3``, rulings **R-BAL166** (the twin rows are
deleted in the release that stops their upkeep), **R-BAL258** (the database
refuses a new one, "Database refuses") and **R-BAL260** (the downgrade restores
each twin from a kept copy, "Keep a copy").  The revision keeps a copy of every
twin in ``system.transfer_twin_purge``, deletes them, and adds
``ck_transactions_names_no_transfer``; it REFUSES, writing nothing, while a twin
could not go cleanly; its downgrade restores every kept twin column for column,
brings the columns the app changed since back in line with the transfer, builds
two new twins for a transfer created since, and drops the copy.

Driven against the test database, which is built at head, through the shipped
``upgrade`` / ``downgrade``.  At head no twin exists, so the first downgrade in
each case BUILDS two per transfer (the copy holds none) -- the pre-step shape the
upgrade meets.  The schema differs between the two revisions only by the CHECK
and the copy, so the app's doors run at either.  Every case ends at head.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

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
                "transfer_category_id, "
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

            with pytest.raises(RuntimeError) as refused:
                _MIGRATION.refuse_twins_that_cannot_go(db.session.connection())
            assert (
                f"({twin['id']}, {xfer.id}, "
                "'transactions.credit_payback_for_id')"
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

            with pytest.raises(RuntimeError) as refused:
                _MIGRATION.refuse_twins_that_cannot_go(db.session.connection())
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
            _downgrade()

            assert _twin_rows(ids) == before
            _upgrade()

    def test_what_changed_since_is_brought_in_line(
        self, app, db, seed_user, seed_periods,
    ):
        """An edit between upgrade and downgrade re-syncs exactly the listed columns.

        Re-synced from the transfer: ``account_id``, ``pay_period_id``,
        ``status_id``, ``due_date``, ``is_override`` (and ``user_id``,
        ``scenario_id``, ``is_deleted``, unchanged here); ``name`` on BOTH sides
        because an endpoint moved; ``category_id`` on the from-side always, and on the to-side
        because the transfer's category changed; the day, basis and link from
        each side's record.  Every other column is the kept copy's.
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
                # An endpoint moved, so both sides are re-named from the
                # endpoints' current names, as the move below re-named both.
                assert now["name"] == (
                    f"Transfer from {seed_user['account'].name}" if income
                    else "Transfer to One Row Van Fund"
                )
                # Everything else is the kept copy's, id and counter included.
                for column in now:
                    if column not in resynced and column != "name":
                        assert now[column] == kept[column], column
            assert kept_income["account_id"] == savings.id
            assert kept_expense["name"] == "Transfer to One Row Savings"
            _upgrade()

    def test_an_unchanged_transfer_keeps_its_stale_income_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A to-side twin keeps a kept category while its transfer's is unchanged."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            stale = seed_user["categories"]["Rent"].id
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {"c": stale, "t": _twin_of(xfer.id, income=True)["id"]})
            db.session.commit()
            _upgrade()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, notes="a note, no category",
            )
            db.session.commit()
            _downgrade()

            assert _twin_of(xfer.id, income=True)["category_id"] == stale
            assert (
                _twin_of(xfer.id, income=False)["category_id"]
                == _transfer(xfer.id)["category_id"]
            )
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
            for twin in (expense, income):
                assert twin["version_id"] == 1
                assert twin["created_at"] == parent["created_at"]
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
