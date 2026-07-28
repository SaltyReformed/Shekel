"""Pytest bootstrap for the Shekel pylint-checker unit tests.

Puts the plugin directory (``tools/pylint``) on ``sys.path`` so the tests can
``import shekel_checkers`` directly, matching how ``.pylintrc``'s ``init-hook``
makes the plugin importable for pylint itself.

It also isolates astroid's process-global module cache between tests -- see
:func:`_isolate_astroid_module_cache`, which is finding N-45's structural half
(balance plan step X-h).
"""

import sys
from pathlib import Path

import pytest
from astroid import MANAGER

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_astroid_module_cache():
    """Drop the astroid module registrations each test leaves behind.

    ``astroid.parse(source, module_name="a.b.c")`` does not merely build a
    tree: it REGISTERS the result in ``MANAGER.astroid_cache`` under that
    dotted name, so a later ``import_module("a.b.c")`` -- in a different test,
    in a different class -- resolves to the earlier test's synthetic module
    instead of raising or reading disk.  The cache is process-global mutable
    state, which the project's testing rule forbids sharing
    (``.claude/rules/testing.md``: tests are independent, no ordering, no
    shared mutable state).

    **It had already cost a green that was not earned (finding N-45).**
    ``TestShekelPackagePrivacyChecker::test_allows_seam_submodules_importing_each_other``
    asserted the privacy checker stays SILENT on an intra-package private
    import spelled with real ``app.services.balance_at`` names.  Under this
    directory's rootdir the real ``app`` package is not importable, so the
    checker's resolution fails, it fail-CLOSES (correctly, by design) and the
    assertion should have fired -- and it did, whenever the class ran alone.
    It passed in a whole-file run only because ``TestShekelBalanceSeamChecker``
    runs earlier in the same file and parses a synthetic module under the real
    dotted name ``app.services.balance_at._context``, warming the cache the
    later test then hit.  Measured at the repair, with the CI/pre-commit
    invocation (``pytest tools/pylint/tests -c /dev/null``): the whole
    directory 146 passed, that class alone 1 failed / 30 passed.

    The file's own fixture docstring already documented a CONVENTION for
    avoiding exactly this ("the fixture names are used ONLY as resolution
    targets, never as a parsed module's ``module_name``").  A convention is a
    discipline; this makes it a predicate, which is the whole argument of the
    plan step that added it.

    Only entries a test ADDED are removed, so on-disk resolutions and
    astroid's builtin bootstrap stay warm: measured ``0.46s -> 1.63s`` over
    the 146 tests, against ``13.4s`` for a full ``MANAGER.clear_cache()`` per
    test, which finds nothing this does not.
    """
    registered_before = set(MANAGER.astroid_cache)
    yield
    for name in set(MANAGER.astroid_cache) - registered_before:
        del MANAGER.astroid_cache[name]
