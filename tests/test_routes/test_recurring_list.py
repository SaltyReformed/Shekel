"""
Tests for the unified Recurring surface: GET /templates and the unit toggle.

The unified /templates page (Recurring cluster Loop B) replaces the former
/templates + /transfers lists and the /obligations page.  It lists every
active recurring definition of all three kinds, a summary band, per-section
subtotals, and per row a monthly + per-paycheck equivalent, an engine-backed
next date, and share of section committed total.

These route-level tests assert the rendered surface: all kinds present,
subtotals in the HTML, the management surface shows one-time definitions the
old /obligations page hid, the unit toggle swaps every figure and persists,
and ownership isolation holds.  The producer's arithmetic is locked
separately in ``tests/test_services/test_recurring_view.py``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service


# ── Helpers ──────────────────────────────────────────────────────────


def _rule(user, pattern_enum, *, day_of_month=None):
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=ref_cache.recurrence_pattern_id(pattern_enum),
        interval_n=1,
        day_of_month=day_of_month,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


def _txn(user, account, category, rule, amount, *, type_enum, name):
    tmpl = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=category.id,
        recurrence_rule_id=rule.id if rule else None,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


def _savings(user, name="Test Savings"):
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()
    return account


def _transfer(user, from_account, to_account, rule, amount, *, name):
    tmpl = TransferTemplate(
        user_id=user.id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


# ── Rendering ────────────────────────────────────────────────────────


class TestUnifiedRender:
    """The unified surface renders every kind with correct figures."""

    def test_all_three_kinds_render(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Income, expense, and transfer definitions all appear by name."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        savings = _savings(user)

        rule_bw = _rule(user, RecurrencePatternEnum.EVERY_PERIOD)
        rule_mo = _rule(user, RecurrencePatternEnum.MONTHLY, day_of_month=15)
        _txn(user, checking, category, rule_bw, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electricity")
        _txn(user, checking, category, rule_bw, "1500.00",
             type_enum=TxnTypeEnum.INCOME, name="Paycheck")
        _transfer(user, checking, savings, rule_mo, "500.00",
                  name="Savings Transfer")
        db.session.commit()

        resp = auth_client.get("/templates")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Electricity" in html
        assert "Paycheck" in html
        assert "Savings Transfer" in html
        assert "Transfers" in html

    def test_subtotal_renders_monthly(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A biweekly $100 expense shows its $216.67 monthly equivalent.

        100 * 26 / 12 = 216.666... -> $216.67 (ROUND_HALF_UP).
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        rule = _rule(user, RecurrencePatternEnum.EVERY_PERIOD)
        _txn(user, checking, category, rule, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electric")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "$216.67" in html
        # The lone recurring expense is 100% of its section's committed total.
        assert "100.0% of section" in html

    def test_one_time_definition_is_shown(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A one-time (ONCE) expense IS listed -- the management surface shows
        every active definition, unlike the retired /obligations lens.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        rule_once = _rule(user, RecurrencePatternEnum.ONCE)
        _txn(user, checking, category, rule_once, "999.00",
             type_enum=TxnTypeEnum.EXPENSE, name="One Time Buy")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "One Time Buy" in html

    def test_empty_state(self, auth_client, seed_user, db, seed_periods_today):
        """With no definitions the empty-state message renders."""
        html = auth_client.get("/templates").data.decode()
        assert "No active recurring definitions" in html


# ── Unit toggle ──────────────────────────────────────────────────────


class TestUnitToggle:
    """The Monthly / Per-paycheck toggle swaps every figure and persists."""

    def test_default_is_monthly_and_toggle_persists(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Default view is monthly; POSTing the per-paycheck preference swaps
        every figure and persists across requests.

        A monthly $1,300 expense: monthly equivalent $1,300.00,
        per-paycheck 1300 * 12 / 26 = $600.00.  The $600.00 figure appears
        only in the per-paycheck view; the $1,300.00 amount always shows.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        rule = _rule(user, RecurrencePatternEnum.MONTHLY, day_of_month=1)
        _txn(user, checking, category, rule, "1300.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Rent Bill")
        db.session.commit()

        # Default: monthly.  The per-paycheck-only figure is absent.
        html = auth_client.get("/templates").data.decode()
        assert "$1,300.00" in html
        assert "$600.00" not in html

        # Persist the per-paycheck preference.
        post = auth_client.post(
            "/templates/unit-preference", data={"unit": "per_paycheck"},
        )
        assert post.status_code == 302

        # Now every figure is per-paycheck: the $600.00 equivalent shows.
        html2 = auth_client.get("/templates").data.decode()
        assert "$600.00" in html2

        # And the choice persisted on the settings row.
        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is True

    def test_invalid_unit_is_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An unrecognized unit value leaves the preference unchanged."""
        post = auth_client.post(
            "/templates/unit-preference", data={"unit": "furlongs"},
        )
        assert post.status_code == 302
        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is False


# ── Ownership isolation ──────────────────────────────────────────────


class TestUnifiedIDOR:
    """Only the authenticated user's definitions appear."""

    def test_only_current_user_definitions(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """User 2's templates never appear on user 1's Recurring surface."""
        user1 = seed_user["user"]
        user2 = second_user["user"]
        checking1 = seed_user["account"]
        checking2 = second_user["account"]
        category1 = seed_user["categories"]["Rent"]
        category2 = list(second_user["categories"].values())[0]

        rule1 = _rule(user1, RecurrencePatternEnum.MONTHLY, day_of_month=1)
        rule2 = _rule(user2, RecurrencePatternEnum.MONTHLY, day_of_month=1)
        _txn(user1, checking1, category1, rule1, "1200.00",
             type_enum=TxnTypeEnum.EXPENSE, name="My Rent")
        _txn(user2, checking2, category2, rule2, "900.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Their Rent")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "My Rent" in html
        assert "Their Rent" not in html
