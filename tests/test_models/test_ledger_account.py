"""Tests for the ``LedgerAccount`` model (Build-Order Step 2, Commit 2).

``budget.ledger_accounts`` is the chart of accounts for the double-entry
posting ledger.  These tests pin the storage-tier invariants the model and
its migration jointly guarantee:

  * the partial unique index permits exactly one *linked* ledger account
    per real account, while allowing many *unlinked* rows (NULL
    ``account_id``);
  * the ``ck_ledger_accounts_name_present`` CHECK refuses a row that
    carries neither a ``name`` nor an ``account_id`` (the display rule
    COALESCE(account.name, ledger_account.name) could otherwise resolve to
    NULL);
  * a linked row's display label derives from the live ``account.name``,
    even after a rename;
  * the ``account_id`` CASCADE disposes of the ledger account when an
    empty account is deleted, while the ``class_id`` RESTRICT refuses to
    drop a referenced accounting class;
  * the table is registered for auditing and its trigger fires.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import LedgerAccountClassEnum
from app.extensions import db as _db
from app.models.ledger_account import LedgerAccount
from tests._test_helpers import create_account_of_type


def _class_id(member):
    """Resolve a LedgerAccountClassEnum member to its integer PK."""
    return ref_cache.ledger_account_class_id(member)


class TestPartialUnique:
    """``uq_ledger_accounts_account`` -- one linked row per account."""

    def test_second_linked_row_for_same_account_rejected(
        self, app, db, seed_user,
    ):
        """A second ledger account for the same ``account_id`` trips the index.

        The account already has one paired row from the sync hook; a second
        linked row with the same ``account_id`` must raise on the partial
        unique index (the uniqueness applies to linked rows only).
        """
        with app.app_context():
            account = create_account_of_type(seed_user, _db.session, "Checking", "Dup Checking")
            with pytest.raises(IntegrityError):
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.ASSET),
                    account_id=account.id,
                    name=None,
                ))
                _db.session.commit()
            _db.session.rollback()

    def test_multiple_unlinked_rows_permitted(self, app, db, seed_user):
        """Two NULL-``account_id`` rows coexist (partial index excludes them).

        The unique index is partial (``WHERE account_id IS NOT NULL``), so
        unlinked Income/Expense/Equity rows -- all carrying NULL
        ``account_id`` -- never collide with one another.  Each must carry a
        ``name`` to satisfy the name-present CHECK.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                account_id=None,
                name="Groceries (expense)",
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                account_id=None,
                name="Rent (expense)",
            ))
            _db.session.commit()
            unlinked = (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.account_id.is_(None))
                .count()
            )
            assert unlinked == 2


class TestNamePresentCheck:
    """``ck_ledger_accounts_name_present`` -- a row carries name or account."""

    def test_null_name_and_null_account_rejected(
        self, app, db, seed_user,
    ):
        """A row with neither ``name`` nor ``account_id`` trips the CHECK.

        The display rule COALESCE(account.name, ledger_account.name) would
        resolve to NULL for such a row, so the storage tier refuses it.
        """
        with app.app_context():
            with pytest.raises(IntegrityError):
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EQUITY),
                    account_id=None,
                    name=None,
                ))
                _db.session.commit()
            _db.session.rollback()


class TestLinkedRowDisplayName:
    """A linked row's display name derives from the live account."""

    def test_name_null_and_derives_from_account_including_rename(
        self, app, db, seed_user,
    ):
        """``name`` is NULL; ``account.name`` supplies the label, live.

        The linked row stores no name of its own; the relationship reads
        ``account.name`` at render time.  After renaming the account, a
        fresh load of the ledger account reflects the new name -- proving
        the display label is never a stale snapshot.
        """
        with app.app_context():
            account = create_account_of_type(seed_user, _db.session, "Checking", "Original Name")
            ledger_account = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account.id)
                .one()
            )
            ledger_account_id = ledger_account.id
            assert ledger_account.name is None
            assert ledger_account.account.name == "Original Name"

            # Rename the real account, then reload the ledger account from
            # scratch so the relationship re-reads the live name.
            account.name = "Renamed Checking"
            _db.session.commit()
            _db.session.expire_all()

            reloaded = _db.session.get(LedgerAccount, ledger_account_id)
            assert reloaded.name is None
            assert reloaded.account.name == "Renamed Checking"


class TestForeignKeyActions:
    """CASCADE on ``account_id``; RESTRICT on ``class_id``."""

    def test_empty_account_delete_cascades_to_ledger_account(
        self, app, db, seed_user,
    ):
        """Deleting an empty account removes its paired ledger account.

        The ``account_id`` FK is ``ON DELETE CASCADE`` and the
        Account->LedgerAccount relationship is one-directional, so an ORM
        delete of the account emits the account DELETE and the database
        cascade removes the ledger row -- no orphan, no ORM SET-NULL
        attempt.  An empty account (no transactions/transfers) is the only
        account a delete can reach (see the model's impossibility argument).
        """
        with app.app_context():
            account = create_account_of_type(seed_user, _db.session, "Savings", "Empty Savings")
            account_id = account.id
            ledger_account_id = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account_id)
                .one()
                .id
            )

            _db.session.delete(account)
            _db.session.commit()

            assert _db.session.get(LedgerAccount, ledger_account_id) is None

    def test_referenced_class_delete_restricted(
        self, app, db, seed_user,
    ):
        """Deleting a referenced ``ledger_account_classes`` row is refused.

        The ``class_id`` FK is ``ON DELETE RESTRICT`` because the seeded
        classes are non-removable invariants: a successful delete would
        strand every ledger account in that class.  An unlinked Equity row
        references the Equity class; the raw DELETE on that class row must
        raise.
        """
        with app.app_context():
            equity_class_id = _class_id(LedgerAccountClassEnum.EQUITY)
            _db.session.add(LedgerAccount(
                user_id=seed_user["user"].id,
                class_id=equity_class_id,
                account_id=None,
                name="Retained earnings (equity)",
            ))
            _db.session.commit()

            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "DELETE FROM ref.ledger_account_classes WHERE id = :c"
                ), {"c": equity_class_id})
                _db.session.commit()
            _db.session.rollback()


class TestAuditTableRegistration:
    """``ledger_accounts`` is audited and its trigger fires.

    Per the coding standard "Every new table in auth, budget, or salary
    MUST be added to AUDITED_TABLES."  ``EXPECTED_TRIGGER_COUNT =
    len(AUDITED_TABLES)`` drives the entrypoint health check, so a missing
    entry would also fail the container start gate.
    """

    def test_table_registered(self):
        """Static check: ('budget', 'ledger_accounts') is in the list."""
        assert ("budget", "ledger_accounts") in AUDITED_TABLES

    def test_audit_trigger_attached_in_db(self, db):
        """Live check: the named trigger exists on the table."""
        count = _db.session.execute(_db.text(
            "SELECT count(*) FROM pg_trigger t "
            " JOIN pg_class c ON c.oid = t.tgrelid "
            " JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE t.tgname = 'audit_ledger_accounts' "
            "  AND n.nspname = 'budget' "
            "  AND c.relname = 'ledger_accounts' "
            "  AND NOT t.tgisinternal"
        )).scalar()
        assert count == 1, (
            "audit_ledger_accounts trigger missing -- the entrypoint "
            "trigger-count health check would refuse to start the container."
        )

    def test_audit_log_captures_inserts(self, app, db, seed_user):
        """Creating a ledger account materialises an INSERT audit row.

        Arithmetic: one new account fires the sync hook -> one
        ledger_accounts INSERT -> exactly one audit_log row tagged
        table_schema='budget', table_name='ledger_accounts',
        operation='INSERT'.  A trigger pointed at the wrong function would
        silently no-op, so the count delta proves the trail is intact.
        """
        with app.app_context():
            baseline = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'ledger_accounts' "
                "   AND operation = 'INSERT'"
            )).scalar()

            create_account_of_type(seed_user, _db.session, "Checking", "Audited Account")

            after = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'ledger_accounts' "
                "   AND operation = 'INSERT'"
            )).scalar()
            assert after - baseline == 1, (
                "audit_ledger_accounts trigger did not materialise an "
                "INSERT row -- forensic trail is broken for this table."
            )
