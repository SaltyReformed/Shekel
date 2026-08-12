"""
Shekel Budget App -- obligations_aggregator service tests (E-24, HIGH-05).

Locks the single canonical monthly-equivalent aggregator behind both
the Recurring surface and the ``/savings`` emergency-fund baseline +
per-goal contribution floors. Before this aggregator, four near-
identical loops applied the filter (skip ONCE / skip expired / skip
no-rule / skip missing-or-zero amount); only the three
``/obligations`` loops applied the expired-rule guard, so an expired
recurring expense inflated the EF baseline forever (HIGH-05 / D6-05).

Every test below sets up real ORM templates against the test DB so
the relationship-driven attribute access in
``template_monthly_or_none`` is exercised end-to-end, and computes
its expected value by hand from the named factors -- no test
inlines a literal 26/12 for the expectation, so a regression of
the factor would surface here.

**One of those factors stopped being a constant at plan step R7a-2a.**
``PAY_PERIODS_PER_YEAR`` was a module-level ``Decimal("26")`` while
``budget.pay_schedule.cadence_days`` is user-selectable 1..365, so this
"single canonical aggregator" produced a figure that was simply wrong for an
owner not paid biweekly.  Every case here now states the cadence it was
hand-computed at (:data:`_BIWEEKLY`) and no assertion moved.
``test_the_conversion_side_paycheck_count_has_one_producer`` is where a second
cadence is exercised end to end, and it is the regression guard that replaced
the constant's own.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator
from app.services.pay_calendar import PayCadence
from app.utils.money import MONTHS_PER_YEAR

#: 14 days between paydays, 26 a year -- the cadence every hand-computed
#: figure in this file assumes, and the one the retired
#: ``PAY_PERIODS_PER_YEAR`` constant assumed for every owner (R7a-2a).
_BIWEEKLY = PayCadence(cadence_days=14)


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
    """Create and flush an expense TransactionTemplate.

    ``rule`` may be ``None`` -- that is how a definition says "does not
    repeat" since plan step R2e-3.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id if rule is not None else None,
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

            result = obligations_aggregator.committed_monthly(
                [tmpl], as_of, _BIWEEKLY,
            )

            assert result == Decimal("0.00"), (
                f"Expired template must not contribute (HIGH-05). "
                f"Pre-fix value was {Decimal('100') * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR}; "
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
                Decimal("100") * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            result = obligations_aggregator.committed_monthly(
                [tmpl], as_of, _BIWEEKLY,
            )

            assert result == Decimal("216.67"), (
                f"Active biweekly $100 -> $100 * 26/12 = {expected}; got {result}"
            )

    def test_non_repeating_template_contributes_zero(self, app, seed_user):
        """C23-3: a template that does not repeat contributes zero.

        A one-time commitment is not a recurring monthly obligation.  Setup:
        one rule-less template for $5,000 plus one EVERY_PERIOD template for
        $100.  Expected total = $100 * 26 / 12 = $216.67 (the rule-less entry
        returns ``None`` from ``template_monthly_or_none`` before there is a
        pattern to convert).  If it were counted the total would be $5,000 +
        $216.67 = $5,216.67 -- this assertion proves it is not.

        Named for the ``Once`` PATTERN until plan step R2e-3 retired it;
        rule-less is now the only spelling of "does not repeat".
        """
        as_of = date(2026, 5, 20)
        with app.app_context():
            once_tmpl = _create_expense(
                seed_user, None, Decimal("5000.00"),
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
                [once_tmpl, recurring_tmpl], as_of, _BIWEEKLY,
            )

            # Hand-computed: ONCE excluded -> $100 * 26 / 12 = $216.67.
            assert result == Decimal("216.67"), (
                f"ONCE must not contribute; recurring -> $216.67; got {result}"
            )

    def test_recurring_surface_and_savings_agree(
        self, app, seed_user, auth_client,
    ):
        """C23-4: the unified Recurring surface's expense subtotal and
        savings_dashboard's EF committed_monthly baseline -- both route
        through obligations_aggregator -- agree on the same dollar number
        for the same templates.

        The /obligations page retired (Loop B); its monthly-equivalent
        kernel moved to the unified /templates surface, reached here by
        following the /obligations redirect.

        Setup: two expenses on checking ($100 biweekly + $500 monthly).
        Hand-computed monthly equivalents:
            $100 * 26 / 12 = $216.67
            $500           = $500.00
            total expense  = $716.67
        The rendered subtotal and the EF baseline both call the canonical
        aggregator, so they cannot diverge.
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

            # Expense subtotal read from the rendered unified Recurring
            # surface (via the /obligations redirect) so we test the actual
            # route, not just the function.
            resp = auth_client.get("/obligations", follow_redirects=True)
            assert resp.status_code == 200
            assert "$716.67" in resp.data.decode(), (
                "unified Recurring surface expense subtotal must show $716.67"
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
                templates, date.today(), _BIWEEKLY,
            )
            assert agg_total == Decimal("716.67"), (
                f"Aggregator total must equal /obligations subtotal "
                f"($716.67); got {agg_total}"
            )

    def test_the_conversion_side_paycheck_count_has_one_producer(self):
        """C23-5, as plan step R7a-2a restated it: ONE producer, no constant.

        This test's SUBJECT moved and its purpose did not.  It used to assert
        that the biweekly-to-monthly factor was a constant defined in exactly
        one module (``app.utils.money.PAY_PERIODS_PER_YEAR``); the defect that
        step fixed is that "how many paychecks a year" is not a constant at
        all -- ``budget.pay_schedule.cadence_days`` is user-selectable 1..365.
        So the invariant is now "one DERIVATION, reachable through both doors,
        and no module-level number to drift from it", and this is where a
        reintroduced constant is caught.

        **Named for the CONVERSION side, because it is not the whole story.**
        ``salary.salary_profiles.pay_periods_per_year`` is a second, stored,
        user-selected paycheck count on the PRODUCTION side -- the divisor the
        paycheck engine uses -- and nothing ties the two together.  This test
        does not and cannot assert against it; the developer ruled that second
        producer's removal into this arc on 2026-08-11, as the leaf after
        R7a-2a.  A name claiming "one producer" flat would be the false claim
        this file exists to catch.

        ``MONTHS_PER_YEAR`` stays a constant and stays here, because 12 is a
        property of the calendar rather than of an owner.
        """
        from app.services import savings_goal_service
        from app.services.pay_calendar import PayCadence as _Cadence
        from app.utils import money

        # 1. The month denominator is still one named constant in one module.
        assert MONTHS_PER_YEAR == Decimal("12")
        assert money.MONTHS_PER_YEAR is MONTHS_PER_YEAR

        # 2. The paycheck count is GONE as a constant -- from the module that
        #    held it and from the module that held it privately before that.
        assert not hasattr(money, "PAY_PERIODS_PER_YEAR"), (
            "app.utils.money must not re-declare a paychecks-per-year "
            "constant: the count is a function of the owner's cadence "
            "(plan step R7a-2a)."
        )
        assert not hasattr(savings_goal_service, "_PAY_PERIODS_PER_YEAR")
        assert not hasattr(savings_goal_service, "_MONTHS_PER_YEAR")

        # 3. The one derivation, at the three cadences a real schedule uses.
        #    round(365.2425 / days): 14 -> 26, 7 -> 52, 30 -> 12.
        assert _Cadence(cadence_days=14).periods_per_year == Decimal("26")
        assert _Cadence(cadence_days=7).periods_per_year == Decimal("52")
        assert _Cadence(cadence_days=30).periods_per_year == Decimal("12")

        # 4. The aggregator reads THAT value and nothing else: a cadence the
        #    old constant would have called 26 answers 52 here.  A duck-typed
        #    template rather than an ORM row -- the exception to this file's
        #    "real ORM templates" rule, taken because the subject is the
        #    CADENCE and an ORM round-trip would add a second variable to a
        #    test whose whole point is that only one thing changed.
        #    $100 every paycheck weekly = 100 * 52 / 12 = $433.33.
        weekly_template = SimpleNamespace(
            recurrence_rule=SimpleNamespace(
                end_date=None,
                pattern_id=ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
                interval_n=1,
            ),
            default_amount=Decimal("100.00"),
        )
        assert obligations_aggregator.committed_monthly(
            [weekly_template], date(2026, 5, 20), _Cadence(cadence_days=7),
        ) == Decimal("433.33")
        assert obligations_aggregator.committed_monthly(
            [weekly_template], date(2026, 5, 20), _BIWEEKLY,
        ) == Decimal("216.67")

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
                Decimal("1500") * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            assert inflated == Decimal("3250.00")
            assert "$3,250/mo" not in html, (
                "Expired template must not inflate EF baseline "
                "(HIGH-05 / D6-05 regression)."
            )
