"""The arc list's one fact that is not a literal: where the repository is."""
from __future__ import annotations

from tools.ci import arcs


def test_the_repository_root_is_the_checkout_holding_this_package():
    """``tools/ci/arcs.py`` -> the root two levels above ``tools``.

    Moved here from what is now ``tools/quill/test__git.py``, whose ``repository_root``
    computed the same root a second time until step X-cx's L4.
    """
    assert (arcs.REPO / "tools" / "ci" / "arcs.py").is_file()
