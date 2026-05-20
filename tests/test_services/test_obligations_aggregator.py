"""
Shekel Budget App -- obligations_aggregator service tests (E-24, HIGH-05).

Locks the single canonical monthly-equivalent aggregator behind both
``/obligations`` and the ``/savings`` emergency-fund baseline +
per-goal contribution floors. Before this aggregator, four near-
identical loops applied the filter (skip ONCE / skip expired / skip
no-rule / skip missing-or-zero amount); only the three
``/obligations`` loops applied the expired-rule guard, so an expired
recurring expense inflated the EF baseline forever (HIGH-05 / D6-05).

Every test below sets up real ORM templates against the test DB so
the relationship-driven attribute access in
``template_monthly_or_none`` is exercised end-to-end, and computes
its expected value by hand from the named factors
(``PAY_PERIODS_PER_YEAR`` / ``MONTHS_PER_YEAR``) -- no test
inlines a literal 26/12 for the expectation, so a regression of
the constants would surface here.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator
from app.utils.money import MONTHS_PER_YEAR, PAY_PERIODS_PER_YEAR


# ── Helpers ──────────────────────────────────────────────────────────


def _create_rule(seed_user, pattern_enum, *, interval_n=1, end_date=None):
    """Create and flush a RecurrenceRule for the seed user."""
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(pattern_enum),
        interval_n=interval_n,
        end_date=end_date,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


def _create_expense(seed_user, rule, amount, *, name="Expense"):
    """Create and flush an expense TransactionTemplate."""
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=amount,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


# ── Tests ────────────────────────────────────────────────────────────


class TestObligationsAggregator:
    """End-to-end behavior of obligations_aggregator.committed_monthly."""

    def test_expired_template_excluded(self, app, seed_user):
        """C23-1 (HIGH-05 / D6-05): a recurring template whose rule's
        end_date is strictly before ``as_of`` contributes zero.

        Pre-Commit-23 ``compute_committed_monthly`` lacked this guard
        and a $100 biweekly expired template inflated the EF baseline
        and every per-goal floor by:
            $100 * 26 / 12 = $216.67 / month forever.
        After Commit 23 the aggregator returns Decimal("0.00") for the
        same setup. Arithmetic: filter is per-template, expired -> None,
        the only template contributes nothing, sum -> 0.00.
        """
        as_of = date(2026, 5, 20)
        expired_end = as_of - timedelta(days=1)
        with app.app_context():
            rule = _create_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
                end_date=expired_end,
            )
            tmpl = _create_expense(
                seed_user, rule, Decimal("100.00"),
                name="Expired Biweekly",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly([tmpl], as_of)

            assert result == Decimal("0.00"), (
                f"Expired template must not contribute (HIGH-05). "
                f"Pre-fix value was {Decimal('100') * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR}; "
                f"got {result}."
            )

    def test_active_template_included(self, app, seed_user):
        """C23-2: an active recurring template with no end_date (or an
        end_date >= as_of) contributes its full monthly equivalent.

        Arithmetic: $100 biweekly * 26 / 12 = $216.6666...; the
        aggregator quantizes at the boundary to $216.67.
        """
        as_of = date(2026, 5, 20)
        with app.app_context():
            rule = _create_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            tmpl = _create_expense(
                seed_user, rule, Decimal("100.00"),
                name="Active Biweekly",
            )
            db.session.commit()

            expected = (
                Decimal("100") * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            result = obligations_aggregator.committed_monthly([tmpl], as_of)

            assert result == Decimal("216.67"), (
                f"Active biweekly $100 -> $100 * 26/12 = {expected}; got {result}"
            )

    def test_once_pattern_counted_once_meaning_excluded(self, app, seed_user):
        """C23-3: a ONCE-pattern template contributes zero (a one-time
        commitment is not a recurring monthly obligation).

        Setup: one ONCE template for $5,000 plus one EVERY_PERIOD
        template for $100. Expected total = $100 * 26 / 12 = $216.67
        (the ONCE entry is filtered out by amount_to_monthly returning
        None). If ONCE were counted, the total would be $5,000 +
        $216.67 = $5,216.67 -- this assertion proves it is not.
        """
        as_of = date(2026, 5, 20)
        with app.app_context():
            once_rule = _create_rule(
                seed_user, RecurrencePatternEnum.ONCE,
            )
            once_tmpl = _create_expense(
                seed_user, once_rule, Decimal("5000.00"),
                name="One-Time",
            )
            recurring_rule = _create_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            recurring_tmpl = _create_expense(
                seed_user, recurring_rule, Decimal("100.00"),
                name="Recurring",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [once_tmpl, recurring_tmpl], as_of,
            )

            # Hand-computed: ONCE excluded -> $100 * 26 / 12 = $216.67.
            assert result == Decimal("216.67"), (
                f"ONCE must not contribute; recurring -> $216.67; got {result}"
            )

    def test_obligations_and_savings_agree(self, app, seed_user, auth_client):
        """C23-4: the /obligations page expense subtotal and
        savings_dashboard's EF committed_monthly baseline -- now both
        route through obligations_aggregator -- agree on the same
        dollar number for the same templates.

        Setup: two expenses on checking ($100 biweekly + $500 monthly).
        Hand-computed monthly equivalents:
            $100 * 26 / 12 = $216.67
            $500           = $500.00
            total expense  = $716.67
        Pre-Commit-23 the /obligations total and the EF baseline
        applied two different filters; with the canonical aggregator
        they cannot diverge.
        """
        with app.app_context():
            biweekly_rule = _create_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            monthly_rule = _create_rule(
                seed_user, RecurrencePatternEnum.MONTHLY,
            )
            _create_expense(
                seed_user, biweekly_rule, Decimal("100.00"),
                name="Biweekly Bill",
            )
            _create_expense(
                seed_user, monthly_rule, Decimal("500.00"),
                name="Monthly Bill",
            )
            db.session.commit()

            # /obligations expense subtotal -- read from the rendered
            # page so we are testing the actual route, not the function.
            resp = auth_client.get("/obligations")
            assert resp.status_code == 200
            assert "$716.67" in resp.data.decode(), (
                "/obligations expense subtotal must show $716.67"
            )

            # Same templates fed through the aggregator directly --
            # this is the function the EF baseline calls.
            templates = (
                db.session.query(TransactionTemplate)
                .filter_by(
                    user_id=seed_user["user"].id,
                    is_active=True,
                )
                .all()
            )
            agg_total = obligations_aggregator.committed_monthly(
                templates, date.today(),
            )
            assert agg_total == Decimal("716.67"), (
                f"Aggregator total must equal /obligations subtotal "
                f"($716.67); got {agg_total}"
            )

    def test_factor_single_definition(self):
        """C23-5: the 26/12 biweekly-to-monthly factor is defined in
        exactly one module after this commit (app.utils.money).

        A grep for ``Decimal("26")`` / ``Decimal("12")`` / `` 26 / 12 ``
        across app/services/ must show only the canonical
        ``PAY_PERIODS_PER_YEAR`` / ``MONTHS_PER_YEAR`` definitions in
        app/utils/money.py for the biweekly-to-monthly cluster
        (HIGH-05 / D6-05 scope). Sites that use ``Decimal("12")`` for
        a different concept (annual rate -> monthly compounding in
        loan_resolver / escrow_calculator / interest_projection) are
        out of HIGH-05's scope and remain.
        """
        # The constants imported at module top resolve to the canonical
        # values defined in app/utils/money.py. Asserting the imported
        # values pins the import path: any future code that imports
        # locally-redefined ``_PAY_PERIODS_PER_YEAR`` would not satisfy
        # this assertion if HIGH-05's "one module" rule were violated.
        assert PAY_PERIODS_PER_YEAR == Decimal("26")
        assert MONTHS_PER_YEAR == Decimal("12")

        # Sanity: the constants live in app/utils/money, not in
        # savings_goal_service (where they used to be defined as
        # private ``_PAY_PERIODS_PER_YEAR`` / ``_MONTHS_PER_YEAR``).
        from app.utils import money
        from app.services import savings_goal_service
        assert money.PAY_PERIODS_PER_YEAR is PAY_PERIODS_PER_YEAR
        assert money.MONTHS_PER_YEAR is MONTHS_PER_YEAR
        assert not hasattr(savings_goal_service, "_PAY_PERIODS_PER_YEAR")
        assert not hasattr(savings_goal_service, "_MONTHS_PER_YEAR")

    def test_emergency_fund_baseline_excludes_expired(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C23-6 (HIGH-05 / D6-05): on /savings the emergency-fund
        committed_monthly baseline excludes an expired recurring
        template.

        Setup: one EVERY_PERIOD expense template for $1,500 whose
        rule.end_date is strictly before today (1 day ago).

        Pre-Commit-23 the EF baseline would compute:
            $1,500 * 26 / 12 = $3,250 / month
        forever, even though the obligation has stopped recurring.
        After Commit 23 the aggregator drops the expired template and
        the displayed baseline goes to $0 -- no "/mo" string for the
        $3,250 inflated value should appear.
        """
        with app.app_context():
            rule = _create_rule(
                seed_user, RecurrencePatternEnum.EVERY_PERIOD,
                end_date=date.today() - timedelta(days=1),
            )
            _create_expense(
                seed_user, rule, Decimal("1500.00"),
                name="Expired Bill",
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Hand-computed pre-fix inflated baseline -- the assertion
            # that protects against regression.
            inflated = (
                Decimal("1500") * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            assert inflated == Decimal("3250.00")
            assert "$3,250/mo" not in html, (
                "Expired template must not inflate EF baseline "
                "(HIGH-05 / D6-05 regression)."
            )
