"""
Shekel Budget App -- recurring_view producer tests (Recurring cluster Loop B, P1).

Locks the unified Recurring surface's display-model producer: the summary
band, the three kind-grouped sections with per-section subtotals, and per
row the defined amount, monthly + per-paycheck equivalents, engine-backed
next date, and share of section committed total.

The producer has exactly one monthly source of truth
(``obligations_aggregator.template_monthly_or_none``); the per-paycheck
value is DERIVED from it by
``PayCadence.monthly_to_per_paycheck`` -- the OWNER's cadence since plan step
R7a-2a, a hardcoded ``12 / 26`` before it.
Every expectation below is a LITERAL hand-computed at the biweekly cadence
with its arithmetic in a comment, never a re-derivation through the producer's
own value object -- an expectation computed with ``PayCadence`` would move with
a derivation bug instead of catching it.  Real ORM templates run against the
test DB so the relationship-driven attribute access and ``ref_cache`` lookups
are exercised end to end.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from tests._test_helpers import make_pattern_rule
from app.models.ref import AccountType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service, recurring_view
from app.services.obligations_aggregator import committed_monthly
from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    PayCalendarError,
    calendar_for,
)
from app.services.recurrence import (
    RecurrenceResolutionError,
    read_rule,
)
from app.services.recurrence import rule_occurrences
# Imported as a MODULE so the firing controls below patch the names the
# read door resolves at CALL time.  Patching this file's own imported
# names would leave the composition calling the real ones.
from app.services.recurrence import _reading
from app.services.recurrence_engine import compute_due_date

#: The cadence ``seed_periods_today`` builds and every figure below is
#: hand-computed at: 14 days between paydays, 26 a year.  An explicit input
#: since plan step R7a-2a, where the per-paycheck column read a hardcoded
#: ``MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR`` ratio.
_BIWEEKLY = PayCadence(cadence_days=14)


# ── Helpers ──────────────────────────────────────────────────────────


def _calendar(periods):
    """Return the owner's schedule as the surface reads it.

    ``recurring_view.build_view`` takes a
    :class:`~app.services.pay_calendar.PayCalendar` rather than a period list
    since plan step R4b-1: a recurrence's next date is measured against the
    OWNER's schedule, so the surface has to be handed that schedule rather
    than rebuild one per row.

    **Loaded through the one door since plan step C2-b2**, rather than built
    from the rows the caller happens to hold.  ``calendar_for`` reads the whole
    payday set and the owner's cadence and derives the rest, so the calendar a
    test hands the surface is the one the route would.

    Args:
        periods: The owner's pay periods -- read only for whose they are.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for their owner.
    """
    return calendar_for(periods[0].user_id)


def _create_rule(seed_user, pattern_enum, *, interval_n=1,
                 day_of_month=None, month_of_year=None, end_date=None):
    """Author and flush a RecurrenceRule for the seed user.

    Through the write door since plan step R7c-b, which made the two-axis
    columns NOT NULL: a rule naming only a pattern no longer produces a row.
    """
    return make_pattern_rule(
        seed_user["user"].id, pattern_enum,
        interval_n=interval_n,
        fires_on_day=day_of_month,
        fires_in_month=month_of_year,
        end_date=end_date,
    )


def _create_txn_template(seed_user, rule, amount, *, type_enum, name):
    """Create and flush an income or expense TransactionTemplate."""
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id if rule else None,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        name=name,
        default_amount=amount,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


def _create_expense(seed_user, rule, amount, *, name="Expense"):
    """Create and flush an expense TransactionTemplate."""
    return _create_txn_template(
        seed_user, rule, amount, type_enum=TxnTypeEnum.EXPENSE, name=name,
    )


def _create_income(seed_user, rule, amount, *, name="Income"):
    """Create and flush an income TransactionTemplate."""
    return _create_txn_template(
        seed_user, rule, amount, type_enum=TxnTypeEnum.INCOME, name=name,
    )


def _create_savings(seed_user, name="Test Savings"):
    """Create and flush a savings Account for the seed user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()
    return account


def _create_transfer(seed_user, rule, amount, to_account, *, name="Transfer"):
    """Create and flush a recurring TransferTemplate."""
    tmpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=amount,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    return tmpl


# ── Both-units equivalents ───────────────────────────────────────────


class TestUnitEquivalents:
    """Monthly and per-paycheck equivalents for each cadence."""

    def test_biweekly_both_units(self, seed_user, seed_periods_today):
        """A $100 every-paycheck expense: monthly = 100 * 26 / 12 = $216.67,
        per-paycheck = that monthly re-expressed = exactly $100.00.
        """
        rule = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        tmpl = _create_expense(seed_user, rule, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )
        row = view.expenses.rows[0]
        # Hand-computed at 26 paychecks a year: 100 * 26 / 12 = 216.6667 ->
        # 216.67, and 216.6667 * 12 / 26 collapses back to the original 100.00.
        assert row.equivalent.monthly == Decimal("216.67")
        assert row.equivalent.per_paycheck == Decimal("100.00")

    def test_monthly_both_units(self, seed_user, seed_periods_today):
        """A $500 monthly expense: monthly = $500.00,
        per-paycheck = 500 * 12 / 26 = 230.7692... -> $230.77.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=15,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("500.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 500 * 12 / 26 = 230.7692... -> 230.77.
        assert row.equivalent.monthly == Decimal("500.00")
        assert row.equivalent.per_paycheck == Decimal("230.77")

    def test_annual_both_units(self, seed_user, seed_periods_today):
        """A $1,200 annual expense: monthly = 1200 / 12 = $100.00,
        per-paycheck = 1200 / 26 = 46.1538... -> $46.15.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.ANNUAL,
            day_of_month=1, month_of_year=6,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("1200.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 1200 / 12 = 100.00 a month; 100 * 12 / 26 =
        # 46.1538... -> 46.15 a paycheck.
        assert row.equivalent.monthly == Decimal("100.00")
        assert row.equivalent.per_paycheck == Decimal("46.15")


# ── Subtotals and the aggregator SSOT ────────────────────────────────


class TestSubtotals:
    """Section subtotals stay identical to the canonical aggregator."""

    def test_subtotal_matches_committed_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The expense section's monthly subtotal equals
        ``committed_monthly`` for the same templates, so the unified surface
        and /savings can never disagree.

        $100 biweekly (216.67) + $500 monthly (500.00) = $716.67.
        """
        rule_bw = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        rule_mo = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=15,
        )
        e1 = _create_expense(seed_user, rule_bw, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, rule_mo, Decimal("500.00"), name="B")
        as_of = date.today()

        view = recurring_view.build_view(
            [], [e1, e2], [], _calendar(seed_periods_today), as_of,
        )
        assert view.expenses.subtotal.monthly == Decimal("716.67")
        assert view.expenses.subtotal.monthly == committed_monthly(
            [e1, e2], as_of, _calendar(seed_periods_today),
        )

    def test_subtotal_per_paycheck_derives_from_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The per-paycheck subtotal is the full-precision monthly total
        re-expressed per paycheck.

        Full monthly total = 100*26/12 + 500 = 716.6667;
        per-paycheck = 716.6667 * 12 / 26 = 330.769... -> $330.77.
        """
        rule_bw = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        rule_mo = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=15,
        )
        e1 = _create_expense(seed_user, rule_bw, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, rule_mo, Decimal("500.00"), name="B")

        view = recurring_view.build_view(
            [], [e1, e2], [], _calendar(seed_periods_today), date.today(),
        )
        # Hand-computed: (100 * 26 / 12) + 500 = 716.6667 a month at full
        # precision, then * 12 / 26 = 330.7692... -> 330.77 a paycheck.
        assert view.expenses.subtotal.per_paycheck == Decimal("330.77")

    def test_empty_section_subtotal_is_zero(self, seed_user, seed_periods_today):
        """A section with no templates subtotals to $0.00 in both units."""
        view = recurring_view.build_view(
            [], [], [], _calendar(seed_periods_today), date.today(),
        )
        assert view.expenses.rows == ()
        assert view.expenses.subtotal.monthly == Decimal("0.00")
        assert view.expenses.subtotal.per_paycheck == Decimal("0.00")


# ── Non-recurring rows: present but blank, excluded from totals ───────


class TestNonRecurringRows:
    """Non-repeating / expired definitions show as manageable rows
    but contribute nothing to any total (the management surface shows all
    active definitions; the totals are the /obligations kernel).

    "Does not repeat" is ``recurrence_rule_id IS NULL`` on both template
    kinds since plan step R2e-3.  These cases named a ``Once``-PATTERN rule
    before it, which is the second spelling that step removed."""

    def test_non_repeating_row_present_but_blank(
        self, seed_user, seed_periods_today,
    ):
        """A rule-less expense appears as a row with a blank equivalent
        and no next date, and adds $0 to the subtotal.
        """
        recurring = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")
        real = _create_expense(seed_user, recurring, Decimal("100.00"), name="Real")

        view = recurring_view.build_view(
            [], [once, real], [], _calendar(seed_periods_today), date.today(),
        )
        names = {row.template.name: row for row in view.expenses.rows}
        assert "OneTime" in names, "one-time definition must still be listed"
        once_row = names["OneTime"]
        assert once_row.equivalent.monthly is None
        assert once_row.equivalent.per_paycheck is None
        assert once_row.next_date is None
        assert once_row.share_pct is None
        # Only the real recurring expense counts toward the subtotal.
        # 100.00 * 26 / 12 = 216.666... -> 216.67
        assert view.expenses.subtotal.monthly == Decimal("216.67")

    def test_non_repeating_row_logs_no_unknown_pattern_warning(
        self, seed_user, seed_periods_today, caplog,
    ):
        """A rule-less definition emits no 'unknown pattern' warning.

        ``_next_occurrence`` returns on the rule-less branch before reaching
        the reverse matcher, which logged that warning for any pattern it had no
        branch for.  Until plan step R2e-3 a second guard was needed beside it
        for the ``Once`` pattern, which the matcher also had no branch
        for -- so a one-time definition logged it on EVERY render without one.
        """
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")

        with caplog.at_level(logging.WARNING):
            recurring_view.build_view(
                [], [once], [], _calendar(seed_periods_today), date.today(),
            )
        assert "Unknown recurrence pattern" not in caplog.text

    def test_no_rule_row_present_but_blank(self, seed_user, seed_periods_today):
        """A template with no recurrence rule appears with a blank equivalent."""
        tmpl = _create_expense(seed_user, None, Decimal("42.00"), name="NoRule")

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "NoRule"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")

    def test_expired_row_present_but_blank(self, seed_user, seed_periods_today):
        """An active template whose rule.end_date is in the past appears as
        a manageable row with a blank equivalent and no next date, and adds
        nothing to the subtotal (it is no longer a future commitment).
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            end_date=date.today() - timedelta(days=1),
        )
        tmpl = _create_expense(seed_user, rule, Decimal("1500.00"), name="Expired")

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "Expired"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")


# ── Summary band ─────────────────────────────────────────────────────


class TestSummaryBand:
    """The obligations-kernel band: income vs committed outflow."""

    def test_band_net_and_pct(self, seed_user, seed_periods_today):
        """Income $1,500 biweekly, expense $100 biweekly, transfer $500 monthly.

        income monthly   = 1500 * 26 / 12 = 3250.00
        expense monthly  = 100 * 26 / 12  = 216.67
        transfer monthly = 500.00
        net monthly      = 3250.00 - 216.67 - 500.00 = 2533.33
        expenses % income = 216.67 / 3250.00 * 100 = 6.6667 -> 6.7
        net per-paycheck = 1500.00 - 100.00 - 230.77 = 1169.23
        """
        savings = _create_savings(seed_user)
        rule_bw = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        rule_mo = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=1,
        )
        income = _create_income(seed_user, rule_bw, Decimal("1500.00"))
        expense = _create_expense(seed_user, rule_bw, Decimal("100.00"))
        transfer = _create_transfer(
            seed_user, rule_mo, Decimal("500.00"), savings,
        )

        view = recurring_view.build_view(
            [income], [expense], [transfer], _calendar(seed_periods_today), date.today(),
        )
        band = view.band
        assert band.income.monthly == Decimal("3250.00")
        assert band.expenses.monthly == Decimal("216.67")
        assert band.transfers_out.monthly == Decimal("500.00")
        assert band.net.monthly == Decimal("2533.33")
        assert band.expenses_pct_of_income == Decimal("6.7")
        # per-paycheck net from the rounded subtotals: 1500 - 100 - 230.77.
        assert band.net.per_paycheck == Decimal("1169.23")

    def test_pct_of_income_none_without_income(
        self, seed_user, seed_periods_today,
    ):
        """With no income, the expenses-percent-of-income chip is None."""
        rule = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        expense = _create_expense(seed_user, rule, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [expense], [], _calendar(seed_periods_today), date.today(),
        )
        assert view.band.expenses_pct_of_income is None
        assert view.band.income.monthly == Decimal("0.00")

    def test_empty_band(self, seed_user, seed_periods_today):
        """No definitions: every band figure is $0.00 and the pct is None."""
        view = recurring_view.build_view(
            [], [], [], _calendar(seed_periods_today), date.today(),
        )
        assert view.band.net.monthly == Decimal("0.00")
        assert view.band.net.per_paycheck == Decimal("0.00")
        assert view.band.expenses_pct_of_income is None


# ── Share of committed and default ordering ──────────────────────────


class TestSharesAndOrdering:
    """Per-row share bars and the cost-descending default order."""

    def test_share_pct(self, seed_user, seed_periods_today):
        """Two expenses: $100 biweekly (216.67) and $500 monthly (500.00),
        section total 716.6667.

        share A = 216.6667 / 716.6667 * 100 = 30.2326 -> 30.2
        share B = 500 / 716.6667 * 100      = 69.7674 -> 69.8
        """
        rule_bw = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        rule_mo = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=15,
        )
        _create_expense(seed_user, rule_bw, Decimal("100.00"), name="Small")
        _create_expense(seed_user, rule_mo, Decimal("500.00"), name="Big")

        view = recurring_view.build_view(
            [],
            _load_expenses(seed_user),
            [],
            _calendar(seed_periods_today),
            date.today(),
        )
        by_name = {row.template.name: row for row in view.expenses.rows}
        assert by_name["Small"].share_pct == Decimal("30.2")
        assert by_name["Big"].share_pct == Decimal("69.8")

    def test_rows_sorted_by_monthly_desc_then_nonrecurring_last(
        self, seed_user, seed_periods_today,
    ):
        """Rows land in monthly-cost-descending order, non-repeating last.

        The last row's amount (999.00) is the LARGEST, so ordering by amount
        would put it first; it sorts last because a rule-less definition has
        no monthly equivalent at all.
        """
        rule = _create_rule(seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=1)
        _create_expense(seed_user, rule, Decimal("300.00"), name="Mid")
        _create_expense(seed_user, rule, Decimal("900.00"), name="High")
        _create_expense(seed_user, rule, Decimal("100.00"), name="Low")
        _create_expense(seed_user, None, Decimal("999.00"), name="Once")

        view = recurring_view.build_view(
            [], _load_expenses(seed_user), [], _calendar(seed_periods_today), date.today(),
        )
        order = [row.template.name for row in view.expenses.rows]
        assert order == ["High", "Mid", "Low", "Once"]


# ── Engine-backed next dates ─────────────────────────────────────────


class TestNextDates:
    """Next occurrence is the recurrence engine's own due date."""

    def test_next_date_monthly_is_engine_due_date(
        self, seed_user, seed_periods_today,
    ):
        """A monthly-on-the-15th expense's next_date equals the engine's
        due date for the next matching period on or after today, and lands
        on the 15th.
        """
        today = date.today()
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=15,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), today,
        )
        next_date = view.expenses.rows[0].next_date
        # Independent engine recomputation of the contract.
        matched = [
            placement.period
            for placement in rule_occurrences(
                rule, _calendar(seed_periods_today),
            )
            if placement.period is not None
            and placement.period.end_date >= today
        ]
        expected = next(
            compute_due_date(rule, p)
            for p in matched
            if compute_due_date(rule, p) >= today
        )
        assert next_date == expected
        assert next_date >= today
        assert next_date.day == 15

    def test_next_date_every_period_is_future_period_start(
        self, seed_user, seed_periods_today,
    ):
        """An every-paycheck expense's next_date is a pay-period start on or
        after today (the current period's start is already past).
        """
        today = date.today()
        rule = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        tmpl = _create_expense(seed_user, rule, Decimal("50.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), today,
        )
        next_date = view.expenses.rows[0].next_date
        assert next_date is not None
        assert next_date >= today
        assert next_date in {p.start_date for p in seed_periods_today}


# ── Module-local loader (mirrors the route's active-template load) ────


def _load_expenses(seed_user):
    """Return the seed user's active expense templates ordered like the route.

    The producer applies the cost-descending default itself, so the incoming
    order only fixes the tie-break among non-recurring rows; sort by name for
    a deterministic starting point.
    """
    expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    return (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == seed_user["user"].id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.transaction_type_id == expense_id,
        )
        .order_by(TransactionTemplate.name)
        .all()
    )


# ── The cadence phrase, and the ONE read it comes from (plan step R7a) ─


class TestTheRecurrenceDescription:
    """Every row carries how it repeats, produced here rather than in Jinja."""

    def test_an_active_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The producer answers the phrase the cell renders.

        Until plan step R7a the Recurrence column was eight Jinja branches
        reading ``pattern_id`` / ``day_of_month`` / ``month_of_year`` off the
        rule -- columns plan step R7c drops.  The phrase is a function of what
        the recurrence MEANS against the owner's schedule, which a template
        holds neither of.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=22,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )

        assert view.expenses.rows[0].recurrence.cadence == "Monthly (day 22)"

    def test_a_rule_less_row_carries_none(self, seed_user, seed_periods_today):
        """"Does not repeat" is ``recurrence_rule_id IS NULL`` since R2e-3.

        ``None`` rather than a phrase, so the cell's "One-time" wording is a
        display decision and the producer states absence honestly.
        """
        tmpl = _create_expense(seed_user, None, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )

        assert view.expenses.rows[0].recurrence is None

    def test_the_closing_bound_reaches_the_row_as_its_own_line(
        self, seed_user, seed_periods_today,
    ):
        """The closing bound is carried as the cell's own second line.

        A worded phrase since plan step R7b-3 rather than a bare date, so the
        surface renders one string and the wording of a stop is decided in the
        producer beside the wording of a cadence.

        Both dates are LITERALS and so is the expectation.  Deriving the
        expected string with ``f"{end:%b}"`` would assert the locale-safe
        producer against the ``strftime`` it replaced, and could not fail for
        the reason it was written; a date built from ``today()`` would make
        the case's subject move with the calendar.  A bound in 2029 is past
        every fixture's horizon, which is what this asserts about -- the cell
        carries the stop whether or not the schedule reaches it.
        """
        end = date(2029, 9, 15)
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY,
            day_of_month=22, end_date=end,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _calendar(seed_periods_today), date.today(),
        )

        assert view.expenses.rows[0].recurrence.stops == "until Sep 15, 2029"

    def test_a_transfer_row_carries_one_too(
        self, seed_user, seed_periods_today,
    ):
        """The transfers section takes the same producer, not a second one."""
        savings = _create_savings(seed_user)
        rule = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        tmpl = _create_transfer(seed_user, rule, Decimal("50.00"), savings)

        view = recurring_view.build_view(
            [], [], [tmpl], _calendar(seed_periods_today), date.today(),
        )

        assert view.transfers.rows[0].recurrence.cadence == "Every paycheck"

    def test_each_rule_is_RESOLVED_exactly_once_per_build(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The phrase and the next date are two questions about ONE read.

        ``read_rule`` returns the resolved meaning and the placements
        together, so a row costs one ``resolve``.  Composing the two steps at
        the call site instead would resolve twice per row -- a second
        resolution point in one request.

        Patched at the DEFINITION site the composition calls
        (``_reading.resolve``), not at this module's imported name: patching a
        re-export proves only that the harness reads what it reads.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=22,
        )
        expense = _create_expense(seed_user, rule, Decimal("100.00"))
        income_rule = _create_rule(seed_user, RecurrencePatternEnum.EVERY_PERIOD)
        income = _create_income(seed_user, income_rule, Decimal("2000.00"))

        calls = []
        real_resolve = _reading.resolve

        def counting_resolve(spec, calendar):
            # The CADENCE identifies the call since plan step R7b, which is
            # what a spec carries now; the two definitions above are a monthly
            # bill and an every-paycheck income, so the two entries also say
            # the right rules were read.
            calls.append((spec.interval_n, spec.unit))
            return real_resolve(spec, calendar)

        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [income], [expense], [], _calendar(seed_periods_today),
            date.today(),
        )

        # The control must be shown to FIRE: if the patch missed, ``calls`` is
        # empty and "exactly two" would pass for the wrong reason.
        assert view.expenses.rows[0].recurrence is not None
        assert view.income.rows[0].recurrence is not None
        assert len(calls) == 2, (
            f"two rule-bearing definitions resolved {len(calls)} times; each "
            f"must be read once per build"
        )


class TestTheArchivedDrawer:
    """Archived definitions get a producer, not raw ORM handed to Jinja."""

    def test_an_archived_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The drawer shows how a definition repeated before it was archived."""
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.ANNUAL,
            day_of_month=1, month_of_year=11,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("400.00"))
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _calendar(seed_periods_today),
        )

        assert len(rows) == 1
        assert rows[0].template is tmpl
        assert rows[0].recurrence.cadence == "Yearly (Nov 1)"

    def test_a_rule_less_archived_row_carries_none(
        self, seed_user, seed_periods_today,
    ):
        """A non-repeating archived definition reads "One-time" in the cell."""
        tmpl = _create_expense(seed_user, None, Decimal("400.00"))
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _calendar(seed_periods_today),
        )

        assert rows[0].recurrence is None

    def test_it_preserves_the_callers_order(
        self, seed_user, seed_periods_today,
    ):
        """The drawer's own sort is the route's; the producer does not re-sort.

        Unlike the active sections, which apply the locked cost-descending
        default -- archived rows have no cost to sort by.
        """
        first = _create_expense(seed_user, None, Decimal("1.00"), name="Zed")
        second = _create_expense(seed_user, None, Decimal("900.00"), name="Abe")
        for tmpl in (first, second):
            tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [first, second], _calendar(seed_periods_today),
        )

        assert [row.template.name for row in rows] == ["Zed", "Abe"]

    def test_it_walks_no_occurrences(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """An archived definition generates nothing, so nothing is placed.

        It needs the cadence and not where rows land, so it takes the read
        door's FIRST step alone.  Computing placements and discarding them is
        the defect ledger row D26 names, one surface over.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=9,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("50.00"))
        tmpl.is_active = False
        db.session.flush()

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError(
                "the archived drawer walked occurrences it does not render"
            )

        monkeypatch.setattr(_reading, "occurrence_placements", fail_if_called)

        rows = recurring_view.build_archived_rows(
            [tmpl], _calendar(seed_periods_today),
        )

        assert rows[0].recurrence.cadence == "Monthly (day 9)"


class TestNoneMeansDoesNotRepeat:
    """One sentinel, one meaning, on the value the cell reads.

    ``recurrence is None`` renders "One-time".  ``resolved_recurrence`` also
    answers ``None`` for an owner with no pay periods, and letting the two
    share a sentinel would report a quarterly bill as a one-off -- on the
    surface whose whole job is to say how definitions repeat.  The empty
    schedule is unreachable through any application path (registration
    bootstraps period 0, ``truncate_pay_periods`` always keeps the period its
    ``keep_through_period_id`` names -- plan step C3-a, which replaced the
    ordinal wire key and with it the Marshmallow floor this sentence used to
    cite -- ``reset_pay_periods`` deletes and regenerates in one transaction),
    so it is refused rather than worded.

    **The empty calendars below carry a CADENCE, and that is deliberate** (plan
    step R7a-2a).  "No pay periods" and "no stated pay cadence" are two
    different states, and this class is about the first: an owner who HAS said
    how often they are paid but whose payday list is empty.  The second is
    refused one door earlier, by a different value, with a different exception
    -- see :class:`TestAnAbsentCadenceIsRefused`.  Building these fixtures with
    ``cadence_days=None`` would trip that earlier door and report a pass for a
    claim these cases never reached.
    """

    def test_an_empty_schedule_refuses_rather_than_reading_one_time(
        self, seed_user, seed_periods_today,
    ):
        """A repeating definition is never described as non-repeating."""
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.QUARTERLY,
            day_of_month=2, month_of_year=3,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("60.00"))
        empty = PayCalendar.from_paydays(
            paydays=(), cadence_days=14, user_id=seed_user["user"].id,
        )

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_view([], [tmpl], [], empty, date.today())

    def test_the_archived_drawer_refuses_it_too(
        self, seed_user, seed_periods_today,
    ):
        """Both row kinds reach the same discriminator."""
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=9,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("25.00"))
        tmpl.is_active = False
        db.session.flush()
        empty = PayCalendar.from_paydays(
            paydays=(), cadence_days=14, user_id=seed_user["user"].id,
        )

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_archived_rows([tmpl], empty)

    def test_a_rule_less_definition_still_answers_none(
        self, seed_user, seed_periods_today,
    ):
        """The control: the sentinel still means what it is supposed to.

        Without this, "raise whenever the resolution is absent" would pass the
        two cases above while breaking the only state ``None`` may describe.
        """
        tmpl = _create_expense(seed_user, None, Decimal("25.00"))
        empty = PayCalendar.from_paydays(
            paydays=(), cadence_days=14, user_id=seed_user["user"].id,
        )

        view = recurring_view.build_view([], [tmpl], [], empty, date.today())

        assert view.expenses.rows[0].recurrence is None
        assert recurring_view.build_archived_rows(
            [tmpl], empty,
        )[0].recurrence is None


class TestAnAbsentCadenceIsRefused:
    """No stated pay cadence -> no per-paycheck column (plan step R7a-2a).

    A DIFFERENT state from :class:`TestNoneMeansDoesNotRepeat`'s empty
    schedule, and refused one door earlier.  Every money figure this surface
    publishes exists in two units, and the second unit is "per paycheck" --
    which is unanswerable for an owner who has never said how often they are
    paid.  The alternative was to assume biweekly, which is precisely the
    hardcoded ``PAY_PERIODS_PER_YEAR = 26`` this step deleted: it would have
    reported a weekly-paid owner's commitments at double their per-paycheck
    cost with nothing on the page saying so.

    Unreachable through any application path -- registration writes the
    ``budget.pay_schedule`` row (plan step X-ad-a) and
    ``pay_schedule_service.resolve_cadence`` falls back to the last period's
    length for legacy owners -- so this is the control on a broken invariant,
    not on a user state.
    """

    def test_the_active_sections_refuse_an_owner_with_no_cadence(
        self, seed_user, seed_periods_today,
    ):
        """A rule-LESS definition is refused too, and that is the point.

        The row itself needs no conversion, but the section subtotal it lands
        in is published in both units, so the page cannot be rendered honestly
        without the cadence.  Pinning the rule-less case is what stops a later
        change from making the refusal conditional on there being a figure to
        convert -- which would leave the subtotal's own per-paycheck value
        computed against an assumed rhythm.
        """
        tmpl = _create_expense(seed_user, None, Decimal("25.00"))
        cadence_less = PayCalendar.from_paydays(
            paydays=(), cadence_days=None, user_id=seed_user["user"].id,
        )

        with pytest.raises(PayCalendarError, match="no pay cadence"):
            recurring_view.build_view(
                [], [tmpl], [], cadence_less, date.today(),
            )

    def test_the_archived_drawer_does_NOT_need_a_cadence(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The drawer has no money columns, so it must not read the cadence.

        **The asymmetry cannot be shown with a fixture, and an earlier draft of
        this test pretended otherwise.** It passed a normal calendar and
        asserted the drawer rendered -- which it would whether or not it read
        the cadence, so the control could not fail.  The two states that would
        distinguish it are both unconstructible: ``derive_periods`` refuses a
        non-empty payday set with ``cadence_days=None``, and an EMPTY calendar
        makes the drawer raise ``RecurrenceResolutionError`` for an unrelated
        reason (its sibling above pins that).

        So the cadence is made to REFUSE on a calendar that is otherwise
        healthy.  The drawer must still answer; ``build_view`` on the same
        calendar must not.  Both halves are asserted, because the first alone
        would pass if the property were removed from the value entirely.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.MONTHLY, day_of_month=9,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("25.00"))
        tmpl.is_active = False
        db.session.flush()
        calendar = _calendar(seed_periods_today)

        def _refuse(_self):
            raise PayCalendarError("the cadence was read")

        monkeypatch.setattr(PayCalendar, "cadence", property(_refuse))

        # The drawer words each rule's cadence phrase off the schedule -- which
        # needs paydays -- and converts no money, so it must not touch this.
        rows = recurring_view.build_archived_rows([tmpl], calendar)
        assert rows[0].recurrence.cadence == "Monthly (day 9)"

        # The control on the control: the active sections DO convert, so the
        # same planted refusal must reach them.  Without this half, deleting
        # ``PayCalendar.cadence`` outright would pass the assertion above.
        tmpl.is_active = True
        db.session.flush()
        with pytest.raises(PayCalendarError, match="the cadence was read"):
            recurring_view.build_view([], [tmpl], [], calendar, date.today())


class TestTheValuesCannotDisagree:
    """The pairs are checked, not merely documented."""

    def test_a_prepared_row_refuses_a_rule_without_its_reading(
        self, seed_user, seed_periods_today,
    ):
        """A docstring guarantee the generated ``__init__`` does not enforce
        is what ``OccurrencePlacement.__post_init__`` exists to stop repeating.
        """
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.EVERY_PERIOD,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("10.00"))

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, monthly_full=None, rule=rule, reading=None,
            )

    def test_a_prepared_row_refuses_a_reading_without_its_rule(
        self, seed_user, seed_periods_today,
    ):
        """The other direction, so the check is a biconditional not a guard."""
        rule = _create_rule(
            seed_user, RecurrencePatternEnum.EVERY_PERIOD,
        )
        tmpl = _create_expense(seed_user, rule, Decimal("10.00"))
        reading = read_rule(rule, _calendar(seed_periods_today))

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, monthly_full=None, rule=None, reading=reading,
            )
