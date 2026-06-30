"""Tests for the ``LedgerAccount`` model (Build-Order Step 2, Commit 2;
the category chart-of-accounts columns added Step 3, Commit 2).

``budget.ledger_accounts`` is the chart of accounts for the double-entry
posting ledger.  These tests pin the storage-tier invariants the model and
its migration jointly guarantee:

  * the partial unique index permits exactly one *linked* ledger account
    per real account, while allowing many *unlinked* rows (NULL
    ``account_id``);
  * the ``ck_ledger_accounts_name_present`` CHECK refuses a row that
    carries neither a ``name`` nor an ``account_id`` (the display rule
    COALESCE(account.name, ledger_account.name) could otherwise resolve to
    NULL) -- including a category row whose only other link is
    ``category_id``;
  * a linked row's display label derives from the live ``account.name``,
    even after a rename;
  * the ``account_id`` CASCADE disposes of the ledger account when an
    empty account is deleted, while the ``class_id`` RESTRICT refuses to
    drop a referenced accounting class;
  * (Step 3) ``uq_ledger_accounts_category`` keys one category ledger
    account per (owner, category, class) and ``uq_ledger_accounts_uncategorized``
    one *fallback* per (owner, class) (keyed ``WHERE is_fallback``); those
    plus ``uq_ledger_accounts_account`` constrain the linked / category /
    fallback kinds, while deleted-category *orphans* (``is_fallback`` False,
    NULL/NULL) carry no unique and coexist freely;
  * (Step 3) ``ck_ledger_accounts_account_or_category_null`` forbids a row
    setting BOTH ``account_id`` and ``category_id``, and
    ``ck_ledger_accounts_fallback_shape`` forbids ``is_fallback`` on
    anything but the NULL/NULL shape (so the flag stays a true discriminator);
  * (Step 3) ``category_id`` is SET NULL on a category delete -- the
    posted-to ledger account survives as an orphan with its ``name``
    snapshot intact, coexisting with the fallback rather than colliding with
    it (the H1 regression the ``is_fallback`` flag closes);
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
        """Multiple unlinked rows coexist -- the linked unique excludes them.

        ``uq_ledger_accounts_account`` is partial (``WHERE account_id IS NOT
        NULL``), so unlinked rows (NULL ``account_id``) never collide on it.
        Two DISTINCT category rows (different ``category_id``, same class)
        demonstrate this: they fall outside the linked unique, are kept apart
        by ``uq_ledger_accounts_category``, and coexist.

        (Step 3 note: only two *fallback* rows -- same class, both
        ``is_fallback`` True -- collide, on the
        ``uq_ledger_accounts_uncategorized`` singleton; two NULL/NULL
        *orphans* (``is_fallback`` False) instead coexist freely.  Both are
        asserted by ``TestCategoryFallbackUniques`` below; here we use
        distinct category rows to isolate the *linked* unique's exclusion of
        non-linked rows.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            groceries = seed_user["categories"]["Groceries"]
            rent = seed_user["categories"]["Rent"]
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=groceries.id,
                name=groceries.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=rent.id,
                name=rent.display_name,
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

    def test_category_row_without_name_rejected(self, app, db, seed_user):
        """A category row (account_id NULL) with NULL name still trips the CHECK.

        Setting ``category_id`` does NOT satisfy
        ``ck_ledger_accounts_name_present`` (which requires ``name`` or
        ``account_id``), so a category / fallback / orphan row must always
        carry the snapshot label the display rule reads.  Proves the CHECK is
        not bypassed by the new ``category_id`` link, and pins the constraint
        name so a future schema change can't let this pass for another reason.
        """
        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    account_id=None,
                    category_id=seed_user["categories"]["Groceries"].id,
                    name=None,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_name_present" in str(excinfo.value), (
                str(excinfo.value)
            )
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

    def test_category_delete_sets_category_id_null_keeps_name(
        self, app, db, seed_user,
    ):
        """Deleting a category SET-NULLs the ledger row's ``category_id``.

        A category ledger account accumulates immutable postings, so it must
        survive its budgeting category's deletion: the ``category_id`` FK is
        ``ON DELETE SET NULL`` (mirroring ``transactions.category_id``) and
        the ``name`` snapshot is retained, leaving the row identifiable as an
        **orphan** -- ``is_fallback`` stays False (it is NOT promoted to the
        Uncategorized fallback), which is what lets it coexist with the
        fallback (see ``test_category_delete_with_fallback_present_does_not_collide``).
        The ``category`` relationship then resolves to None.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            category_id = category.id
            snapshot = category.display_name  # "Family: Groceries"
            ledger_account = LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                account_id=None,
                category_id=category_id,
                name=snapshot,
            )
            _db.session.add(ledger_account)
            _db.session.commit()
            ledger_account_id = ledger_account.id
            assert ledger_account.category is not None
            assert ledger_account.category.id == category_id

            # Delete the budgeting category at the storage tier, then reload
            # the ledger account from scratch.
            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            reloaded = _db.session.get(LedgerAccount, ledger_account_id)
            assert reloaded is not None, (
                "the posted-to ledger account must survive a category delete"
            )
            assert reloaded.category_id is None, (
                "the category back-link must be SET NULL, not cascaded"
            )
            assert reloaded.is_fallback is False, (
                "a deleted-category row becomes an orphan, NOT the fallback"
            )
            assert reloaded.name == snapshot, (
                "the name snapshot must survive so the orphaned row stays "
                "identifiable"
            )
            assert reloaded.category is None

    def test_category_delete_with_fallback_present_does_not_collide(
        self, app, db, seed_user,
    ):
        """Deleting a category does NOT collide with an existing fallback (H1).

        Regression lock for the design defect the adversarial review caught:
        a category ledger account and the Uncategorized fallback of the same
        class both exist; deleting the budget category SET-NULLs the category
        row into the ``(account_id NULL, category_id NULL)`` space.  Because
        the orphan keeps ``is_fallback`` False, it does NOT collide with the
        fallback (``is_fallback`` True) on
        ``uq_ledger_accounts_uncategorized`` -- so the category delete
        SUCCEEDS, the orphan and the fallback both survive, and the fallback
        singleton is untouched.  Without the ``is_fallback`` discriminator
        this delete raised an IntegrityError (a 500 on the ordinary "delete a
        category that has posted history" action).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            category = seed_user["categories"]["Groceries"]
            category_id = category.id
            # The Uncategorized-Expense fallback ...
            fallback = LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Expense",
            )
            # ... and a category ledger account of the SAME class.
            category_row = LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=category_id, is_fallback=False,
                name=category.display_name,
            )
            _db.session.add_all([fallback, category_row])
            _db.session.commit()
            fallback_id, category_row_id = fallback.id, category_row.id

            # Delete the budget category -> SET NULL on the category row.
            # MUST NOT raise (this is the exact H1 crash).
            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            orphan = _db.session.get(LedgerAccount, category_row_id)
            assert orphan is not None and orphan.category_id is None
            assert orphan.is_fallback is False
            surviving_fallback = _db.session.get(LedgerAccount, fallback_id)
            assert surviving_fallback is not None
            assert surviving_fallback.is_fallback is True
            # Both coexist in the NULL/NULL space, told apart by the flag.
            null_null = (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count()
            )
            assert null_null == 2


class TestCategoryFallbackUniques:
    """The Step-3 partial uniques key the category / fallback rows.

    ``uq_ledger_accounts_category`` (one row per owner+category+class) and
    ``uq_ledger_accounts_uncategorized`` (one *fallback* per owner+class,
    keyed ``WHERE is_fallback``) are the natural keys of the per-category
    chart of accounts.  Their predicates are disjoint from each other and
    from ``uq_ledger_accounts_account``, and deleted-category *orphans*
    (``is_fallback`` False, NULL/NULL) fall outside every unique, so all four
    row kinds coexist correctly.
    """

    def test_second_category_row_same_key_rejected(self, app, db, seed_user):
        """A duplicate (owner, category, class) category row trips the unique.

        ``uq_ledger_accounts_category`` permits one ledger account per owner,
        category, and accounting class; a second with the same key must raise
        on exactly that index.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=class_id,
                    account_id=None, category_id=category.id,
                    name=category.display_name,
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_category" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_same_category_two_classes_permitted(self, app, db, seed_user):
        """One category yields TWO rows -- Income-class and Expense-class.

        ``uq_ledger_accounts_category`` keys on ``class_id`` too, so a
        type-agnostic category used for both an income and an expense
        transaction correctly gets one ledger account per class (the edge
        case the (category, class) natural key is designed for).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.INCOME),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(category_id=category.id)
                .count() == 2
            )

    def test_second_fallback_row_same_class_rejected(
        self, app, db, seed_user,
    ):
        """A duplicate (owner, class) FALLBACK row trips the singleton.

        ``uq_ledger_accounts_uncategorized`` (keyed ``WHERE is_fallback``)
        enforces exactly one fallback per owner per class; a second
        ``is_fallback`` row of the same class must raise on that index.  Only
        rows flagged ``is_fallback`` are constrained -- orphans of the same
        class (tested separately) are not.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=class_id,
                    account_id=None, category_id=None, is_fallback=True,
                    name="Uncategorized Expense",
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_uncategorized" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_fallback_two_classes_permitted(self, app, db, seed_user):
        """Uncategorized-Income and Uncategorized-Expense fallbacks coexist."""
        with app.app_context():
            user_id = seed_user["user"].id
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.INCOME),
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Income",
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.is_fallback.is_(True))
                .filter_by(user_id=user_id)
                .count() == 2
            )

    def test_category_and_fallback_coexist(self, app, db, seed_user):
        """A category row and the fallback of the same class coexist.

        Proves the partial-unique predicates are disjoint (``category_id IS
        NOT NULL AND account_id IS NULL`` vs. ``is_fallback``): a same
        (owner, class) pair lands in different indexes, so the category and
        fallback kinds never collide.  A single commit of both rows raising
        no IntegrityError is the proof.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.account_id.is_(None))
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )

    def test_fallback_and_orphan_coexist(self, app, db, seed_user):
        """The fallback and an orphan of the SAME class coexist (H1 unit lock).

        Both are ``(account_id NULL, category_id NULL)`` and same class; the
        ``is_fallback`` flag (True for the fallback, False for the orphan) is
        the only difference, and it keeps the orphan outside the
        ``WHERE is_fallback`` singleton.  This is the unit-level proof of the
        property the end-to-end ``test_category_delete_with_fallback_present_does_not_collide``
        relies on: a commit of both, raising no IntegrityError.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=True,
                name="Uncategorized Expense",
            ))
            _db.session.add(LedgerAccount(  # the orphan
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=False,
                name="Family: Groceries",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )

    def test_multiple_orphans_same_class_coexist(self, app, db, seed_user):
        """Two orphans of the same class coexist -- orphans carry no unique.

        A user can retire two expense categories that both had posted
        history; each leaves an orphan (``is_fallback`` False, NULL/NULL).
        Nothing constrains them, so both persist -- the "second retired
        category of a class" case the old NULL/NULL singleton wrongly forbade.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=False,
                name="Family: Groceries",
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                account_id=None, category_id=None, is_fallback=False,
                name="Home: Rent",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                    LedgerAccount.is_fallback.is_(False),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )


class TestAccountOrCategoryExclusiveCheck:
    """``ck_ledger_accounts_account_or_category_null`` -- never both links.

    A ledger account is EITHER a linked real-account mirror OR a category
    bucket, never both (both-NULL is the legitimate uncategorized kind).
    The CHECK storage-enforces the linked/category/uncategorized partition
    so no writer bug can mint a both-set row -- one that would carry a
    non-NULL ``account_id`` and so fall OUTSIDE
    ``uq_ledger_accounts_category`` (whose predicate requires ``account_id
    IS NULL``), silently escaping the category uniqueness guarantee.
    """

    def test_both_account_and_category_set_rejected(self, app, db, seed_user):
        """A row with BOTH account_id and category_id set trips the CHECK.

        The account's auto-paired linked row is removed first
        (``ledger_accounts`` is not append-only) so the only constraint a
        both-set row can violate is the partition CHECK, not
        ``uq_ledger_accounts_account`` -- pinning the CHECK as the surface
        regardless of constraint-evaluation order.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Both Links",
            )
            category = seed_user["categories"]["Groceries"]
            # Free the account_id by removing its linked row so a both-set
            # row cannot collide on uq_ledger_accounts_account.
            linked = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account.id)
                .one()
            )
            _db.session.delete(linked)
            _db.session.commit()

            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    account_id=account.id,
                    category_id=category.id,
                    name=category.display_name,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_account_or_category_null" in str(
                excinfo.value
            ), str(excinfo.value)
            _db.session.rollback()


class TestFallbackShapeCheck:
    """``ck_ledger_accounts_fallback_shape`` -- is_fallback only on NULL/NULL.

    ``is_fallback`` may be True only on a row with neither a real account nor
    a category (the genuine Uncategorized bucket).  Forbidding it on any other
    shape keeps the flag a true discriminator, so the fallback singleton index
    (``WHERE is_fallback``) cannot be subverted by a linked or category row
    flagged ``is_fallback``.
    """

    def test_fallback_with_category_id_rejected(self, app, db, seed_user):
        """``is_fallback`` True on a row that ALSO sets category_id is refused."""
        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    account_id=None,
                    category_id=seed_user["categories"]["Groceries"].id,
                    is_fallback=True,
                    name="bad fallback",
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_fallback_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_fallback_with_account_id_rejected(self, app, db, seed_user):
        """``is_fallback`` True on a row that ALSO links a real account is refused.

        The account's auto-paired linked row is removed first so the only
        constraint a (linked AND is_fallback) row can violate is the
        fallback-shape CHECK, not ``uq_ledger_accounts_account``.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Fallback Account",
            )
            linked = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account.id)
                .one()
            )
            _db.session.delete(linked)
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.ASSET),
                    account_id=account.id,
                    category_id=None,
                    is_fallback=True,
                    name=None,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_fallback_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
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
