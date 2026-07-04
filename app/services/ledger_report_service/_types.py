"""Frozen report shapes for the confirmed-ledger statements (Build-Order Step 5).

The plain-data results the reporting readers (:mod:`._income_statement`,
:mod:`._balance_sheet`) return, plus the :class:`StatementWindow` selector the
income statement is computed over.  Decimal-only, frozen, Flask-free: a route
builds a window, a reader returns a report, a template renders it -- no logic
lives here.

Every monetary field is presented in NATURAL-BALANCE terms (the reader-contract
C-4 rule): a debit-normal class (Asset, Expense) carries its debit-positive
posting sum as-is; a credit-normal class (Liability, Income, Equity) carries the
NEGATED sum, so a revenue line, a liability line, and an equity line all read
positive when the account holds its natural balance.  The signing is done once,
in the readers; consumers of these dataclasses read the already-natural value.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


# Pylint: ``duplicate-code`` -- incidental structural similarity with
# ``budget_variance_service.VarianceWindow`` (both are four-field frozen window
# selectors).  They are deliberately separate value objects in separate bounded
# contexts: a variance window attributes by a transaction's ``due_date`` while a
# statement window attributes by the confirmed ledger's paid-date basis, so a
# shared base would couple two unrelated attribution rules (coding-standards
# rule 13).  One-sided disable so ``VarianceWindow`` stays un-disabled,
# mirroring the journal_entry/transfer FK-block precedent.
# pylint: disable=duplicate-code
@dataclass(frozen=True)
class StatementWindow:
    """The time window an income statement is computed over.

    A discriminated selector mirroring
    :class:`app.services.budget_variance_service.VarianceWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for ``"month"``,
    ``year`` for ``"year"``.  A ``"pay_period"`` window filters
    ``JournalEntry.pay_period_id`` directly (reader-contract C-2); a
    ``"month"`` / ``"year"`` window filters the display-timezone attribution
    core by calendar date (C-3).  The two are deliberately separate value
    objects from ``VarianceWindow`` -- the variance tab attributes by a
    transaction's ``due_date`` while the statements attribute by the confirmed
    ledger's paid-date basis, so coupling them would couple two different
    attribution rules; the route layer shares only the parameter PARSING.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None
    # pylint: enable=duplicate-code


@dataclass(frozen=True)
class StatementLine:
    """One presented line of a statement: a chart account and its natural amount.

    A single Income / Expense / Asset / Liability / Equity ledger account's
    contribution to a statement, its ``amount`` already signed into
    natural-balance terms (see the module docstring).  ``ledger_account_id`` is
    the chart row the line reads from, or ``None`` for the DERIVED
    retained-earnings line on the balance sheet (which has no posted account --
    it is computed from the Income + Expense accounts, reader-contract C-5).

    Attributes:
        label: The display label -- a live ``category.display_name`` for a
            category row, the live ``account.name`` for a linked row, or the
            snapshot ``LedgerAccount.name`` for every other kind (see
            ``_attribution.ledger_account_label``).
        amount: The line's natural-balance value as a cent-quantized
            ``Decimal``.
        ledger_account_id: The ``budget.ledger_accounts`` id the line sums, or
            ``None`` for the derived retained-earnings line.
    """

    label: str
    amount: Decimal
    ledger_account_id: int | None = None


@dataclass(frozen=True)
class StatementSection:
    """One labeled group of statement lines with its total.

    A cohesive section of a statement -- Income / Expense on the income
    statement, Assets / Liabilities / Equity on the balance sheet -- pairing the
    (label-sorted, natural-signed, zero-net-dropped) lines with their summed
    ``total``, so a consumer reads a section as one unit and the total is
    derived once beside the lines it sums.

    Attributes:
        lines: The section's :class:`StatementLine` list, sorted by label.
        total: The sum of the section's line amounts (natural-balance terms).
    """

    lines: list[StatementLine]
    total: Decimal


@dataclass(frozen=True)
class TrialBalanceTieOut:
    """The balance sheet's two-part tie-out check.

    The confirmed ledger closes when BOTH: the presented Assets total equals
    the presented Liabilities-plus-Equity total (the accounting identity, with
    retained earnings closing Income + Expense into equity), AND the mechanical
    debit-positive net of every included posting is exactly zero (the
    double-entry self-check -- each entry sums to zero, and whole entries are
    included, so the total must be zero unless a raw unbalanced leg exists).
    ``in_balance`` requires both, so a classification/presentation bug is caught
    by the first and a raw-leg bug by the second.

    Attributes:
        assets: The presented total of the Asset section.
        liabilities_plus_equity: The presented Liabilities total plus the
            presented Equity total (Equity includes the derived retained
            earnings line).
        ledger_net: The mechanical sum of every included debit-positive posting
            net through the as-of date; ``Decimal("0")`` for a balanced ledger.
        in_balance: ``True`` iff ``assets == liabilities_plus_equity`` AND
            ``ledger_net == 0``.
    """

    assets: Decimal
    liabilities_plus_equity: Decimal
    ledger_net: Decimal
    in_balance: bool


@dataclass(frozen=True)
class IncomeStatementReport:
    """A confirmed-ledger income statement over one window.

    Revenue (Income-class accounts) and costs (Expense-class accounts) drawn
    from the confirmed posting ledger for one window, each as a
    :class:`StatementSection` (label-sorted lines + total).  ``net_income`` is
    ``income.total - expense.total``.  Transfers never appear (both their legs
    land on linked Asset/Liability accounts, which are not Income or Expense),
    so this is a pure operating statement.

    Attributes:
        window_label: The human label of the window (e.g. ``"January 2026"``).
        income: The Income section, natural (positive = revenue).
        expense: The Expense section, natural (positive = cost).
        net_income: ``income.total - expense.total``.
    """

    window_label: str
    income: StatementSection
    expense: StatementSection
    net_income: Decimal


@dataclass(frozen=True)
class BalanceSheetReport:
    """A confirmed-ledger balance sheet as of one date.

    The posted ledger's position as of ``as_of``: the Assets, Liabilities, and
    Equity :class:`StatementSection` s (each label-sorted, zero-net accounts
    dropped), the Equity section carrying the derived retained-earnings line
    (reader-contract C-5 -- never posted, computed from the Income + Expense
    accounts).  ``tie_out`` reports whether the ledger closes.  This is the
    POSTED ledger's statement: it excludes modeled growth / appreciation /
    interest between anchor true-ups (that is Net Worth's job, via the
    ``balance_at`` seam), so a footnote records the honesty boundary.

    Attributes:
        as_of: The evaluation date; nets attributed on or before it are folded.
        assets: The Asset section, natural (positive = held).
        liabilities: The Liability section, natural.
        equity: The Equity section (including the derived retained-earnings
            line), natural.
        tie_out: The two-part :class:`TrialBalanceTieOut`.
    """

    as_of: date
    assets: StatementSection
    liabilities: StatementSection
    equity: StatementSection
    tie_out: TrialBalanceTieOut
