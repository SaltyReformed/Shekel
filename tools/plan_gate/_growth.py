"""The registries' GROWTH BOUND: one change may add at most 100 rows to one.

**Developer ruling balance:R-BAL128 (2026-09-23), and what it replaced.**
``rulings.md``, ``steps.md`` and ``ledger.md`` carry no LINE cap
(conventions.md rule 4): one line per thing, so a line cap caps how many
decisions the project may take, steps it may decompose, defects it may
measure.  Each carried instead an absolute "runaway backstop" -- 600 rulings,
400 steps, 400 findings -- whose only job was to catch a duplicated table or a
generator loop, each set "far above" its registry's size on the day and each
said to be unable to bind on real work.  Real work reached the first one: the
ruling registry grew from 106 rows to 589 between 2026-08-27 and 2026-09-23,
about eighteen a day, and a tick filing its rulings stopped at 600.  A fixed
total on a table that grows with the work is the dropped line cap again under
another name, so all three were replaced with ONE bound on GROWTH per change.
A duplicated table is already refused by the one-id-one-row arms
(:func:`_registry.unique_key_violations`, :func:`_rulings.key_violations`);
what a bound per change adds is the loop that mints NEW ids, and it never
binds on how big a registry has become.

**The bound.**  A change may add at most :data:`GROWTH_BOUND` rows to any one
registry, counted NET: the rows the registry holds after the change less the
rows it held before.  A row removed and re-added -- moved, rewritten,
re-ranked, or archived in one place and filed in another -- therefore counts
once or not at all, never twice, and a change that drops 30 rows and files 120
adds 90.  Rows are counted exactly as each registry's own producer counts them
(:func:`_registry.ledger_rows`, :func:`_registry.step_rows`,
:func:`_rulings.ruling_rows`: :func:`_tables.rows_under` under that registry's
header), and a revision without the file holds none, so CREATING a registry
counts every row it is created with.  Only the REGISTRY'S table is counted:
``steps.md``'s forks and outcomes tables sit under other headers and are no
registry's rows, which the fixed totals did not count either.

**A MERGE is graded by the rows it adds OF ITS OWN**:
``rows(merge) - rows(first parent) - rows(second parent) + rows(their merge
base)``, the rows in the result that neither side brought.  The rows a merge
BRINGS were graded as commits on the side they came from, so a clean merge
counts zero however much its sides grew -- a ``dev -> main`` release carrying
days of ticks, a feature branch taking a far-ahead ``dev`` -- and what is left
is exactly what the ruling's "runaway script" names inside a merge: rows
written while it is being committed, by hand or by a script run to resolve a
registry conflict.  Three edges, each answered the CONSERVATIVE way, towards
firing where the formula has no answer: a merge whose registry is IDENTICAL
to every parent's added nothing and counts zero (the formula alone would
double-count a change both sides made the same way); a merge whose parents
share NO ancestor is graded against its first parent alone, as a non-merge
commit is (the formula with the base taken to be the second parent, so every
row the unrelated side brings counts); and an OCTOPUS merge is graded against
its first two parents and their base, so the rows every later head brings
count as its own.

**"A change" is graded in BOTH places the gate runs**, because an arm defined
for one of them grades nothing in the other:

(a) :func:`working_tree_growth_violations` -- the WORKING TREE against
    ``HEAD``, which is the commit about to be made when the pre-commit hook
    runs this package; while a merge is being committed (``MERGE_HEAD``
    exists, :func:`_shipped.graded_heads`), the working tree as a merge of
    ``HEAD`` and ``MERGE_HEAD``.  Under ``git commit --amend`` it is only the
    change on top of the commit being replaced, so two passes of 90 each pass
    here; the amended commit is graded WHOLE by (b), in CI.  In CI the tree
    IS ``HEAD``, so this arm is zero there by construction; that is what (b)
    is for.
(b) :func:`commit_growth_violations` -- every commit ``HEAD`` carries that
    its FIRST PARENT does not (``HEAD^1..HEAD``; ``HEAD`` alone when it has
    no parent): a non-merge commit against its parent, a merge by its own
    rows.  ``--full-history`` walks THROUGH each merge to the commits it
    brings.  **The range is derived from git, not handed in by the
    workflow**, so there is no variable to be missing, misspelled or stale,
    and the one rule is right in every place the gate runs.  In CI's
    ``pull_request`` checkout ``HEAD`` is GitHub's test merge of the pull
    request's head INTO its base, first parent the base -- a two-parent merge
    even when the base is an ancestor of the head, measured on PR #453's run
    (``6650e648 Merge 48f86b73 into 28fdfcc9``) -- so the range is exactly
    the pull request's own commits and that test merge.  On a push to
    ``main``, ``HEAD`` is the ``Merge pull request`` commit and the range is
    what that pull request brought.  Locally ``HEAD`` is the last commit,
    graded again harmlessly, or a resync merge, whose incoming commits are
    graded one by one.  **What this range rests on, stated**: that CI checks
    out the test merge (``actions/checkout``'s default for a pull request)
    and that ``main`` takes pull requests as MERGE commits.  A squash-merge
    would arrive as one non-merge commit and be graded whole.

**What CAN reach the bound on real work**: any one change that adds more than
100 rows to one registry -- a revert of an archive that removed more than 100,
for one.  That is the case rule 4 sends to the developer as a question.

**A revision the producer cannot read is measured by its TABLE LINES.**
:func:`_tables.rows_under` refuses a document whose table carries a different
header or holds a row that mis-splits, and history holds the first:
``steps.md``'s header took its present form at ``32193c16`` (2026-08-11), so
no revision before it has a table under today's header.  When any side of a
change is unreadable, EVERY side is counted as table lines instead (every
``|`` line: rows, headers and separators, in every table of the file), so a
header change counts the rows it added and not the whole table, and nothing is
skipped -- a loop that also broke a row is still counted.

**Measured over the whole history on 2026-09-23 with this module's own
counter**, before the arm existed: 441 non-merge commits had touched a
registry, 27 (commit, file) pairs had an unreadable side (all ``steps.md``,
at ``32193c16`` or before it), and exactly TWO added more than 100 rows, both
CREATING a registry -- ``ledger.md`` at ``6eeae53d`` (138) and ``rulings.md``
at ``b8f1c862`` (106; the ruling's text says 105).  The largest addition to a
registry that already existed was the lift's second half, 83 rulings at
``91f95f43``; after it, none exceeded 21.  Of the 667 merges that touched a
registry (no octopus, none without a merge base), none added more than 11 rows
of its own -- ``b1bf98d5`` and ``5b0fce02``, both to ``steps.md`` -- and the
reviewer's independent counter had found the same +11.

**Blindness is reported, not hidden.**  Both arms need git and (b) needs
history; on a checkout :func:`_shipped.history_is_gradeable` refuses, they
return nothing, as every arm in :mod:`_shipped` does, and
``test_shipped_against_git`` asserts the live checkout IS gradeable.
"""
from __future__ import annotations

import functools

import _registry as registry
import _rulings as rulings
from _shipped import git, graded_heads, history_is_gradeable
from _tables import LEDGER_HEADER, RULINGS_HEADER, STEPS_HEADER, cells, rows_under

#: Cited by every message below, so a failure sends the reader to the rule.
_RULE = "conventions.md rule 4; balance:R-BAL128"

#: The most rows one change may add to one registry, net.
#:
#: **The developer's number, not a fit to today's history** (balance:R-BAL128,
#: 2026-09-23): the largest real batch so far was the 105 rulings lifted when
#: ``rulings.md`` was created, once.  Raising it is a question for the
#: developer, as every cap here is (rule 4); a change that needs more is either
#: an accident or a decision.
GROWTH_BOUND = 100

#: Each bounded registry, by its path from the repository root (the spelling
#: ``git ls-tree --full-tree`` prints), with the header its producer reads it
#: by.  Derived from the producers' own path constants, so a registry that
#: moves moves here with it.
REGISTRIES = {
    path.relative_to(registry.REPO).as_posix(): header
    for path, header in (
        (rulings.RULINGS, RULINGS_HEADER),
        (registry.STEPS, STEPS_HEADER),
        (registry.LEDGER, LEDGER_HEADER),
    )
}


@functools.lru_cache(maxsize=512)
def _rows(text: str | None, header: tuple[str, ...]) -> int | None:
    """Return the registry's row count as its producer reads it, or ``None``.

    Cached because a merge's four sides mostly share their documents with the
    commits around it; the key is the text itself, so no entry can outlive
    what it counted.

    Args:
        text: The document at one revision, or ``None`` when that revision
            has no such file.
        header: The registry's header.

    Returns:
        ``0`` for an absent file, the row count when :func:`_tables.rows_under`
        can read it, and ``None`` when it refuses the document (another
        header, or a row that mis-splits).
    """
    if text is None:
        return 0
    try:
        return len(rows_under(text, header))
    except AssertionError:
        return None


def _table_lines(text: str | None) -> int:
    """Return how many lines of *text* are table lines, by the parser's own test.

    Args:
        text: The document at one revision, or ``None`` when absent.

    Returns:
        The number of lines :func:`_tables.cells` reads as a table line.
    """
    if text is None:
        return 0
    return sum(1 for line in text.splitlines() if cells(line) is not None)


def _signed_rows(
    terms: list[tuple[int, str | None]], header: tuple[str, ...],
) -> int:
    """Return the signed sum of each side's rows, every side counted ONE way.

    Args:
        terms: ``(sign, document)`` per side, the sign ``+1`` or ``-1`` and
            the document ``None`` where that revision has no such file.
        header: The registry's header.

    Returns:
        The sum by the producer's count -- or, when any side is unreadable by
        it, by :func:`_table_lines` on every side, so two counters never mix
        in one sum.
    """
    counts = [_rows(text, header) for _, text in terms]
    if None in counts:
        counts = [_table_lines(text) for _, text in terms]
    return sum(sign * count for (sign, _), count in zip(terms, counts))


def net_rows_added(
    before: str | None, after: str | None, header: tuple[str, ...],
) -> int:
    """Return the rows a change added to one registry, net.

    Args:
        before: The document before the change, ``None`` when absent.
        after: The document after the change, ``None`` when absent.
        header: The registry's header.

    Returns:
        Rows after less rows before; negative when the change removed rows.
    """
    return _signed_rows([(1, after), (-1, before)], header)


def merge_rows_added(
    merge: str | None,
    parents: tuple[str | None, str | None],
    base: str | None,
    header: tuple[str, ...],
) -> int:
    """Return the rows a merge added to one registry OF ITS OWN.

    Args:
        merge: The document the merge produced, ``None`` when absent.
        parents: The document in its first and in its second parent.
        base: The document in their merge base.  A caller whose parents share
            no ancestor passes the SECOND parent's document here, which makes
            the sum the result against its first parent alone.
        header: The registry's header.

    Returns:
        ``merge - first - second + base``; ``0`` when the merge's document is
        identical to both parents' (it added nothing, whatever the two sides
        did the same way).
    """
    first, second = parents
    if merge == first == second:
        return 0
    return _signed_rows(
        [(1, merge), (-1, first), (-1, second), (1, base)], header,
    )


@functools.lru_cache(maxsize=512)
def _blob_text(blob: str) -> str:
    """Return one blob's text; a blob is named by its content, so the cache is exact."""
    return git("cat-file", "blob", blob).stdout


def _documents(rev: str | None) -> dict[str, str]:
    """Return each registry's text at *rev*; a registry absent there is absent here.

    Args:
        rev: A revision, or ``None`` for one that does not exist (nothing
            committed, or a root commit's parent).

    Returns:
        ``{registry path: text}`` for the registries *rev* holds.

    Raises:
        AssertionError: When git cannot list *rev*'s tree -- a tree that
            cannot be read is not an empty one.
    """
    if rev is None:
        return {}
    listed = git("ls-tree", "--full-tree", rev, "--", *REGISTRIES)
    assert not listed.returncode, (
        f"git could not list {rev}'s tree ({listed.stderr.strip()}), so its "
        f"growth cannot be graded ({_RULE})"
    )
    documents = {}
    for line in listed.stdout.splitlines():
        meta, _, path = line.partition("\t")
        documents[path] = _blob_text(meta.split()[2])
    return documents


def _merge_base(first: str, second: str) -> str | None:
    """Return the best common ancestor of two revisions, or ``None`` when they share none."""
    found = git("merge-base", first, second)
    return None if found.returncode else found.stdout.split()[0]


def _message(change: str, added: int, rel: str, *, own: bool = False) -> str:
    """Return one violation, naming the change, the registry and the count."""
    rows = f"{added} rows of its own" if own else f"{added} rows"
    return (
        f"{change} adds {rows} to {rel}, over the {GROWTH_BOUND}-row "
        f"bound on one change.  A batch this size is a duplicated block or a "
        f"generator loop until shown otherwise: look for one first.  If the "
        f"rows are real work, the bound is a question for the developer and "
        f"is never raised without being asked for ({_RULE})"
    )


def _merge_violations(
    change: str, result: dict[str, str], heads: tuple[str, ...],
) -> list[str]:
    """Return one message per registry a merge adds more than the bound to, of its own.

    Args:
        change: How a message names the merge.
        result: The merge's documents.
        heads: Its parents, first parent first; the first two are read, so an
            octopus merge's later heads count as its own rows.

    Returns:
        The messages, in registry order.
    """
    first, second = _documents(heads[0]), _documents(heads[1])
    base_rev = _merge_base(heads[0], heads[1])
    base = _documents(base_rev) if base_rev else second
    problems = []
    for rel, header in REGISTRIES.items():
        added = merge_rows_added(
            result.get(rel), (first.get(rel), second.get(rel)), base.get(rel),
            header,
        )
        if added > GROWTH_BOUND:
            problems.append(_message(change, added, rel, own=True))
    return problems


def working_tree_growth_violations() -> list[str]:
    """Arm (a): the working tree may add at most the bound to each registry.

    Against ``HEAD``; while a merge is being committed, by the rows the merge
    adds of its own, against ``HEAD``, ``MERGE_HEAD`` and their merge base.

    Returns:
        One message per registry over :data:`GROWTH_BOUND`; empty when the
        checkout cannot be graded.
    """
    if not history_is_gradeable():
        return []
    tree = {
        rel: (registry.REPO / rel).read_text()
        for rel in REGISTRIES if (registry.REPO / rel).exists()
    }
    heads = graded_heads()
    if len(heads) > 1:
        return _merge_violations(
            "The merge being committed (the working tree against HEAD and "
            "MERGE_HEAD)", tree, heads,
        )
    head = git("rev-parse", "-q", "--verify", "HEAD^{commit}")
    before = _documents(None if head.returncode else "HEAD")
    problems = []
    for rel, header in REGISTRIES.items():
        added = net_rows_added(before.get(rel), tree.get(rel), header)
        if added > GROWTH_BOUND:
            problems.append(
                _message("The working tree, against HEAD,", added, rel),
            )
    return problems


def graded_commits() -> list[tuple[str, tuple[str, ...], list[str]]]:
    """Return the commits arm (b) grades: each with its parents and the registries it touched.

    Every commit ``HEAD`` carries that its first parent does not, limited to
    those that touched a registry.  ``--full-history`` is what walks through a
    merge to the commits it brings: without it git prunes a side whose merge
    left a path unchanged, and a side that added 150 rows and removed them
    again would never be listed.  A merge is listed when it differs from at
    least one parent on a registry; git names no paths for a merge, so it is
    graded on all three.

    Returns:
        ``(sha, parents, registries touched)`` per commit, newest first;
        ``parents`` is empty for a root commit and holds two or more for a
        merge.  Empty when ``HEAD`` does not resolve, since nothing is
        committed.

    Raises:
        AssertionError: When git cannot list the range -- a range that cannot
            be read is not an empty one.
    """
    if git("rev-parse", "-q", "--verify", "HEAD^{commit}").returncode:
        return []
    first = git("rev-parse", "-q", "--verify", "HEAD^1^{commit}")
    span = ("-1", "HEAD") if first.returncode else ("HEAD", "--not", first.stdout.strip())
    listed = git(
        "log", "--full-history", "--no-renames", "--name-only",
        "--format=%x00%H %P", *span, "--", *REGISTRIES,
    )
    assert not listed.returncode, (
        f"git could not list the commits HEAD brings ({listed.stderr.strip()}), "
        f"so their growth cannot be graded ({_RULE})"
    )
    commits = []
    for record in listed.stdout.split("\x00")[1:]:
        lines = [line for line in record.splitlines() if line.strip()]
        sha, *parents = lines[0].split()
        commits.append((sha, tuple(parents), lines[1:]))
    return commits


def commit_growth_violations() -> list[str]:
    """Arm (b): each commit HEAD brings may add at most the bound to each registry.

    A non-merge commit against its parent; a merge by the rows it adds of its
    own (:func:`merge_rows_added`).

    Returns:
        One message per (commit, registry) over :data:`GROWTH_BOUND`; empty
        when the history cannot be graded.
    """
    if not history_is_gradeable():
        return []
    problems = []
    for sha, parents, touched in graded_commits():
        subject = git("log", "-1", "--format=%s", sha).stdout.strip()
        after = _documents(sha)
        if len(parents) > 1:
            problems += _merge_violations(
                f"merge {sha[:9]} ({subject!r})", after, parents,
            )
            continue
        before = _documents(parents[0] if parents else None)
        for rel in touched:
            added = net_rows_added(before.get(rel), after.get(rel), REGISTRIES[rel])
            if added > GROWTH_BOUND:
                problems.append(_message(f"{sha[:9]} ({subject!r})", added, rel))
    return problems
