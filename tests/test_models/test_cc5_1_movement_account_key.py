"""``9900b309f0b0`` round-trips, backfills, REFUSES, and its doors state the owner.

Plan step **credit_card:CC-5-1**, rulings **R-BAL75**, **R-BAL76**, **R-CC32**.
The companion of ``test_clearing_link_schema.py``'s
``TestAMovementsAccountIsItsOwnAndItsOwnerIsItsRows``, which grades the KEYS
the migration installs at the database tier; this grades the MIGRATION --
Definition of Done item 7, the shape ``test_c13a_owner_backfill`` uses -- and
the two application WRITERS of a movement, which must state the owner rather
than leave the column to a default that does not exist.

**Why the revision's own callables are driven rather than hand-written DDL.**
Hand-written statements standing in for them would be a second statement of the
migration that could drift from it without failing anything.  The bootstrap is
:func:`tests._test_helpers.run_migration_callable`, shared rather than copied.

**Why the template does not already grade this.**
``scripts/build_test_template.py`` runs the chain against an EMPTY database, so
``_BACKFILL_OWNER_SQL``'s ``UPDATE ... FROM`` join touches zero rows and the
downgrade's refusal counts zero movements there.  Every case here puts rows in
FIRST.

**The controls that FIRE, and the mutation each was shown to fire under**
(``docs/plans/verification.md`` standard 4; one mutation per run,
2026-09-20): the two round-trip cases and the SUCCEEDS control fail with a
``NotNullViolation`` when the backfill statement is deleted (``SET NOT NULL``
trips on every row); the refusal case fails when
``refuse_cross_account_movements`` is deleted (the downgrade's
``ADD CONSTRAINT`` then fails by the old key's own name rather than by
count); each door case fails with a ``NotNullViolation`` when that door's
``owner_id`` line is deleted.  The keys themselves are graded in
``test_clearing_link_schema.py``, where each owner key's refusal case alone
reads DID NOT RAISE when that key is not created.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import TxnTypeEnum
from app.services import entry_service, transaction_service
from tests._test_helpers import (
    add_entry,
    create_savings_account,
    generate_row_of,
    load_migration_module,
    make_expense_template,
    one_off_row_of,
    run_migration_callable as _run,
    typed,
)

#: This step's own revision, loaded so its SHIPPED callables are what runs.
_M_CC5_1 = load_migration_module("9900b309f0b0_a_movements_account_is_its_own.py")

_NEW_KEYS = (
    "fk_transaction_entries_account_id",
    "fk_transaction_entries_owner_transaction",
    "fk_transaction_entries_owner_account",
)
_OLD_KEY = "fk_transaction_entries_parent_account"


def _installed(session):
    """Return the subset of this revision's objects the database holds."""
    return {
        "column": session.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_schema='budget' AND table_name='transaction_entries' "
            "   AND column_name='owner_id'"
        )).scalar(),
        "new_keys": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid='budget.transaction_entries'::regclass "
            "   AND conname = ANY(:names)"
        ), {"names": list(_NEW_KEYS)}).scalar(),
        "old_key": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid='budget.transaction_entries'::regclass "
            "   AND conname = :name"
        ), {"name": _OLD_KEY}).scalar(),
        "superkey": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid='budget.transactions'::regclass "
            "   AND conname='uq_transactions_id_user'"
        )).scalar(),
    }


_UPGRADED = {"column": 1, "new_keys": 3, "old_key": 0, "superkey": 1}
_DOWNGRADED = {"column": 0, "new_keys": 0, "old_key": 1, "superkey": 0}


def _envelope_row(seed, period, *, name="Groceries"):
    """Place one Projected envelope row of *seed*'s in *period* and return it."""
    return one_off_row_of(
        period,
        name=name,
        amount=Decimal("120.00"),
        user_id=period.user_id,
        account_id=seed["account"].id,
        scenario_id=seed["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed["categories"]["Groceries"].id,
        is_envelope=True,
    )


def _owners_of(session, entry_ids):
    """Return ``{entry id: owner_id}`` straight off the table."""
    return dict(session.execute(text(
        "SELECT id, owner_id FROM budget.transaction_entries "
        " WHERE id = ANY(:ids)"
    ), {"ids": list(entry_ids)}).all())


class TestTheRevisionRoundTrips:
    """``downgrade`` then ``upgrade``, over a database that HOLDS movements."""

    def test_downgrade_removes_exactly_what_upgrade_added(
        self, db, seed_user, seed_periods,
    ):
        """The column, the three keys and the superkey go; the old key returns.

        The movement rows staying is the half worth asserting: a downgrade
        that took the movements with the column would still leave a schema
        that looks right.
        """
        row = _envelope_row(seed_user, seed_periods[0])
        add_entry(
            db.session, seed_user, row, Decimal("18.64"),
            seed_periods[0].start_date,
        )
        db.session.commit()
        before = db.session.execute(
            text("SELECT count(*) FROM budget.transaction_entries"),
        ).scalar()
        assert before >= 1

        assert _installed(db.session) == _UPGRADED
        _run(_M_CC5_1.downgrade, db.session)
        assert _installed(db.session) == _DOWNGRADED
        assert db.session.execute(
            text("SELECT count(*) FROM budget.transaction_entries"),
        ).scalar() == before
        _run(_M_CC5_1.upgrade, db.session)
        assert _installed(db.session) == _UPGRADED

    def test_upgrade_backfills_every_movement_from_its_row(
        self, db, seed_user, seed_second_user, seed_periods,
    ):
        """``_BACKFILL_OWNER_SQL`` writes each movement's ROW's owner onto it.

        Two of this owner's purchases under one row and one of a SECOND
        owner's under theirs, so a backfill that took one owner for the whole
        table -- a constant, ``current_user`` -- could not pass, and none is
        left NULL for ``SET NOT NULL`` to trip on.
        """
        mine = _envelope_row(seed_user, seed_periods[0])
        theirs = _envelope_row(
            seed_second_user, seed_second_user["bootstrap_period"],
            name="Theirs",
        )
        for row, seed, amount in (
            (mine, seed_user, "18.64"),
            (mine, seed_user, "7.10"),
            (theirs, seed_second_user, "42.00"),
        ):
            add_entry(
                db.session, seed, row, Decimal(amount),
                row.pay_period.start_date,
            )
        db.session.commit()
        ids_by_row = {
            mine.id: [e.id for e in mine.entries],
            theirs.id: [e.id for e in theirs.entries],
        }
        my_owner, their_owner = seed_user["user"].id, seed_second_user["user"].id
        assert my_owner != their_owner, (
            "the fixture stopped holding two owners, so this case can no "
            "longer tell a per-row backfill from a table-wide one"
        )

        _run(_M_CC5_1.downgrade, db.session)
        assert _installed(db.session)["column"] == 0
        _run(_M_CC5_1.upgrade, db.session)

        owners = _owners_of(
            db.session, ids_by_row[mine.id] + ids_by_row[theirs.id],
        )
        assert {owners[i] for i in ids_by_row[mine.id]} == {my_owner}
        assert {owners[i] for i in ids_by_row[theirs.id]} == {their_owner}
        assert db.session.execute(text(
            "SELECT count(*) FROM budget.transaction_entries "
            " WHERE owner_id IS NULL"
        )).scalar() == 0


class TestTheDowngradeREFUSESAMovementOffItsRowsAccount:
    """The downgrade's one refusal, and its control."""

    def test_a_cross_account_movement_refuses_the_downgrade_by_count(
        self, db, seed_user, seed_periods,
    ):
        """A movement the old key cannot hold is REPORTED, never re-pointed.

        A purchase on savings under a checking row -- the design's card swipe,
        writable since this revision -- has no place under the re-created
        ``fk_transaction_entries_parent_account``, and the revision does not
        choose an account for it.  Asserting the COUNT in the message is what
        makes this a test of the explicit arm: the ``ADD CONSTRAINT`` alone
        would also refuse, by the key's name, and a bare ``raises`` could not
        tell the two apart.  The schema reads UPGRADED after the rollback,
        which grades the refusal leaving nothing committed; that the refusal
        precedes every DROP is the code's order (``downgrade()``'s first
        statement), not this assertion's -- DDL is transactional here and
        the rollback would restore a dropped key too.
        """
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        row = _envelope_row(seed_user, seed_periods[0])
        add_entry(
            db.session, seed_user, row, Decimal("18.64"),
            seed_periods[0].start_date,
        )
        swipe = row.entries[0]
        swipe.account = savings
        db.session.commit()
        assert swipe.account_id != row.account_id

        with pytest.raises(RuntimeError) as exc:
            _run(_M_CC5_1.downgrade, db.session)
        assert str(exc.value).startswith(
            "1 budget.transaction_entries row(s) sit on an account other "
            "than their parent row's"
        )
        db.session.rollback()
        assert _installed(db.session) == _UPGRADED

    def test_the_same_downgrade_SUCCEEDS_once_every_movement_is_on_its_rows_account(
        self, db, seed_user, seed_periods,
    ):
        """THE CONTROL for the refusal above.

        Same row, same purchase, on the row's own account: the downgrade
        re-creates the old key over it and the upgrade takes it back.
        Without this case the refusal would pass just as well against a
        migration that could never be downgraded at all.
        """
        row = _envelope_row(seed_user, seed_periods[0])
        add_entry(
            db.session, seed_user, row, Decimal("18.64"),
            seed_periods[0].start_date,
        )
        db.session.commit()

        _run(_M_CC5_1.downgrade, db.session)
        assert _installed(db.session) == _DOWNGRADED
        _run(_M_CC5_1.upgrade, db.session)
        assert _installed(db.session) == _UPGRADED


class TestTheDoorsStateTheOwner:
    """Both writers of a movement state the ROW's owner, never the author."""

    def test_a_companions_purchase_is_the_owners(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """``entry_service.create_entry``: the author is the companion, the owner the row's.

        The one case where the two columns differ, and the reason the owner
        is a column of its own (ruling **R-CC11**: a companion records a
        purchase on the owner's envelope and the purchase is the owner's).
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="120.00",
                name="Groceries", category_key="Groceries", is_envelope=True,
            )
            row = generate_row_of(template, seed_periods[0])
            db.session.commit()
            companion_id = seed_companion["user"].id
            assert companion_id != row.user_id

            entry = entry_service.create_entry(
                row.id, companion_id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("18.64")),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.flush()

            assert entry.user_id == companion_id
            assert entry.owner_id == row.user_id
            assert entry.account_id == row.account_id
            db.session.rollback()

    def test_the_seams_covering_movement_is_the_rows_owners(
        self, app, db, seed_user, seed_periods,
    ):
        """``status_seam._covering._cover`` states the row's owner on the mirror."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="148.32",
                name="Electric", category_key="Rent",
            )
            row = generate_row_of(template, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(row)
            db.session.flush()

            movements = row.covering_movements
            assert len(movements) == 1
            assert movements[0].owner_id == row.user_id
            assert movements[0].account_id == row.account_id
            db.session.rollback()
