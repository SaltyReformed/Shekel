"""Confirmed-ledger balance sheet (Build-Order Step 5).

The posted ledger's position as of a date: Assets, Liabilities, and Equity
sections folded from the display-timezone attribution core, plus the derived
retained-earnings line that closes Income + Expense into equity (reader-contract
C-5 -- never posted).  A two-part tie-out reports whether the ledger closes: the
presented accounting identity (Assets == Liabilities + Equity) AND the mechanical
double-entry self-check (every included debit-positive posting nets to zero).

This is the POSTED ledger's statement.  It reflects only asserted anchor facts
and settled activity; it excludes modeled growth / appreciation / interest
between anchor true-ups -- that projection is Net Worth's job, via the
``balance_at`` seam.  A future-dated opening (the rolling edge) is simply
excluded from an as-of-today sheet, and because whole entries attribute
together, excluding one keeps the tie-out closed.  Reads the baseline scenario
only (R8 owns the multi-scenario policy); a user with no baseline yields an empty
sheet with a green tie-out.  Flask-isolated, read-only.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.models.ledger_account import LedgerAccount
from app.services.scenario_resolver import require_baseline_scenario

from ._attribution import (
    StatementClassIds,
    build_section,
    dated_account_nets,
    load_chart,
    section_lines,
    statement_class_ids,
)
from ._types import (
    BalanceSheetReport,
    StatementLine,
    StatementSection,
    TrialBalanceTieOut,
)

_ZERO_MONEY = Decimal("0.00")
_RETAINED_EARNINGS_LABEL = "Retained Earnings"


def compute_balance_sheet(user_id: int, as_of: date) -> BalanceSheetReport:
    """Return the confirmed-ledger balance sheet for a user as of a date.

    Folds every posted source attributed on or before *as_of* into per-account
    cumulative positions, sections them by accounting class, derives retained
    earnings, and reports the two-part tie-out.  A user with no baseline scenario
    yields an empty sheet whose tie-out is green (0 == 0).

    Args:
        user_id: The owner whose balance sheet to compute.
        as_of: The evaluation date; sources attributed on or before it are
            folded (a future-dated source is excluded, whole).

    Returns:
        The :class:`BalanceSheetReport`.

    Raises:
        PostingError: If a source with a nonzero net cannot resolve its
            attribution date (a broken linkage invariant -- from
            :func:`._attribution.dated_account_nets`).
        BaselineMissingError: When the user has no baseline scenario, so this
            ledger cannot be read at all.  It used to return an EMPTY sheet
            instead: assets ``$0.00``, liabilities ``$0.00``, equity ``$0.00``
            and ``tie_out.in_balance = True`` -- the app ASSERTING that a
            user's books balance over a ledger it could not read (plan step
            X-v2's adversarial review; the same fabrication ruling R-CA deleted
            from the net-worth hero, one screen over).  A statement that cannot
            be produced is not a statement of zeros.
    """
    class_ids = statement_class_ids()
    scenario = require_baseline_scenario(user_id)
    chart = load_chart(user_id)
    cumulative = _cumulative_nets_through(user_id, scenario.id, as_of)
    return _balance_sheet_from_cumulative(as_of, cumulative, chart, class_ids)


def _cumulative_nets_through(
    user_id: int, scenario_id: int, as_of: date,
) -> dict[int, Decimal]:
    """Return per-account cumulative debit nets attributed on or before *as_of*.

    Folds the display-timezone attribution core
    (:func:`._attribution.dated_account_nets`) over the dates ``<= as_of``,
    accumulating each account's running debit-positive net.  Because a source's
    legs attribute together, this includes whole entries, so the cumulative over
    all accounts is exactly the trial balance through *as_of*.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        as_of: The inclusive fold bound.

    Returns:
        ``{ledger_account_id: cumulative_debit_net}`` (zero-net accounts
        included; the section builders drop them).
    """
    cumulative: dict[int, Decimal] = defaultdict(lambda: _ZERO_MONEY)
    for (ledger_account_id, attribution_date), net in dated_account_nets(
        user_id, scenario_id,
    ).items():
        if attribution_date <= as_of:
            cumulative[ledger_account_id] += net
    return dict(cumulative)


def _balance_sheet_from_cumulative(
    as_of: date,
    cumulative: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_ids: StatementClassIds,
) -> BalanceSheetReport:
    """Assemble the report from per-account cumulative nets.

    Sections the nets (Asset / Liability / Equity), appends the derived
    retained-earnings line to Equity, and computes the totals and two-part
    tie-out.

    Args:
        as_of: The evaluation date recorded on the report.
        cumulative: ``{ledger_account_id: cumulative_debit_net}`` through
            *as_of* (empty for the no-baseline empty sheet).
        chart: The user's chart (empty for the empty sheet).
        class_ids: The resolved accounting-class ids.

    Returns:
        The assembled :class:`BalanceSheetReport`.
    """
    assets = build_section(cumulative, chart, class_ids.asset)
    liabilities = build_section(cumulative, chart, class_ids.liability)
    equity = _equity_section(cumulative, chart, class_ids)

    liabilities_plus_equity = liabilities.total + equity.total
    # The mechanical double-entry self-check: the raw debit-positive net of every
    # included posting, independent of the class-based presentation above.  Zero
    # for a balanced ledger (whole entries each sum to zero); nonzero exposes a
    # raw unbalanced leg the section identity alone could miss.
    ledger_net = sum(cumulative.values(), _ZERO_MONEY)

    return BalanceSheetReport(
        as_of=as_of,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        tie_out=TrialBalanceTieOut(
            assets=assets.total,
            liabilities_plus_equity=liabilities_plus_equity,
            ledger_net=ledger_net,
            in_balance=(
                assets.total == liabilities_plus_equity and ledger_net == 0
            ),
        ),
    )


def _equity_section(
    cumulative: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_ids: StatementClassIds,
) -> StatementSection:
    """Return the Equity section, with the derived retained-earnings line last.

    The Equity accounts (:func:`._attribution.section_lines`) plus the derived
    retained-earnings line (reader-contract C-5) appended AFTER them so it reads
    as the section's closing line, with the total summing all of them.  Built
    directly rather than via :func:`._attribution.build_section` because that
    closing line is computed, not a posted account.

    Args:
        cumulative: ``{ledger_account_id: cumulative_debit_net}`` through the
            as-of date.
        chart: The user's chart.
        class_ids: The resolved accounting-class ids.

    Returns:
        The Equity :class:`StatementSection`.
    """
    lines = section_lines(cumulative, chart, class_ids.equity)
    lines.append(StatementLine(
        label=_RETAINED_EARNINGS_LABEL,
        amount=_retained_earnings(cumulative, chart, class_ids),
        ledger_account_id=None,
    ))
    return StatementSection(
        lines=lines,
        total=sum((line.amount for line in lines), _ZERO_MONEY),
    )


def _retained_earnings(
    cumulative: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_ids: StatementClassIds,
) -> Decimal:
    """Return the derived retained-earnings equity line (reader-contract C-5).

    The accumulated net income through the as-of date: the NEGATED cumulative
    debit net of every Income and Expense account (Income credits and Expense
    debits net to income, and equity presents credit-normal, so the natural
    line is the negated debit net).  Computed, never posted -- there are no
    closing entries in this ledger.

    Args:
        cumulative: ``{ledger_account_id: cumulative_debit_net}`` through the
            as-of date.
        chart: The user's chart, supplying each account's class.
        class_ids: The resolved accounting-class ids.

    Returns:
        The retained-earnings amount as a ``Decimal`` (positive when
        cumulatively profitable).
    """
    income_expense = (class_ids.income, class_ids.expense)
    net_income_debit = sum(
        (
            debit_net
            for ledger_account_id, debit_net in cumulative.items()
            if chart[ledger_account_id].class_id in income_expense
        ),
        _ZERO_MONEY,
    )
    return _ZERO_MONEY - net_income_debit
