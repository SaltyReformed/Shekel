"""Classify a pull request's change set for CI: ``registry-only`` or ``full``.

``ci.yml``'s ``lint`` job and six ``test`` shard jobs run pylint and the whole
pytest suite -- about ten minutes and seven runners since ``bank_import:X-gy``
split them, forty minutes on one runner before -- on every pull request, and a
registry pass (the ``docs/plans`` commit that ticks a step, files its findings
and records its rulings) is a pull request whose only grader is the plan gate.
The registry lane is serialized, one such PR at a time each computed on the
tree the last one left, so each of those minutes is on the queue's critical
path and grades nothing: no code changed, so no pylint message and no test
outcome can have moved.

This module says which change sets are that.  ``ci.yml``'s ``scope`` job asks
it once, and every code-grading JOB is skipped unless the answer is ``full``;
the plan gate itself (``pytest tools/plan_gate`` and its pylint
floor) runs in BOTH scopes, as do this package's own tests, the tracker
tool's, and the release tool's (``tools/release``, which reads ``steps.md``
as each commit holds it until the cutover), and the ``polyglot-lint`` job
(rumdl, typos and the rest) is untouched and still lints every Markdown file.
Approved by the developer 2026-09-11 as its own tooling PR; moved here from
``tools/plan_gate`` by step X-cx's L4.

**The boundary is DERIVED, not spelled.**  A registry-only change set may
touch only: the registries' directory (``docs/plans``, which also holds five
of the six arc documents, the ``historical/`` archive and ``STANDING.md``),
each arc document's own directory (balance's is
``docs/audits/balance_architecture``, with its ``archive/``), and the plan
gate's package (:data:`PLAN_GATE`), which grades them in both scopes.
:func:`registry_only_prefixes` reads the first two off :mod:`tools.ci.arcs`,
so a seventh arc's document extends the set with no edit here and no third
spelling beside ``tools.ci.arcs.ARC_DOCS`` and ``.pre-commit-config.yaml``'s
file list.  The plan gate grades the documents it names under those prefixes,
not every file in them: what makes the skip SOUND is not that everything
there is graded but that nothing the skip omits READS there, which is the
census below.

**This package is NOT a prefix.**  Until step X-cx's L4 the classifier lived
in the plan gate's package, so an edit to it was registry-only by where it
sat, not by design.  Here, a change to the code that decides what CI runs,
or to the arc list it reads, runs everything: a wrong ``full`` costs minutes,
a wrong ``registry-only`` a merge no test graded (decided by the
session under ruling ``balance:R-BAL207``).

**It fails CLOSED, twice.**  Here: an empty change set, an absolute path, a
``..`` and any path outside the prefixes all answer ``full``.  In ``ci.yml``:
every guard is ``!= 'registry-only'``, so a missing or misspelled output runs
everything rather than nothing, and the ``lint-and-test`` aggregate
(``ci_verdict``) lets a code grader skip only on exactly ``registry-only``.
The cost of a wrong ``full`` is a full run; the cost of a wrong
``registry-only`` is a merge no test graded.

**Its honesty rests on one census**, :func:`registry_readers`: no module in
a suite the ``registry-only`` scope SKIPS may read a registry-only path,
because a test's outcome would then move with a change the scope says moves
nothing.  Measured when this landed: exactly one did --
``TestTheGrossContractIsDocumented``'s id-citation arm in
``tests/test_services/test_paycheck_calculator.py``, whose docstring said
outright that it exists to grade the docs-side commit -- and it moved into
the plan gate's package as ``test_citations.py``, where it runs in both scopes.
``test_ci_scope.py`` holds the census at zero.  A module "in" a skipped suite
is every ``.py`` under the suite's root (but ``tests/manual``, below), and
the files pytest itself imports from ABOVE the root to run it: the
``conftest.py`` and ``__init__.py`` of each directory between the repository
root and the suite's (:func:`censused`; ``test_ci_skipped_suites.py`` grades
that set against pytest itself, each skipped suite started as ``ci.yml``
starts it).  A module named for pytest to import (``pytest_plugins``, ``-p``) is an
import, which neither census follows (below).  The
census is ``ast``-based and LEXICAL: a docstring that DISCUSSES
``docs/plans`` is prose and is skipped; a string a statement evaluates is a
read, in the spellings a read usually takes -- a joined literal, a
``/``-chain whose links are string pieces, ``Path(...)`` / ``PurePath(...)``
calls and ``.joinpath(...)`` /
``os.path.join(...)`` calls (nested inside one another or inside a chain),
an f-string's literal part, each with leading ``..`` segments dropped.  What
it CANNOT see, and what a reviewer of a new test must: a path assembled by
``str.format``, ``%`` or ``+``; a chain or call with a VARIABLE piece in the
middle (``REPO / "docs" / PLANS / "x.md"``, ``Path("docs", plans)``); a
walk from a parent directory (``(REPO / "docs").rglob("*.md")``); a
subprocess running a script that reads the registries itself (the
session-start hook, whose test is the one exemption); and a read in a module
OUTSIDE the census that a censused file imports, by a statement or by name
(``app/``, ``scripts/``; a ``pytest_plugins`` entry or a ``-p``): the census
grades what a censused file SPELLS, never what its imports read.  None
of those reaches a registry under the censused roots today (grepped when this
landed; the last re-measured 2026-10-16, the census run over ``app/``,
``scripts/``, ``migrations/`` and the checkers' package: no read).

**An import is a read too.**  Since step X-cx's L4 made ``tools/`` one package
tree, any test under ``tests/`` can ``from tools.ci.arcs import PLANS`` or
``from tools.plan_gate import _registry`` and read a registry without spelling
a path (review of 1e52a05ae, finding S1).  :func:`imports_of_the_tools` is the
second census: no module in a skipped suite imports :data:`TOOLS` at all, and
``test_ci_scope.py`` holds it at zero.  The rule names the whole package, not
the modules that read registries today, so no list of them can go stale; the
tools' own tests run in both scopes (``ci.yml`` step 4b), so a test that needs
a tool belongs there.  Like the path census it is LEXICAL, and what it CANNOT
see, a reviewer of a new test must: an import by string
(``importlib.import_module("tools...")``, ``__import__``), a plugin named in
``pytest_plugins``, a ``sys.path`` entry under ``tools/`` followed by a bare
import (``tools/pylint/tests`` reaches its checkers that way, and they read no
registry), and a module of ``app/`` or ``scripts/`` a test imports that imports
``tools`` itself (none does: grepped at step X-cx's L4, review of 46d9b578a).

Usage from ``ci.yml``, at the repository root::

    git diff --name-only --no-renames "${BASE}...${HEAD}" | python -m tools.ci.ci_scope

prints ``registry-only`` or ``full`` and exits 0 either way: this is a
classifier, and the gate it feeds is the workflow's ``if:``.
"""
from __future__ import annotations

import ast
import posixpath
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path, PurePosixPath

from tools.ci import arcs

REGISTRY_ONLY = "registry-only"
FULL = "full"

#: The plan gate's package, relative to the repository root.  A registry pass
#: may touch it (a tick can need one of the gate's constants), and the plan
#: gate job lints and tests it in both scopes.
PLAN_GATE = PurePosixPath("tools/plan_gate")

#: The test roots the ``registry-only`` scope does NOT run, so the roots
#: :func:`registry_readers` must find empty: the application suite (step 7 of
#: ``ci.yml``, with ``tests/test_performance`` under step 7b) and the custom
#: checkers' own tests (step 5b).  ``tools/plan_gate`` runs in both scopes and
#: is allowed to read what it grades.
SKIPPED_TEST_ROOTS = ("tests", "tools/pylint/tests")

#: EVERY ``.py`` under those roots is censused, not only what pytest collects:
#: a read in ``tests/_test_helpers.py`` or ``tests/oracles/`` is a read by
#: every test that imports it.  So are the ``conftest.py`` and the
#: ``__init__.py`` of every directory above a root up to the repository root,
#: which pytest imports to run the root's tests: the repository-root conftest
#: among them (:func:`censused`).  The one directory left out is ``tests/manual``:
#: its hand-run proof instruments (``verify_*``, ``measure_*``, ``rehearse_*``
#: and the like) read the registries BY DESIGN, pytest never collects them
#: (``pytest.ini`` pins ``python_files = test_*.py`` and the directory holds
#: no ``test_*.py`` or ``conftest.py`` -- ``test_ci_scope.py`` holds both
#: halves), nothing imports them (held too), and the only CI step that
#: touches them compiles rather than runs them (step 5a3).
NOT_CENSUSED = ("tests/manual",)

#: The repository's tooling package, which a skipped suite may not import: its
#: modules name the registry-only paths (the arc list, the plan gate) without a
#: string the path census could see (:func:`imports_of_the_tools`).
TOOLS = "tools"

#: What pytest WOULD collect if it appeared under :data:`NOT_CENSUSED`: the
#: patterns the premise above depends on.
COLLECTED_BY_PYTEST = ("test_*.py", "conftest.py")

#: The files pytest itself imports from a directory ABOVE a skipped root to run the root's
#: tests: its conftest, and the ``__init__.py`` that makes it a package (:func:`censused`).
#: A module a conftest names in ``pytest_plugins``, or ``-p`` names, is an import by name,
#: which the census does not follow (the module docstring's CANNOT-see lists).
ABOVE_A_ROOT = ("conftest.py", "__init__.py")

#: Reads the census finds that are NOT reads of this repository's documents,
#: keyed ``"<file>: <path>"`` (the hit without its line number, so an edit
#: above the read does not stale the key, and a DIFFERENT read in the same
#: file is still caught), each with the reason.  Held EXACTLY by
#: ``test_ci_scope.py``: an entry the census no longer finds is a finding too
#: (the ``useless-suppression`` shape), so the list cannot outlive what it
#: excuses.  A lexical census cannot tell ``linked / "docs" / "plans"`` under a
#: scratch repository from ``REPO / "docs" / "plans"``; this is where that
#: limit is written down.
EXEMPT_READERS = {
    "tests/test_hooks/test_session_start_standing_constraints.py: docs/plans": (
        "writes docs/plans/STANDING.md into the scratch repository its "
        "``checkouts`` fixture builds under tmp_path, so the hook under test "
        "reads it there; nothing is read from this repository"
    ),
}


def registry_only_prefixes() -> tuple[PurePosixPath, ...]:
    """Return the repo-relative directories a registry-only change set may touch.

    Read off :mod:`tools.ci.arcs` at call time (``PLANS`` and each
    ``ARC_DOCS`` parent) plus :data:`PLAN_GATE`, sorted so the answer is
    stable for a log line.
    """
    dirs = {arcs.PLANS, *(doc.parent for doc in arcs.ARC_DOCS.values())}
    prefixes = {PurePosixPath(d.relative_to(arcs.REPO).as_posix()) for d in dirs}
    prefixes.add(PLAN_GATE)
    return tuple(sorted(prefixes))


def _under_a_prefix(path: PurePosixPath, prefixes: tuple[PurePosixPath, ...],
                    *, strictly: bool) -> bool:
    """Return whether ``path`` lies under one of ``prefixes``.

    ``strictly`` refuses the prefix itself: a changed FILE is always below its
    directory, so the classifier asks strictly and a path that IS a prefix
    answers ``full``; the census asks inclusively, because ``"docs/plans"``
    is the piece a chain names before its leaf.
    """
    return any(
        path.is_relative_to(prefix) and not (strictly and path == prefix)
        for prefix in prefixes
    )


def scope_of(changed: Iterable[str]) -> str:
    """Return :data:`REGISTRY_ONLY` when every changed path is under a prefix, else :data:`FULL`.

    Blank lines are ignored; an empty change set is ``full``.  A path is
    graded whole (``docs/plans2/x`` is not under ``docs/plans``), and an
    absolute or ``..``-bearing one is ``full`` on sight.
    """
    paths = [line.strip() for line in changed if line.strip()]
    if not paths:
        return FULL
    prefixes = registry_only_prefixes()
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            return FULL
        if not _under_a_prefix(path, prefixes, strictly=True):
            return FULL
    return REGISTRY_ONLY


# ---------------------------------------------------------------- the census


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return the ids of every ``Constant`` that is a docstring."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            found.add(id(body[0].value))
    return found


#: The call spellings that assemble a path from their positional arguments:
#: ``Path("docs", "plans")`` and its pure cousins, and ``x.joinpath(...)`` /
#: ``os.path.join(...)`` where ``x`` may itself be a chain or call.
_PATH_CONSTRUCTORS = ("Path", "PurePath", "PurePosixPath", "PosixPath")
_PATH_JOINERS = ("join", "joinpath")


def _path_call_pieces(node: ast.Call) -> list[str] | None:
    """Return the string pieces a path-building call assembles, left to right.

    ``Path("docs", "plans", "x.md")`` gives its arguments; ``x.joinpath(...)``
    and ``os.path.join(x, ...)`` give the pieces of ``x`` (a chain, a call or
    a constant, recursively) followed by their string arguments.  ``None``
    when the call is neither shape or holds no string at all.  A variable
    piece is SKIPPED, not refused: ``Path(root, "docs", "plans")`` is the
    common spelling, and the docstring above names what that costs.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in _PATH_CONSTRUCTORS:
        head: list[str] = []
    elif isinstance(func, ast.Attribute) and func.attr in _PATH_JOINERS:
        head = _pieces_of(func.value) or []
    else:
        return None
    pieces = head + [arg.value for arg in node.args
                     if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
    return pieces or None


def _pieces_of(node: ast.expr) -> list[str] | None:
    """Return the string pieces one expression contributes to a path, or ``None``.

    A ``/`` chain contributes its links in order, a path-building call its
    pieces, a string constant itself; anything else (a name, a subscript, an
    f-string with no literal part) contributes nothing.  Recursive, so
    ``(root / "docs").joinpath("plans")`` and ``Path("docs") / "plans"`` both
    flatten whole.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _pieces_of(node.left) or []
        right = _pieces_of(node.right) or []
        return (left + right) or None
    if isinstance(node, ast.Call):
        return _path_call_pieces(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return None


def _inner_nodes(tree: ast.AST) -> set[int]:
    """Return the ids of every path expression nested inside a larger one.

    ``a / "docs" / "plans" / "x.md"`` parses left-nested, so ``ast.walk``
    visits three ``Div`` nodes; only the outermost holds the whole path, and
    the inner two would report its prefixes as further reads.  The same
    holds for a call inside a chain and a chain inside a ``.joinpath``.
    """
    inner: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            inner.update(id(child) for child in (node.left, node.right)
                         if isinstance(child, (ast.BinOp, ast.Call)))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _PATH_JOINERS):
            if isinstance(node.func.value, (ast.BinOp, ast.Call)):
                inner.add(id(node.func.value))
    return inner


def _spellings(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield ``(line, path text)`` for every string a statement evaluates as a path.

    A string constant on its own, and the joined pieces of every OUTERMOST
    path expression: a ``/`` chain, a ``Path(...)`` call, a ``.joinpath`` /
    ``os.path.join`` call, nested however.  A docstring is not a statement's
    operand and is skipped; an f-string's literal parts are constants and
    are seen.
    """
    docstrings = _docstring_nodes(tree)
    inner = _inner_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.lineno, node.value
        elif isinstance(node, (ast.BinOp, ast.Call)) and id(node) not in inner:
            pieces = _pieces_of(node)
            if pieces:
                yield node.lineno, "/".join(pieces)


def _names_a_registry_path(text: str, prefixes: tuple[PurePosixPath, ...]) -> bool:
    """Return whether ``text``, read as a path, starts under a registry-only prefix.

    A leading ``/`` is dropped first so the literal part of
    ``f"{ROOT}/docs/plans/steps.md"`` is graded as the relative path it
    completes; then ``posixpath.normpath`` folds interior ``..`` and ``.``
    (so ``"docs/../app/x.py"`` is ``app/x.py``) and every LEADING ``..``
    segment is dropped, so ``"../../docs/plans/steps.md"`` reads as the path
    it reaches from wherever it starts.  Segments only: ``"..docs"`` and
    ``".docs"`` are names, not climbs.  A prefix appearing MID-string (an
    assertion message that says "see docs/plans/steps.md") is prose, not a
    read, and does not count.
    """
    stripped = text.lstrip("/")
    if not stripped:
        return False
    parts = PurePosixPath(posixpath.normpath(stripped)).parts
    while parts and parts[0] == "..":
        parts = parts[1:]
    if not parts or parts[0] == "/":
        return False
    return _under_a_prefix(PurePosixPath(*parts), prefixes, strictly=False)


def censused(roots: Iterable[Path] = (), *, repo: Path | None = None) -> list[Path]:
    """Return every file the census reads for ``roots`` under the repository ``repo``, sorted,
    each once.  ``roots`` / ``repo`` as :func:`registry_readers` takes them.  Every census
    arm reads its files from here (:func:`_census`), so no arm can walk fewer.

    The set is a superset of the files pytest itself imports to run a skipped suite: every
    ``.py`` under a root except those under :data:`NOT_CENSUSED`, whether pytest imports it
    or a test does, and :data:`ABOVE_A_ROOT` in every directory from ``repo`` down to a
    root's parent.  pytest imports the ``conftest.py`` of every directory above a test down
    from its confcutdir (``_pytest/config/__init__.py``, ``_loadconftestmodules``), and,
    when a test below it runs, a directory's ``__init__.py`` to set that package up
    (``_pytest/python.py``, ``Package.setup``), so a read in either is a read by the tests
    under the root.  Under ``pytest.ini`` the conftest walk starts at the repository root;
    under ``-c /dev/null`` it starts above the repository, where no change set reaches and
    the census does not look.  Measured 2026-10-16, pytest 9.1.1: on this tree, step 5b
    loads the repository-root ``conftest.py`` and its first test call sets
    ``tools/__init__.py`` up; in a scratch repository holding this one's ``pytest.ini``, each
    skipped suite started as ``ci.yml`` starts it (steps 7, 7b and 5b) loads the
    repository-root ``conftest.py``, and step 5b sets the scratch ``tools/__init__.py`` up by
    ``Package.setup`` alone.  ``test_ci_skipped_suites.py`` re-measures the scratch half on
    every run.

    The census's DIRECTION: it grades what a censused file SPELLS, never what a module that
    file imports reads.  A file above a root is censused because pytest imports it, not
    because a censused file does; a module a censused file imports is censused only when it
    lies under a root itself.
    """
    base = repo or arcs.REPO
    roots = tuple(roots) or tuple(base / r for r in SKIPPED_TEST_ROOTS)
    skipped = tuple(base / d for d in NOT_CENSUSED)
    found: set[Path] = set()
    for root in roots:
        found.update(path for path in root.rglob("*.py")
                     if not any(path.is_relative_to(d) for d in skipped))
        found.update(path for directory in root.relative_to(base).parents
                     for name in ABOVE_A_ROOT if (path := base / directory / name).is_file())
    return sorted(found)


def imports_of_the_uncensused(roots: Iterable[Path] = (), *,
                              repo: Path | None = None) -> list[str]:
    """Return ``"<file>:<line>"`` for every censused module that imports from :data:`NOT_CENSUSED`.

    The directory is left out because nothing runs it AND nothing imports it;
    this is the control on the second half.
    """
    targets = set()
    for directory in NOT_CENSUSED:
        targets.add(directory.replace("/", "."))
        targets.add(directory.rsplit("/", 1)[-1])
    return _imports_of(roots, repo, frozenset(targets))


def imports_of_the_tools(roots: Iterable[Path] = (), *,
                         repo: Path | None = None) -> list[str]:
    """Return ``"<file>:<line>"`` for every censused module that imports :data:`TOOLS`.

    Same ``roots`` / ``repo`` contract as :func:`registry_readers`.
    """
    return _imports_of(roots, repo, frozenset({TOOLS}))


def _census(roots: Iterable[Path], repo: Path | None,
            hits_in: Callable[[str, ast.Module], Iterable[str]]) -> list[str]:
    """Return what ``hits_in`` finds in each file :func:`censused` names, in file order.

    The ONE reader every census arm consumes, so every arm reads the same files the same
    way: ``hits_in`` is handed each file's path relative to ``repo`` and its parsed tree.
    A file that will not parse is reported here, for every arm, as ``"<file>:<line>:
    SyntaxError"``: a census that skips what it cannot read passes on nothing.
    """
    base = repo or arcs.REPO
    hits: list[str] = []
    for path in censused(roots, repo=base):
        rel = path.relative_to(base).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            hits.append(f"{rel}:{exc.lineno or 0}: SyntaxError")
            continue
        hits.extend(hits_in(rel, tree))
    return hits


def _imports_of(roots: Iterable[Path], repo: Path | None,
                targets: frozenset[str]) -> list[str]:
    """Return ``"<file>:<line>"`` for every censused import naming one of ``targets``."""
    return _census(roots, repo, lambda rel, tree: [
        f"{rel}:{node.lineno}" for node in ast.walk(tree)
        if _imports_one_of(node, targets, PurePosixPath(rel).parent.parts)
    ])


def _imports_one_of(node: ast.AST, targets: frozenset[str], package: tuple[str, ...]) -> bool:
    """Return whether one import statement names one of ``targets``.

    Every dotted name the statement binds is tested on its own: ``import
    os, manual.x`` names ``manual.x``; ``from tests import manual`` names
    ``tests.manual``; ``from tests.manual.x import y`` names
    ``tests.manual.x``.  A name matches when it IS a target or starts under
    it; a look-alike (``manual_helpers``, ``mytests.manual``, ``toolsmith``)
    does not.  A relative import is first made absolute against ``package``
    (:func:`_absolute`), so ``from .ci import arcs`` in ``tools/__init__.py``
    names ``tools.ci.arcs``.
    """
    if isinstance(node, ast.Import):
        dotted = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        module = _absolute(node, package)
        if module is None:
            return False
        dotted = [f"{module}.{alias.name}" if module else alias.name
                  for alias in node.names]
        dotted.append(module)
    else:
        return False
    return any(
        name == target or name.startswith(f"{target}.")
        for name in dotted if name for target in targets
    )


def _absolute(node: ast.ImportFrom, package: tuple[str, ...]) -> str | None:
    """Return the module a ``from`` import names, made absolute against ``package``.

    ``package`` is the repository-relative directory the importing file lies in, as parts:
    the package a relative import starts from, for an ``__init__.py`` and a plain module
    alike.  Each dot past the first climbs one part (``from ..x import y`` in
    ``tests/sub/m.py`` names ``tests.x``).  ``None`` when the import climbs past the top
    package, which Python refuses, so the statement names nothing.
    """
    if not node.level:
        return node.module or ""
    if node.level > len(package):
        return None
    head = package[:len(package) - node.level + 1]
    return ".".join((*head, *(node.module.split(".") if node.module else ())))


def registry_readers(roots: Iterable[Path] = (), *, repo: Path | None = None) -> list[str]:
    """Return ``"<file>:<line>: <path>"`` for every registry-path read in a censused file.

    The files are the ones :func:`censused` names: those under ``roots``, which
    default to :data:`SKIPPED_TEST_ROOTS` under ``repo`` (the repository by
    default; a control passes a scratch tree), and those above the roots that
    pytest imports.  Paths are reported relative to ``repo``, in file order then
    line order.  Every ``(file, line, path)`` is reported once.  A file that
    will not parse is reported as a reader (``SyntaxError``, by :func:`_census`),
    because a census that skips what it cannot read passes on nothing.
    """
    prefixes = registry_only_prefixes()

    def reads_in(rel: str, tree: ast.Module) -> list[str]:
        found = {
            (line, text) for line, text in _spellings(tree)
            if _names_a_registry_path(text, prefixes)
        }
        return [f"{rel}:{line}: {text}" for line, text in sorted(found)]

    return _census(roots, repo, reads_in)


def unexempted_readers(roots: Iterable[Path] = (), *, repo: Path | None = None) -> list[str]:
    """Return the census less :data:`EXEMPT_READERS`, plus any exemption that excuses nothing.

    Same ``roots`` / ``repo`` contract as :func:`registry_readers`.
    """
    hits = registry_readers(roots, repo=repo)
    keys = {hit: exemption_key(hit) for hit in hits}
    live = [hit for hit in hits if keys[hit] not in EXEMPT_READERS]
    live.extend(
        f"{key}: exempted but the census finds no such read; delete the entry"
        for key in sorted(EXEMPT_READERS) if key not in set(keys.values())
    )
    return live


def exemption_key(hit: str) -> str:
    """``"<file>:<line>: <path>"`` -> ``"<file>: <path>"``.

    A ``SyntaxError`` hit keys as the whole hit, line included, so an
    unparseable file cannot be excused by a line-free entry: it is not a
    read to excuse but a file the census could not grade.
    """
    file, _, rest = hit.partition(":")
    _, sep, path = rest.partition(": ")
    if not sep or path == "SyntaxError":
        return hit
    return f"{file}: {path}"


def main(argv: list[str] | None = None) -> int:
    """Print the scope of the change set on stdin; ``--readers`` prints both censuses' hits."""
    args = sys.argv[1:] if argv is None else argv
    if args == ["--readers"]:
        for hit in unexempted_readers() + imports_of_the_tools():
            print(hit)
        return 0
    if args:
        print("usage: python -m tools.ci.ci_scope [--readers] < changed-paths", file=sys.stderr)
        return 2
    print(scope_of(sys.stdin))
    return 0


if __name__ == "__main__":
    sys.exit(main())
