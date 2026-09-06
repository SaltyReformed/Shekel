"""``d4a92f6b13c8`` round-trips, backfills, and ABORTS on a disagreement.

Plan step **pay_calendar:C13-a**.  The companion of
``test_c13a_transaction_owner_key``, which grades the KEYS the migration
installs; this grades the MIGRATION -- Definition of Done item 7, and the shape
``test_c40_account_id_backfill`` and ``test_c4b2_pay_period_schedule_key``
already use.

**Why the revision's own callables are driven rather than hand-written DDL.**
Hand-written statements standing in for them would be a second statement of the
migration that could drift from it without failing anything -- the same
argument ``test_c4b2`` makes, and the reason it loads the module.  The
bootstrap is :func:`tests._test_helpers.run_migration_callable`, SHARED rather
than copied: a first version of this file hand-copied it and met the ten-second
``lock_timeout`` that helper commits first to avoid, and ledger row **P79**
already owns consolidating the other eighteen copies.

**Why the template does not already grade this.**
``scripts/build_test_template.py`` runs the chain against an EMPTY database, so
``_BACKFILL_OWNER_SQL``'s ``UPDATE ... FROM`` join touches zero rows and its
correctness is never observed.  Every case here puts rows in FIRST.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from tests._test_helpers import (
    load_migration_module,
    run_migration_callable as _run,
)

#: This step's own revision, loaded so its SHIPPED callables are what runs.
#: Hand-written DDL standing in for them would be a second statement of the
#: migration that could drift from it without failing anything.
_M_C13A = load_migration_module("d4a92f6b13c8_a_transaction_has_an_owner.py")

_KEYS = (
    "fk_transactions_user_id",
    "fk_transactions_owner_account",
    "fk_transactions_owner_period",
)


def _installed(session):
    """Return the subset of this revision's objects the database holds."""
    return {
        "column": session.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_schema='budget' AND table_name='transactions' "
            "   AND column_name='user_id'"
        )).scalar(),
        "keys": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid='budget.transactions'::regclass "
            "   AND conname = ANY(:names)"
        ), {"names": list(_KEYS)}).scalar(),
        "superkey": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid='budget.pay_periods'::regclass "
            "   AND conname='uq_pay_periods_id_user'"
        )).scalar(),
    }


def _a_row(seed_user, period, **overrides):
    """Stage one ordinary Projected transaction and return it flushed."""
    fields = {
        "user_id": period.user_id,
        "account_id": seed_user["account"].id,
        "pay_period_id": period.id,
        "scenario_id": seed_user["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Backfill subject",
        "category_id": seed_user["categories"]["Groceries"].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "amount_ownership": AmountOwnership.own(Decimal("15.00")),
    }
    fields.update(overrides)
    row = Transaction(**fields)
    _db.session.add(row)
    _db.session.flush()
    return row


class TestTheRevisionRoundTrips:
    """``downgrade`` then ``upgrade``, over a database that HOLDS rows."""

    def test_downgrade_removes_exactly_what_upgrade_added(
        self, db, seed_user, seed_periods,
    ):
        """The column, the three keys and the superkey go; the ROWS stay.

        The rows staying is the half worth asserting: this revision's
        ``downgrade`` drops a column off the busiest table in the schema, and
        a downgrade that took the money with it would still leave a schema
        that looks right.
        """
        _a_row(seed_user, seed_periods[0])
        db.session.commit()
        before = db.session.execute(
            text("SELECT count(*) FROM budget.transactions"),
        ).scalar()
        assert before >= 1

        assert _installed(db.session) == {
            "column": 1, "keys": 3, "superkey": 1,
        }
        _run(_M_C13A.downgrade, db.session)
        assert _installed(db.session) == {
            "column": 0, "keys": 0, "superkey": 0,
        }
        assert db.session.execute(
            text("SELECT count(*) FROM budget.transactions"),
        ).scalar() == before

    def test_upgrade_backfills_every_row_from_its_pay_period(
        self, db, seed_user, seed_periods,
    ):
        """``_BACKFILL_OWNER_SQL`` writes the paycheck's owner onto every row.

        **The template cannot grade this and this case is why it is here**: the
        chain is built against an empty database, so the ``UPDATE ... FROM``
        join runs over zero rows there.  Here it runs over rows that exist,
        with a row in a SECOND pay period so a backfill that took one owner
        for the whole table would still pass -- and a row of a SECOND OWNER'S,
        so a backfill that took ``current_user`` or a constant could not.
        """
        mine_first = _a_row(seed_user, seed_periods[0])
        mine_second = _a_row(seed_user, seed_periods[1], name="Second")
        db.session.commit()
        ids = (mine_first.id, mine_second.id)
        owner_id = seed_user["user"].id

        _run(_M_C13A.downgrade, db.session)
        assert _installed(db.session)["column"] == 0

        _run(_M_C13A.upgrade, db.session)
        owners = dict(db.session.execute(text(
            "SELECT id, user_id FROM budget.transactions "
            " WHERE id = ANY(:ids)"
        ), {"ids": list(ids)}).all())
        assert owners == {ids[0]: owner_id, ids[1]: owner_id}
        assert db.session.execute(text(
            "SELECT count(*) FROM budget.transactions WHERE user_id IS NULL"
        )).scalar() == 0

    def test_a_second_owners_row_takes_the_second_owner(
        self, db, seed_user, seed_second_user, seed_periods,
    ):
        """The backfill is per ROW, not one owner for the table.

        Split from the case above deliberately: on a single-owner database
        every candidate expression -- the join, a constant, ``current_user`` --
        returns the same integer, so nothing there can tell a per-row backfill
        from a table-wide one.
        """
        mine = _a_row(seed_user, seed_periods[0])
        theirs = _a_row(
            seed_second_user, seed_second_user["bootstrap_period"],
            name="Theirs",
        )
        db.session.commit()
        mine_id, theirs_id = mine.id, theirs.id

        _run(_M_C13A.downgrade, db.session)
        _run(_M_C13A.upgrade, db.session)

        owners = dict(db.session.execute(text(
            "SELECT id, user_id FROM budget.transactions "
            " WHERE id = ANY(:ids)"
        ), {"ids": [mine_id, theirs_id]}).all())
        assert owners[mine_id] == seed_user["user"].id
        assert owners[theirs_id] == seed_second_user["user"].id
        assert owners[mine_id] != owners[theirs_id], (
            "the fixture stopped holding two owners, so this case can no "
            "longer tell a per-row backfill from a table-wide one"
        )


class TestTheUpgradeABORTSOnADisagreement:
    """The arm the revision's docstring argues for at length."""

    def test_a_cross_owner_row_refuses_the_upgrade_by_name(
        self, db, seed_user, seed_second_user, seed_periods,
    ):
        """A database holding a cross-owner row does not get a quiet winner.

        The backfill takes the PERIOD'S owner, so it is the ACCOUNT pair that
        then has no match and ``fk_transactions_owner_account`` is what
        refuses.  Asserting the KEY and not merely "it raised" is what makes
        this a test of that arm: a bare ``IntegrityError`` would also be
        raised by a fixture that had stopped building a coherent row.

        The row is written with the keys OFF -- which is the only way to write
        it, and is exactly the state a database this chain has not seen could
        be in.
        """
        _a_row(seed_user, seed_periods[0])
        db.session.commit()
        _run(_M_C13A.downgrade, db.session)

        # Now storable, because the keys are gone.  This owner's account,
        # the OTHER owner's paycheck.
        db.session.execute(text("""
            INSERT INTO budget.transactions
                (account_id, pay_period_id, scenario_id, status_id, name,
                 transaction_type_id, estimated_amount, is_override,
                 is_deleted, is_envelope, companion_visible, version_id,
                 created_at, updated_at)
            VALUES (:aid, :pid, :sid, :stid, 'Cross-owner', :ttid, 1.00,
                    FALSE, FALSE, FALSE, FALSE, 1, now(), now())
        """), {
            "aid": seed_user["account"].id,
            "pid": seed_second_user["bootstrap_period"].id,
            "sid": seed_user["scenario"].id,
            "stid": ref_cache.status_id(StatusEnum.PROJECTED),
            "ttid": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        })
        db.session.commit()

        with pytest.raises(IntegrityError) as exc:
            _run(_M_C13A.upgrade, db.session)
        assert "fk_transactions_owner_account" in str(exc.value)
        db.session.rollback()

    def test_the_same_upgrade_SUCCEEDS_once_the_row_agrees(
        self, db, seed_user, seed_second_user, seed_periods,
    ):
        """THE CONTROL for the abort above.

        Same fixtures, same downgrade, same INSERT -- with the account moved
        to the paycheck's owner.  Without this case the abort would pass just
        as well against a migration that could never be re-applied at all.
        """
        _a_row(seed_user, seed_periods[0])
        db.session.commit()
        _run(_M_C13A.downgrade, db.session)

        db.session.execute(text("""
            INSERT INTO budget.transactions
                (account_id, pay_period_id, scenario_id, status_id, name,
                 transaction_type_id, estimated_amount, is_override,
                 is_deleted, is_envelope, companion_visible, version_id,
                 created_at, updated_at)
            VALUES (:aid, :pid, :sid, :stid, 'Consistent', :ttid, 1.00,
                    FALSE, FALSE, FALSE, FALSE, 1, now(), now())
        """), {
            "aid": seed_second_user["account"].id,
            "pid": seed_second_user["bootstrap_period"].id,
            "sid": seed_second_user["scenario"].id,
            "stid": ref_cache.status_id(StatusEnum.PROJECTED),
            "ttid": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        })
        db.session.commit()

        _run(_M_C13A.upgrade, db.session)
        assert _installed(db.session) == {
            "column": 1, "keys": 3, "superkey": 1,
        }
        assert db.session.execute(text(
            "SELECT user_id FROM budget.transactions "
            " WHERE name = 'Consistent'"
        )).scalar() == seed_second_user["user"].id
