"""The plan gate's registry readers, as one public read-only door for X-cx's migration.

X-cx's migration (piece L7, ``tools/quill``) moves the plan in ``docs/plans/`` into the
tracker, and must read ``steps.md``, ``ledger.md`` and ``rulings.md`` through the ONE
reader each already has (CLAUDE.md rule 14), which live in this package's private
modules; ``shekel-private-module-import`` refuses another package's import of one.  So
this module re-exports them, and holds no logic and no name of its own (ruling
``balance:R-BAL244``, which amends card #8's "Nothing new is built into
``tools/plan_gate``" by this one exception).  It is DELETED at the cutover (L8) with the
readers it exposes; until then ``tools/quill`` depends on ``tools/plan_gate``, beside
R-BAL223's layering (both import ``tools/ci``, which imports neither).

A name is added here only with the coordinator's clearance.  ``outcome_scopes`` is read
for MEMBERSHIP only: R-BAL243 keeps the outcomes' priority in the board's order.
"""
from __future__ import annotations

from tools.plan_gate._classes import decomposition_leaf_keys, identity_class
from tools.plan_gate._order import outcome_scopes
from tools.plan_gate._plan_gate import NON_STEP_OWNERS, OWNER_RX, split_owners
from tools.plan_gate._registry import ledger_rows, step_rows
from tools.plan_gate._rulings import ruling_rows
from tools.plan_gate._tables import LedgerRow, RulingRow, StepRow

__all__ = [
    "NON_STEP_OWNERS",
    "OWNER_RX",
    "LedgerRow",
    "RulingRow",
    "StepRow",
    "decomposition_leaf_keys",
    "identity_class",
    "ledger_rows",
    "outcome_scopes",
    "ruling_rows",
    "split_owners",
    "step_rows",
]
