"""Confirmed-ledger income statement (Build-Order Step 5).

Revenue and costs drawn from the append-only posting ledger for one window:
the Income-class and Expense-class chart accounts' presented natural balances
over the window, plus the derived net income.  Reads the baseline scenario only
(the deferred multi-scenario policy is R8's); a user with no baseline yields an
empty statement.

**Below that line sits the Unrealized section** (ruling **R-FO**, plan step
X-f3d): a modelled account's balance-assertion true-up books its counter leg
to a per-account Change in Value chart row, so investment return is
reported instead of vanishing into equity.  It is a class of its own precisely
so ``net_income`` cannot count it, and ``comprehensive_income`` is the sum the
statement states rather than leaves to the reader.

**Two window paths, one honesty rule.**  A ``"pay_period"`` window filters
``JournalEntry.pay_period_id`` directly (reader-contract C-2): the R2 storage
rule (a reversal carries the period of the postings it reverses) makes each
period's nets honest with no reader-side compensation, so residue cancels
itself.  A ``"month"`` / ``"year"`` window filters the display-timezone
attribution core by calendar date (C-3), so an early-settled source appears in
its actual paid month even when its pay period sits elsewhere -- both are honest
answers to different questions.

Transfers never appear on either path: both a transfer's legs land on linked
Asset/Liability accounts, outside the Income/Expense filter.  Flask-isolated,
read-only.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.services import spending_analysis
from app.services.scenario_resolver import require_baseline_scenario

from ._attribution import (
    StatementClassIds,
    build_section,
    dated_account_nets,
    load_chart,
    statement_class_ids,
)
from ._types import IncomeStatementReport, StatementWindow

_ZERO_MONEY = Decimal("0.00")


def compute_income_statement(
    user_id: int, window: StatementWindow,
) -> IncomeStatementReport:
    """Return the confirmed-ledger income statement for a user over a window.

    Income (revenue) and Expense (cost) lines from the baseline scenario's
    posted ledger, each natural-signed (positive = revenue / cost) and sorted by
    label, plus ``net_income = total_income - total_expense``; then, BELOW that
    line, the Unrealized section and ``comprehensive_income = net_income +
    unrealized.total`` (ruling **R-FO**).  A user with no baseline scenario
    yields an empty statement (zero totals) -- the deferred multi-scenario case
    is R8's.

    Args:
        user_id: The owner whose statement to compute.
        window: The :class:`StatementWindow` selector (a ``"pay_period"`` window
            filters the period directly; a ``"month"`` / ``"year"`` window
            filters the display-timezone attribution core by calendar date).

    Returns:
        The :class:`IncomeStatementReport`.

    Raises:
        ValueError: If the window is an invalid type or omits a field its type
            requires (via
            :func:`app.services.spending_analysis.validate_window`).
        PostingError: If a source with a nonzero net cannot resolve its
            attribution date (a broken linkage invariant -- from
            :func:`._attribution.dated_account_nets`).
    """
    spending_analysis.validate_window(
        window.window_type, window.period_id, window.month, window.year,
    )
    period = (
        db.session.get(PayPeriod, window.period_id)
        if window.window_type == "pay_period" else None
    )
    window_label = _window_label(window, period)
    class_ids = statement_class_ids()

    # Raises for a user with no baseline (see the balance sheet's twin for the
    # argument): this used to report an all-zero income statement, which reads
    # as "you earned and spent nothing this period" rather than "this ledger
    # cannot be read".
    scenario = require_baseline_scenario(user_id)
    chart = load_chart(user_id)
    if window.window_type == "pay_period":
        nets = _pay_period_statement_nets(
            user_id, scenario.id, window.period_id, class_ids,
        )
    else:
        nets = _calendar_statement_nets(
            user_id,
            scenario.id,
            spending_analysis.calendar_window_bounds(
                window.window_type, window.year, window.month,
            ),
            chart,
            class_ids,
        )
    return _income_statement_from_nets(nets, chart, class_ids, window_label)


def _window_label(window: StatementWindow, period: PayPeriod | None) -> str:
    """Return the human label for a window (matches the variance convention).

    Args:
        window: The window to label.
        period: The resolved :class:`~app.models.pay_period.PayPeriod` for a
            ``"pay_period"`` window (``None`` for calendar windows, or when the
            period id resolves no row).

    Returns:
        ``"Feb 21 - Mar 06, 2026"`` (pay period), ``"January 2026"`` (month),
        ``"2026"`` (year), or ``""`` when a pay-period window's period is
        missing.
    """
    if window.window_type == "pay_period":
        if period is None:
            return ""
        return (
            f"{period.start_date.strftime('%b %d')} - "
            f"{period.end_date.strftime('%b %d')}, {period.end_date.year}"
        )
    if window.window_type == "month":
        month_name = date(window.year, window.month, 1).strftime("%B")
        return f"{month_name} {window.year}"
    return str(window.year)


def _statement_class_set(class_ids: StatementClassIds) -> tuple[int, ...]:
    """Return the accounting classes the income statement reports on.

    Income and Expense (the operating result) plus Unrealized (ruling
    **R-FO**'s other comprehensive income, reported below the net-income
    line).  Stated once so the two window paths -- the pay-period SQL filter
    and the calendar fold -- select the SAME set: they answer different
    questions about which entries fall in the window, never different
    questions about which accounts belong on the statement.

    Args:
        class_ids: The resolved accounting-class ids.

    Returns:
        The class ref ids this statement sections, in presentation order.
    """
    return (class_ids.income, class_ids.expense, class_ids.unrealized)


def _pay_period_statement_nets(
    user_id: int, scenario_id: int, period_id: int, class_ids: StatementClassIds,
) -> dict[int, Decimal]:
    """Return per-reported-account debit nets for one pay period.

    The reader-contract C-2 path: sum the postings on the user's ledger
    accounts in the reported classes (:func:`_statement_class_set`) across
    every journal entry whose ``pay_period_id`` is *period_id* in
    *scenario_id*, grouped by ledger account.  Whole-entry by construction
    (the filter is on the entry's period), and R2-honest (a reversal lands in
    the period it reverses, so residue nets to zero here and drops out).

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        period_id: The pay period whose entries to sum.
        class_ids: The resolved accounting-class ids.

    Returns:
        ``{ledger_account_id: debit_net}`` over the nonzero reported accounts
        in the period.
    """
    rows = (
        db.session.query(
            Posting.ledger_account_id, db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.pay_period_id == period_id,
            LedgerAccount.class_id.in_(_statement_class_set(class_ids)),
        )
        .group_by(Posting.ledger_account_id)
        .all()
    )
    return {
        ledger_account_id: net
        for ledger_account_id, net in rows
        if net != 0
    }


def _calendar_statement_nets(
    user_id: int,
    scenario_id: int,
    bounds: tuple[date, date],
    chart: dict[int, LedgerAccount],
    class_ids: StatementClassIds,
) -> dict[int, Decimal]:
    """Return per-reported-account debit nets over a calendar window.

    The reader-contract C-3 path: fold the display-timezone attribution core
    (:func:`._attribution.dated_account_nets`) over the dates in *bounds*,
    keeping only the reported classes (:func:`_statement_class_set`).  Each
    source's whole net lands on its paid date, so a source settled early
    appears in its actual paid month -- and an anchor correction lands on the
    civil day its assertion was true for, which is what puts a value change in
    the month the balance moved.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        bounds: The inclusive ``(first_day, last_day)`` of the calendar window.
        chart: The user's chart (:func:`._attribution.load_chart`) -- supplies
            each account's class.
        class_ids: The resolved accounting-class ids.

    Returns:
        ``{ledger_account_id: debit_net}`` over the nonzero reported accounts
        in the window.
    """
    first_day, last_day = bounds
    reported = _statement_class_set(class_ids)
    nets: dict[int, Decimal] = defaultdict(lambda: _ZERO_MONEY)
    for (ledger_account_id, attribution_date), net in dated_account_nets(
        user_id, scenario_id,
    ).items():
        if not first_day <= attribution_date <= last_day:
            continue
        if chart[ledger_account_id].class_id in reported:
            nets[ledger_account_id] += net
    return {
        ledger_account_id: net
        for ledger_account_id, net in nets.items()
        if net != 0
    }


def _income_statement_from_nets(
    nets: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_ids: StatementClassIds,
    window_label: str,
) -> IncomeStatementReport:
    """Assemble the report from per-account debit nets.

    Builds the Income, Expense and Unrealized sections (label-sorted,
    natural-signed: revenue / cost / gain positive), derives net income from
    the first two, and comprehensive income from net income plus the third.

    **Net income is the operating result and the Unrealized total is NOT in
    it** (ruling **R-FO**): a change in what a holding is worth has not been
    sold into cash, so folding it in would let a house revaluation read as
    earnings.

    Args:
        nets: ``{ledger_account_id: debit_net}`` over the reported accounts.
        chart: The user's chart (empty for the no-baseline empty report).
        class_ids: The resolved accounting-class ids.
        window_label: The human window label.

    Returns:
        The assembled :class:`IncomeStatementReport`.
    """
    income = build_section(nets, chart, class_ids.income)
    expense = build_section(nets, chart, class_ids.expense)
    unrealized = build_section(nets, chart, class_ids.unrealized)
    net_income = income.total - expense.total
    return IncomeStatementReport(
        window_label=window_label,
        income=income,
        expense=expense,
        net_income=net_income,
        unrealized=unrealized,
        comprehensive_income=net_income + unrealized.total,
    )
