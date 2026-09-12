"""A handful of live ids that ``app/`` cites have their row in the registry they name.

This arm was ``TestTheGrossContractIsDocumented
.test_the_plan_identifiers_this_step_cites_actually_exist`` in
``tests/test_services/test_paycheck_calculator.py`` until 2026-09-11, when
``ci_scope.py`` gave the ``lint-and-test`` job a ``registry-only`` scope that
skips the application suite on a pull request touching only the planning
documents.  It was the ONE test in that suite whose outcome depends on
``docs/plans`` -- ``ci_scope.registry_readers`` measured it -- and its own
docstring says it exists to grade exactly the commit that scope describes.  A
test that grades the registries belongs in the package that grades the
registries, which runs in both scopes; keeping it where it was would have
meant either skipping it on the commit it was written for, or building the
test database to run one file read.  The docstring below is the original
with its first paragraph re-pointed (the "three cases above" are now in
another file, and "runs only when a planning document is edited" stopped
being true when this package joined every CI run); the rest is kept whole
because its history is the argument for the arm's shape.
"""
from __future__ import annotations

import pytest

import _registry as registry


@pytest.mark.parametrize("registry_name,ident", [
    ("rulings.md", "| balance | R-HW |"),
    ("rulings.md", "| balance | R-IA |"),
    ("rulings.md", "| balance | R-IF |"),
    ("ledger.md", "| salary | N-391 "),
    ("ledger.md", "| recurrence | N-399 "),
])
def test_the_plan_identifiers_this_step_cites_actually_exist(registry_name, ident):
    """A citation is only worth as much as the row it names.

    The three docstring cases beside this one in
    ``tests/test_services/test_paycheck_calculator.py`` pin STRINGS in
    docstrings, which is what they are for -- the superseded wording must not
    creep back. But a string pin cannot tell a recorded ruling from an
    invented one, and an adversarial review of that step found exactly that
    state: `R-HW` cited eighteen times from `app/` and `tests/` while
    `rulings.md` ended at `R-HO`, and `N-390` / `N-391` cited while
    `ledger.md` ended at `N-388`. The plan gate could not see it, because **it
    ran only when a planning document was edited** and the code commit edits
    none -- which is why the arm was born in the application suite, and no
    longer holds: ``ci.yml`` runs this package on every pull request.

    **This is deliberately scoped to the ids THIS step mints, and
    `tools/plan_gate/_rulings.py:135-141` says why the general arm cannot
    exist**: "an arc document may name no ruling id that has no
    `rulings.md` row" would fire on 88 live citations today, because an
    archived ruling's text stays in its archive. Scoped to a handful of
    live ids it is decidable, and it is the difference between grading the
    citation and grading the ruling.

    **`N-390` LEFT this list at plan step balance:X-bh-2, which closed
    it**, and the removal is the arm working rather than being weakened. A
    closed finding leaves `ledger.md` by design -- `ledger.md`'s own
    preamble says a row leaves when its fix SHIPS -- so pinning a closed id
    here would assert the opposite of the convention and fail forever. What
    replaced it was what that step LEFT live: `N-398`, `N-399` and the
    ruling pair `R-IA` / `R-IF`.  **`N-398` then LEFT it the same way at
    plan step `pay_calendar:C14-e-3`** (`ed267298`), which closed it: the
    removal is this arm working, and N-398's four `app/` citations stay put
    exactly as `N-390`'s five did.

    *It caught a real defect on the way out.* X-bh-2 committed its code and
    its plan documents separately, and the full suite ran against the
    documents in their PRE-change state -- so both commits were green alone
    and the pair was red. This case is the only thing in the corpus that
    would have said so, and it says it about the SECOND commit, which is
    the one no code-side gate looks at.
    """
    path = registry.PLANS / registry_name
    assert ident in path.read_text(encoding="utf-8"), (
        f"{ident.strip('| ')} is cited from app/ but has no row in "
        f"docs/plans/{registry_name}. conventions.md rules 1 and 9"
    )
