"""Tests for ``ledger_account_service`` (Build-Order Step 2, Commit 2).

The service is the go-forward writer of ``budget.ledger_accounts``: it
pairs every real account with exactly one Asset or Liability ledger
account when the account is created (via ``account_service.create_account``)
and is idempotent so a repeat call is a no-op.

These tests pin the three load-bearing properties:

  * **Shape** -- one paired linked row per account, ``account_id`` set,
    ``name`` NULL (display derives from ``account.name``), ``user_id``
    copied from the account.
  * **Class derivation** -- a Liability-category account maps to the
    Liability ledger class; every other category (Asset, Retirement,
    Investment) maps to the Asset ledger class.  The derivation branches
    on the account-type category INTEGER ID, never the string name.
  * **Idempotency** -- a second sync returns the existing row, never a
    duplicate (the partial unique ``uq_ledger_accounts_account`` would
    otherwise raise).
"""
from __future__ import annotations

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db as _db
from app.services import ledger_account_service
from tests._test_helpers import (
    create_account_of_type,
    ledger_accounts_for_account,
)


class TestSyncHookShape:
    """The hook materialises exactly one correctly-shaped linked row."""

    def test_create_account_pairs_exactly_one_ledger_account(
        self, app, db, seed_user,
    ):
        """A new Checking account gets one Asset ledger account.

        Shape contract: exactly one linked row; ``account_id`` points at
        the new account; ``name`` is NULL (a linked row derives its
        display label from ``account.name``); ``user_id`` is copied from
        the account; the class is Asset (Checking is Asset-category).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "New Checking",
            )
            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            ledger_account = rows[0]
            assert ledger_account.account_id == account.id
            assert ledger_account.name is None
            assert ledger_account.user_id == account.user_id
            assert ledger_account.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )

    @pytest.mark.parametrize("type_name,expected_class", [
        ("Checking", LedgerAccountClassEnum.ASSET),
        ("HYSA", LedgerAccountClassEnum.ASSET),
        ("Property", LedgerAccountClassEnum.ASSET),
        ("Mortgage", LedgerAccountClassEnum.LIABILITY),
        ("Auto Loan", LedgerAccountClassEnum.LIABILITY),
        ("Credit Card", LedgerAccountClassEnum.LIABILITY),
        # Retirement and Investment categories are assets on the books --
        # only borrowed money is a liability.
        ("401(k)", LedgerAccountClassEnum.ASSET),
        ("Roth IRA", LedgerAccountClassEnum.ASSET),
        ("Brokerage", LedgerAccountClassEnum.ASSET),
    ])
    def test_class_derivation_by_category(
        self, app, db, seed_user, type_name, expected_class,
    ):
        """Each account type maps to the expected ledger class.

        Liability-category types (Mortgage, Auto Loan, Credit Card) ->
        Liability; every other category (Asset, Retirement, Investment) ->
        Asset.  Proves the category-ID branch end to end across all four
        categories.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, type_name, f"Test {type_name}",
            )
            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            assert rows[0].class_id == ref_cache.ledger_account_class_id(
                expected_class,
            )


class TestSyncHookIdempotency:
    """A repeat sync never duplicates; a deleted pairing is restored."""

    def test_hook_idempotent_returns_existing_row(
        self, app, db, seed_user,
    ):
        """A second sync returns the existing row, count stays one.

        ``create_account`` already paired the account once; calling the
        hook again must short-circuit on the existing row (the partial
        unique would otherwise raise) and return the same PK.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Idem Savings",
            )
            first = ledger_accounts_for_account(_db.session, account.id)
            assert len(first) == 1
            first_id = first[0].id

            again = ledger_account_service.create_ledger_account_for_account(
                account,
            )
            assert again.id == first_id
            assert len(ledger_accounts_for_account(_db.session, account.id)) == 1

    def test_hook_recreates_after_deletion(self, app, db, seed_user):
        """Deleting the pairing then re-syncing restores exactly one row.

        Proves the hook's create path standalone (independent of
        ``create_account``): with the auto-created ledger account removed,
        the next sync inserts a fresh linked row with the same class.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Re-sync Loan",
            )
            original = ledger_accounts_for_account(_db.session, account.id)[0]
            original_class_id = original.class_id

            # Remove the auto-created pairing via raw SQL so the next sync
            # sees an unpaired account.
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": account.id})
            _db.session.commit()
            assert ledger_accounts_for_account(_db.session, account.id) == []

            ledger_account_service.create_ledger_account_for_account(account)
            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            # Re-derived class matches the original (Mortgage -> Liability).
            assert rows[0].class_id == original_class_id
            assert rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )
