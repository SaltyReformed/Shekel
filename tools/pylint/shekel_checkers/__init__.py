"""Project-specific pylint checkers for the Shekel budget app.

These AST checkers encode financial-correctness rules from
``docs/coding-standards.md`` that generic pylint does not cover, so the rules
are enforced by a deterministic tool instead of relying on a reviewer (human or
LLM) to remember them. They are loaded through ``.pylintrc`` (``load-plugins``),
so every pylint invocation -- the per-edit hook, the Stop-hook full run, CI, and
pre-commit -- applies them automatically.

This package is the plugin: pylint imports it and calls :func:`register`, which
registers every checker below. Each rule lives in its own module (one checker
per module, the shared AST primitives in ``_common``) so no single file grows
past pylint's module-size cap and each rule is read and tested in isolation.

Rules implemented:

* ``shekel-decimal-from-float`` (W9901, :mod:`.money`): flags ``Decimal``
  constructed from a float, which inherits binary float imprecision. Monetary
  values must be built from strings.
* ``shekel-refname-compare`` (W9902, :mod:`.refname`): flags comparing a
  ``.name`` attribute against a string literal. Reference-table name columns are
  for display only; logic must key off IDs or enums.
* ``shekel-disable-rationale`` (W9903, :mod:`.disable_rationale`): flags any
  ``# pylint: disable=`` that lacks a standard ``Pylint:`` why-comment in the
  mandated location (the docstring for a def/class-scoped directive, a comment
  immediately above otherwise). Keeps every suppression auditable with one grep.
* ``shekel-bare-money-quantize`` (W9904, :mod:`.money`): flags a ``.quantize()``
  of a cents quantum (``Decimal("0.01")`` / ``CENTS`` / ``TWO_PLACES``) with no
  explicit rounding mode, which silently uses Python's default
  ``ROUND_HALF_EVEN`` (banker's). ``app/utils/money.py`` mandates monetary
  rounding go through ``round_money`` (``ROUND_HALF_UP``); this locks the rule
  that the financial_calculations audit's E-26 / HIGH-04 remediation
  established.
* ``shekel-original-principal-as-balance`` (W9905, :mod:`.loan_balance`): flags
  passing a stored loan column (``original_principal`` / ``current_principal``)
  as the pre-first-payment / empty-schedule fallback to
  ``compute_loan_period_balance_map`` or ``balance_from_schedule_at_date``. That
  fallback must be the resolver-derived ``current_balance``; a stored column
  makes a loan's projected balance leap to its real value when the first payment
  lands -- the recurring net-worth defect fixed in F-21 / Commit 19 and PR #44.
* ``shekel-balance-producer-bypass`` (W9906, :mod:`.balance_seam`): flags any
  module OUTSIDE the ``app.services.balance_at`` seam and the engine cluster it
  composes from calling a balance producer directly -- or IMPORTING one by name
  (``from ... import balances_for as bf``), the aliased-import evasion that
  call-site name matching alone cannot see. The seam owns all four per-kind
  balance-at-T boundary rules (cash / loan / investment / property) in ONE
  tested place; a consumer re-inventing that boundary is how the
  loan/investment balance-bug family kept recurring across files for months
  (``docs/audits/balance_architecture/``). The rich projection-detail
  primitives ``project_balance`` and ``resolve_loan`` / ``resolve_account_loan``
  are NOT producers (they return ProjectedBalance / LoanState detail the seam
  composes) and stay callable by the chart and loan-route consumers.
* ``shekel-transaction-status-bypass`` (W9907, :mod:`.status_bypass`): flags
  writing ``status_id`` outside the transaction status seam. Every non-transfer
  ``Transaction.status_id`` change must funnel through
  ``status_seam.apply_status_change`` (the seam that verifies the transition,
  maintains ``paid_at``, and refreshes the eager ``status`` relationship) so the
  settled-state boundary is uniform and a confirmed settle can never be emitted
  twice or skipped (Build-Order Step 3's named highest risk). Only
  ``app.services.status_seam`` (the seam) and ``transfer_service`` (which mirrors
  ``status_id`` onto a transfer's two shadow ``Transaction`` rows, which a
  syntactic checker cannot tell from a real transaction) may write it. Four
  write forms are matched: direct assignment, the literal
  ``setattr(x, "status_id", ...)`` form, a ``status_id`` key or keyword in a
  bulk ``.update(...)`` / ``.values(...)`` call, and a ``Transaction(...)`` /
  ``Transfer(...)`` constructor ``status_id=`` kwarg whose value is not
  recognizably born-Projected (the 2026-07-02 adversarial review's H3:
  born-Projected was a convention three layers deep, not a machine rule). The
  status analog of the W9906 balance fence.

Deliberately NOT implemented as a checker: a blanket ``float()`` ban. The
codebase's real ``float()`` call sites are all legitimate (config timeouts that
are genuinely floats, and documented Decimal-to-float boundaries for Chart.js
JSON serialization). A static rule cannot distinguish a precision-losing
calculation from an end-of-pipeline serialization boundary without false
positives, so that judgment lives in the code-reviewer subagent instead.
"""

from .balance_seam import (
    _BALANCE_PRODUCERS,
    _BALANCE_SEAM_MODULES,
    _LOAN_LEDGER_READER_MODULES,
    _LOAN_LEDGER_READER_PRODUCERS,
    ShekelBalanceSeamChecker,
)
from .disable_rationale import ShekelDisableRationaleChecker
from .loan_balance import ShekelLoanBalanceSourceChecker
from .money import ShekelMoneyChecker
from .refname import ShekelRefNameChecker
from .status_bypass import _STATUS_SEAM_MODULES, ShekelTransactionStatusBypassChecker

# Re-exported so the plugin's import surface is identical to the pre-split
# single module: ``.pylintrc`` names the package in ``load-plugins`` and calls
# ``register``; the checker unit tests import the six classes and the five
# module/producer sets straight ``from shekel_checkers``.  The underscore-
# prefixed sets are internal-but-tested, listed here so re-export is explicit
# rather than an unused-import.
__all__ = [
    "_BALANCE_PRODUCERS",
    "_BALANCE_SEAM_MODULES",
    "_LOAN_LEDGER_READER_MODULES",
    "_LOAN_LEDGER_READER_PRODUCERS",
    "_STATUS_SEAM_MODULES",
    "ShekelBalanceSeamChecker",
    "ShekelDisableRationaleChecker",
    "ShekelLoanBalanceSourceChecker",
    "ShekelMoneyChecker",
    "ShekelRefNameChecker",
    "ShekelTransactionStatusBypassChecker",
    "register",
]


def register(linter) -> None:
    """Register the Shekel checkers with the pylint ``linter`` (plugin entry point).

    Called by pylint when this package is named in ``.pylintrc``'s
    ``load-plugins``. ``linter`` is the active PyLinter instance.
    """
    linter.register_checker(ShekelMoneyChecker(linter))
    linter.register_checker(ShekelRefNameChecker(linter))
    linter.register_checker(ShekelDisableRationaleChecker(linter))
    linter.register_checker(ShekelLoanBalanceSourceChecker(linter))
    linter.register_checker(ShekelBalanceSeamChecker(linter))
    linter.register_checker(ShekelTransactionStatusBypassChecker(linter))
