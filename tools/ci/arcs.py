"""The arcs, and each arc's planning document: the one home of both (rule 14).

Three tools read this.  The plan gate (``tools/plan_gate``) grades each arc's
document; ``ci_scope`` treats each document's directory as a planning-only
path; and the tracker tool (``tools/plan``) makes one label per arc and
accepts an arc only from :data:`ARCS`.  Until step X-cx's L4 the set was
spelled twice, here (then in ``tools/plan_gate/_registry.py``) and as the
tracker tool's own list, with nothing holding the two equal.  The map lives
beside CI because it outlives the plan gate: X-cx's cutover (L8) deletes the
gate's plan arms, and each arc's reasoning document stays in the repository.

Adding an arc is one entry here.  The tracker's labels follow on the next
``python -m tools.plan.setup_tracker --apply``; the plan gate's pre-commit
hook ``files`` pattern in ``.pre-commit-config.yaml`` is checked against this
map by ``tools/plan_gate/test_gate_coverage.py``.
"""
from __future__ import annotations

from pathlib import Path

#: The repository root (``tools/ci/arcs.py`` -> its root).
REPO = Path(__file__).resolve().parents[2]

#: The planning registries' directory, which also holds five of the six arc
#: documents.
PLANS = REPO / "docs" / "plans"

#: One arc document, by the slug its registry rows and its tracker label carry.
#: The order is the tracker's label order, which assigns each label its colour.
ARC_DOCS = {
    "balance": REPO / "docs/audits/balance_architecture/README.md",
    "recurrence": PLANS / "implementation_plan_recurrence_redesign.md",
    "pay_calendar": PLANS / "implementation_plan_pay_calendar.md",
    "credit_card": PLANS / "implementation_plan_credit_card.md",
    "bank_import": PLANS / "implementation_plan_bank_import.md",
    "salary": PLANS / "implementation_plan_salary.md",
}

#: The arcs, in :data:`ARC_DOCS`' order.
ARCS = tuple(ARC_DOCS)
