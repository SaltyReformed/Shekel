"""
Tests for OP-3: per-entry breakdown in year-end summary.

Verifies that compute_year_end_summary attaches an `entry_breakdown`
sub-dict to spending category items whose parent template has
track_individual_purchases=True.  Each test asserts the breakdown
fields with hand-computed expected values, not just presence.

Covered scenarios:
- Single tracked category over 26 pay periods (full year coverage).
- Mixed tracked and non-tracked categories in the same summary.
- Credit/debit splits (zero credit, all credit, mixed).
- Partially tracked categories (some periods with entries, some without).
- Decimal precision in avg_entry (rounding edge cases).
- Year boundary (due_date crossing into next year).
- Empty / no-tracking scenarios.
- Single-query performance.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import event

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.year_end_summary_service import (
    _compute_entry_breakdowns,
    compute_year_end_summary,
)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
YEAR = 2026


# ── Helpers ───────────────────────────────────────────────────────


def _make_template(user, account, category, name, default_amount,
                   tracked=True):
    """Create a TransactionTemplate with the given tracking flag.

    Each call creates its own RecurrenceRule so multiple templates can
    coexist without sharing rule state.

    Args:
        user: User object (template owner).
        account: Account the template charges.
        category: Category for grouping in spending hierarchy.
        name: Template display name.
        default_amount: Default transaction amount (Decimal).
        tracked: track_individual_purchases flag value.

    Returns:
        Persisted TransactionTemplate object.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    rule = RecurrenceRule(user_id=user.id, pattern_id=every_period.id)
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type_id,
        name=name,
        default_amount=default_amount,
        track_individual_purchases=tracked,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_settled_txn(template, account, scenario, period, name,
                      estimated, actual=None, due_date_val=None):
    """Create a settled (Done) expense transaction tied to a template.

    Mirrors the post-Commit-5 state: actual_amount is set, status is
    Done.  When `actual` is None it defaults to `estimated`, matching
    a transaction settled before any entries were recorded.

    Args:
        template: Parent TransactionTemplate.
        account: Account charged.
        scenario: Baseline scenario.
        period: PayPeriod the transaction belongs to.
        name: Transaction display name.
        estimated: Decimal estimated amount.
        actual: Decimal actual amount, or None to copy estimated.
        due_date_val: Optional date.  Used by attribution-year filter.

    Returns:
        Persisted Transaction object.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = Transaction(
        account_id=account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        template_id=template.id,
        status_id=paid_status_id,
        transaction_type_id=expense_type_id,
        name=name,
        estimated_amount=estimated,
        actual_amount=actual if actual is not None else estimated,
        category_id=template.category_id,
        due_date=due_date_val,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _add_entries(txn, user, amounts, credit_flags=None,
                 entry_date_val=None):
    """Attach a list of TransactionEntry rows to a transaction.

    Args:
        txn: Parent Transaction (must be persisted).
        user: User to attribute the entries to.
        amounts: list of Decimal entry amounts.
        credit_flags: Optional list of bool with the same length as
            `amounts`.  Defaults to all False (debit).
        entry_date_val: Optional date for all entries.  Defaults to
            the parent pay period's start_date.

    Returns:
        List of persisted TransactionEntry objects.
    """
    if credit_flags is None:
        credit_flags = [False] * len(amounts)
    if entry_date_val is None:
        entry_date_val = txn.pay_period.start_date

    created = []
    for amount, is_credit in zip(amounts, credit_flags):
        entry = TransactionEntry(
            transaction_id=txn.id,
            user_id=user.id,
            amount=amount,
            description="test entry",
            entry_date=entry_date_val,
            is_credit=is_credit,
        )
        db.session.add(entry)
        created.append(entry)
    db.session.flush()
    return created


def _find_item(spending, group_name, item_name):
    """Locate the item dict for a (group_name, item_name) pair."""
    for group in spending:
        if group["group_name"] != group_name:
            continue
        for item in group["items"]:
            if item["item_name"] == item_name:
                return item
    return None


# ── Single-Category Aggregation ──────────────────────────────────


class TestEntryBreakdownSingleCategory:
    """Tests with a single tracked category."""

    def test_full_year_groceries_26_periods(
        self, app, db, seed_user, seed_periods_52,
    ):
        """26 tracked grocery transactions, one per pay period in 2026.

        Each transaction has 3 entries totalling $150 ($50 each).
        Expected entry_count = 26 * 3 = 78.
        Expected entry_total = 26 * $150 = $3,900.
        avg_entry = 3900 / 78 = $50.00.
        credit_total = $0 (no credit entries).
        transaction_count_with_entries = 26.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("200.00"), tracked=True,
        )

        # Only the first 26 of seed_periods_52 fall in calendar 2026.
        periods_in_year = [
            p for p in seed_periods_52
            if p.start_date.year == YEAR
        ]
        assert len(periods_in_year) == 26, (
            f"Expected 26 in-year periods, got {len(periods_in_year)}"
        )

        for idx, period in enumerate(periods_in_year):
            txn = _make_settled_txn(
                template, account, scenario, period,
                f"Groceries {idx}",
                estimated=Decimal("200.00"),
                actual=Decimal("150.00"),
            )
            _add_entries(
                txn, user,
                [Decimal("50.00"), Decimal("50.00"), Decimal("50.00")],
            )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        assert item is not None, "Groceries item missing from spending"
        assert "entry_breakdown" in item

        bd = item["entry_breakdown"]
        # Hand-computed: 26 periods * 3 entries = 78
        assert bd["entry_count"] == 78
        # 26 * 3 * $50 = $3,900
        assert bd["entry_total"] == Decimal("3900.00")
        assert bd["credit_total"] == ZERO
        assert bd["debit_total"] == Decimal("3900.00")
        # 3900 / 78 = 50.00
        assert bd["avg_entry"] == Decimal("50.00")
        assert bd["transaction_count_with_entries"] == 26

        # item_total should equal sum of actual_amounts = 26 * $150 = $3,900
        assert item["item_total"] == Decimal("3900.00")

    def test_single_transaction_single_entry(
        self, app, db, seed_user, seed_periods,
    ):
        """One tracked transaction with one $500 entry.

        avg_entry = 500 / 1 = $500.00.  Verifies the singular case.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("500.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0],
            "Groceries Jan",
            estimated=Decimal("500.00"),
            actual=Decimal("500.00"),
        )
        _add_entries(txn, user, [Decimal("500.00")])
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        assert item is not None
        bd = item["entry_breakdown"]
        assert bd["entry_count"] == 1
        assert bd["entry_total"] == Decimal("500.00")
        assert bd["avg_entry"] == Decimal("500.00")
        assert bd["transaction_count_with_entries"] == 1


# ── Mixed Tracked / Non-Tracked Categories ──────────────────────


class TestEntryBreakdownMixedCategories:
    """Tests with multiple categories, only some tracked."""

    def test_three_tracked_two_non_tracked(
        self, app, db, seed_user, seed_periods,
    ):
        """3 tracked categories, 2 non-tracked.

        Only the tracked items get entry_breakdown.  Non-tracked items
        appear in spending_by_category as before but without the key.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        cats = seed_user["categories"]

        # Add two more categories so we have 5 leaf items.
        dining = Category(
            user_id=user.id, group_name="Family", item_name="Dining",
        )
        gas = Category(
            user_id=user.id, group_name="Auto", item_name="Gas",
        )
        db.session.add_all([dining, gas])
        db.session.flush()

        # Tracked: Groceries, Dining, Gas
        # Non-tracked: Rent, Car Payment
        tracked_specs = [
            (cats["Groceries"], Decimal("200.00"), Decimal("180.00"),
             [Decimal("60.00"), Decimal("60.00"), Decimal("60.00")]),
            (dining,            Decimal("100.00"), Decimal("90.00"),
             [Decimal("45.00"), Decimal("45.00")]),
            (gas,               Decimal("80.00"),  Decimal("70.00"),
             [Decimal("35.00"), Decimal("35.00")]),
        ]
        for cat, est, actual, entries in tracked_specs:
            tmpl = _make_template(
                user, account, cat, cat.item_name,
                est, tracked=True,
            )
            txn = _make_settled_txn(
                tmpl, account, scenario, seed_periods[0],
                cat.item_name, estimated=est, actual=actual,
            )
            _add_entries(txn, user, entries)

        non_tracked_specs = [
            (cats["Rent"],        Decimal("1200.00")),
            (cats["Car Payment"], Decimal("350.00")),
        ]
        for cat, amount in non_tracked_specs:
            tmpl = _make_template(
                user, account, cat, cat.item_name,
                amount, tracked=False,
            )
            _make_settled_txn(
                tmpl, account, scenario, seed_periods[0],
                cat.item_name, estimated=amount, actual=amount,
            )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]

        # Tracked categories have entry_breakdown.
        groceries_item = _find_item(spending, "Family", "Groceries")
        assert "entry_breakdown" in groceries_item
        assert groceries_item["entry_breakdown"]["entry_count"] == 3
        assert (
            groceries_item["entry_breakdown"]["entry_total"]
            == Decimal("180.00")
        )

        dining_item = _find_item(spending, "Family", "Dining")
        assert "entry_breakdown" in dining_item
        assert dining_item["entry_breakdown"]["entry_count"] == 2
        assert (
            dining_item["entry_breakdown"]["entry_total"]
            == Decimal("90.00")
        )

        gas_item = _find_item(spending, "Auto", "Gas")
        assert "entry_breakdown" in gas_item
        assert gas_item["entry_breakdown"]["entry_count"] == 2
        assert (
            gas_item["entry_breakdown"]["entry_total"] == Decimal("70.00")
        )

        # Non-tracked categories have NO entry_breakdown key at all.
        rent_item = _find_item(spending, "Home", "Rent")
        assert rent_item is not None
        assert "entry_breakdown" not in rent_item, (
            "Non-tracked Rent should have no entry_breakdown"
        )

        car_item = _find_item(spending, "Auto", "Car Payment")
        assert car_item is not None
        assert "entry_breakdown" not in car_item, (
            "Non-tracked Car Payment should have no entry_breakdown"
        )


# ── Credit / Debit Split ─────────────────────────────────────────


class TestCreditDebitSplit:
    """Tests for the credit_total / debit_total breakdown."""

    def test_zero_credit_entries(self, app, db, seed_user, seed_periods):
        """All entries are debits.

        credit_total == 0, debit_total == entry_total.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("100.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("100.00"), actual=Decimal("100.00"),
        )
        _add_entries(
            txn, user,
            [Decimal("40.00"), Decimal("60.00")],
            credit_flags=[False, False],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["credit_total"] == ZERO
        assert bd["debit_total"] == Decimal("100.00")
        assert bd["entry_total"] == Decimal("100.00")

    def test_all_credit_entries(self, app, db, seed_user, seed_periods):
        """All entries are credit-card purchases.

        debit_total == 0, credit_total == entry_total.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("100.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("100.00"), actual=Decimal("100.00"),
        )
        _add_entries(
            txn, user,
            [Decimal("40.00"), Decimal("60.00")],
            credit_flags=[True, True],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["credit_total"] == Decimal("100.00")
        assert bd["debit_total"] == ZERO
        assert bd["entry_total"] == Decimal("100.00")

    def test_mixed_credit_and_debit(self, app, db, seed_user, seed_periods):
        """3 credit + 2 debit entries.

        Mixed: credit_total + debit_total == entry_total.
        Hand-computed:
          credit: $20 + $30 + $50 = $100
          debit:  $25 + $75 = $100
          entry_total = $200
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("200.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("200.00"), actual=Decimal("200.00"),
        )
        _add_entries(
            txn, user,
            [
                Decimal("20.00"), Decimal("30.00"), Decimal("50.00"),
                Decimal("25.00"), Decimal("75.00"),
            ],
            credit_flags=[True, True, True, False, False],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["credit_total"] == Decimal("100.00")
        assert bd["debit_total"] == Decimal("100.00")
        assert bd["entry_total"] == Decimal("200.00")
        # avg = 200 / 5 = 40.00
        assert bd["avg_entry"] == Decimal("40.00")


# ── Partial Tracking (some periods have entries, some don't) ────


class TestPartialTracking:
    """Tests where the same template has some periods with and
    some periods without entries."""

    def test_some_transactions_with_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """5 periods with the tracked template.

        Periods 0, 1, 2 have entries.
        Periods 3, 4 are settled but have no entries (actual_amount
        was set manually, not via mark-paid-with-entries).

        Expected:
          transaction_count_with_entries = 3 (only the entry-bearing txns)
          entry_count = 3 * 2 = 6
          entry_total = 3 * $100 = $300

          item_total includes all 5 transactions:
          item_total = 3 * $100 + 2 * $100 = $500

          So entry_total ($300) < item_total ($500).  This is the
          expected behavior for mixed tracking.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("100.00"), tracked=True,
        )

        for idx in range(5):
            txn = _make_settled_txn(
                template, account, scenario, seed_periods[idx],
                f"Groceries {idx}",
                estimated=Decimal("100.00"),
                actual=Decimal("100.00"),
            )
            if idx < 3:
                _add_entries(
                    txn, user,
                    [Decimal("40.00"), Decimal("60.00")],
                )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["transaction_count_with_entries"] == 3
        assert bd["entry_count"] == 6
        assert bd["entry_total"] == Decimal("300.00")
        # item_total reflects ALL 5 transactions ($100 each)
        assert item["item_total"] == Decimal("500.00")
        # entry_total < item_total intentionally
        assert bd["entry_total"] < item["item_total"]


# ── avg_entry Decimal Precision ──────────────────────────────────


class TestAvgEntryPrecision:
    """Tests for Decimal precision in avg_entry computation."""

    def test_avg_three_entries_uneven(
        self, app, db, seed_user, seed_periods,
    ):
        """3 entries: $33.33, $33.33, $33.34.

        Sum = $100.00.  Average = 100.00 / 3 = 33.333...
        Quantize ROUND_HALF_UP to 2 decimal places = $33.33.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("100.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("100.00"), actual=Decimal("100.00"),
        )
        _add_entries(
            txn, user,
            [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["entry_total"] == Decimal("100.00")
        assert bd["avg_entry"] == Decimal("33.33")
        # Verify it's a Decimal with exactly 2 decimal places, not float.
        assert isinstance(bd["avg_entry"], Decimal)

    def test_avg_round_half_up(self, app, db, seed_user, seed_periods):
        """3 entries summing to $100.00 / 3 = $33.333... -> $33.33.

        And 3 entries summing to $100.01 / 3 = $33.336... -> $33.34.
        Verifies ROUND_HALF_UP semantics on the second case.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("200.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("200.00"), actual=Decimal("100.01"),
        )
        # 3 entries summing to $100.01
        _add_entries(
            txn, user,
            [Decimal("33.34"), Decimal("33.34"), Decimal("33.33")],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["entry_total"] == Decimal("100.01")
        # 100.01 / 3 = 33.336666... -> ROUND_HALF_UP at 2 places = 33.34
        assert bd["avg_entry"] == Decimal("33.34")

    def test_avg_very_small_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """5 entries of $0.01 each.

        Sum = $0.05, count = 5, avg = $0.01.  Verifies the average
        does not round to $0.00 from precision loss.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("1.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("1.00"), actual=Decimal("0.05"),
        )
        _add_entries(
            txn, user,
            [Decimal("0.01")] * 5,
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        bd = item["entry_breakdown"]
        assert bd["entry_count"] == 5
        assert bd["entry_total"] == Decimal("0.05")
        assert bd["avg_entry"] == Decimal("0.01")


# ── Total Consistency ────────────────────────────────────────────


class TestTotalConsistency:
    """Tests verifying entry_total relationship to item_total."""

    def test_fully_tracked_entry_total_matches_item_total(
        self, app, db, seed_user, seed_periods,
    ):
        """Single tracked transaction, actual = entry sum.

        For a fully-tracked-and-paid category, entry_total should
        equal item_total exactly.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("250.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("250.00"),
            actual=Decimal("237.50"),
        )
        _add_entries(
            txn, user,
            [
                Decimal("100.00"),
                Decimal("75.50"),
                Decimal("62.00"),
            ],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        # actual_amount was set to entry sum = 237.50
        # item_total uses effective_amount which returns actual_amount
        assert item["item_total"] == Decimal("237.50")
        bd = item["entry_breakdown"]
        assert bd["entry_total"] == Decimal("237.50")
        assert bd["entry_total"] == item["item_total"]


# ── Edge Cases ───────────────────────────────────────────────────


class TestEntryBreakdownEdgeCases:
    """Tests for empty / boundary scenarios."""

    def test_no_tracked_categories(
        self, app, db, seed_user, seed_periods,
    ):
        """A year with only non-tracked transactions.

        spending_by_category populates as before; no item has an
        entry_breakdown key.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        cats = seed_user["categories"]

        template = _make_template(
            user, account, cats["Rent"], "Rent",
            Decimal("1200.00"), tracked=False,
        )
        _make_settled_txn(
            template, account, scenario, seed_periods[0], "Rent",
            estimated=Decimal("1200.00"),
            actual=Decimal("1200.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        assert len(spending) == 1
        rent_item = _find_item(spending, "Home", "Rent")
        assert rent_item is not None
        assert "entry_breakdown" not in rent_item

    def test_tracked_template_with_no_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Tracked template, settled transactions, but ZERO entries.

        The user enabled tracking but has not yet logged any entries.
        Behavior: no entry_breakdown key (because there are no
        rows in transaction_entries to aggregate).
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("200.00"), tracked=True,
        )
        _make_settled_txn(
            template, account, scenario, seed_periods[0], "Groceries",
            estimated=Decimal("200.00"),
            actual=Decimal("200.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        assert item is not None
        # The category appears in spending (via item_total) but has no
        # entry_breakdown because there are no rows in transaction_entries.
        assert "entry_breakdown" not in item

    def test_year_boundary_due_date_excludes(
        self, app, db, seed_user, seed_periods,
    ):
        """Tracked transaction in 2026 pay period but due_date in 2027.

        _attribution_year uses COALESCE(due_date, pp.start_date), so a
        transaction with due_date 2027-01-15 is attributed to 2027.
        It must NOT appear in the 2026 entry_breakdown.

        Verifies the breakdown's year filter matches the existing
        category aggregation's year filter.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        groceries = seed_user["categories"]["Groceries"]

        template = _make_template(
            user, account, groceries, "Groceries",
            Decimal("100.00"), tracked=True,
        )

        # Period 0: in 2026, no due date -> attributed to 2026.
        txn_in = _make_settled_txn(
            template, account, scenario, seed_periods[0],
            "Groceries In",
            estimated=Decimal("100.00"),
            actual=Decimal("100.00"),
        )
        _add_entries(txn_in, user, [Decimal("100.00")])

        # Period 1: in 2026 but due_date pushed to 2027.
        # We need a SECOND template here because the unique constraint
        # only allows one transaction per (template, period, scenario).
        template2 = _make_template(
            user, account, groceries, "Groceries 2",
            Decimal("999.00"), tracked=True,
        )
        txn_out = _make_settled_txn(
            template2, account, scenario, seed_periods[1],
            "Groceries Out",
            estimated=Decimal("999.00"),
            actual=Decimal("999.00"),
            due_date_val=date(2027, 1, 15),
        )
        _add_entries(txn_out, user, [Decimal("999.00")])
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        item = _find_item(
            result["spending_by_category"], "Family", "Groceries",
        )
        assert item is not None
        bd = item["entry_breakdown"]
        # Only the in-year transaction's entries are counted.
        assert bd["entry_count"] == 1
        assert bd["entry_total"] == Decimal("100.00")
        assert bd["transaction_count_with_entries"] == 1
        # And item_total also excludes the 2027-attributed transaction.
        assert item["item_total"] == Decimal("100.00")

    def test_only_other_users_data(
        self, app, db, seed_user, seed_periods, second_user,
    ):
        """Owner has no tracked entries; another user does.

        The owner's year-end summary must NOT see the second user's
        entries (basic ownership-filter check).
        """
        owner = seed_user["user"]
        other_user = second_user["user"]
        other_account = second_user["account"]
        other_scenario = second_user["scenario"]

        # Need a category for the other user.
        other_groceries = Category(
            user_id=other_user.id,
            group_name="Family",
            item_name="Groceries",
        )
        db.session.add(other_groceries)
        db.session.flush()

        # Other user needs their own pay period since the period FK
        # is per user.
        from app.services import pay_period_service  # pylint: disable=import-outside-toplevel
        other_periods = pay_period_service.generate_pay_periods(
            user_id=other_user.id,
            start_date=date(2026, 1, 2),
            num_periods=2,
            cadence_days=14,
        )
        other_account.current_anchor_period_id = other_periods[0].id
        db.session.flush()

        template = _make_template(
            other_user, other_account, other_groceries, "Groceries",
            Decimal("200.00"), tracked=True,
        )
        txn = _make_settled_txn(
            template, other_account, other_scenario, other_periods[0],
            "Groceries",
            estimated=Decimal("200.00"),
            actual=Decimal("200.00"),
        )
        _add_entries(txn, other_user, [Decimal("200.00")])
        db.session.commit()

        # Owner's summary -- should see no entries.
        result = compute_year_end_summary(owner.id, YEAR)
        spending = result["spending_by_category"]
        for group in spending:
            for item in group["items"]:
                assert "entry_breakdown" not in item, (
                    f"Owner saw entry_breakdown for "
                    f"{group['group_name']}/{item['item_name']} "
                    f"-- cross-user leakage"
                )


# ── Performance: bounded query count ─────────────────────────────


class TestEntryBreakdownPerformance:
    """Tests verifying _compute_entry_breakdowns runs in bounded queries."""

    def test_breakdown_uses_one_query(
        self, app, db, seed_user, seed_periods,
    ):
        """The aggregation runs ONE SQL query regardless of category count.

        With 4 tracked categories, each with multiple transactions and
        entries, only one SELECT statement should be issued by
        _compute_entry_breakdowns().  Verifies the SQL aggregation
        approach (rather than per-category Python loops).
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        cats = seed_user["categories"]

        # Add a fourth category.
        gas = Category(
            user_id=user.id, group_name="Auto", item_name="Gas",
        )
        db.session.add(gas)
        db.session.flush()

        for cat in [cats["Groceries"], cats["Rent"], cats["Car Payment"], gas]:
            tmpl = _make_template(
                user, account, cat, cat.item_name,
                Decimal("100.00"), tracked=True,
            )
            txn = _make_settled_txn(
                tmpl, account, scenario, seed_periods[0],
                cat.item_name,
                estimated=Decimal("100.00"),
                actual=Decimal("100.00"),
            )
            _add_entries(
                txn, user,
                [Decimal("25.00"), Decimal("75.00")],
            )
        db.session.commit()

        # Capture SELECT statements emitted by _compute_entry_breakdowns.
        # We only count statements that touch transaction_entries since
        # ref_cache lookups may issue an initial cache-warm query.
        statements: list[str] = []

        def _capture(conn, cursor, statement, parameters,
                     context, executemany):
            statements.append(statement)

        engine = db.session.get_bind()
        event.listen(engine, "before_cursor_execute", _capture)
        try:
            period_ids = [p.id for p in seed_periods]
            breakdowns = _compute_entry_breakdowns(
                user.id, YEAR, period_ids, scenario.id,
            )
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        entry_statements = [
            s for s in statements if "transaction_entries" in s.lower()
        ]
        assert len(entry_statements) == 1, (
            f"Expected exactly one transaction_entries query, got "
            f"{len(entry_statements)}: {entry_statements}"
        )
        # Sanity check: the aggregation produced the expected breakdowns.
        assert len(breakdowns) == 4
