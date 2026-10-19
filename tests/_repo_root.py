"""The checkout's root directory, for a test that names a file by its place in the tree.

A test that runs ``scripts/test.sh`` or reads a source file needs the root of
the checkout it runs in, and the only fact that gives it is where a file sits.
Spelled in the test itself, ``Path(__file__).resolve().parents[N]`` carries the
test's own depth in ``N``: move the test one directory deeper (``tools/split``
turns a module into a package, every part one level down) and the same line
computes another directory without complaint.  That is why ``tools/split``
refuses to move code that reads ``__file__``.

This module is the fact's ONE home.  It sits directly under ``tests/``, so
``parents[1]`` is the root by construction, and it does not move when its
importers do.  Moved or renamed itself, it breaks every importer LOUDLY, at
import, never with a wrong path.
"""
from pathlib import Path

#: The root of the checkout this module sits in: the parent of ``tests/``.
REPO_ROOT = Path(__file__).resolve().parents[1]
