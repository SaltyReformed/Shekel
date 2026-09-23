"""The arms that grade ``steps.md`` against GIT, which nothing else here does.

**Every other module in this package reconciles the registries to EACH OTHER.**
``_registry`` grades what they say about one another -- owners, identity
classes, the dependency graph; ``_order`` grades the sequence; ``_rulings``
grades the ruling table.  All of it is documents against documents, and a
document set can be perfectly self-consistent and entirely wrong about the
code.  That is not a hypothetical: on 2026-09-10 ``balance:X-au-f-2`` shipped
at ``cb4239a2`` and ``steps.md`` went on ranking it ``#17`` as startable work,
with the balance README agreeing that it was open and **the gate passing
320/320 for a day**.  ``lessons.md`` already carried the half-lesson that names
why -- *two sides AGREEING is not evidence either* -- and here the two sides
were the index and the arc document, agreeing with each other and disagreeing
with ``dev``.

**Why the tick goes missing, measured rather than guessed.**
:func:`_registry.commit_column_violations` refuses a SHIPPED row carrying no
hash AND an open row carrying one, so the tick and the hash must be written in
ONE edit -- and a commit cannot contain its own hash.  The tick is therefore
STRUCTURALLY a second commit, and nothing requires it: over the whole corpus,
**126 of 126 ticks were written by a different commit than the one they cite,
with no exceptions**.  The cutover above even edited ``steps.md`` in its own
commit (it absorbed a leaf and corrected three counts) and still left the rank
standing, because re-planning a row is what makes it feel handled.

**These arms are a reconciler, and conventions rule 14 is explicit that a
reconciler is the SECOND-best answer** -- an invariant that cannot be violated
beats one something enforces.  The goal state is that ``steps.md`` stops
STORING the shipped set and derives it, at which point this module and the
``commit`` column delete together.  The claim that derivation would read is the
``Ships:`` trailer (:data:`_SHIPS`, ruling **R-BAL134**), and no commit on any
ref carried one when it was ruled (measured 2026-09-23).
**So every commit from before R-BAL134 is invisible to these arms**: a leaf
shipped under prose and never ticked is caught by nothing (two were, on one
unmerged branch, when it was ruled).  Nor can the arm tell whether the
convention has taken: a shipping commit without the trailer reads exactly as
one that shipped nothing.

**Ancestry is asked of HEAD, not of a named branch.**  The question an arm can
actually answer is *is this true of the tree I am grading*, which is right on
``dev``, on a feature branch off it, and in CI's detached checkout alike.
Resolving ``origin/dev`` instead would need a remote the grader may not have
and would answer the wrong question on a branch that has shipped a step and not
yet ticked it -- the one case these arms exist to catch.

**A merge being COMMITTED has two parents, and both are graded.**  While a
conflicted ``git merge origin/dev`` is resolved and committed, ``HEAD`` is
still the branch's old tip and dev's side is ``MERGE_HEAD``; the commit about
to exist descends from both.  The pre-commit hook runs these arms in exactly
that state, and the resolved ``steps.md`` rightly carries every tick dev made
-- so asking ``HEAD`` alone refuses each of them as *work this tree does not
carry*.  Measured on the first conflicted registry resync after this module
merged (2026-09-11, `fix/rec516-undated-row-skip` taking dev at `584b30fa`):
``balance:X-au-f-2``'s ``cb4239a2`` was an ancestor of ``MERGE_HEAD`` and not
of ``HEAD``, and the arm refused a merge that was correct.  A CLEAN merge
never reaches these arms locally: git commits it without running the
``pre-commit`` hook, so nothing local grades it and CI grades it at PR time --
a gap, not a reassurance.  **Installing git's ``pre-merge-commit`` hook type
would NOT close it with this fix as written**: measured 2026-09-11 on git
2.55, that hook runs on a clean merge with NO ``MERGE_HEAD`` (git writes merge
state only when it stops) and names the merged head only as a
``GITHEAD_<sha>`` environment variable, so :func:`graded_heads` would answer
``("HEAD",)`` and refuse every tick dev made -- this defect reborn on the
other hook type.  Closing the gap means teaching :func:`graded_heads` that
signal too, which is separate work.  :func:`graded_heads` is the one place
that decides which refs a grade is asked of, so the two arms cannot disagree
about it.
"""
from __future__ import annotations

import subprocess

import _registry as registry

#: Cited by every message below, so a failure sends the reader to the rule.
_RULE = "conventions.md rule 7"

#: The git trailer a commit claims a step with: one ``Ships: <arc>:<id>`` line
#: per step it SHIPS, in the message's trailer block (ruling **R-BAL134**).
#:
#: **Only the trailer block is read, and git parses it**, so no sentence is a
#: claim however it is worded.  The prose reading this replaced
#: (``Plan step <id>`` anywhere in the message) could not tell a claim from a
#: mention, and a message cannot be reworded once pushed, so a sentence that
#: only NAMED an open step wedged the gate on every branch carrying it --
#: ``ec1a5b28``, a C18 checkpoint, said the open ``recurrence:R22`` "deletes"
#: its stopgap and blocked C18's merge with ``dev`` -- and a list of phrasings
#: that excused a sentence (``does NOT tick its step``, ``WIP``) was the fence
#: grown around it.  A commit that does not ship a step now carries no
#: trailer, and there is nothing to excuse.  **A trailer binds once pushed,
#: as the sentence did**: it is a deliberate claim, so a leaf withdrawn or
#: reverted after its push still claims its step until the row is ticked, and
#: nothing here reads a revert.
#:
#: **The value is the step's KEY, exactly** (conventions.md rule 10): a bare
#: id or a trailing note names no step, and is dropped silently.  **The
#: trailer block is git's, not a line that looks like one**: it is the
#: message's LAST paragraph, and only when every line in it is a trailer (or a
#: quarter are and one is git-generated or configured, such as
#: ``Signed-off-by:``).  So ``Ships:`` goes in the SAME paragraph as
#: ``Co-Authored-By:`` -- written as a paragraph of its own above that block,
#: it is prose and names nothing.
_SHIPS = "Ships"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command at the repository root and return the finished process."""
    return subprocess.run(
        ("git", *args),
        cwd=registry.REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def history_is_gradeable() -> bool:
    """Return whether this checkout can answer the questions these arms ask.

    **A gate that silently passes when it cannot run is worse than no gate**,
    and this package's own history has the specimen: rule 7's hash arm
    documents that it grades a hash's FORM and not its existence because
    resolving one "would need git, and CI checks out shallow".  That reasoning
    is sound and the conclusion was to grade less; the other way out is to stop
    checking out shallow, which is one line of ``ci.yml``.  So the arms below
    report their own blindness instead of hiding it, and
    ``test_shipped_against_git`` asserts this predicate is TRUE on the live
    tree -- a shallow CI checkout fails that assertion rather than quietly
    grading nothing.
    """
    if _git("rev-parse", "--git-dir").returncode:
        return False
    return _git("rev-parse", "--is-shallow-repository").stdout.strip() != "true"


def graded_heads() -> tuple[str, ...]:
    """Return the refs whose union is the history of the commit being graded.

    Outside a merge that is ``HEAD``.  While a merge is being committed it is
    ``HEAD`` and ``MERGE_HEAD`` together, because the commit about to exist
    has both as parents -- see the module docstring for the resync this was
    measured on.  ``MERGE_HEAD`` is asked of git rather than looked for on
    disk because a worktree keeps it under ``.git/worktrees/<name>/``, and
    ``rev-parse`` resolves it from either layout.

    An OCTOPUS merge (three or more parents) is graded against its first
    merged head only, since ``rev-parse MERGE_HEAD`` answers the first line of
    that file.  Stated rather than handled: this history holds no such merge,
    and the house resync is a two-parent ``git merge origin/dev``.
    """
    if _git("rev-parse", "-q", "--verify", "MERGE_HEAD^{commit}").returncode:
        return ("HEAD",)
    return ("HEAD", "MERGE_HEAD")


def is_carried(sha: str, heads: tuple[str, ...] | None = None) -> bool:
    """Return whether ``sha`` is reachable from any ref the grade is asked of.

    ``heads`` lets an arm ask :func:`graded_heads` once and thread the answer
    through every row, rather than re-asking git per shipped row.
    """
    return any(
        not _git("merge-base", "--is-ancestor", sha, head).returncode
        for head in (heads or graded_heads())
    )


def _shipping_claims() -> dict[str, tuple[str, str]]:
    """Return ``{"<arc>:<id>": (sha, subject)}`` for each :data:`_SHIPS` trailer graded.

    **One ``git log`` over the whole history, reading ``%(trailers)``**,
    rather than ``git interpret-trailers --parse`` once per commit: this reads
    each message as git stored it, in one process.  ``interpret-trailers`` is
    built for a patch e-mail and ends the message at a ``---`` line, so the two
    disagree on a message holding one (measured 2026-09-23, git 2.55: a
    mid-body ``---`` with ``Ships: a:b`` at the end reads ``a:b`` here and
    nothing there).  ``key=`` matches case-insensitively and ``separator``
    keeps a commit's several claims apart.  ``git log`` lists a child before
    its parent on one line of history, so of two claims there the later is
    the one reported.

    Raises:
        RuntimeError: when ``git log`` fails.  The walk's empty output would
            otherwise read as "no commit claims anything" -- the gate passing
            on nothing.
    """
    walk = _git(
        "log", *graded_heads(),
        f"--format=%H%x01%s%x01%(trailers:key={_SHIPS},valueonly,separator=%x02)%x03",
    )
    if walk.returncode:
        raise RuntimeError(
            f"git log could not read the {_SHIPS}: trailers: {walk.stderr.strip()}",
        )
    claims: dict[str, tuple[str, str]] = {}
    for record in walk.stdout.split("\x03"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, rest = record.partition("\x01")
        subject, _, values = rest.partition("\x01")
        for value in values.split("\x02"):
            if value.strip():
                claims.setdefault(value.strip(), (sha, subject))
    return claims


def shipped_commit_violations() -> list[str]:
    """Rule 7: a SHIPPED row's hash resolves, and it is an ancestor of :func:`graded_heads`.

    Catches a tick citing a commit that never merged -- a branch abandoned, a
    hash mistyped, or a row carried across a rebase -- which reads as finished
    work in the one document a cold session is told to start from.

    Returns:
        One message per shipped row whose hash does not resolve or has not
        merged; empty when the history cannot be graded.
    """
    if not history_is_gradeable():
        return []
    heads = graded_heads()
    problems = []
    for row in registry.step_rows():
        if not row.shipped:
            continue
        sha = row.commit.strip().strip("`")
        if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode:
            problems.append(
                f"{row.key} is SHIPPED at `{sha}`, which is not a commit in this "
                f"repository.  A tick names the commit a reader can go and read "
                f"({_RULE})",
            )
            continue
        if not is_carried(sha, heads):
            problems.append(
                f"{row.key} is SHIPPED at `{sha}`, which is not an ancestor of "
                f"{' or '.join(heads)}.  The step is ticked against work "
                f"this tree does not carry ({_RULE})",
            )
    return problems


def unticked_leaf_violations() -> list[str]:
    """Rules 7 and 14: no commit in this tree claims a step the index calls open.

    **This is the arm the ``X-au-f-2`` miss exists for.**  A commit carrying
    ``Ships: <arc>:<id>`` is the shipping session's own statement that it
    built that step; if the row is still ranked, the tick it owed never came.
    Only that trailer is a claim (:data:`_SHIPS`).

    **CONTAINERS are exempt, and that is not a softening.**  A container's
    tick FOLLOWS its leaves' both ways: :func:`_order.starts_violations`
    refuses one left open after its last leaf ships
    (``_container_starts_problem``), and rule 13's sixth arm refuses a declared
    decomposed parent ticked while a leaf is open -- which every container row
    declares itself (30 of 30, 2026-09-23).  So the claim that ships a
    container is its last leaf's trailer, and a trailer naming the container
    while a leaf is open is one no tick could answer: grading it would block
    every branch carrying it.

    Returns:
        One message per open leaf a commit claims; empty when the history
        cannot be graded.
    """
    if not history_is_gradeable():
        return []
    claims = _shipping_claims()
    problems = []
    for row in registry.step_rows():
        if row.shipped or row.is_container or row.key not in claims:
            continue
        sha, subject = claims[row.key]
        problems.append(
            f"{row.key} is `{row.state}` in steps.md, but {sha[:9]} in this "
            f"tree carries `{_SHIPS}: {row.key}` -- {subject!r}.  A step that "
            f"ships is ticked in the same push, and its findings re-pointed "
            f"with it ({_RULE}, conventions.md rule 2)",
        )
    return problems
