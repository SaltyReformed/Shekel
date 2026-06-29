"""Tests for ``ledger_account_service`` (Build-Order Step 2, Commit 2;
the category/fallback resolver added Step 3, Commit 3).

The service is the sole go-forward writer of ``budget.ledger_accounts``.
Two entry points:

  * :func:`create_ledger_account_for_account` (Step 2) pairs every real
    account with exactly one Asset or Liability ledger account when the
    account is created.
  * :func:`get_or_create_category_ledger_account` (Step 3) lazily
    materialises the per-category Income/Expense counter accounts and the
    per-(owner, class) Uncategorized fallback an ordinary settled
    transaction's category leg books into.

Both are idempotent (a repeat call is a no-op).  The Step-2 tests pin:

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

The Step-3 tests pin the resolver's behaviour (the storage-tier
constraints themselves are covered by ``test_models/test_ledger_account``):
correct category-row shape and name snapshot; idempotency; the
mixed-category two-class case; the Uncategorized fallback (creation,
naming, per-(owner, class) singleton, and the H1 property that its lookup
keys on ``is_fallback`` so it never returns a deleted-category orphan); and
the input guards (class must be Income/Expense; a non-NULL category id must
exist).
"""
from __future__ import annotations

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db as _db
from app.models.category import Category
from app.models.ledger_account import LedgerAccount
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


def _expense_class_id():
    """Resolve the Expense ledger-account-class PK (test convenience)."""
    return ref_cache.ledger_account_class_id(LedgerAccountClassEnum.EXPENSE)


class TestCategoryLedgerAccountResolver:
    """``get_or_create_category_ledger_account`` materialises category rows."""

    def test_creates_category_row_with_correct_shape(
        self, app, db, seed_user,
    ):
        """A first call for (category, Expense) creates one correctly-shaped row.

        Shape contract for a category ledger account: ``account_id`` NULL (it
        is a counter account, not a real-account mirror); ``category_id``
        points at the budget category; ``is_fallback`` False; ``class_id`` is
        the Expense class; ``name`` snapshots the category's display label
        ("Family: Groceries"); ``user_id`` is the owner; the row is flushed
        (``id`` assigned).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert row.id is not None
            assert row.account_id is None
            assert row.category_id == groceries.id
            assert row.is_fallback is False
            assert row.class_id == _expense_class_id()
            assert row.name == groceries.display_name  # "Family: Groceries"
            assert row.user_id == user_id

    def test_idempotent_returns_existing_row(self, app, db, seed_user):
        """A second call for the same key returns the same row, not a duplicate.

        ``uq_ledger_accounts_category`` permits one row per (owner, category,
        class); the resolver short-circuits on the existing row (a second
        insert would raise) and returns the same PK.  The category-row count
        stays exactly one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            first = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )
            second = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert second.id == first.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 1
            )

    def test_same_category_two_classes_yields_two_rows(
        self, app, db, seed_user,
    ):
        """One category used as both income and expense yields two distinct rows.

        A ``Category`` is type-agnostic and double-entry needs one
        normal-balance side per account, so the same category resolves to an
        Income-class row AND an Expense-class row (the (category, class)
        natural key's edge case).  Both carry the same ``category_id``; their
        ``class_id`` and ``id`` differ.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            income_row = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, LedgerAccountClassEnum.INCOME,
                )
            )
            expense_row = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert income_row.id != expense_row.id
            assert income_row.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.INCOME,
            )
            assert expense_row.class_id == _expense_class_id()
            assert income_row.category_id == groceries.id
            assert expense_row.category_id == groceries.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 2
            )

    def test_category_row_becomes_orphan_on_category_delete(
        self, app, db, seed_user,
    ):
        """A resolver-created category row survives its category's deletion.

        The ``category_id`` FK is SET NULL, so deleting the budget category
        turns the resolver's row into an orphan: ``category_id`` goes NULL,
        ``is_fallback`` stays False, and the ``name`` snapshot persists so the
        row stays identifiable in posted history.  Complements the model-level
        SET-NULL test by proving the RESOLVER's output (not a hand-built row)
        participates correctly.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]
            category_id = groceries.id
            snapshot = groceries.display_name

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, category_id, LedgerAccountClassEnum.EXPENSE,
            )
            row_id = row.id
            _db.session.commit()

            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            orphan = _db.session.get(LedgerAccount, row_id)
            assert orphan is not None
            assert orphan.category_id is None
            assert orphan.is_fallback is False
            assert orphan.name == snapshot

    def test_long_category_name_truncated_to_fit_column(
        self, app, db, seed_user,
    ):
        """A display_name wider than the name column is truncated, not rejected.

        ``group_name`` and ``item_name`` are each ``String(100)``, so
        ``display_name`` ("Group: Item") can reach 202 chars -- wider than the
        ``ledger_accounts.name`` ``String(100)`` column, which PostgreSQL
        rejects (not silently truncates) on insert.  The resolver clips the
        snapshot to the column width so the row inserts cleanly; ``name`` is
        display-only (the natural key is the (user, category, class) IDs), so
        the clip is lossless for logic.  Without the fix this resolve raised a
        ``DataError`` and 500'd the settling transaction.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            long_category = Category(
                user_id=user_id,
                group_name="G" * 100,
                item_name="I" * 100,
            )
            _db.session.add(long_category)
            _db.session.flush()
            assert len(long_category.display_name) == 202  # 100 + ": " + 100

            # The resolver flushes internally, so without the [:N] truncation
            # this call itself would raise DataError (name overflows
            # VARCHAR(100)).  Its returning at all proves the clip fired.
            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, long_category.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert len(row.name) == 100
            assert row.name == long_category.display_name[:100]


class TestUncategorizedFallbackResolver:
    """The resolver materialises the per-(owner, class) Uncategorized fallback."""

    @pytest.mark.parametrize("ledger_class,expected_name", [
        (LedgerAccountClassEnum.INCOME, "Uncategorized Income"),
        (LedgerAccountClassEnum.EXPENSE, "Uncategorized Expense"),
    ])
    def test_null_category_creates_fallback(
        self, app, db, seed_user, ledger_class, expected_name,
    ):
        """A NULL category resolves to the fallback with ``is_fallback`` True.

        Shape: ``account_id`` NULL, ``category_id`` NULL, ``is_fallback``
        True, ``class_id`` the requested class, ``name`` the canonical
        "Uncategorized {Income|Expense}" label.  Parametrized across both
        classes.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, ledger_class,
            )

            assert row.account_id is None
            assert row.category_id is None
            assert row.is_fallback is True
            assert row.class_id == ref_cache.ledger_account_class_id(ledger_class)
            assert row.name == expected_name
            assert row.user_id == user_id

    def test_fallback_is_idempotent_singleton(self, app, db, seed_user):
        """Repeated NULL-category calls return the one fallback per (owner, class).

        ``uq_ledger_accounts_uncategorized`` (``WHERE is_fallback``) permits
        one fallback per owner per class; the resolver returns the same row on
        re-call, and the Expense-fallback count for the owner stays one.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            first = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, LedgerAccountClassEnum.EXPENSE,
            )
            second = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, LedgerAccountClassEnum.EXPENSE,
            )

            assert second.id == first.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(
                    user_id=user_id, is_fallback=True,
                    class_id=_expense_class_id(),
                )
                .count() == 1
            )

    def test_fallback_lookup_ignores_preexisting_orphan(
        self, app, db, seed_user,
    ):
        """The fallback lookup never returns a deleted-category orphan (H1).

        An orphan (``is_fallback`` False, ``account_id`` NULL, ``category_id``
        NULL) of the Expense class already exists -- the remnant of a deleted
        category.  A NULL-category resolve must NOT return that orphan (which
        would commingle this transaction's posting with the deleted category's
        history); it must create a fresh ``is_fallback`` True fallback,
        distinct from the orphan.  Service-level proof that the lookup keys on
        ``is_fallback``, not ``category_id IS NULL``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            expense_class_id = _expense_class_id()
            # Seed an orphan directly (the shape a category delete leaves).
            orphan = LedgerAccount(
                user_id=user_id, class_id=expense_class_id,
                account_id=None, category_id=None, is_fallback=False,
                name="Family: Groceries",
            )
            _db.session.add(orphan)
            _db.session.commit()
            orphan_id = orphan.id

            fallback = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, None, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert fallback.id != orphan_id
            assert fallback.is_fallback is True
            assert fallback.name == "Uncategorized Expense"
            # The orphan is untouched; two NULL/NULL Expense rows now coexist.
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=expense_class_id)
                .count() == 2
            )
            assert _db.session.get(LedgerAccount, orphan_id).is_fallback is False

    def test_fallback_is_per_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """Each owner gets their own Expense fallback (user-scoped singleton).

        The fallback singleton is keyed (user_id, class_id) ``WHERE
        is_fallback``, so two users each have their own.  The resolver's
        lookup filters by ``user_id``, so user B's resolve must NOT return
        user A's fallback -- it creates B's own, distinct row.  (Forgetting
        the ``user_id`` filter would leak A's fallback to B, a cross-tenant
        commingle.)
        """
        with app.app_context():
            user_a = seed_user["user"].id
            user_b = seed_second_user["user"].id

            fallback_a = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_a, None, LedgerAccountClassEnum.EXPENSE,
                )
            )
            fallback_b = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_b, None, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert fallback_a.id != fallback_b.id
            assert fallback_a.user_id == user_a
            assert fallback_b.user_id == user_b


class TestCategoryResolverValidation:
    """The resolver guards its inputs before writing.

    The accounting class must be Income or Expense (no database CHECK
    constrains a category row's class, so this guard is the sole defense
    against a malformed chart entry), and a non-NULL ``category_id`` must
    name an existing category.
    """

    @pytest.mark.parametrize("bad_class", [
        LedgerAccountClassEnum.ASSET,
        LedgerAccountClassEnum.LIABILITY,
        LedgerAccountClassEnum.EQUITY,
    ])
    def test_rejects_non_income_expense_class(
        self, app, db, seed_user, bad_class,
    ):
        """A class other than Income/Expense raises ValueError, creating no row.

        All three non-counter classes (Asset, Liability, Equity) must be
        refused before any row is written -- an Asset-class "category" account
        would be a malformed chart entry the database would not catch.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            with pytest.raises(ValueError, match="Income or Expense"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, bad_class,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 0
            )

    def test_missing_category_raises_value_error(self, app, db, seed_user):
        """A non-NULL category_id that names no category raises ValueError.

        A live transaction's ``category_id`` always references an existing
        category (the FK SET-NULLs it on delete), so a miss is a caller error;
        the resolver fails loud with the offending id rather than an opaque
        ``AttributeError`` on ``None.display_name``.  No row is created.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            missing_id = 999_999

            with pytest.raises(ValueError, match="999999"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, missing_id, LedgerAccountClassEnum.EXPENSE,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=missing_id)
                .count() == 0
            )

    def test_foreign_category_rejected(
        self, app, db, seed_user, seed_second_user,
    ):
        """A category_id owned by another user is treated as not-found (tenancy).

        The category snapshot load filters by ``user_id``, so passing user A's
        id with user B's category id finds nothing and raises -- the resolver
        never snapshots a foreign category's label into an A-owned ledger
        account, nor mints an A-owned row keyed to B's ``category_id``.
        Honours the project's "filter every user-data query by ``user_id``"
        rule and matches the idempotency lookup's scoping.
        """
        with app.app_context():
            user_a = seed_user["user"].id
            foreign_category = seed_second_user["categories"]["Groceries"]

            with pytest.raises(ValueError, match="owned by user_id"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_a, foreign_category.id, LedgerAccountClassEnum.EXPENSE,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_a, category_id=foreign_category.id)
                .count() == 0
            )
