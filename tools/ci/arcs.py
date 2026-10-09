"""The arcs and each one's document, steps heading and steps label: one table (rule 14).

Three tools read this.  The plan gate (``tools/plan_gate``) grades each arc's
document; ``ci_scope`` treats each document's directory as a planning-only
path; and the tracker tool (``tools/quill``) makes one label per arc and
accepts an arc only from :data:`ARCS`.  Until step X-cx's L4 the set was
spelled twice, here (then in ``tools/plan_gate/_registry.py``) and as the
tracker tool's own list, with nothing holding the two equal.  The map lives
beside CI because it outlives the plan gate: X-cx's cutover (L8) deletes the
gate's plan arms, and each arc's reasoning document stays in the repository.

Each document's steps HEADING joined the table at X-cx's L7: it was spelled only
in the plan gate's test specs, where the migration that lifts each step's entry
(:mod:`tools.ci.arc_steps`) into its card could not read it.

Adding an arc is one row here.  The tracker's labels follow on the next
``python -m tools.quill.setup_tracker --apply``; the plan gate's pre-commit
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

#: One row per arc: the slug its registry rows and its tracker label carry, its
#: document, the heading prefix of that document's steps section, and how a
#: message names that section.  The rows' order is the tracker's label order,
#: which assigns each label its colour.  The maps below are VIEWS of this one
#: table, so no two of them can disagree about which arcs exist, and a heading
#: and its name change on one line.
_ARC_TABLE = (
    ("balance", REPO / "docs/audits/balance_architecture/README.md", "## 5.", "Section 5"),
    ("recurrence", PLANS / "implementation_plan_recurrence_redesign.md", "## 4.", "section 4"),
    ("pay_calendar", PLANS / "implementation_plan_pay_calendar.md", "## 4.", "section 4"),
    ("credit_card", PLANS / "implementation_plan_credit_card.md", "## The steps", "The steps"),
    ("bank_import", PLANS / "implementation_plan_bank_import.md", "## The steps", "The steps"),
    ("salary", PLANS / "implementation_plan_salary.md", "## 4.", "section 4"),
)

#: One arc document, by its arc's slug.  The plan gate's ``_registry``,
#: ``_rulings``, ``_duplication`` and ``_census`` read a document's PATH here at
#: call time, which is what lets the gate's controls stage a defect by re-pointing
#: one entry of this dict (``tools/plan_gate/conftest.py``).  The per-document
#: specs in ``test_arc_documents.py`` capture theirs at import.
ARC_DOCS = {arc: path for arc, path, _, _ in _ARC_TABLE}

#: The heading prefix of each arc document's steps section
#: (:func:`tools.ci.arc_steps.section_span`), read by the plan gate's
#: per-document specs and by X-cx's migration.  It leaves with the steps
#: sections at L8.
STEPS_HEADINGS = {arc: heading for arc, _, heading, _ in _ARC_TABLE}

#: How a plan-gate message names each steps section (``PlanSpec.steps_label``),
#: on its heading's row so the two are edited together; nothing grades one against
#: the other.  It leaves with the steps sections at L8.
STEPS_LABELS = {arc: label for arc, _, _, label in _ARC_TABLE}

#: The arcs, in :data:`ARC_DOCS`' order.
ARCS = tuple(ARC_DOCS)
