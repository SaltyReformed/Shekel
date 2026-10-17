"""The live ``ci.yml`` as the tests of :mod:`tools.ci` name it: the file, the guard its
code-grading jobs carry, and the net that finds a step starting a suite.

The ONE home of each for ``test_ci_workflow.py`` (the wiring) and
``test_ci_skipped_suites.py`` (what the suites the guard skips import), which step
balance:X-er's L-2 split apart.  Standard library only, as this layer is
(``test_bottom_layer.py``), so the file is PARSED in ``conftest.py``, the one reader both
take as a fixture.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from tools.ci import arcs

#: The workflow file the tests read.
PATH = arcs.REPO / ".github/workflows/ci.yml"

#: The guard every code-grading job carries: ``ci_scope``'s answer, failing closed on a
#: missing output (``test_ci_workflow.py`` holds the workflow to it).
GUARD = "needs.scope.outputs.scope != 'registry-only'"

#: The suite wrapper's file name (:data:`tools.ci.arcs.WRAPPER`'s last part).  The nets look
#: for the NAME, so a step starting the wrapper by any path or through any shell
#: (``cd scripts && ./test.sh``) is caught, and a renamed wrapper renames them.
WRAPPER_NAME = PurePosixPath(arcs.WRAPPER).name


def names_a_suite(line: str) -> bool:
    """Whether a line of a step's ``run`` names pytest or the suite wrapper, however it
    starts it: the net of the derivation in ``test_ci_skipped_suites.py``."""
    return "pytest" in line or WRAPPER_NAME in line
