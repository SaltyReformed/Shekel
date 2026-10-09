"""Migration ``e616adf7fe22``: a transfer side's payment hangs off the transfer.

Plan step ``balance:X-bi-6-4d-2``, rulings **R-BAL88** (two side links),
**R-BAL107** (a transfer's accounts are its owner's) and **R-BAL168** (the side
keys carry a record on an endpoint move).  The revision files every payment
record under a twin row under its TRANSFER by a side link, moving no figure,
day, account or link; it REFUSES, writing nothing, while any stored row could
not take its link; and its downgrade puts each record back under its side's
twin, refusing while a side has none or while a twin no longer says what its
transfer and its side's record say (the cp2b-1 review's L-4).

Driven against the test database, which is built at head, through the shipped
``upgrade`` / ``downgrade``: each case first downgrades, so the pre-step shape
(every record under a twin) is what the upgrade meets -- whichever parent the
code under test writes a record under, the downgrade has put it under a twin.
Between the two the ORM's models name columns the table does not have, so that
window is raw SQL only.  Every case ends at head.

**A case that settles through the app brings its twins into step before it
downgrades** (:func:`_twins_rebuilt`): the app writes a settle on the transfer
and the side's record alone at this revision, so its twins still say Projected,
and the downgrade refuses that, as it must; plan step
``balance:X-bi-6-4d-3``'s downgrade rebuilds the twins first in the release
this revision ships in, and the helper does what it will.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "e616adf7fe22_a_transfer_side_s_payment_hangs_off_the_transfer.py",
)

#: A twin row's columns less its key and its soft-delete flag, so a raw
#: statement can copy one the way a writer nobody enumerated would.
_TWIN_COLUMNS = ", ".join(
    column.name for column in Transaction.__table__.columns
    if column.name not in ("id", "is_deleted")
)

#: A record's columns in the PRE-STEP table: less its key, its row and the two
#: side links this revision adds.
_RECORD_COLUMNS = ", ".join(
    column.name for column in TransactionEntry.__table__.columns
    if column.name not in (
        "id", "transaction_id", "expense_transfer_id", "income_transfer_id",
    )
)

#: What a record IS, less what it is filed under: the fields the revision must
#: not move.
_RECORD_FIELDS = (
    "id, amount, settled_on, settled_day_basis_id, account_id, owner_id, "
    "reconciled_by_id, purchased_on, description, figure_source_id, "
    "covers_settlement, version_id"
)


def _record_ids(transfer_ids):
    """Return the ids of the transfers' records, at head, under either parent."""
    return [row[0] for row in _db.session.execute(text(
        "SELECT e.id FROM budget.transaction_entries e "
        "WHERE coalesce(e.expense_transfer_id, e.income_transfer_id, "
        "  (SELECT t.transfer_id FROM budget.transactions t "
        "    WHERE t.id = e.transaction_id)) = ANY(:ids) "
        "ORDER BY e.id"
    ), {"ids": list(transfer_ids)})]


def _records(entry_ids):
    """Return each record's fields, sorted by id -- readable on either schema."""
    return _db.session.execute(text(
        f"SELECT {_RECORD_FIELDS} FROM budget.transaction_entries "
        "WHERE id = ANY(:ids) ORDER BY id"
    ), {"ids": list(entry_ids)}).all()


def _parents(entry_ids):
    """Return ``{record id: (row id, from-side link, to-side link)}``, at head."""
    return {
        row[0]: tuple(row[1:])
        for row in _db.session.execute(text(
            "SELECT e.id, e.transaction_id, e.expense_transfer_id, "
            "e.income_transfer_id FROM budget.transaction_entries e "
            "WHERE e.id = ANY(:ids)"
        ), {"ids": list(entry_ids)})
    }


def _has_side_links():
    """Return whether ``transaction_entries`` carries the side-link columns."""
    return _db.session.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transaction_entries' "
        "AND column_name IN ('expense_transfer_id', 'income_transfer_id')"
    )).scalar_one() == 2


def _settled_pair(seed_user):
    """Two Paid transfers on two accounts, committed; returns ``(a, b)``."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Migration Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "Migration Van Fund",
    )
    _db.session.commit()
    first = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=Decimal("500.00"),
    )
    second = create_settled_transfer(
        seed_user, _db.session, savings, van,
        seed_user["bootstrap_period"], amount=Decimal("120.00"),
    )
    _db.session.commit()
    return first, second


def _twins_rebuilt():
    """Rebuild every twin's status, day, basis and statement link from its transfer and record.

    By SQL, at head: each twin takes its transfer's status, and a LIVE twin
    its side's record's day, basis and link (``NULL`` where the side holds
    none, and on a deleted twin) -- what the downgrade's
    ``refuse_drifted_twins`` reads, and what ``X-bi-6-4d-3``'s downgrade
    promises to write before this revision's runs.  A sub-select returning no
    row sets its columns ``NULL``.  Staged; the caller's downgrade runs in the
    same transaction.
    """
    _db.session.execute(text(
        "UPDATE budget.transactions t SET "
        "status_id = (SELECT x.status_id FROM budget.transfers x "
        "  WHERE x.id = t.transfer_id), "
        "(settled_on, settled_day_basis_id, reconciled_by_id) = ("
        "  SELECT e.settled_on, e.settled_day_basis_id, e.reconciled_by_id "
        "  FROM budget.transaction_entries e "
        "  WHERE NOT t.is_deleted "
        "    AND CASE WHEN t.transaction_type_id = (SELECT id FROM "
        "             ref.transaction_types WHERE name = 'Income') "
        "             THEN e.income_transfer_id "
        "             ELSE e.expense_transfer_id END = t.transfer_id) "
        "WHERE t.transfer_id IS NOT NULL"
    ))


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "e616adf7fe22"
        assert _MIGRATION.down_revision == "1f431fec6547"


#: Every constraint the revision adds, by table.
_NEW_CONSTRAINTS = {
    "transfers": (
        "uq_transfers_id_from_account", "uq_transfers_id_to_account",
        "fk_transfers_owner_from_account", "fk_transfers_owner_to_account",
    ),
    "transaction_entries": (
        "ck_transaction_entries_one_parent",
        "ck_transaction_entries_side_link_is_a_record",
        "fk_transaction_entries_expense_side",
        "fk_transaction_entries_income_side",
    ),
}

#: Every index the revision adds.
_NEW_INDEXES = (
    "uq_transaction_entries_one_expense_side_record",
    "uq_transaction_entries_one_income_side_record",
)

#: The scratch schema the models are built into for the comparison.
_MODEL_SCHEMA = "parity_budget"


def _catalog(schema):
    """Return what PostgreSQL holds in *schema* for the revision's objects.

    Each definition is PostgreSQL's own spelling (``pg_get_constraintdef``,
    ``pg_indexes.indexdef``), with *schema* written as ``S`` so the two builds
    compare equal when they agree.

    Returns:
        ``(constraints, indexes, columns)``: ``{(table, name):
        definition}``, ``{name: definition}`` and ``{column: (is_nullable,
        data_type)}`` for ``transaction_entries``' three parent columns.
    """
    names = [name for group in _NEW_CONSTRAINTS.values() for name in group]
    constraints = {
        (table, name): definition.replace(f"{schema}.", "S.")
        for table, name, definition in _db.session.execute(text(
            "SELECT c.relname, con.conname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND con.conname = ANY(:names)"
        ), {"schema": schema, "names": names})
    }
    indexes = {
        name: definition.replace(f"{schema}.", "S.")
        for name, definition in _db.session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND indexname = ANY(:names)"
        ), {"schema": schema, "names": list(_NEW_INDEXES)})
    }
    columns = {
        column: (is_nullable, data_type)
        for column, is_nullable, data_type in _db.session.execute(text(
            "SELECT column_name, is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND table_name = 'transaction_entries' "
            "AND column_name IN ('transaction_id', 'expense_transfer_id', "
            "'income_transfer_id')"
        ), {"schema": schema})
    }
    return constraints, indexes, columns


class TestTheTables:
    """The migrated tables and the models agree on everything the revision adds.

    ``scripts/init_database.py`` builds a fresh database from the MODELS and
    stamps head, while the suite's template replays the chain; a key, check or
    index declared in one and not the other would split the two.  Alembic's
    ``compare_metadata`` reads neither a CHECK nor a partial index's predicate
    (measured: deleting the side-link CHECK from the model, or inverting an
    index's predicate, left it reporting no drift), so the models are built
    into a scratch schema and PostgreSQL's own definitions are compared.
    """

    def test_the_models_build_what_the_revision_built(self, app):
        """Every new constraint and index, and the three parent columns' nullability and type."""
        del app
        budget_tables = [
            table for table in _db.metadata.sorted_tables
            if table.schema == "budget"
        ]
        _db.session.execute(text(f"CREATE SCHEMA {_MODEL_SCHEMA}"))
        _db.metadata.create_all(
            bind=_db.session.connection().execution_options(
                schema_translate_map={"budget": _MODEL_SCHEMA},
            ),
            tables=budget_tables,
        )

        migrated = _catalog("budget")
        modelled = _catalog(_MODEL_SCHEMA)
        _db.session.rollback()

        assert len(migrated[0]) == 8 and len(migrated[1]) == 2
        assert migrated[2] == {
            "transaction_id": ("YES", "integer"),
            "expense_transfer_id": ("YES", "integer"),
            "income_transfer_id": ("YES", "integer"),
        }
        assert modelled == migrated


class TestTheRoundTrip:
    """Down files every record under its twin, up under its transfer; nothing else moves."""

    def test_down_up_down_moves_only_what_a_record_is_filed_under(
        self, app, seed_user,
    ):
        """Four records, two per transfer: same fields; twin, side link, the SAME twin.

        The second downgrade is the one that re-attaches side-linked records
        (the first meets them under twins whenever the code under test writes
        them there), so each must land back on the very twin it left.
        """
        del app
        ids = tuple(xfer.id for xfer in _settled_pair(seed_user))
        entry_ids = _record_ids(ids)
        before = _records(entry_ids)
        assert len(before) == 4

        _twins_rebuilt()
        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert not _has_side_links()
        assert _records(entry_ids) == before
        twins = _db.session.execute(text(
            "SELECT e.id, t.id, (t.transaction_type_id = (SELECT id FROM "
            "ref.transaction_types WHERE name = 'Income')) "
            "FROM budget.transaction_entries e "
            "JOIN budget.transactions t ON t.id = e.transaction_id "
            "WHERE t.transfer_id = ANY(:ids) ORDER BY e.id"
        ), {"ids": list(ids)}).all()
        assert [entry_id for entry_id, _twin, _income in twins] == entry_ids

        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert _has_side_links()
        assert _records(entry_ids) == before
        parents = _parents(entry_ids)
        for entry_id, _twin, is_income in twins:
            row_id, expense_link, income_link = parents[entry_id]
            assert row_id is None
            assert (income_link is not None) is is_income
            assert (expense_link is not None) is (not is_income)
            assert (expense_link or income_link) in ids
        # CONTROL for the downgrade's refusal below: every side has its twin.
        _MIGRATION.refuse_twinless_sides(_db.session.connection())

        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert not _has_side_links()
        assert _records(entry_ids) == before
        assert _db.session.execute(text(
            "SELECT id, transaction_id FROM budget.transaction_entries "
            "WHERE id = ANY(:ids) ORDER BY id"
        ), {"ids": entry_ids}).all() == [
            (entry_id, twin) for entry_id, twin, _income in twins
        ]
        run_migration_callable(_MIGRATION.upgrade, _db.session)

    def test_the_downgrade_withdraws_the_transfer_arm_and_the_upgrade_restores_it(
        self, app,
    ):
        """The trigger set follows the revision: two attachments below it, three at it."""
        del app
        def attachments():
            return sorted(row[0] for row in _db.session.execute(text(
                "SELECT tgname FROM pg_trigger WHERE tgname IN ("
                "'ck_movement_row_not_deleted', 'ck_hidden_row_holds_nothing', "
                "'ck_hidden_transfer_holds_nothing')"
            )))

        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert attachments() == [
            "ck_hidden_row_holds_nothing", "ck_movement_row_not_deleted",
        ]
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert attachments() == [
            "ck_hidden_row_holds_nothing", "ck_hidden_transfer_holds_nothing",
            "ck_movement_row_not_deleted",
        ]


def _downgraded_with(seed_user, plant_sql, params_of):
    """Downgrade, then stage *plant_sql* over the first transfer's from-side record.

    Returns the transfer pair; the plant is staged, not committed, and the
    caller rolls it back before upgrading.
    """
    first, second = _settled_pair(seed_user)
    _twins_rebuilt()
    run_migration_callable(_MIGRATION.downgrade, _db.session)
    (entry_id, shadow_id) = _db.session.execute(text(
        "SELECT e.id, t.id FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "WHERE t.transfer_id = :t AND t.account_id = :a"
    ), {"t": first.id, "a": first.from_account_id}).one()
    _db.session.execute(
        text(plant_sql),
        params_of(entry_id=entry_id, shadow_id=shadow_id, transfer=first),
    )
    return first, second


def _back_to_head():
    """Discard a staged plant and run the upgrade."""
    _db.session.rollback()
    run_migration_callable(_MIGRATION.upgrade, _db.session)
    assert _has_side_links()


class TestTheUpgradeRefuses:
    """Every stored row that could not take its link is named; nothing is written."""

    @pytest.mark.parametrize("plant_sql, reason", [
        ("UPDATE budget.transaction_entries SET covers_settlement = FALSE "
         "WHERE id = :entry_id", "not a settlement record"),
        ("UPDATE budget.transactions SET is_deleted = TRUE "
         "WHERE id = :shadow_id", "under a deleted twin"),
        ("UPDATE budget.transfers SET is_deleted = TRUE "
         "WHERE id = :transfer_id", "under a deleted transfer"),
    ])
    def test_a_record_that_cannot_take_its_link(
        self, app, seed_user, plant_sql, reason,
    ):
        """Each arm of the unlinkable census, named with its reason."""
        del app
        _downgraded_with(seed_user, plant_sql, lambda **kw: {
            "entry_id": kw["entry_id"], "shadow_id": kw["shadow_id"],
            "transfer_id": kw["transfer"].id,
        })
        with pytest.raises(RuntimeError, match=reason):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_a_record_off_its_sides_account(self, app, seed_user):
        """The from-side record moved onto the to-account: the side key would refuse it."""
        del app
        _downgraded_with(
            seed_user,
            "UPDATE budget.transaction_entries SET account_id = :to "
            "WHERE id = :entry_id",
            lambda **kw: {
                "entry_id": kw["entry_id"],
                "to": kw["transfer"].to_account_id,
            },
        )
        with pytest.raises(RuntimeError, match="not on its side's account"):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_a_transfer_naming_another_users_account(
        self, app, seed_user, second_user,
    ):
        """R-BAL107's census: the owner key would refuse the transfer."""
        del app
        first, _second = _downgraded_with(
            seed_user,
            "UPDATE budget.transfers SET to_account_id = :foreign "
            "WHERE id = :transfer_id",
            lambda **kw: {
                "transfer_id": kw["transfer"].id,
                "foreign": second_user["account"].id,
            },
        )
        with pytest.raises(RuntimeError, match=rf"ids \[{first.id}\]"):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_two_records_on_one_side(self, app, seed_user):
        """A second from-side twin holding a covering copy: one side, two records.

        Planted the one order the row arm allows: the original twin is hidden
        first (a twin is the row arm's carve-out), a live copy of it takes its
        place, and a copy of the record arrives under the live copy.
        """
        del app
        first, _second = _downgraded_with(
            seed_user,
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE id = :shadow_id",
            lambda **kw: {"shadow_id": kw["shadow_id"]},
        )
        (entry_id, shadow_id) = _db.session.execute(text(
            "SELECT e.id, t.id FROM budget.transaction_entries e "
            "JOIN budget.transactions t ON t.id = e.transaction_id "
            "WHERE t.transfer_id = :t AND t.account_id = :a"
        ), {"t": first.id, "a": first.from_account_id}).one()
        twin_id = _db.session.execute(text(
            f"INSERT INTO budget.transactions ({_TWIN_COLUMNS}, is_deleted) "
            f"SELECT {_TWIN_COLUMNS}, FALSE FROM budget.transactions "
            "WHERE id = :s RETURNING id"
        ), {"s": shadow_id}).scalar_one()
        _db.session.execute(text(
            "INSERT INTO budget.transaction_entries "
            f"(transaction_id, {_RECORD_COLUMNS}) "
            f"SELECT :twin, {_RECORD_COLUMNS} "
            "FROM budget.transaction_entries WHERE id = :e"
        ), {"twin": twin_id, "e": entry_id})
        with pytest.raises(
            RuntimeError,
            match=rf"more than one movement \(transfer id, is the to-side, "
                  rf"count: \[\({first.id}, False, 2\)\]",
        ):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_the_upgrade_writes_nothing_before_it_refuses(self, app, seed_user):
        """The refusal runs first: a committed bad row leaves no new column behind."""
        del app
        _downgraded_with(
            seed_user,
            "UPDATE budget.transaction_entries SET covers_settlement = FALSE "
            "WHERE id = :entry_id",
            lambda **kw: {"entry_id": kw["entry_id"]},
        )
        _db.session.commit()
        with pytest.raises(RuntimeError, match="not a settlement record"):
            run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.rollback()
        assert not _has_side_links()
        _db.session.execute(text(
            "UPDATE budget.transaction_entries SET covers_settlement = TRUE "
            "WHERE transaction_id IN (SELECT id FROM budget.transactions "
            "WHERE transfer_id IS NOT NULL)"
        ))
        _db.session.commit()
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert _has_side_links()


class TestTheDowngradeRefuses:
    """A record whose side has no LIVE twin cannot go back under one."""

    def test_a_side_whose_twin_was_hidden_refuses_the_downgrade_by_name(
        self, app, seed_user,
    ):
        """A twin hidden around the service: named, and nothing is written.

        A deleted twin cannot take the record back -- the row arm the
        downgrade restores refuses a movement arriving under a deleted row --
        so the census names it before any statement runs.
        """
        del app
        first, _second = _settled_pair(seed_user)
        _twins_rebuilt()
        run_migration_callable(_MIGRATION.downgrade, _db.session)
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.execute(text(
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE transfer_id = :t AND account_id = :a"
        ), {"t": first.id, "a": first.from_account_id})
        _db.session.commit()
        with pytest.raises(RuntimeError, match=rf"no live twin.*, {first.id}\)"):
            run_migration_callable(_MIGRATION.downgrade, _db.session)
        _db.session.rollback()
        assert _has_side_links()

    def test_a_side_with_no_twin(self, app, seed_user):
        """A from-side record whose transfer's twins are gone is named; nothing is written."""
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Twinless Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("75.00"),
        )
        _db.session.commit()
        _db.session.execute(text(
            "DELETE FROM budget.transactions WHERE transfer_id = :t"
        ), {"t": xfer.id})
        _db.session.execute(text(
            "INSERT INTO budget.transaction_entries (expense_transfer_id, "
            "account_id, owner_id, user_id, amount, description, "
            "covers_settlement, figure_source_id) "
            "SELECT :t, :a, :u, :u, 75.00, 'Twinless', TRUE, id "
            "FROM ref.movement_figure_sources WHERE name = 'resolved'"
        ), {"t": xfer.id, "a": xfer.from_account_id, "u": xfer.user_id})
        with pytest.raises(RuntimeError, match=rf"\(\d+, {xfer.id}\)"):
            _MIGRATION.refuse_twinless_sides(_db.session.connection())
        _db.session.rollback()


def _from_side_twin(xfer):
    """Return *xfer*'s live from-side twin's id."""
    return _db.session.execute(text(
        "SELECT id FROM budget.transactions "
        "WHERE transfer_id = :t AND account_id = :a AND NOT is_deleted"
    ), {"t": xfer.id, "a": xfer.from_account_id}).scalar_one()


class TestTheDowngradeRefusesADriftedTwin:
    """A twin that no longer says what its transfer and record say: named, nothing written.

    The cp2b-1 review's L-4: this revision's app writes a side's status, day,
    basis and statement link on the transfer and the side's record alone, so
    the code below it would read a twin it outlived as a drifted pair.
    """

    def test_a_pair_settled_at_this_revision_refuses_the_downgrade_by_name(
        self, app, seed_user,
    ):
        """Two transfers marked Paid by the app: their twins still say Projected."""
        del app
        first, second = _settled_pair(seed_user)

        with pytest.raises(RuntimeError, match="no longer say") as refused:
            run_migration_callable(_MIGRATION.downgrade, _db.session)
        _db.session.rollback()

        message = str(refused.value)
        assert f"({first.id}, {_from_side_twin(first)}, ['status'" in message
        assert f"({second.id}, {_from_side_twin(second)}, ['status'" in message
        assert "4 twin row(s)" in message
        assert _has_side_links()

    def test_every_shape_a_pre_step_door_wrote_passes_and_downgrades(
        self, app, seed_user,
    ):
        """CONTROL, planted as the doors below this revision wrote it, not as the census reads.

        Two Paid pairs whose twins mirror their records (the design's M4),
        and a ``$0.00`` close live and soft-deleted: the seam below this
        revision dated both twins of a close on the day it was stated, with no
        record beside them, and its soft delete kept them (the cp2c review's
        M-1, which a control built by :func:`_twins_rebuilt` -- the census's
        complement -- could never fail on).  The census passes, the whole
        downgrade runs, and the closes' twins keep their day.
        """
        del app
        first, _second = _settled_pair(seed_user)
        live = create_transfer(
            seed_user, _db.session, seed_user["account"], first.to_account,
            seed_user["bootstrap_period"], amount=Decimal("75.00"),
        )
        hidden = create_transfer(
            seed_user, _db.session, seed_user["account"], first.to_account,
            seed_user["bootstrap_period"], amount=Decimal("80.00"),
        )
        _db.session.commit()
        _twins_rebuilt()
        closed_on = _db.session.execute(text(
            "SELECT settled_on FROM budget.transaction_entries "
            "WHERE expense_transfer_id = :t"
        ), {"t": first.id}).scalar_one()
        zero_closes = [live.id, hidden.id]
        _db.session.execute(text(
            "UPDATE budget.transfers SET status_id = (SELECT id FROM "
            "ref.statuses WHERE name = 'Paid') WHERE id = ANY(:ids)"
        ), {"ids": zero_closes})
        _db.session.execute(text(
            "UPDATE budget.transactions SET status_id = (SELECT id FROM "
            "ref.statuses WHERE name = 'Paid'), settled_on = :d, "
            "settled_day_basis_id = (SELECT id FROM ref.settled_day_bases "
            "WHERE name = 'borrowed') WHERE transfer_id = ANY(:ids)"
        ), {"d": closed_on, "ids": zero_closes})
        _db.session.execute(text(
            "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"
        ), {"t": hidden.id})
        _db.session.execute(text(
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE transfer_id = :t"
        ), {"t": hidden.id})

        _MIGRATION.refuse_drifted_twins(_db.session.connection())
        run_migration_callable(_MIGRATION.downgrade, _db.session)

        assert not _has_side_links()
        assert _db.session.execute(text(
            "SELECT DISTINCT settled_on FROM budget.transactions "
            "WHERE transfer_id = ANY(:ids)"
        ), {"ids": zero_closes}).scalars().all() == [closed_on]
        run_migration_callable(_MIGRATION.upgrade, _db.session)

    def test_a_zero_close_made_at_this_revision_refuses(self, app, seed_user):
        """The carve-out's boundary: a close made HERE leaves its twins Projected."""
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Zero Close Savings",
        )
        _db.session.commit()
        xfer = create_settled_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("90.00"),
            settled_amount=Decimal("0.00"),
        )
        _db.session.commit()

        with pytest.raises(RuntimeError, match="2 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert f"({xfer.id}, {_from_side_twin(xfer)}, ['status'])" in str(
            refused.value,
        )

    def test_a_dated_twin_under_an_unsettled_transfer_is_refused(
        self, app, seed_user,
    ):
        """A recordless twin is read for its day while its transfer is not settled."""
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Unsettled Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("60.00"),
        )
        _db.session.commit()
        twin = _from_side_twin(xfer)
        _db.session.execute(text(
            "UPDATE budget.transactions SET settled_on = :d, "
            "settled_day_basis_id = (SELECT id FROM ref.settled_day_bases "
            "WHERE name = 'entered') WHERE id = :twin"
        ), {"d": seed_user["bootstrap_period"].start_date, "twin": twin})

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert (
            f"({xfer.id}, {twin}, ['settle day', 'day basis'])"
            in str(refused.value)
        )

    @pytest.mark.parametrize("drift_sql, fact", [
        ("UPDATE budget.transactions SET status_id = (SELECT id FROM "
         "ref.statuses WHERE name = 'Projected') WHERE id = :twin", "status"),
        ("UPDATE budget.transactions SET settled_on = settled_on - 1 "
         "WHERE id = :twin", "settle day"),
        ("UPDATE budget.transactions t SET settled_day_basis_id = (SELECT "
         "min(b.id) FROM ref.settled_day_bases b "
         "WHERE b.id <> t.settled_day_basis_id) WHERE id = :twin", "day basis"),
        ("UPDATE budget.transactions t SET reconciled_by_id = (SELECT "
         "max(h.id) FROM budget.account_anchor_history h "
         "WHERE h.account_id = t.account_id) WHERE id = :twin",
         "statement link"),
    ])
    def test_each_fact_is_read(self, app, seed_user, drift_sql, fact):
        """One fact moved on one in-step twin: that twin is named with that fact alone."""
        del app
        first, _second = _settled_pair(seed_user)
        _twins_rebuilt()
        twin = _from_side_twin(first)
        _db.session.execute(text(drift_sql), {"twin": twin})

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert f"({first.id}, {twin}, ['{fact}'])" in str(refused.value)

    def test_a_deleted_twin_is_read_against_no_record(self, app, seed_user):
        """A hidden copy of a LINKED twin: its record goes back under the LIVE twin, not it.

        Read through the link, the one fact a deleted twin of a settled
        transfer is read for besides its status: its day and basis are not,
        since the seam below this revision left a soft-deleted ``$0.00``
        close's twins dated (the M-1 carve-out).
        """
        del app
        first, _second = _settled_pair(seed_user)
        _db.session.execute(text(
            "UPDATE budget.transaction_entries e SET reconciled_by_id = ("
            "SELECT max(h.id) FROM budget.account_anchor_history h "
            "WHERE h.account_id = e.account_id) "
            "WHERE e.expense_transfer_id = :t"
        ), {"t": first.id})
        _twins_rebuilt()
        copy = _db.session.execute(text(
            f"INSERT INTO budget.transactions (is_deleted, {_TWIN_COLUMNS}) "
            f"SELECT TRUE, {_TWIN_COLUMNS} FROM budget.transactions "
            "WHERE id = :twin RETURNING id"
        ), {"twin": _from_side_twin(first)}).scalar_one()

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert (
            f"({first.id}, {copy}, ['statement link'])" in str(refused.value)
        )
