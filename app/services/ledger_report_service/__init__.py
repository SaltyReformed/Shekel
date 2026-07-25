"""Shekel Budget App -- Ledger Report Service (Build-Order Step 5).

Reads the append-only double-entry posting ledger into two financial
statements on the CONFIRMED ledger -- an income statement
(:func:`compute_income_statement`, pay-period AND calendar month/year windows)
and a balance sheet (:func:`compute_balance_sheet`, as-of a date, with a
trial-balance tie-out).  These are the read side of Step 5: once every non-loan
account posts its anchor-equity corrections the trial balance closes app-wide,
and these statements present that closed ledger.

## Package layout

* :mod:`._types` -- the frozen result shapes (``StatementWindow``,
  ``StatementLine``, ``TrialBalanceTieOut``, ``IncomeStatementReport``,
  ``BalanceSheetReport``); Decimal-only.
* :mod:`._attribution` -- the shared read core BOTH statements consume
  (``dated_account_nets`` + the chart load, label resolver, class-id
  sectioning, and natural-balance presentation), so the reader-contract
  attribution rule (M3 / L9) is defined once and the statements articulate
  automatically.
* :mod:`._income_statement` -- ``compute_income_statement``.
* :mod:`._balance_sheet` -- ``compute_balance_sheet``.

## The reader contract (M3, recorded durably)

Whole entries in, never lone legs (C-1); pay-period windows filter
``pay_period_id`` directly (C-2); calendar windows and as-of dates attribute
each source's net to its CURRENT paid date in the DISPLAY timezone, sourceless
corrections by ``entry_date``, hard-delete residue dropped whole (C-3);
presentation by natural balance via ``ref_cache.ledger_class_is_debit_normal``,
no ``-abs`` normalization (C-4); retained earnings derived, never posted (C-5).
See :mod:`._attribution` and ``docs/audits/balance_architecture/`` for the full
rule.

## Balance-seam posture

The report functions are statement AGGREGATES over the WHOLE chart, not the
single-account balance-at-T the seam owns -- a caller that needs one account's
balance at a moment routes to ``balance_at``, never to a statement.  That is
why these read the postings freely: a statement is the general ledger's own
presentation of what it recorded, and the sections articulate only because they
are read together (the trial balance ties).  Pulling ONE line out of a balance
sheet to answer "what is this account worth on date T" is the misuse this
posture exists to name -- that answer comes from the seam's fold, not from the
posting cache (plan Section 3).

**Not W9909-scoped, and that is a recorded gap, not a ruling** (finding N-35):
a new public function here answering balance-at-T would rate 10.00/10 with
every gate silent, exactly the N-28 shape the completeness registry closes for
the other ingredient packages.  Scoping it is its own step.

Reads only, Flask-isolated: plain ids in, frozen reports out; no writes, no
commit.
"""

from ._balance_sheet import compute_balance_sheet
from ._income_statement import compute_income_statement
from ._types import (
    BalanceSheetReport,
    IncomeStatementReport,
    StatementLine,
    StatementWindow,
    TrialBalanceTieOut,
)

__all__ = [
    "BalanceSheetReport",
    "IncomeStatementReport",
    "StatementLine",
    "StatementWindow",
    "TrialBalanceTieOut",
    "compute_balance_sheet",
    "compute_income_statement",
]
