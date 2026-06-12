"""
Shekel Budget App -- Dashboard Service Tests

Tests for the dashboard aggregation service: upcoming bills, alerts,
balance/cash runway, payday info, savings goals, debt summary,
spending comparison, and full integration.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import dashboard_service
from app.services import account_service
from app.services import transfer_service


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    actual_amount=None, scenario_id=None,
):
    """Create a transaction for testing.

    ``scenario_id`` defaults to the user's baseline scenario; pass an
    explicit id to place the transaction in another scenario.

    Returns the created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=(
            scenario_id if scenario_id is not None
            else seed_user["scenario"].id
        ),
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_anchor_history(db_session, account, period, balance, days_ago=0):
    """Create an anchor history entry.

    Args:
        days_ago: How many days in the past the entry's created_at should be.

    Returns:
        The created AccountAnchorHistory.
    """
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=period.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


# ── Empty/Minimal State Tests ───────────────────────────────────────


class TestDashboardEmpty:
    """Tests for dashboard with minimal/no data."""

    def test_dashboard_empty_user(self, app, seed_user):
        """All sections return empty/zero/None gracefully with no data."""
        with app.app_context():
            result = dashboard_service.compute_dashboard_data(
                user_id=seed_user["user"].id,
            )
            # Should not crash; basic structure present.
            assert result["has_default_account"] is True
            assert result["upcoming_bills"] == []
            assert isinstance(result["alerts"], list)

    def test_dashboard_no_periods(self, app, seed_user):
        """User with account but no periods -> graceful degradation."""
        with app.app_context():
            result = dashboard_service.compute_dashboard_data(
                user_id=seed_user["user"].id,
            )
            assert result["upcoming_bills"] == []
            assert result["payday_info"]["days_until"] is None
            # Spending comparison removed (audit fix A); the key is gone.
            assert "spending_comparison" not in result


# ── Upcoming Bills Tests ────────────────────────────────────────────


def _flatten_bills(groups):
    """Flatten the grouped upcoming-bills structure into a single bill list.

    ``_get_upcoming_bills`` now returns a list of per-period group dicts
    (each ``{period_id, period_start_date, period_end_date, bills}``).
    The group order is period order (current period first, then next),
    and within a group the bills keep their due_date-then-name sort, so
    concatenating the groups' ``bills`` lists in order reproduces the flat
    sequence these content/order assertions check.
    """
    flat = []
    for group in groups:
        flat.extend(group["bills"])
    return flat


class TestUpcomingBills:
    """Tests for the upcoming bills section."""

    def test_upcoming_bills_filters_paid(self, app, seed_user, seed_periods, db):
        """Paid transactions excluded; only projected bills returned."""
        with app.app_context():
            p = seed_periods[0]
            _add_txn(db.session, seed_user, p, "Unpaid 1", "500.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, p, "Unpaid 2", "300.00",
                     due_date=date(2026, 1, 8))
            _add_txn(db.session, seed_user, p, "Paid", "200.00",
                     status_enum=StatusEnum.DONE,
                     due_date=date(2026, 1, 3))
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            bills = _flatten_bills(groups)
            assert len(bills) == 2
            names = [b["name"] for b in bills]
            assert "Paid" not in names

    def test_upcoming_bills_two_periods(self, app, seed_user, seed_periods, db):
        """Bills from both current and next period included as two groups.

        With one bill in each of the two periods, the grouped result has
        two groups (one per period) of one bill each, in period order.
        """
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Bill P0", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[1], "Bill P1", "200.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(groups) == 2
            assert groups[0]["period_id"] == seed_periods[0].id
            assert groups[1]["period_id"] == seed_periods[1].id
            assert [b["name"] for b in groups[0]["bills"]] == ["Bill P0"]
            assert [b["name"] for b in groups[1]["bills"]] == ["Bill P1"]
            assert len(_flatten_bills(groups)) == 2

    def test_upcoming_bills_sorted_by_due_date(self, app, seed_user, seed_periods, db):
        """Bills sorted by due_date ascending within a period group."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Late", "100.00",
                     due_date=date(2026, 1, 15))
            _add_txn(db.session, seed_user, seed_periods[0], "Early", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Mid", "100.00",
                     due_date=date(2026, 1, 10))
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            # All three are in seed_periods[0], so one group, sorted.
            assert len(groups) == 1
            names = [b["name"] for b in groups[0]["bills"]]
            assert names == ["Early", "Mid", "Late"]

    def test_upcoming_bills_excludes_income(self, app, seed_user, seed_periods, db):
        """Income transactions not included in bills."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Expense", "500.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Paycheck", "2000.00",
                     is_income=True, due_date=date(2026, 1, 2))
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            bills = _flatten_bills(groups)
            assert len(bills) == 1
            assert bills[0]["name"] == "Expense"

    def test_upcoming_bills_excludes_deleted(self, app, seed_user, seed_periods, db):
        """Soft-deleted transactions excluded from bills."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Active", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Deleted", "200.00",
                     due_date=date(2026, 1, 6), is_deleted=True)
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            bills = _flatten_bills(groups)
            assert len(bills) == 1
            assert bills[0]["name"] == "Active"

    def test_upcoming_bills_null_due_date_sorted(self, app, seed_user, seed_periods, db):
        """Bills without due_date sort by period start_date within the group."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "NoDue", "100.00",
                     due_date=None)
            _add_txn(db.session, seed_user, seed_periods[0], "HasDue", "100.00",
                     due_date=date(2026, 1, 3))
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            bills = _flatten_bills(groups)
            # NoDue uses period.start_date (Jan 2) as sort key,
            # HasDue uses due_date (Jan 3).
            assert bills[0]["name"] == "NoDue"
            assert bills[1]["name"] == "HasDue"

    def test_upcoming_bills_includes_days_until_due(self, app, seed_user, seed_periods, db):
        """Bills include days_until_due based on due_date."""
        with app.app_context():
            future_date = date.today() + timedelta(days=5)
            _add_txn(db.session, seed_user, seed_periods[0], "Future", "100.00",
                     due_date=future_date)
            db.session.commit()

            groups = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            bills = _flatten_bills(groups)
            assert len(bills) == 1
            assert bills[0]["days_until_due"] == 5


# ── Entry-tracked bill row, single declared base (E-21 / MED-03) ────


class TestBillRowSingleBase:
    """E-21 (MED-03 / F-028 / F-056): the entry-tracked bill row's amount,
    remaining, and over-budget all anchor on ``estimated_amount`` -- the
    declared budget base -- and the base is disclosed via
    ``bill["amount_base"]``.  Pre-fix the amount cell used
    ``effective_amount`` (tier-3 actual when populated) while remaining
    used ``estimated_amount``, producing internally inconsistent rows.
    """

    def _make_entry_tracked_txn(
        self, db, seed_user, period, estimated, actual=None,
        status_enum=StatusEnum.PROJECTED,
    ):
        """Construct an entry-tracked (is_envelope=True) Transaction.

        Returns the flushed Transaction.  The seed_entry_template
        fixture is not used because these tests need explicit control
        over estimated_amount / actual_amount / status.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.ref import RecurrencePattern
        from app.models.recurrence_rule import RecurrenceRule
        from app.models.transaction_template import TransactionTemplate
        every_period = (
            db.session.query(RecurrencePattern)
            .filter_by(name="Every Period").one()
        )
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=every_period.id,
        )
        db.session.add(rule)
        db.session.flush()
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            recurrence_rule_id=rule.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name="Envelope bill",
            default_amount=Decimal(str(estimated)),
            is_envelope=True,
        )
        db.session.add(template)
        db.session.flush()
        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(status_enum),
            name="Envelope bill",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            estimated_amount=Decimal(str(estimated)),
            actual_amount=Decimal(str(actual)) if actual is not None else None,
            template_id=template.id,
            due_date=date(2026, 1, 5),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def _add_entries(self, db, seed_user, txn, *amounts):
        """Attach debit entries to ``txn`` summing the supplied amounts."""
        # pylint: disable=import-outside-toplevel
        from app.models.transaction_entry import TransactionEntry
        for amt in amounts:
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal(str(amt)),
                description="purchase",
                entry_date=date(2026, 1, 5),
            ))
        db.session.flush()

    def test_row_single_base_actual_lt_estimated(
        self, app, db, seed_user, seed_periods,
    ):
        """C30-1: actual=$100, estimated=$120, entries sum $80.

        Pre-fix (F-028): amount=$100 (effective=actual) while
        remaining=$120-$80=$40 -- one row, two undisclosed bases.

        E-21: amount must equal $120 (estimated) so all three figures
        share the declared base.
            amount         = estimated_amount      = $120.00
            entry_total    = $50 + $30             = $80.00
            entry_remaining = estimated - entries  = $120.00 - $80.00 = $40.00
            entry_over_budget = (entries > est)    = ($80 > $120) = False
        amount_base = "budget" (disclosed in the UI).
        """
        with app.app_context():
            txn = self._make_entry_tracked_txn(
                db, seed_user, seed_periods[0],
                estimated="120.00", actual="100.00",
            )
            self._add_entries(db, seed_user, txn, "50.00", "30.00")
            db.session.commit()

            bill = dashboard_service.txn_to_bill_dict(txn, date(2026, 1, 1))

            # MED-03 / F-028: amount now equals estimated (was
            # effective=actual=$100); the row's three numbers share
            # one declared base.
            assert bill["amount"] == Decimal("120.00")
            assert bill["amount_base"] == "budget"
            assert bill["is_tracked"] is True
            assert bill["entry_total"] == Decimal("80.00")
            assert bill["entry_remaining"] == Decimal("40.00")
            assert bill["entry_over_budget"] is False
            # Internal consistency: entry_total + entry_remaining == amount.
            assert bill["entry_total"] + bill["entry_remaining"] == bill["amount"]

    def test_base_disclosed_in_dict(
        self, app, db, seed_user, seed_periods,
    ):
        """C30-2: entry-tracked rows expose ``amount_base = "budget"``.

        The disclosure field is what the template renders so the user
        reads one mental model.  A non-entry-tracked bill has no
        progress fields to disclose against, so ``amount_base`` is
        None.
        """
        with app.app_context():
            envelope = self._make_entry_tracked_txn(
                db, seed_user, seed_periods[0],
                estimated="200.00",
            )
            # Non-entry-tracked bill: no template at all, so
            # _is_entry_tracked is False and amount falls back to
            # effective_amount.
            plain = _add_txn(
                db.session, seed_user, seed_periods[0],
                "Plain bill", "75.00",
                due_date=date(2026, 1, 6),
            )
            db.session.commit()

            envelope_bill = dashboard_service.txn_to_bill_dict(
                envelope, date(2026, 1, 1),
            )
            plain_bill = dashboard_service.txn_to_bill_dict(
                plain, date(2026, 1, 1),
            )

            assert envelope_bill["amount_base"] == "budget"
            assert plain_bill["amount_base"] is None
            # Non-entry-tracked unchanged: amount = effective_amount.
            assert plain_bill["amount"] == Decimal("75.00")

    def test_over_budget_consistent_with_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """C30-4: an overspent envelope flags over-budget; under-budget does not.

        Overspent case: estimated=$100, entries sum $130.
            amount         = $100.00
            entry_total    = $130.00
            entry_remaining = $100.00 - $130.00 = -$30.00
            entry_over_budget = ($130 > $100) = True
        amount/remaining/over-budget all reference the same $100 base.
        """
        with app.app_context():
            overspent = self._make_entry_tracked_txn(
                db, seed_user, seed_periods[0],
                estimated="100.00",
            )
            self._add_entries(db, seed_user, overspent, "70.00", "60.00")

            under = self._make_entry_tracked_txn(
                db, seed_user, seed_periods[1],
                estimated="100.00",
            )
            self._add_entries(db, seed_user, under, "40.00")
            db.session.commit()

            over_bill = dashboard_service.txn_to_bill_dict(
                overspent, date(2026, 1, 1),
            )
            under_bill = dashboard_service.txn_to_bill_dict(
                under, date(2026, 1, 1),
            )

            assert over_bill["amount"] == Decimal("100.00")
            assert over_bill["entry_total"] == Decimal("130.00")
            assert over_bill["entry_remaining"] == Decimal("-30.00")
            assert over_bill["entry_over_budget"] is True
            # Same base across the three fields: total > amount iff
            # over-budget, and amount - total = remaining.
            assert (
                over_bill["entry_over_budget"]
                is (over_bill["entry_total"] > over_bill["amount"])
            )

            assert under_bill["amount"] == Decimal("100.00")
            assert under_bill["entry_total"] == Decimal("40.00")
            assert under_bill["entry_remaining"] == Decimal("60.00")
            assert under_bill["entry_over_budget"] is False
            assert (
                under_bill["entry_over_budget"]
                is (under_bill["entry_total"] > under_bill["amount"])
            )

    def test_actual_amount_does_not_shift_base(
        self, app, db, seed_user, seed_periods,
    ):
        """E-21 (MED-03): the base is estimated unconditionally.

        Even when ``actual_amount`` is populated on a still-Projected
        entry-tracked txn, the amount cell stays on ``estimated_amount``
        so it agrees with the entry-derived remaining/over-budget.

        actual=$77, estimated=$120, no entries:
            amount = $120.00 (estimated, NOT $77 actual)
            entry_remaining = None (no entries -> progress fields off)
        """
        with app.app_context():
            txn = self._make_entry_tracked_txn(
                db, seed_user, seed_periods[0],
                estimated="120.00", actual="77.00",
            )
            db.session.commit()

            bill = dashboard_service.txn_to_bill_dict(txn, date(2026, 1, 1))

            assert bill["amount"] == Decimal("120.00")
            assert bill["amount_base"] == "budget"
            # Without entries the progress fields are off; the amount
            # cell's base is still disclosed so the template can render
            # the label.
            assert bill["is_tracked"] is True
            assert bill["entry_total"] is None


# ── Alert Tests ─────────────────────────────────────────────────────


class TestAlerts:
    """Tests for the alerts section.

    ``_compute_alerts`` now takes a ``_PeriodProjection`` bundle plus the
    as-of-today ``current_balance`` (fix B/D).  ``_HIGH_BALANCE`` is passed
    where a test wants to isolate the stale / negative alerts: it is well
    above the default $500 low-balance threshold, so the low-balance alert
    never fires and pollutes the count.
    """

    _HIGH_BALANCE = Decimal("5000.00")

    @staticmethod
    def _projection(balance_results, current_period, all_periods):
        """Build the _PeriodProjection bundle the alert helper now takes."""
        return dashboard_service._PeriodProjection(
            balance_results=balance_results,
            current_period=current_period,
            all_periods=all_periods,
        )

    def test_alert_stale_anchor(self, app, seed_user, seed_periods, db):
        """Stale anchor alert when last update > staleness threshold.

        Re-pin (E-19, Commit 3): the account_service factory writes
        a fixture-time origination history row at NOW; the original
        test pre-supposed an empty history.  Clear it first so the
        20-days-ago row we add is the LATEST and the staleness check
        sees the intended state.
        """
        with app.app_context():
            account = seed_user["account"]
            db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).delete()
            _add_anchor_history(
                db.session, account, seed_periods[0], "1000.00", days_ago=20,
            )
            db.session.commit()

            settings = seed_user["settings"]
            # staleness_days defaults to 14.
            alerts = dashboard_service._compute_alerts(
                account, settings,
                self._projection({}, seed_periods[0], seed_periods),
                self._HIGH_BALANCE,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 1
            assert stale[0]["severity"] == "warning"
            # days_ago=20 may show 19 or 20 depending on time-of-day.
            assert "days" in stale[0]["message"]
            # Structured link routes to the anchor-update flow for this
            # account (fix D); the route maps the kind to a URL.
            assert stale[0]["link"] == {
                "kind": "anchor_update", "account_id": account.id,
            }

    def test_alert_no_stale_anchor(self, app, seed_user, seed_periods, db):
        """No stale anchor alert when recently updated."""
        with app.app_context():
            account = seed_user["account"]
            _add_anchor_history(
                db.session, account, seed_periods[0], "1000.00", days_ago=5,
            )
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                account, seed_user["settings"],
                self._projection({}, seed_periods[0], seed_periods),
                self._HIGH_BALANCE,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 0

    @patch("app.services.dashboard_service.date")
    def test_alert_negative_balance(self, mock_date, app, seed_user, seed_periods, db):
        """Negative balance alert for first future period with balance < 0.

        ``date.today()`` is mocked so the test deterministically picks a
        seed_periods entry that is in the future.  Without the mock, the
        original implementation looped over ``seed_periods`` looking for
        ``p.start_date > date.today()`` and silently skipped the entire
        assertion block when no period matched (e.g. once today advances
        past the last seed_period on May 8, 2026).
        """
        # Mock today to March 13, 2026 (start of seed_periods[5]) so
        # seed_periods[6] through [9] are all unambiguously in the
        # future, both for this test's setup and the production
        # ``_compute_alerts`` call site that also reads ``date.today()``.
        mock_date.today.return_value = date(2026, 3, 13)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        with app.app_context():
            # Seed positive balances for every period, then knock the
            # first post-today period (index 6) negative.  The
            # production code returns the FIRST future period whose
            # balance < 0; by construction that is index 6.
            balance_results = {p.id: Decimal("100.00") for p in seed_periods}
            future_period = seed_periods[6]
            balance_results[future_period.id] = Decimal("-500.00")

            # Need a recent anchor so the stale-anchor alert does not
            # also fire (the test isolates the negative-balance alert).
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "1000.00", days_ago=1,
            )
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                self._projection(balance_results, seed_periods[0], seed_periods),
                self._HIGH_BALANCE,
            )
            neg = [a for a in alerts if a["type"] == "negative_balance"]
            assert len(neg) == 1, (
                f"Expected exactly one negative_balance alert; got {len(neg)}: "
                f"{[a['message'] for a in alerts]}"
            )
            assert neg[0]["severity"] == "danger"
            # The link names the offending period by its offset from the
            # current period (fix D).  current = index 0, offending = index 6,
            # so offset = 6 - 0 = 6.
            offset = seed_periods[6].period_index - seed_periods[0].period_index
            assert neg[0]["link"] == {
                "kind": "negative_projection", "offset": offset,
            }

    def test_alert_no_anchor_history(self, app, db, seed_user, seed_periods):
        """No anchor history -> stale anchor alert.

        Re-pin (E-19, Commit 3): under the new factory contract every
        account has an origination history row at fixture time.  The
        "no anchor history" scenario the test originally asserted is
        materially impossible in production now; the test still
        exercises the alert's empty-history fallback path by deleting
        the origination row before computing the alert.
        """
        with app.app_context():
            account = seed_user["account"]
            db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).delete()
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                account, seed_user["settings"],
                self._projection({}, seed_periods[0], seed_periods),
                self._HIGH_BALANCE,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 1
            assert stale[0]["message"] == "Your checking balance has never been set."

    def test_alert_low_balance_uses_as_of_today(self, app, seed_user, seed_periods, db):
        """Low-balance alert reads the as-of-today balance, not end-of-period.

        Fix B/D ripple: the alert quotes the same as-of-today figure the
        card displays, so the two can never disagree.  A fresh anchor keeps
        the stale alert quiet; a $250 as-of-today balance is below the
        default $500 threshold but >= 0, so the alert is a WARNING quoting
        $250.00.
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "1000.00", days_ago=1,
            )
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                self._projection({}, seed_periods[0], seed_periods),
                Decimal("250.00"),
            )
            low = [a for a in alerts if a["type"] == "low_balance"]
            assert len(low) == 1
            assert low[0]["severity"] == "warning"
            assert "$250.00" in low[0]["message"]
            assert low[0]["link"] == {"kind": "low_balance"}

    def test_alert_negative_current_balance_is_danger(
        self, app, seed_user, seed_periods, db,
    ):
        """A negative as-of-today balance raises a DANGER alert.

        Fix D severity inversion: pre-fix an already-negative current
        balance produced only the low-balance WARNING (never a danger).
        A -$100 as-of-today balance is below the $500 threshold AND below
        zero, so the alert is a DANGER quoting -$100.00.
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "1000.00", days_ago=1,
            )
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                self._projection({}, seed_periods[0], seed_periods),
                Decimal("-100.00"),
            )
            low = [a for a in alerts if a["type"] == "low_balance"]
            assert len(low) == 1
            assert low[0]["severity"] == "danger"
            # The money format places the sign after the dollar sign
            # (``$-100.00``), matching the app's other money displays.
            assert "$-100.00" in low[0]["message"]

    @patch("app.services.dashboard_service.date")
    def test_alerts_sorted_by_severity(
        self, mock_date, app, seed_user, seed_periods, db,
    ):
        """Danger alerts come before warning alerts.

        Mocked today and explicit period selection guarantee both a
        danger (negative balance) and warning (stale anchor) alert
        are produced.  The previous implementation wrapped both
        assertions in ``if alerts >= 2`` plus ``if danger_idx is not
        None and warning_idx is not None`` so the test would silently
        no-op once today drifted past the last seed_period.
        """
        mock_date.today.return_value = date(2026, 3, 13)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        with app.app_context():
            # Positive balances everywhere except the first future
            # period (index 6), which is negative -> danger alert
            # (negative projection).
            balance_results = {p.id: Decimal("100.00") for p in seed_periods}
            balance_results[seed_periods[6].id] = Decimal("-100.00")

            # A fresh anchor (the factory's NOW row) keeps the stale alert
            # quiet; a $250 as-of-today balance is below the $500 default
            # threshold but >= 0, so the low-balance WARNING fires.  The
            # set is therefore one danger + one warning, which is what the
            # sort order is being checked against.
            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                self._projection(balance_results, seed_periods[0], seed_periods),
                Decimal("250.00"),
            )
            severities = [a["severity"] for a in alerts]
            assert "danger" in severities, (
                f"Expected at least one danger alert; got: {severities}"
            )
            assert "warning" in severities, (
                f"Expected at least one warning alert; got: {severities}"
            )
            danger_idx = severities.index("danger")
            warning_idx = severities.index("warning")
            assert danger_idx < warning_idx, (
                f"Severity sort broken: danger at {danger_idx} "
                f"is not before warning at {warning_idx}"
            )

    def test_no_alerts_clean_state(self, app, seed_user, seed_periods, db):
        """No alerts when everything is current and positive."""
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "5000.00", days_ago=1,
            )
            db.session.commit()

            balance_results = {p.id: Decimal("5000.00") for p in seed_periods}
            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                self._projection(balance_results, seed_periods[0], seed_periods),
                self._HIGH_BALANCE,
            )
            # Should have no stale, no negative, no low balance.
            assert len(alerts) == 0


# ── Balance and Cash Runway Tests ───────────────────────────────────


class TestBalanceInfo:
    """Tests for balance and cash runway section.

    Fix B: the headline ``current_balance`` is now the as-of-today
    balance from ``balance_resolver.balance_as_of_date`` (no longer the
    passed-in end-of-period map; ``_get_balance_info`` dropped that
    parameter).  These tests patch ``balance_as_of_date`` to inject a
    known balance, isolating the runway / headline plumbing from the
    resolver's own (separately tested) projection arithmetic.  The
    cross-page equality lock (``test_cross_page_balance_equality``)
    covers the real resolver value end to end.
    """

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_cash_runway_zero_spending(
        self, mock_balance, app, seed_user, seed_periods,
    ):
        """Zero spending -> runway_days=None (not infinity)."""
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            assert result["cash_runway_days"] is None

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_cash_runway_negative_balance(
        self, mock_balance, app, seed_user, seed_periods,
    ):
        """Negative balance -> runway_days=0."""
        mock_balance.return_value = Decimal("-500.00")
        with app.app_context():
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            assert result["cash_runway_days"] == 0

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_cash_runway_excludes_other_scenario_expenses(
        self, mock_balance, app, seed_user, seed_periods, db,
    ):
        """deep-quality-hunt #44: cash runway counts only baseline-scenario
        settled expenses; a settled expense in another scenario on the
        same account is excluded.

        No shipping code creates a non-baseline scenario yet (multi-
        scenario is Phase 3), but the cash-runway query must scope by
        scenario like its sibling dashboard query so a Phase-3 what-if
        scenario cannot inflate the baseline daily-spend average.  Put a
        recent settled expense ONLY in a second (non-baseline) scenario
        and assert the baseline runway stays None (zero baseline
        spending).  Without the scenario filter the $900 would be counted
        and runway would be a finite number instead.
        """
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            other_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-if",
                is_baseline=False,
            )
            db.session.add(other_scenario)
            db.session.flush()

            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Other-scenario spend", "900.00",
                status_enum=StatusEnum.DONE, due_date=date.today(),
                scenario_id=other_scenario.id,
            )

            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            assert result["cash_runway_days"] is None

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_balance_is_as_of_today(
        self, mock_balance, app, seed_user, seed_periods,
    ):
        """The headline current_balance is the as-of-today resolver value.

        Fix B: the card headline now comes from
        ``balance_resolver.balance_as_of_date(account, scenario_id,
        today)`` -- the same producer the calendar uses -- not the
        end-of-current-period map it read before.  The mocked value flows
        through verbatim as ``current_balance``.
        """
        mock_balance.return_value = Decimal("2500.00")
        with app.app_context():
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            assert result["current_balance"] == Decimal("2500.00")
            mock_balance.assert_called_once_with(
                seed_user["account"], seed_user["scenario"].id, date.today(),
            )


class TestCashRunwayWindow:
    """Cash-runway 30-day window, boundary inclusion, and transfer outflow.

    Fix C / audit Card 3: runway divides settled outflow over the last 30
    calendar days (today and the 29 days before it, inclusive) by 30 to
    get a genuine daily average, and -- per the locked Gate A transfer
    decision -- a settled transfer-out shadow on checking IS counted as
    runway outflow (a sweep to savings genuinely drains checking).

    These tests patch ``balance_as_of_date`` to inject a known balance so
    the assertions isolate the runway arithmetic from the resolver's own
    projection.
    """

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_runway_hand_computed_value(
        self, mock_balance, app, seed_user, seed_periods, db,
    ):
        """A hand-computed runway: $3,000 balance, $600 spent over 30 days.

        Three settled expenses with due dates inside the window
        (today, today-10, today-29) summing to
        200 + 250 + 150 = $600.00.

        Hand arithmetic:
          daily_avg = total_spending / 30 = 600.00 / 30 = 20.00 per day
          runway    = balance / daily_avg = 3000.00 / 20.00 = 150 days
        """
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            today = date.today()
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Settled today", "200.00",
                status_enum=StatusEnum.DONE, due_date=today,
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Settled 10 days ago", "250.00",
                status_enum=StatusEnum.DONE,
                due_date=today - timedelta(days=10),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Settled 29 days ago", "150.00",
                status_enum=StatusEnum.DONE,
                due_date=today - timedelta(days=29),
            )

            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            # 3000.00 / (600.00 / 30) = 3000.00 / 20.00 = 150.
            assert result["cash_runway_days"] == 150

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_runway_excludes_expense_due_30_days_ago(
        self, mock_balance, app, seed_user, seed_periods, db,
    ):
        """An expense due exactly today-30 falls OUTSIDE the window.

        The window starts at today-29 (today and the 29 days before it,
        inclusive = 30 days), so today-30 is one day too old.  With it as
        the ONLY settled expense, the window sees zero spending and runway
        is None (no spending -> no finite runway), proving the boundary
        excludes it.
        """
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Just outside window", "600.00",
                status_enum=StatusEnum.DONE,
                due_date=date.today() - timedelta(days=30),
            )

            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            assert result["cash_runway_days"] is None

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_runway_includes_expense_due_29_days_ago(
        self, mock_balance, app, seed_user, seed_periods, db,
    ):
        """An expense due exactly today-29 falls INSIDE the window.

        today-29 is the first day of the window, so a $600 expense there
        IS counted.

        Hand arithmetic:
          daily_avg = 600.00 / 30 = 20.00 per day
          runway    = 3000.00 / 20.00 = 150 days
        """
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "On window edge", "600.00",
                status_enum=StatusEnum.DONE,
                due_date=date.today() - timedelta(days=29),
            )

            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            # 3000.00 / (600.00 / 30) = 150.
            assert result["cash_runway_days"] == 150

    @patch("app.services.dashboard_service.balance_resolver.balance_as_of_date")
    def test_runway_includes_settled_transfer_out_shadow(
        self, mock_balance, app, seed_user, seed_periods, db,
    ):
        """A settled transfer-out shadow on checking IS counted as outflow.

        Locked Gate A decision: runway measures how fast checking
        depletes, and a settled sweep from checking to savings genuinely
        drains checking, so the transfer's expense shadow counts even
        though it is excluded from any "spending" figure.

        Build a REAL settled transfer through the transfer service (so the
        expense/income shadow pair is genuine, never hand-created) from
        checking to a savings account, $600, due inside the window.  The
        expense shadow lands on checking with the settled (Paid) status.

        Hand arithmetic (the shadow is the sole outflow):
          daily_avg = 600.00 / 30 = 20.00 per day
          runway    = 3000.00 / 20.00 = 150 days
        """
        mock_balance.return_value = Decimal("3000.00")
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Sweep Target",
                    anchor_balance=Decimal("0.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            done_status_id = ref_cache.status_id(StatusEnum.DONE)
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("600.00"),
                    status_id=done_status_id,
                    category_id=None,
                    due_date=date.today() - timedelta(days=5),
                ),
            )
            db.session.flush()

            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_user["scenario"].id,
                seed_periods[0],
            )
            # The transfer-out shadow is the only outflow:
            # 3000.00 / (600.00 / 30) = 150.  A non-None finite runway
            # proves the settled transfer shadow was counted.
            assert result["cash_runway_days"] == 150


class TestNarrowProducers:
    """Direct tests for the narrow partial producers (fix H).

    ``compute_bills_section`` / ``compute_balance_section`` back the live
    ``balanceChanged`` refresh, replacing the full ``compute_dashboard_data``
    build on the refresh path.  These tests pin both their empty-state
    contracts (asserted against ``_resolve_section_context``'s exact
    None-cascade) and the keys the partials actually read.
    """

    # ── compute_bills_section ──────────────────────────────────────

    def test_bills_section_no_account_returns_empty_list(
        self, app, seed_user, db,
    ):
        """No resolvable account -> ``{"upcoming_bills": []}``.

        ``_resolve_section_context`` returns ``(None, None, None)`` when no
        active account exists, so the producer short-circuits to an empty
        bill list (the partial renders the empty state).
        """
        with app.app_context():
            # Query a fresh instance to mutate: the fixture's ``account``
            # reference is not write-through in this session.
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.flush()

            result = dashboard_service.compute_bills_section(
                seed_user["user"].id,
            )
            assert result == {"upcoming_bills": []}

    def test_bills_section_no_scenario_returns_empty_list(
        self, app, seed_user, db,
    ):
        """No baseline scenario -> ``{"upcoming_bills": []}``.

        ``get_baseline_scenario`` returns None, so the producer's
        ``scenario is None`` guard fires before any bill query.
        """
        with app.app_context():
            # Drop the baseline flag on a fresh instance (the fixture's
            # ``scenario`` reference is not write-through in this session).
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.flush()

            result = dashboard_service.compute_bills_section(
                seed_user["user"].id,
            )
            assert result == {"upcoming_bills": []}

    def test_bills_section_no_current_period_returns_empty_list(
        self, app, seed_user,
    ):
        """No period contains today -> ``{"upcoming_bills": []}``.

        ``seed_user`` (no ``seed_periods``) has only the 2024 bootstrap
        period, so ``get_current_period`` returns None and the producer's
        ``current_period is None`` guard fires.
        """
        with app.app_context():
            result = dashboard_service.compute_bills_section(
                seed_user["user"].id,
            )
            assert result == {"upcoming_bills": []}

    def test_bills_section_happy_path_keys_match_partial(
        self, app, seed_user, seed_periods_today, db,
    ):
        """Happy path: returned groups carry the keys the partial reads.

        ``_upcoming_bills.html`` iterates each group's
        ``period_start_date`` / ``period_end_date`` / ``bills``, and
        ``_bill_row.html`` reads each bill's ``name`` and ``amount``.  One
        projected expense in the current period must surface in exactly one
        group with those keys populated.
        """
        with app.app_context():
            current = next(
                p for p in seed_periods_today
                if p.start_date <= date.today() <= p.end_date
            )
            _add_txn(
                db.session, seed_user, current,
                "Electric Bill", "120.00",
                status_enum=StatusEnum.PROJECTED, due_date=date.today(),
            )

            result = dashboard_service.compute_bills_section(
                seed_user["user"].id,
            )
            # Single returned key, no stale current_period (L3).
            assert set(result.keys()) == {"upcoming_bills"}
            groups = result["upcoming_bills"]
            assert len(groups) == 1
            group = groups[0]
            assert group["period_start_date"] == current.start_date
            assert group["period_end_date"] == current.end_date
            bill_names = {b["name"] for b in group["bills"]}
            assert "Electric Bill" in bill_names
            electric = next(
                b for b in group["bills"] if b["name"] == "Electric Bill"
            )
            assert electric["amount"] == Decimal("120.00")

    # ── compute_balance_section ────────────────────────────────────

    def test_balance_section_no_account_returns_none(
        self, app, seed_user, db,
    ):
        """No resolvable account -> ``{"balance_info": None}``.

        ``_resolve_section_context`` returns account None, so the
        producer's ``account is None`` guard yields a None balance_info
        (the partial renders "No balance data available").
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.flush()

            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            assert result == {"balance_info": None}

    def test_balance_section_no_scenario_returns_none(
        self, app, seed_user, db,
    ):
        """No baseline scenario -> ``{"balance_info": None}``.

        The producer guards on ``account is None or scenario is None``;
        with no baseline scenario the second clause fires.
        """
        with app.app_context():
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.flush()

            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            assert result == {"balance_info": None}

    def test_balance_section_no_current_period_uses_raw_anchor(
        self, app, seed_user,
    ):
        """No current period -> balance_info from the raw anchor, not None.

        Unlike bills, ``compute_balance_section`` guards ONLY on account /
        scenario, not current period: with account + scenario present but
        no period containing today, ``_get_balance_info`` cannot project to
        today, so it falls back to the raw ``current_anchor_balance`` and
        still returns a populated dict (``_resolve_section_context`` exact
        shape).  ``seed_user``'s account was seeded at $1,000.00.
        """
        with app.app_context():
            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            balance_info = result["balance_info"]
            assert balance_info is not None
            assert balance_info["current_balance"] == Decimal("1000.00")

    def test_balance_section_happy_path_keys_match_partial(
        self, app, seed_user, seed_periods_today,
    ):
        """Happy path: balance_info carries the keys the partial reads.

        ``_balance_runway.html`` reads ``account_id``, ``account_name``,
        ``current_balance``, ``cash_runway_days``, and
        ``last_true_up_date``.  All must be present.
        """
        with app.app_context():
            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            balance_info = result["balance_info"]
            assert balance_info is not None
            assert set(balance_info.keys()) >= {
                "account_id", "account_name", "current_balance",
                "cash_runway_days", "last_true_up_date",
            }
            assert balance_info["account_id"] == seed_user["account"].id
            assert balance_info["account_name"] == seed_user["account"].name


# ── Payday Info Tests ───────────────────────────────────────────────


class TestPaydayInfo:
    """Tests for the payday info section."""

    def test_payday_info_future_period(self, app, seed_user, seed_periods):
        """Next period with start_date > today -> days_until populated."""
        with app.app_context():
            result = dashboard_service._get_payday_info(
                seed_user["user"].id, seed_periods,
            )
            # At least some periods should be in the future (test data is 2026).
            if result["next_date"] is not None:
                assert result["days_until"] >= 0

    def test_payday_info_no_salary(self, app, seed_user, seed_periods):
        """No salary profile -> next_amount=None, days_until still populated."""
        with app.app_context():
            result = dashboard_service._get_payday_info(
                seed_user["user"].id, seed_periods,
            )
            # seed_user has no salary profile.
            assert result["next_amount"] is None
            # But next_date should be populated (periods are in 2026).
            assert result["next_date"] is not None

    def test_payday_info_no_future_period(self, app, seed_user, db):
        """No future periods -> all fields None."""
        with app.app_context():
            # Periods all in the past relative to today, but extending
            # FORWARD of seed_user's 2024-01-05 bootstrap period (the
            # forward-only generation invariant, DH-#39): start in early
            # 2024, well before any plausible test-run date.
            from app.services import pay_period_service
            old_periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2024, 2, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            result = dashboard_service._get_payday_info(
                seed_user["user"].id, old_periods,
            )
            assert result["days_until"] is None
            assert result["next_amount"] is None
            assert result["next_date"] is None

    def test_net_pay_applies_calibration_override(
        self, app, seed_user, seed_periods, db,
    ):
        """Dashboard net pay matches the grid's CALIBRATED stored paycheck.

        Fix E / audit Card 4: ``_get_net_pay_for_period`` now passes the
        salary calibration override to ``calculate_paycheck`` exactly as
        the recurrence engine does, so the dashboard's "projected net pay"
        equals the grid's stored income for the same period.  This test
        proves both legs:

        (1) the dashboard value equals the recurrence engine's
            ``_get_transaction_amount`` (the producer that writes the
            grid's stored income), and
        (2) it differs from the UN-calibrated paycheck (so the test would
            fail if calibration were silently dropped again).

        The calibration here forces high effective tax rates (50% federal,
        10% state, capped FICA), which materially lowers net pay relative
        to the engine's bracket/FICA computation -- guaranteeing leg (2)
        is a real difference, not numeric noise.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.calibration_override import CalibrationOverride
        from app.models.recurrence_rule import RecurrenceRule
        from app.models.ref import FilingStatus, RecurrencePattern
        from app.models.salary_profile import SalaryProfile
        from app.models.transaction_template import TransactionTemplate
        from app.services import paycheck_calculator, recurrence_engine
        from app.services.tax_config_service import load_tax_configs_for_year

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="Every Period").one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period.id,
            )
            db.session.add(rule)
            db.session.flush()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                recurrence_rule_id=rule.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                name="Paycheck",
                default_amount=Decimal("2000.00"),
            )
            db.session.add(template)
            db.session.flush()

            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                template_id=template.id,
                name="Calibrated Job",
                annual_salary=Decimal("75000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.flush()

            calibration = CalibrationOverride(
                salary_profile_id=profile.id,
                actual_gross_pay=Decimal("2884.62"),
                actual_federal_tax=Decimal("1442.31"),
                actual_state_tax=Decimal("288.46"),
                actual_social_security=Decimal("178.85"),
                actual_medicare=Decimal("41.83"),
                effective_federal_rate=Decimal("0.5000000000"),
                effective_state_rate=Decimal("0.1000000000"),
                effective_ss_rate=Decimal("0.0620000000"),
                effective_medicare_rate=Decimal("0.0145000000"),
                pay_stub_date=date(2026, 1, 2),
                is_active=True,
            )
            db.session.add(calibration)
            db.session.commit()

            period = seed_periods[1]
            all_periods = seed_periods

            dashboard_net = dashboard_service._get_net_pay_for_period(
                seed_user["user"].id, period, all_periods,
            )

            # Leg 1: equals the grid's stored-income producer.
            db.session.refresh(profile)
            grid_net = recurrence_engine._get_transaction_amount(
                template, profile, period, all_periods,
            )
            assert dashboard_net == grid_net

            # Leg 2: differs from the un-calibrated paycheck.
            tax_configs = load_tax_configs_for_year(
                seed_user["user"].id, profile, period.start_date.year,
            )
            uncalibrated_net = paycheck_calculator.calculate_paycheck(
                profile, period, all_periods, tax_configs,
            ).earnings.net_pay
            assert dashboard_net != uncalibrated_net


# ── Savings Goals Tests ─────────────────────────────────────────────


class TestSavingsGoals:
    """Tests for savings goals progress."""

    def test_savings_goals_progress(self, app, seed_user, seed_periods, db):
        """Goal progress computed correctly from account balance and target."""
        with app.app_context():
            # Create a dedicated savings account with known balance.
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Goal Account",
                    anchor_balance=Decimal("2500.00"),
                ),
            )
            db.session.add(savings_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings_acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert len(goals) == 1
            # 2500 / 10000 * 100 = 25.00%
            assert goals[0]["pct_complete"] == Decimal("25.00")

    def test_savings_goals_empty(self, app, seed_user):
        """No active goals -> empty list."""
        with app.app_context():
            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert goals == []

    def test_savings_goals_clamped_100(self, app, seed_user, db):
        """Balance exceeding target -> pct_complete clamped to 100."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            rich_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Over Goal",
                    anchor_balance=Decimal("15000.00"),
                ),
            )
            db.session.add(rich_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=rich_acct.id,
                name="Reached Goal",
                target_amount=Decimal("10000.00"),
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert goals[0]["pct_complete"] == Decimal("100.00")

    def test_savings_goals_null_target(self, app, seed_user, db):
        """Null target_amount (income-relative goal) -> pct_complete=0, no crash."""
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Income Goal",
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            # Income-relative goal with no salary: resolved target is 0,
            # so percent_complete clamps to 0 (no crash, no $0.00 / 0%
            # from an unresolved NULL target).
            assert goals[0]["pct_complete"] == Decimal("0")


class TestSavingsGoalsAgreement:
    """The dashboard card and /savings report the same numbers per goal.

    Fix F / audit Card 5: ``_get_savings_goals`` delegates to the narrow
    ``savings_dashboard_service.compute_goal_progress`` producer, built
    from the SAME internals /savings uses (resolved target + resolver
    balance).  These tests assert agreement on the same goal for both a
    fixed-target goal and an income-relative goal.
    """

    @staticmethod
    def _savings_goal_datum(user_id, account_id):
        """Return the /savings ``goal_data`` entry for the goal on account_id."""
        # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service
        data = savings_dashboard_service.compute_dashboard_data(user_id)
        matches = [
            gd for gd in data["goal_data"]
            if gd["goal"].account_id == account_id
        ]
        assert len(matches) == 1, (
            f"/savings surfaced {len(matches)} goal entries for "
            f"account_id={account_id}; expected exactly one"
        )
        return matches[0]

    def test_fixed_goal_agrees_with_savings(self, app, seed_user, seed_periods, db):
        """Fixed-target goal: dashboard card == /savings on target and percent.

        A $2,500 savings account against a $10,000 fixed target.  Both
        surfaces resolve the target to $10,000 and the balance to $2,500
        (the entries-aware resolver balance, which equals the anchor for
        an account with no transactions), so progress is
        2500 / 10000 * 100 = 25.00% on both.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Fixed Goal Account",
                    anchor_balance=Decimal("2500.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
            )
            db.session.add(goal)
            db.session.commit()

            dash = dashboard_service._get_savings_goals(seed_user["user"].id)
            dash_entry = next(d for d in dash if d["account_id"] == acct.id)
            sav = self._savings_goal_datum(seed_user["user"].id, acct.id)

            # Same target, same balance basis, same percent on both screens.
            assert dash_entry["target_amount"] == sav["resolved_target"]
            assert dash_entry["current_balance"] == sav["current_balance"]
            assert dash_entry["pct_complete"] == sav["progress_pct"]
            # And the hand-computed value: 2500 / 10000 * 100 = 25.00.
            assert dash_entry["pct_complete"] == Decimal("25.00")
            assert dash_entry["target_amount"] == Decimal("10000.00")

    def test_income_relative_goal_agrees_with_savings(
        self, app, seed_user, seed_periods, db,
    ):
        """Income-relative goal: dashboard card == /savings (resolved target).

        With a salary profile, an income-relative goal's target is
        resolved from net biweekly pay * multiplier on BOTH surfaces, so
        the dashboard no longer renders ``$0.00 / 0%`` (the audit's Card 5
        bug) and the two screens agree to the cent.  The exact resolved
        target depends on the engine's net pay; the test asserts both
        screens produce the SAME positive figures rather than a baked-in
        dollar amount.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum
        from app.models.ref import FilingStatus
        from app.models.salary_profile import SalaryProfile

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Income Goal Salary",
                annual_salary=Decimal("75000.00"),
                state_code="NC",
            )
            db.session.add(profile)

            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Income Goal Account",
                    anchor_balance=Decimal("4000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            dash = dashboard_service._get_savings_goals(seed_user["user"].id)
            dash_entry = next(d for d in dash if d["account_id"] == acct.id)
            sav = self._savings_goal_datum(seed_user["user"].id, acct.id)

            # The NULL target now resolves to a positive dollar figure on
            # the dashboard (the Card 5 fix), matching /savings exactly.
            assert dash_entry["target_amount"] == sav["resolved_target"]
            assert dash_entry["target_amount"] > Decimal("0.00")
            assert dash_entry["current_balance"] == sav["current_balance"]
            assert dash_entry["pct_complete"] == sav["progress_pct"]


# ── Debt Summary Tests ──────────────────────────────────────────────


def _create_loan_account(
    seed_user, db_session,
    principal=Decimal("1000.00"), rate=Decimal("0.05000"), term=24,
):
    """Seed a loan account with LoanParams + origination event.

    Mirrors test_savings_dashboard_service._create_small_loan so the
    debt-summary wrapper can be exercised against a real loan: a $1,000
    Auto Loan at 5% for 24 months originated Jan 2026 (remaining months
    comfortably positive from the seeded periods).
    """
    loan_type = db_session.query(AccountType).filter_by(name="Auto Loan").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Dashboard Test Loan",
            anchor_balance=principal,
        ),
    )
    db_session.add(account)
    db_session.flush()

    from app.models.loan_params import LoanParams  # pylint: disable=import-outside-toplevel
    from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
        insert_origination_event,
        insert_origination_rate,
    )
    params = LoanParams(
        account_id=account.id,
        original_principal=principal,
        current_principal=principal,
        term_months=term,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_rate(params, rate)
    insert_origination_event(params)
    db_session.commit()
    return account


class TestDebtSummary:
    """Tests for the debt summary section."""

    def test_debt_summary_no_debt(self, app, seed_user, seed_periods):
        """No debt accounts -> debt_summary is None."""
        with app.app_context():
            result = dashboard_service._get_debt_summary(seed_user["user"].id)
            assert result is None

    def test_debt_summary_with_debt(self, app, db, seed_user, seed_periods):
        """A loan account -> the wrapper returns the populated debt summary.

        Covers the success path of _get_debt_summary (not just the no-debt
        None path, deep-hunt #86): a $1,000 auto loan at 5% for 24 months
        yields total_debt == 1000.00, the single loan's rate as the
        weighted average, a positive monthly payment, and a projected
        payoff date.  _apply_dti_metrics always writes the three DTI keys
        (to None when there is no salary), so their presence proves the
        DTI block ran through the wrapper.  Mirrors
        test_savings_dashboard_service.test_debt_summary_single_loan but
        asserts via the dashboard wrapper rather than the orchestrator.
        """
        with app.app_context():
            _create_loan_account(seed_user, db.session)
            result = dashboard_service._get_debt_summary(seed_user["user"].id)
            assert result is not None
            assert result["total_debt"] == Decimal("1000.00")
            assert result["weighted_avg_rate"] == Decimal("0.05000")
            assert result["total_monthly_payments"] > Decimal("0.00")
            assert result["projected_debt_free_date"] is not None
            assert "dti_ratio" in result
            assert "dti_label" in result
            assert "gross_monthly_income" in result

    def test_debt_summary_propagates_errors(self, app, seed_user):
        """A computation error propagates -- it is NOT masked as None (#82).

        Guards the rule-4 fix: the old wrapper caught
        (ValueError, KeyError, AttributeError) and returned None, which
        silently blanked the debt panel and was indistinguishable from the
        legitimate no-debt None.  With the broad except removed, a genuine
        programming bug inside the producer must surface, not vanish.
        Patching the producer (``compute_debt_summary``, the narrow #82
        entry point the wrapper now calls) to raise KeyError proves the
        masking is gone: under the old code this returned None and the
        test would fail; now it raises.
        """
        with app.app_context():
            with patch(
                "app.services.savings_dashboard_service.compute_debt_summary",
                side_effect=KeyError("simulated computation bug"),
            ):
                with pytest.raises(KeyError):
                    dashboard_service._get_debt_summary(seed_user["user"].id)


# ── Integration Tests ───────────────────────────────────────────────


class TestFullDashboard:
    """Integration tests for the full dashboard."""

    def test_full_dashboard_integration(self, app, seed_full_user_data):
        """Full dashboard with rich data -> all sections populated, no errors."""
        with app.app_context():
            user_id = seed_full_user_data["user"].id
            result = dashboard_service.compute_dashboard_data(user_id)

            assert result["has_default_account"] is True
            assert isinstance(result["upcoming_bills"], list)
            assert isinstance(result["alerts"], list)
            assert isinstance(result["savings_goals"], list)
            # Spending comparison removed (audit fix A): the key is gone.
            assert "spending_comparison" not in result
            assert result["payday_info"] is not None
