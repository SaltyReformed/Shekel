"""
Shekel Budget App -- Dashboard Shared-Helper Service Tests

Tests for the survivors of the Terminal Road rebuild (Loop B B-3): the
shared bill-builder ``txn_to_bill_dict`` (with its E-21 single-base entry
progress) and the hero-shaped ``compute_balance_section`` the anchor
editor's Cancel / Escape reverts to.

The retired summary-card producers (``compute_dashboard_data``, the
upcoming-bills grouping, alerts, cash runway, payday, savings-goal / debt
cards) and their tests were removed in the same pass -- a sanctioned
removal of features ruled out by the developer, not test-gaming (see
``docs/design/dashboard_card_audit.md`` "Retirements").  The pulse / tracks
producers that replaced them are tested in
``tests/test_services/test_dashboard_pulse_service.py``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import balance_at, dashboard_service
from tests._test_helpers import (
    add_txn as _add_txn,
    make_investment_account,
    set_default_grid_account,
)


# ── Entry-tracked bill row, single declared base (E-21 / MED-03) ────


class TestBillRowSingleBase:
    """E-21 (MED-03 / F-028 / F-056): the entry-tracked bill row's amount,
    remaining, and over-budget all anchor on ``estimated_amount`` -- the
    declared budget base -- and the base is disclosed via
    ``bill["amount_base"]``.  Pre-fix the amount cell used
    ``effective_amount`` (tier-3 actual when populated) while remaining
    used ``estimated_amount``, producing internally inconsistent rows.

    These exercise the SURVIVING ``txn_to_bill_dict`` directly (the
    due-soon list's render-ready bill builder), not the retired bills
    grouping.
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


# ── compute_balance_section: the anchor-edit revert fragment ─────────


class TestComputeBalanceSection:
    """The hero-shaped balance fragment behind ``dashboard.balance_section``.

    The anchor editor's Cancel / Escape (and 409-conflict retry) reverts
    through ``accounts._anchor_revert_url`` (``revert=dashboard`` ->
    ``dashboard.balance_section``), which renders ``_pulse_balance.html``.
    The producer therefore returns a ``pulse.hero``-shaped dict carrying
    the ``balance`` and ``account_id`` the click-to-edit control reads.
    """

    def test_no_account_returns_none_hero(self, app, seed_user, db):
        """No resolvable account -> ``{"hero": None}``.

        Deactivating the only account makes ``resolve_grid_account``
        return None, so the producer's ``account is None`` guard fires.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.flush()

            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            assert result == {"hero": None}

    def test_no_scenario_returns_none_hero(self, app, seed_user, db):
        """No baseline scenario -> ``{"hero": None}``.

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
            assert result == {"hero": None}

    def test_no_current_period_uses_raw_anchor(self, app, seed_user):
        """No current period -> the hero balance is the raw anchor, not None.

        ``compute_balance_section`` guards ONLY on account / scenario, not
        current period: with account + scenario present but no period
        containing today, the resolver cannot project to today, so the
        balance falls back to the raw ``current_anchor_balance``.
        ``seed_user``'s account was seeded at $1,000.00.
        """
        with app.app_context():
            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            hero = result["hero"]
            assert hero is not None
            assert hero["balance"] == Decimal("1000.00")
            assert hero["account_id"] == seed_user["account"].id

    def test_happy_path_hero_shape_matches_partial(
        self, app, seed_user, seed_periods_today,
    ):
        """Happy path: the hero dict carries exactly the keys the partial reads.

        ``_pulse_balance.html`` reads ``pulse.hero.balance`` and
        ``pulse.hero.account_id``; both must be present, and the balance is
        the as-of-today projected figure (the seed account at $1,000.00
        with no transactions projects to exactly the anchor).
        """
        with app.app_context():
            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            hero = result["hero"]
            assert hero is not None
            assert set(hero.keys()) == {"balance", "account_id"}
            assert hero["account_id"] == seed_user["account"].id
            # No transactions: as-of-today balance == anchor == $1,000.00.
            assert hero["balance"] == Decimal("1000.00")

    def test_investment_grid_account_hero_shows_cash_not_modeled(
        self, app, seed_user, seed_periods, db,
    ):
        """An investment grid account's hero is the cash carry, not modeled growth.

        Regression lock for the Level-1 ``balance_at`` seam reroute: this
        fragment is the same spending-account runway figure the pulse hero
        shows, so it must read the CASH-FLOW scalar (``cash_balance_at``), not
        the kind-correct ``balance_at`` scalar (which compounds an
        investment).  ``resolve_grid_account`` can return any kind, so make a
        401(k) (7% return) the user's default grid account, anchored
        $100,000.00 at ``seed_periods[0]`` with no contributions: the cash
        carry to today is a flat $100,000.00, while the kind-correct scalar
        reads STRICTLY ABOVE it.  The fragment must show the flat cash carry.
        """
        with app.app_context():
            inv = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            )
            set_default_grid_account(
                db.session, seed_user["user"].id, inv.id,
            )

            scenario = seed_user["scenario"]
            # The kind-correct scalar (the bug's hero) compounds the anchor
            # forward; assert the divergence is real before locking the fix.
            modeled = balance_at.balance_at(inv, scenario, date(2026, 3, 20))
            assert modeled > Decimal("100000.00")

            result = dashboard_service.compute_balance_section(
                seed_user["user"].id,
            )
            hero = result["hero"]
            assert hero["account_id"] == inv.id
            # Anchor $100,000.00 carried flat to today with no contributions
            # -> the cash carry, NOT the compounded modeled value.
            assert hero["balance"] == Decimal("100000.00")
            assert hero["balance"] != modeled
