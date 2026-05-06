"""
Shekel Budget App -- Companion Service Tests

Data isolation and visibility filtering tests for the companion
service.  Verifies that companion users can only see transactions
from templates flagged ``companion_visible=True``, scoped to
their linked owner's pay periods.

Covers plan test IDs: 10.1, 10.2, 10.4, 10.12, 10.13.
Additional tests beyond the plan baseline cover period isolation,
entry eager-loading, misconfigured companions, and edge cases.
"""

import pytest
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RoleEnum, StatusEnum, TxnTypeEnum
from app.exceptions import NotFoundError
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import companion_service
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(seed_user, *, companion_visible, track=False, name="Item"):
    """Create a transaction template for the seed_user owner.

    Args:
        seed_user: The seed_user fixture dict.
        companion_visible: Whether the template is companion-visible.
        track: Whether to enable is_envelope.
        name: Template name.

    Returns:
        The created TransactionTemplate object (flushed, ID available).
    """
    expense_type = (
        db.session.query(TransactionType)
        .filter_by(name="Expense").one()
    )
    category = list(seed_user["categories"].values())[0]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name=name,
        default_amount=Decimal("500.00"),
        transaction_type_id=expense_type.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        companion_visible=companion_visible,
        is_envelope=track,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_txn(seed_user, period, template, *, name=None, amount=None):
    """Create a transaction from a template in a specific period.

    Args:
        seed_user: The seed_user fixture dict.
        period: The PayPeriod to assign.
        template: The TransactionTemplate.
        name: Override the name (defaults to template.name).
        amount: Override the estimated_amount (defaults to template.default_amount).

    Returns:
        The created Transaction object (flushed, ID available).
    """
    expense_type = (
        db.session.query(TransactionType)
        .filter_by(name="Expense").one()
    )
    category = list(seed_user["categories"].values())[0]

    txn = Transaction(
        name=name or template.name,
        estimated_amount=amount or template.default_amount,
        transaction_type_id=expense_type.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        pay_period_id=period.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


# ── Visibility Filtering ─────────────────────────────────────────────


class TestVisibilityFiltering:
    """Verify companion only sees transactions from visible templates."""

    def test_companion_sees_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.1: 3 templates (2 visible, 1 not) -- only 2 returned.

        Arithmetic: 2 visible templates produce 2 transactions in
        period[0].  The non-visible template's transaction is excluded.
        """
        t_vis1 = _make_template(seed_user, companion_visible=True, name="Groceries")
        t_vis2 = _make_template(seed_user, companion_visible=True, name="Gas")
        t_hidden = _make_template(seed_user, companion_visible=False, name="Mortgage")

        _make_txn(seed_user, seed_periods_today[0], t_vis1, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], t_vis2, name="Gas")
        _make_txn(seed_user, seed_periods_today[0], t_hidden, name="Mortgage")
        db.session.commit()

        companion = seed_companion["user"]
        txns, period = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        names = [t.name for t in txns]
        assert len(txns) == 2
        assert "Groceries" in names
        assert "Gas" in names
        assert "Mortgage" not in names

    def test_companion_sees_no_non_visible(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.2: All templates non-visible -- empty list, not error.

        The service returns an empty transaction list without raising.
        """
        t1 = _make_template(seed_user, companion_visible=False, name="Rent")
        t2 = _make_template(seed_user, companion_visible=False, name="Electric")
        _make_txn(seed_user, seed_periods_today[0], t1, name="Rent")
        _make_txn(seed_user, seed_periods_today[0], t2, name="Electric")
        db.session.commit()

        companion = seed_companion["user"]
        txns, period = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert txns == []
        assert period is not None

    def test_mix_tracked_and_untracked_visible(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Both tracked and non-tracked visible templates are returned.

        Verifies that is_envelope does not affect
        companion visibility filtering.
        """
        t_tracked = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        t_simple = _make_template(
            seed_user, companion_visible=True, track=False, name="Gas",
        )
        _make_txn(seed_user, seed_periods_today[0], t_tracked, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], t_simple, name="Gas")
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 2

    def test_visible_template_no_transactions_in_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible template exists but no transactions in the requested period.

        The service returns an empty list (not an error) when the template
        has no transactions in the given period.
        """
        _make_template(seed_user, companion_visible=True, name="Christmas")
        # No transactions created for this template in this period.
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert txns == []

    def test_soft_deleted_transactions_excluded(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Soft-deleted transactions are filtered out even if template is visible.

        The is_deleted=True filter prevents deleted transactions from
        appearing in the companion view.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        txn.is_deleted = True
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 0

    def test_ad_hoc_transactions_excluded(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Ad-hoc transactions (no template) are excluded from companion view.

        The JOIN on TransactionTemplate filters out ad-hoc transactions
        because they have no template_id, so the join produces no match.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense").one()
        )
        category = list(seed_user["categories"].values())[0]
        txn = Transaction(
            name="Ad-hoc",
            estimated_amount=Decimal("100.00"),
            transaction_type_id=expense_type.id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            pay_period_id=seed_periods_today[0].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            scenario_id=seed_user["scenario"].id,
        )
        db.session.add(txn)
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 0

    def test_transactions_ordered_by_name(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible transactions are returned in alphabetical order by name."""
        t_z = _make_template(seed_user, companion_visible=True, name="Zucchini Fund")
        t_a = _make_template(seed_user, companion_visible=True, name="Apples Budget")
        _make_txn(seed_user, seed_periods_today[0], t_z, name="Zucchini Fund")
        _make_txn(seed_user, seed_periods_today[0], t_a, name="Apples Budget")
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert txns[0].name == "Apples Budget"
        assert txns[1].name == "Zucchini Fund"

    def test_companion_sees_override_sibling_alongside_rule_generated(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Carry-forward override siblings stay visible to the companion.

        When the owner carries an unpaid grocery row from a past period
        into the current period that already has the rule-generated
        next instance, both rows now sit in the same period -- one with
        is_override=False (canonical), one with is_override=True
        (carried).  Both still link to the same companion-visible
        TransactionTemplate, so the companion view must return both.
        Without this, the companion would see only the rule-generated
        row and silently miss the carried envelope -- the exact failure
        mode that ruled out the pure-detach fix.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        rule_generated = _make_txn(
            seed_user, seed_periods_today[0], template, name="Groceries",
        )

        # Build the carried row with is_override=True from the start --
        # the relaxed unique index excludes is_override=TRUE rows from
        # its predicate, so two non-override rows for the same
        # (template, period, scenario) would still collide.  This is
        # exactly the constraint that lets carry-forward succeed.
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        category = list(seed_user["categories"].values())[0]
        carried = Transaction(
            name="Groceries",
            estimated_amount=template.default_amount,
            transaction_type_id=expense_type.id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            pay_period_id=seed_periods_today[0].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            scenario_id=seed_user["scenario"].id,
            template_id=template.id,
            is_override=True,
        )
        db.session.add(carried)
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )

        # Both rows are returned -- the override sibling stays visible.
        ids = sorted(t.id for t in txns)
        assert ids == sorted([rule_generated.id, carried.id])

        # And the override flag survives the round-trip so the UI can
        # distinguish carried items from canonical ones if it chooses.
        flags = sorted(t.is_override for t in txns)
        assert flags == [False, True]


# ── Period Isolation ─────────────────────────────────────────────────


class TestPeriodIsolation:
    """Verify transactions from other periods are not included."""

    def test_transactions_from_other_periods_excluded(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Only the requested period's transactions are returned.

        Creates transactions in periods 0 and 1, requests period 0,
        verifies only period 0's transaction appears.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], template, name="Groceries P0")
        _make_txn(seed_user, seed_periods_today[1], template, name="Groceries P1")
        db.session.commit()

        companion = seed_companion["user"]
        txns, period = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 1
        assert txns[0].name == "Groceries P0"
        assert period.id == seed_periods_today[0].id

    def test_period_id_belonging_to_different_owner_raises(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.13: Companion with period from a different owner raises NotFoundError.

        The service verifies period.user_id == linked_owner_id and
        rejects periods belonging to other users.
        """
        # Create a second owner with their own period.
        second_user = User(
            email="other@test.local",
            password_hash=hash_password("otherpass"),
            display_name="Other",
        )
        db.session.add(second_user)
        db.session.flush()
        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        other_period = PayPeriod(
            user_id=second_user.id,
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 15),
            period_index=0,
        )
        db.session.add(other_period)
        db.session.commit()

        companion = seed_companion["user"]
        with pytest.raises(NotFoundError):
            companion_service.get_visible_transactions(
                companion.id, period_id=other_period.id,
            )

    def test_period_id_none_returns_current_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """period_id=None returns the current period's transactions.

        get_current_period picks the period containing today.
        We create a template+transaction in the first period and call
        with period_id=None.  If today falls in that period, the
        transaction is returned; otherwise we get a different period.
        Either way, a valid (transactions, period) tuple is returned.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        companion = seed_companion["user"]
        # This may return a different period than seed_periods_today[0]
        # depending on the current date, but it should not raise.
        txns, period = companion_service.get_visible_transactions(companion.id)
        assert period is not None

    def test_nonexistent_period_id_raises(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Requesting a non-existent period_id raises NotFoundError."""
        companion = seed_companion["user"]
        with pytest.raises(NotFoundError):
            companion_service.get_visible_transactions(
                companion.id, period_id=999999,
            )


# ── User Validation ──────────────────────────────────────────────────


class TestUserValidation:
    """Verify companion service rejects invalid user configurations."""

    def test_owner_user_rejected(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Plan 10.12: Owner user passed to get_visible_transactions raises NotFoundError.

        Defense-in-depth: even though the route checks the role,
        the service independently verifies the user is a companion.
        """
        owner = seed_user["user"]
        with pytest.raises(NotFoundError, match="not a companion"):
            companion_service.get_visible_transactions(owner.id)

    def test_nonexistent_user_id_raises(self, app, db):
        """Non-existent user_id raises NotFoundError."""
        with pytest.raises(NotFoundError, match="not found"):
            companion_service.get_visible_transactions(999999)

    def test_companion_with_no_linked_owner_raises(
        self, app, db, seed_user,
    ):
        """Companion with linked_owner_id=None raises NotFoundError.

        This represents a data integrity issue that should not silently
        return empty results.
        """
        orphan = User(
            email="orphan@test.local",
            password_hash=hash_password("orphanpass"),
            display_name="Orphan",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=None,
        )
        db.session.add(orphan)
        db.session.flush()
        settings = UserSettings(user_id=orphan.id)
        db.session.add(settings)
        db.session.commit()

        with pytest.raises(NotFoundError, match="no linked owner"):
            companion_service.get_visible_transactions(orphan.id)

    def test_valid_companion_succeeds(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Properly configured companion user passes all validation."""
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        companion = seed_companion["user"]
        txns, period = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert period is not None


# ── Entry Eager Loading ──────────────────────────────────────────────


class TestEntryEagerLoading:
    """Verify entries are eager-loaded on returned transactions."""

    def test_entries_accessible_without_lazy_load(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Entries are eagerly loaded via selectinload.

        After closing the session's identity map (expunging all),
        accessing txn.entries should still work because they were
        loaded during the query, not lazily.  We verify by checking
        that entries are populated on the returned objects.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Groceries", amount=Decimal("500.00"),
        )
        entry = TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("42.50"),
            description="Kroger",
            entry_date=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 1
        # Access entries -- they should be loaded already.
        assert len(txns[0].entries) == 1
        assert txns[0].entries[0].amount == Decimal("42.50")
        assert txns[0].entries[0].description == "Kroger"


# ── Entry Data Computation ───────────────────────────────────────────


class TestEntryDataComputation:
    """Verify entry_data computed via entry_service functions is correct.

    Plan 10.4: tracked transaction with entries has correct total,
    remaining, and count.
    """

    def test_entry_sums_computed_correctly(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.4: Entry data (total, remaining, count) is correct.

        Arithmetic: estimated=$500, entries=$100+$50=$150.
        remaining = $500 - $150 = $350.  count = 2.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Groceries", amount=Decimal("500.00"),
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("100.00"), description="Kroger",
            entry_date=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="Walmart",
            entry_date=date(2026, 1, 6),
        ))
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert len(txns) == 1
        assert len(txns[0].entries) == 2

        # Verify the service functions produce correct values.
        from app.services.entry_service import compute_entry_sums, compute_remaining
        sum_debit, sum_credit = compute_entry_sums(txns[0].entries)
        total = sum_debit + sum_credit
        remaining = compute_remaining(txns[0].estimated_amount, txns[0].entries)

        assert total == Decimal("150.00")
        assert remaining == Decimal("350.00")

    def test_over_budget_remaining_negative(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Over-budget scenario: remaining is negative.

        Arithmetic: estimated=$100, entries=$70+$50=$120.
        remaining = $100 - $120 = -$20.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Gas",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Gas", amount=Decimal("100.00"),
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("70.00"), description="Shell",
            entry_date=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="BP",
            entry_date=date(2026, 1, 6),
        ))
        db.session.commit()

        companion = seed_companion["user"]
        txns, _ = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        from app.services.entry_service import compute_remaining
        remaining = compute_remaining(txns[0].estimated_amount, txns[0].entries)
        assert remaining == Decimal("-20.00")


# ── get_companion_periods ────────────────────────────────────────────


class TestGetCompanionPeriods:
    """Verify period navigation helper."""

    def test_returns_all_owner_periods(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Returns all periods belonging to the linked owner."""
        companion = seed_companion["user"]
        periods = companion_service.get_companion_periods(companion.id)
        assert len(periods) == len(seed_periods_today)

    def test_misconfigured_companion_returns_empty(self, app, db, seed_user):
        """Companion with no linked_owner_id returns empty list (not error)."""
        orphan = User(
            email="orphan2@test.local",
            password_hash=hash_password("orphanpass2"),
            display_name="Orphan2",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=None,
        )
        db.session.add(orphan)
        db.session.flush()
        settings = UserSettings(user_id=orphan.id)
        db.session.add(settings)
        db.session.commit()

        periods = companion_service.get_companion_periods(orphan.id)
        assert periods == []

    def test_nonexistent_user_returns_empty(self, app, db):
        """Non-existent user_id returns empty list (not error)."""
        periods = companion_service.get_companion_periods(999999)
        assert periods == []


# ── get_previous_period ──────────────────────────────────────────────


class TestGetPreviousPeriod:
    """Verify previous period navigation."""

    def test_returns_previous_period(self, app, db, seed_user, seed_periods_today):
        """Returns the period with period_index - 1."""
        prev = companion_service.get_previous_period(seed_periods_today[1])
        assert prev is not None
        assert prev.id == seed_periods_today[0].id

    def test_returns_none_for_first_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Returns None for the first period (no previous)."""
        prev = companion_service.get_previous_period(seed_periods_today[0])
        assert prev is None
