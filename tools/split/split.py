"""Split one test module into a package by a plan, and prove the move pure.

    python -m tools.split.split record <plan.json> --into <dir>
    python -m tools.split.split apply  <plan.json> --into <dir> --idmap <map.json>
    python -m tools.split.split prove  <plan.json> --into <dir> --idmap <map.json>

``record`` refuses the plan or measures the source BEFORE anything moves: a copy of it, its
collection and every test's outcome through ``./scripts/test.sh``, every fixture its tests set
up (refusing one whose reach the split would change), and pylint's messages for it and for
each module that imports it, whose text it fingerprints; each run writes into a directory
made for it, and its ``meta.json``, which names that directory, is written last, replacing
the last record's only once the run completed, and ``apply`` and ``prove`` refuse without
one.  ``apply`` writes
the package, deletes the source, re-points the importers, runs the static proof and writes
the node-id maps, as ONE unit: a failure anywhere in it, the proof's included, Ctrl-C, or a
``SIGTERM`` or ``SIGHUP`` undoes every write before it is reported, so no failure it
reports leaves an unproved split in the tree (:func:`tools.split._render.write`).
``prove`` runs the static proof again and every run arm, and prints the block a commit body
quotes when every arm passed.

A record is judged only in the repository state and the environment its observations were
made in (the coordinator's ruling on delta review 2, REVISED (B), 2026-10-15), and under the
plan it was taken under (the fourteenth unit correction, 2026-10-16): ``record`` refuses
unless the working tree is ``HEAD``'s and ``HEAD`` tracks the source, and keeps ``HEAD``,
the environment (:func:`tools.split._run.environment`) and the plan's sha256
(:attr:`tools.split._model.Plan.sha256`); ``apply`` refuses unless all three are the same
and the tree is still ``HEAD``'s; ``prove`` refuses unless all three are the same and the
tree differs from ``HEAD`` at exactly the paths ``apply`` writes.
The tree is judged by its files' bytes, whatever the index's flags or a machine's own
ignore rules say (:func:`tools.split._run.differences`), so one ``HEAD`` covers every file
of the tree that a record's meaning rests on but those the COMMITTED ignore rules hide:
this tool, ``.pylintrc``, ``pytest.ini``, the pylint checkers, every conftest,
``scripts/test.sh`` and its database image's inputs, the app.  Of the files those rules
hide, each one a census of the tree finds a run reading is in the environment instead
(:data:`tools.split._run.IGNORED_READERS`: ``.env`` and ``.env.dev``).
``record`` and ``prove`` judge all of it again after their runs, so a change while they ran
is refused (the coordinator's ruling on delta review 3's M-1, 2026-10-16).  A change made
and undone while they run is outside the model: one session works a worktree at a time
(``CLAUDE.md``, Multi-session operation).

``SIGKILL``, and a machine that stops, end an ``apply`` where it stands, with no undo.
Before its proof passed, that leaves no id map, and a tree differing from ``HEAD`` at only
part of what ``apply`` writes: a state every command refuses (``record`` and ``apply``: the
tree is not ``HEAD``'s; ``prove``: the tree is not exactly the apply's), recovered by hand
with ``git restore -- <the source> <each importer meta.json names>`` and ``git clean -fdx
-- <the package directory, the source's path without .py>``, then ``apply`` again.  After
it passed, only the id maps were left to write, so the tree holds the whole proved split.
Exit status 0 means proved; 1 means refused or failed, with every reason printed; 2 means
the command line was refused before anything ran (``apply`` or ``prove`` without
``--idmap``, or a plan, record or map inside the repository).
A record is what this tool wrote: a record file edited by hand into another JSON shape is
outside its model, and fails closed with a traceback (a ``KeyError`` or ``TypeError``), not
a refusal.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from tools.ci import gitcmd
from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError, git
from tools.split import _dynamic as dynamic
from tools.split import _run as run
from tools.split import _similar as similar
from tools.split._layout import Layout, resolve
from tools.split._model import Plan, PlanError, load_plan
from tools.split._prove import (
    describe,
    import_block,
    importer_lines,
    line_origins,
    prove_static,
)
from tools.split._render import (
    Rewrite,
    fingerprint,
    importer_rewrites,
    line_limit,
    render_all,
    write,
)
from tools.split._source import (
    CollectRules,
    Refusal,
    collect_rules,
    lines_of,
    read_json,
    read_module,
    read_text,
)

#: The record's files.  ``meta.json``, in the record directory, is the record's completion
#: token, and names the directory of its run, which holds the rest.
_SOURCE, _META = "source.py", "meta.json"
_BEFORE_COLLECT, _BEFORE_RUN = "before_collect.json", "before_run.json"
_BEFORE_PYLINT, _BEFORE_IMPORTERS = "before_pylint.json", "before_importers_pylint.json"

#: A prove's observations of the split, in its own directory (with one per part run alone).
_AFTER_COLLECT, _AFTER_RUN = "after_collect.json", "after_run.json"

#: The node-id map ``apply`` keeps in the record: written inside the apply's unit, LAST,
#: after its proof passed, so it stays only when the whole apply succeeded and, a process
#: killed part-way included, exists only once the tree holds the whole proved split, which
#: makes it the mark that the record's split is applied.
_APPLIED = "idmap.json"

#: The signals :func:`main` raises as :class:`Signalled`, so an apply's unit undoes them as
#: it does Ctrl-C: ``kill`` and ``timeout`` send ``SIGTERM`` by default, and a terminal
#: that closes sends ``SIGHUP``.
_RAISED = (signal.SIGTERM, signal.SIGHUP)


class Signalled(BaseException):
    """A signal :func:`main` raises as an exception (:data:`_RAISED`), which Python would
    otherwise answer by ending the process where it stands.  A ``BaseException``, as
    :class:`KeyboardInterrupt` is, so nothing that catches errors catches it."""

    def __init__(self, signum: int) -> None:
        """The signal ``signum``, received."""
        super().__init__(f"{signal.Signals(signum).name} received")
        self.signum = signum


def _raise(signum: int, _frame: object) -> None:
    """The handler :func:`main` installs for each of :data:`_RAISED`."""
    raise Signalled(signum)


@dataclass(frozen=True)
class Context:
    """One invocation's inputs: the plan, the repository root, the record directory, and,
    once a record is bound to it, that record's token (:meth:`bound`, or ``record``'s
    own)."""

    plan: Plan
    root: Path
    into: Path
    meta: dict | None = None

    def rules(self) -> CollectRules:
        """pytest's collection rules, from the repository's ``pytest.ini``."""
        return collect_rules(self.root)

    def layout(self, source: Path) -> Layout:
        """The plan resolved against ``source`` (the live file, or the record's copy)."""
        rules = self.rules()
        return resolve(read_module(source, rules), self.plan, rules)

    def bound(self) -> Context:
        """This context bound to the complete record the directory holds, its token read
        once; refused, by name, when the directory holds none, as before any record
        completed there."""
        if not (self.into / _META).is_file():
            raise Refusal(f"{self.into}: no complete record; record again")
        return replace(self, meta=read_json(self.into / _META))

    def held(self, name: str) -> Path:
        """Where the bound record holds the file ``name``: in the directory of its run, which
        its token names."""
        return self.into / self.meta["run"] / name

    def read(self, name: str) -> object:
        """A JSON file the bound record holds."""
        return read_json(self.held(name))

    def applied(self) -> bool:
        """Whether the directory holds a complete record whose split is applied
        (:data:`_APPLIED`)."""
        return (self.into / _META).is_file() and (self.into / _APPLIED).is_file()


def _save(path: Path, value: object) -> None:
    """Write a JSON file of the record, whole or not at all: written beside its name and
    renamed onto it, so a run stopped mid-write leaves the file that was there, never a part
    of a new one.  That is what lets the token's presence mean a record completed, and keeps
    the last record's token whole until a run that completed replaces it."""
    partial = path.with_name(f"{path.name}.partial")
    partial.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def _run_directory(ctx: Context, command: str) -> Path:
    """A directory made for one run of ``command`` under the record directory, its name the
    command's and :func:`tempfile.mkdtemp`'s suffix, so a reader of ``--into`` sees which
    command wrote each.  Everything the run writes but the record's token goes there, so no
    file in it is older than the run, and a pytest that outlived an earlier run writes into
    that run's directory, never this one's (the coordinator's rulings on 3B-2b's condition
    (c), 2026-10-15, and on 3C-2's prove fork, 2026-10-16).  Nothing deletes one: no command
    reads a directory but the run that made it and, for a record's, the commands its token
    binds."""
    return Path(tempfile.mkdtemp(prefix=f"{command}-", dir=ctx.into))


def _head(root: Path) -> str:
    """The commit the record was taken at, read through :func:`tools.ci.gitcmd.git` so
    ``root`` alone is the repository read."""
    return git(root, "rev-parse", "HEAD").strip()


def _clean(root: Path, then: str) -> None:
    """Refuse unless the working tree is ``HEAD``'s (:func:`tools.split._run.differences`
    empty); ``then`` ends the refusal, saying what to do and what was not done."""
    found = run.differences(root)
    if found:
        raise Refusal(f"the working tree differs from HEAD at {len(found)} path(s), e.g. "
                      f"{sorted(found)[:5]}, and a record is judged only in the repository "
                      f"state it was taken in; {then}")


def _held_to(ctx: Context, nothing: str) -> None:
    """Refuse unless this process runs where, and under what, the bound record was taken:
    ``HEAD`` the commit it names, the environment the one it holds
    (:func:`tools.split._run.environment`), and the plan the one whose bytes it hashed
    (:attr:`tools.split._model.Plan.sha256`).  A record holding no environment, or no plan,
    was written by another version of this tool, and is refused alike.  ``nothing`` ends the
    refusal.

    The plan is the record's as much as ``HEAD`` is: ``record`` lays the source out by it,
    computes each importer's rewrites from it, and lints the importers those rewrites
    re-point.  Before the token held it, a record taken under one plan was applied and
    proved under another: refused only where a check with another subject saw the
    difference, naming the wrong cause, and otherwise proved, as a plan that names a part
    otherwise and keeps every importer's rewrites was (measured on the sample with this
    check removed, 2026-10-16; the lane's report at 3C-2, ruled the fourteenth unit
    correction)."""
    head = _head(ctx.root)
    if head != ctx.meta["head"]:
        raise Refusal(f"HEAD is {head[:9]}, and the record was taken at "
                      f"{ctx.meta['head'][:9]}; {nothing}")
    if ctx.meta.get("environment") != run.environment(ctx.root):
        raise Refusal("the record was taken with another interpreter, other installed "
                      "distributions or another git, or by a version of this tool that did "
                      f"not record them; {nothing}")
    if ctx.meta.get("plan") != ctx.plan.sha256:
        raise Refusal("the record was taken under another plan, or by a version of this tool "
                      f"that did not record its plan; {nothing}")


def _tracked(root: Path, path: Path) -> bool:
    """Whether ``HEAD`` holds ``path``, repository-relative: git's answer for the object
    ``HEAD:<path>`` names, which reads no pathspec."""
    return gitcmd.run(root, "cat-file", "-e", f"HEAD:{path}").returncode == 0


def _unmoved(ctx: Context, before: tuple[str, str, set[str]], nothing: str) -> None:
    """Refuse unless ``HEAD``, the environment and the paths at which the tree differs from
    ``HEAD`` are, after a command's runs, ``before``: what they were judged to be before
    them.  A check made only at the start judged a moment, and an observation is a run of
    minutes: a file edited while record's runs ran, or written beside the split while
    prove's arms ran, was in no check (the coordinator's ruling on delta review 3's M-1, the
    twelfth unit correction's second shape, 2026-10-16).  ``nothing`` ends the refusal."""
    head, environment = _head(ctx.root), run.environment(ctx.root)
    found = run.differences(ctx.root)
    moved = ([f"HEAD moved from {before[0][:9]} to {head[:9]}"] if head != before[0] else []) + (
        ["the environment changed"] if environment != before[1] else [])
    if found != before[2]:
        changed = sorted(found ^ before[2])
        moved.append(f"the working tree changed at {len(changed)} path(s), e.g. "
                     f"{changed[:5]}")
    if moved:
        raise Refusal(f"{'; '.join(moved)} while the runs ran, so no observation was made in "
                      f"the state judged; {nothing}")


def _write_set(ctx: Context, layout: Layout) -> set[str]:
    """Every path ``apply`` writes in the tree, repository-relative: each file of the package
    (the writer's own set, :func:`tools.split._render.render_all`'s), the source it deletes,
    and each importer it re-points (the bound record's)."""
    package = layout.plan.package
    return ({str(package / name) for name in render_all(layout, line_limit(ctx.root))}
            | {str(layout.plan.source)} | set(ctx.meta["importers"]))


def _importer_record(rewrites: list[Rewrite], root: Path) -> dict[str, dict]:
    """Each importer the split re-points, by repository-relative path: its fingerprint and
    its rewrites, ``[line, last, old, new]``, which ``record`` keeps and ``prove`` judges the
    importers' lines by.  ``apply`` re-points exactly these: it runs at ``record``'s ``HEAD``,
    under its plan, on a tree that is ``HEAD``'s, so the walk finds the same importers holding
    the same text, and the plan gives them the same rewrites (the branch's neutral review of
    this leaf, M1, which ``apply`` once re-checked; the plan, since the fourteenth unit
    correction)."""
    importers: dict[str, dict] = {}
    for rewrite in rewrites:
        entry = importers.setdefault(str(rewrite.path.relative_to(root)), {
            "sha256": fingerprint(read_text(rewrite.path)), "rewrites": []})
        entry["rewrites"].append([rewrite.line, rewrite.last, rewrite.old, rewrite.new])
    return importers


def _record_verdict(ctx: Context, layout: Layout) -> tuple[list[str], int]:
    """The record's arms, judged from the record's own files against ``layout``: its
    failures, and how many fixtures the source's tests set up.

    One home for the verdict (the branch's neutral review of this leaf, H1: the fixture arm
    refused at ``record`` and nothing downstream knew).  ``record`` reports it, ``apply``
    refuses a record that failed it, and ``prove`` judges it again as an arm of the proof.
    Each run must be one pytest ran to its end over every test of the source
    (:func:`tools.split._dynamic.run_failures`)."""
    collected, ran = ctx.read(_BEFORE_COLLECT), ctx.read(_BEFORE_RUN)
    setups, setups_ran = run.merged_setups(ctx.held(_BEFORE_RUN))
    failures = (dynamic.run_failures(layout, collected, "the source's collection")
                + dynamic.run_failures(layout, ran, "the source's run"))
    if collected["deselected"]:
        failures.append(f"the source run deselects {len(collected['deselected'])} tests; "
                        "the proof covers only what runs")
    if set(collected["collected"]) != set(ran["outcomes"]):
        failures.append("the source's collection and its run disagree on which tests exist")
    failures += dynamic.fixture_failures(
        {"outcomes": ran["outcomes"], "setups": setups, "setups_ran": setups_ran},
        str(ctx.plan.source), str((ctx.root / ctx.plan.source).resolve()))
    return failures, len(setups)


def record(ctx: Context) -> list[str]:
    """Refuse the plan or measure the source before anything moves.

    A record is the files ONE complete run of this command wrote, into a directory made for
    that run, and ``meta.json`` is the token that the run completed: it names that directory,
    and it is written last, replacing the last record's token only then (the coordinator's
    ruling on delta review 3's N-2, 2026-10-16).  So a run that stops part way (a refusal, a
    process that wrote no observation, an interrupt, ``SIGKILL``, a wrapper that cannot run)
    leaves the last record whole, and its own files where no token names them, which nothing
    reads; and a pytest that outlived a killed run writes into that run's directory, never
    into a later run's (the orphan race, ruled on 3B-2b's condition (c), 2026-10-15).  Nothing
    deletes a directory no token names.  A run that completes but observed no session of the
    tests is a complete record whose verdict fails, which ``apply`` refuses (the
    coordinator's ruling on the delta review's H-A, 2026-10-15: the token was written before
    pylint and the runs, so a record that stopped there reached ``apply`` holding the
    previous source's observations).

    A record whose split is applied is never replaced, because it is the evidence that
    split's commit cites and what ``prove`` judges the split by; a split reverted by hand is
    recorded into a new ``--into`` (the coordinator's rulings on delta review 2's Medium-1,
    2026-10-15, and on 3C-2's question, 2026-10-16).

    Refused unless the working tree is ``HEAD``'s and ``HEAD`` tracks the source, so
    ``HEAD``, which the token keeps with the environment and the plan's sha256, names every
    file the observations rest on (REVISED (B), the module docstring).  The same is judged
    again after the runs, before the token is written.  The token holds no copy of what the
    plan says (the source's path): the plan it binds says it (the coordinator's ruling on
    3C-2b, 2026-10-16, rule 14)."""
    if ctx.applied():
        raise Refusal(f"{ctx.into} holds a record whose split is applied; record into a new "
                      "--into")
    head = _head(ctx.root)
    _clean(ctx.root, "commit or remove them, then record; nothing is recorded")
    if not _tracked(ctx.root, ctx.plan.source):
        raise Refusal(f"{ctx.plan.source} is not a file HEAD tracks, and a record is judged "
                      "only against HEAD; commit it, then record; nothing is recorded")
    source = ctx.root / ctx.plan.source
    layout = ctx.layout(source)
    render_all(layout, line_limit(ctx.root))
    importers = _importer_record(importer_rewrites(layout, ctx.root), ctx.root)
    ctx.into.mkdir(parents=True, exist_ok=True)
    ctx = replace(ctx, meta={
        "plan": ctx.plan.sha256, "sha256": run.sha256(source), "head": head,
        "environment": run.environment(ctx.root), "importers": importers,
        "run": _run_directory(ctx, "record").name})
    shutil.copyfile(source, ctx.held(_SOURCE))
    _save(ctx.held(_BEFORE_PYLINT), run.pylint_messages(ctx.root, [str(ctx.plan.source)]))
    _save(ctx.held(_BEFORE_IMPORTERS),
          run.pylint_messages(ctx.root, sorted(importers)) if importers else [])
    run.observe(ctx.root, [str(ctx.plan.source)], ctx.held(_BEFORE_COLLECT),
                collect_only=True)
    run.observe(ctx.root, [str(ctx.plan.source)], ctx.held(_BEFORE_RUN), collect_only=False)
    _unmoved(ctx, (head, ctx.meta["environment"], set()), "nothing is recorded")
    _save(ctx.into / _META, ctx.meta)
    return _record_verdict(ctx, layout)[0]


def apply(ctx: Context, idmap: Path) -> None:
    """Write the package, delete the source, re-point importers, run the static proof and
    write the id maps; refused, with nothing written, unless a complete record passed, is
    not applied already, and ``HEAD``, the environment and the plan are the record's
    (:func:`_held_to`) and the working tree is ``HEAD``'s: so the source and every importer
    are as ``record`` found them, which ``record`` made sure of by refusing a source
    ``HEAD`` does not track, and are laid out as it laid them out.  The writes and
    the proof are ONE unit (:func:`tools.split._render.write`): a failed write, a static
    proof that fails on what was written, or an interrupt undoes every file the apply wrote,
    the id maps beside the tree included, and the refusal names the failure."""
    ctx = ctx.bound()
    if ctx.applied():
        raise Refusal(f"{ctx.into}: already applied (it holds the id map an apply keeps only "
                      "when it succeeded); nothing is applied")
    _held_to(ctx, "record again; nothing is applied")
    _clean(ctx.root, "undo them, or commit them and record again; nothing is applied")
    layout = ctx.layout(ctx.root / ctx.plan.source)
    refused, _ = _record_verdict(ctx, layout)
    if refused:
        raise Refusal(f"the record did not pass ({len(refused)} failure(s), first: "
                      f"{refused[0]}); nothing is applied")
    texts = render_all(layout, line_limit(ctx.root))
    rewrites = importer_rewrites(layout, ctx.root)
    mapping = dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"])
    payload = json.dumps(mapping, indent=1, sort_keys=True) + "\n"
    beside = {idmap: payload, ctx.into / _APPLIED: payload}
    with write(layout, ctx.root, texts, rewrites, beside):
        failures = prove_static(ctx.layout(ctx.held(_SOURCE)), ctx.root, ctx.rules())
        if failures:
            raise Refusal(f"the static proof failed on the split as written "
                          f"({len(failures)} failure(s): {'; '.join(failures)})")


def _written_alone(ctx: Context, written: set[str]) -> None:
    """Refuse unless the working tree differs from ``HEAD`` at exactly the paths ``apply``
    writes, ``written`` (:func:`_write_set`), naming each path written and not different,
    and each different and not written."""
    found = run.differences(ctx.root)
    if found != written:
        missing, beside = sorted(written - found), sorted(found - written)
        raise Refusal(f"the working tree must differ from HEAD at exactly the paths apply "
                      f"writes: {len(missing)} written by apply and not different, e.g. "
                      f"{missing[:5]}; {len(beside)} different and not written by apply, e.g. "
                      f"{beside[:5]}")


def _run_arms(ctx: Context, layout: Layout, mapping: dict[str, str], runs: Path
              ) -> tuple[list[str], dict]:
    """The collection, outcome and part-alone arms, observed into ``runs``, this prove's
    directory; their failures and what they saw.  Each run, like the record's, must be one
    pytest ran to its end over every test it was run on
    (:func:`tools.split._dynamic.run_failures`)."""
    package = str(layout.plan.package)
    before = ctx.read(_BEFORE_RUN)["outcomes"]
    collected = run.observe(ctx.root, [package], runs / _AFTER_COLLECT, True)
    failures = dynamic.run_failures(layout, collected, "the split's collection")
    failures += dynamic.compare_collection(mapping, collected["collected"])
    if collected["deselected"]:
        failures.append(f"the split deselects {collected['deselected'][:5]}")
    ran = run.observe(ctx.root, [package], runs / _AFTER_RUN, False)
    failures += dynamic.run_failures(layout, ran, "the split's run")
    failures += dynamic.compare_outcomes(mapping, before, ran["outcomes"])
    for module in (p.module for p in layout.plan.parts):
        target = f"{package}/{module}.py"
        alone = run.observe(ctx.root, [target], runs / f"alone_{module}.json", False)
        failures += dynamic.run_failures(layout, alone, f"{module} alone", module)
        scope = {old for old, new in mapping.items() if new.startswith(target + "::")}
        failures += [f"alone: {f}" for f in
                     dynamic.compare_outcomes(mapping, before, alone["outcomes"], scope)]
    return failures, {"collected": len(collected["collected"]), "outcomes": ran["outcomes"]}


def _static_arms(ctx: Context, layout: Layout) -> tuple[list[str], dict]:
    """The static proof's failures, and each written line's origin (with a failure for any
    origin the files do not bear out), which the pylint arm judges every message by."""
    origins, unborne = line_origins(layout, ctx.root)
    return ([f"static: {f}" for f in prove_static(layout, ctx.root, ctx.rules()) + unborne],
            origins)


def prove(ctx: Context, idmap: Path) -> list[str]:
    """Every arm of the proof; print the block a commit body quotes only when every arm
    passed.  The block's fixed sentences claim that each arm held, so printed beside a
    failure they would state what the run refuted (the branch's neutral review, F7).
    Refused without a complete record, unless ``HEAD``, the environment and the plan are the
    record's (:func:`_held_to`), and unless the working tree differs from ``HEAD`` at
    EXACTLY the paths ``apply`` writes
    (:func:`_write_set`): fewer is an apply killed part-way (``SIGKILL``; the coordinator's
    ruling on 3B-2's signal fork, (d)), so it is never proved, and more is a change beside
    the split, which no arm is judging (REVISED (B)).  The same is judged again after every
    arm ran, before the block is printed.  Once those refusals pass, every file the prove
    writes goes into a directory made for it (:func:`_run_directory`), which its block
    names."""
    ctx = ctx.bound()
    _held_to(ctx, "a split is proved where it was recorded, before it is committed")
    layout = ctx.layout(ctx.held(_SOURCE))
    written = _write_set(ctx, layout)
    _written_alone(ctx, written)
    runs = _run_directory(ctx, "prove")
    recorded = _record_verdict(ctx, layout)
    failures, origins = _static_arms(ctx, layout)
    failures = [f"record: {f}" for f in recorded[0]] + failures
    mapping = read_json(idmap)
    if mapping != dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"]):
        failures.append(f"{idmap} is not the map this plan produces")
    arms, seen = _run_arms(ctx, layout, mapping, runs)
    failures += arms
    seen["fixtures"] = recorded[1]
    arms, after_pylint = _pylint_arms(ctx, layout, runs, origins, seen)
    failures += arms
    arms, named = _importer_arm(ctx)
    failures += arms
    arms, gate_line = _gate_arm(ctx)
    failures += arms
    hits = dynamic.classify(ctx.root, run.census(ctx.root, layout.plan), origins)
    _unmoved(ctx, (ctx.meta["head"], ctx.meta["environment"], written), "no block is printed")
    if not failures:
        print("\n".join(_report_moves(ctx, layout, idmap, runs, seen)
                        + _report_lint(ctx, after_pylint, named)
                        + _report_duplicates(ctx, seen)
                        + _report_pointers(gate_line, hits)))
    return failures


def _pylint_arms(ctx: Context, layout: Layout, runs: Path, origins: dict, seen: dict
                 ) -> tuple[list[str], list[dict]]:
    """Lint the parts in one run: the origin arm's and the duplicate-code arm's failures, and
    the parts' messages (``runs``, this prove's directory, is where the duplicate-code arm
    lists what it found)."""
    files = sorted(str(p.relative_to(ctx.root)) for p in (ctx.root / layout.plan.package).glob(
        "*.py"))
    after_pylint = run.pylint_messages(ctx.root, files)
    failures = [f"pylint: {f}" for f in dynamic.compare_pylint(
        ctx.read(_BEFORE_PYLINT), after_pylint, origins, import_block(layout))]
    return failures + _duplicate_arm(ctx, layout, runs, after_pylint, seen), after_pylint


def _importer_arm(ctx: Context) -> tuple[list[str], list[str]]:
    """Judge the re-pointed importers' pylint messages by origin: the failures, and the
    messages on their re-pointed imports, named for the proof block."""
    recorded = ctx.meta["importers"]
    if not recorded:
        return [], []
    repointed, unborne = importer_lines(ctx.root, recorded)
    failures, named = dynamic.compare_importers(
        ctx.read(_BEFORE_IMPORTERS), run.pylint_messages(ctx.root, sorted(recorded)),
        repointed)
    return [f"importers: {f}" for f in unborne + failures], named


def _gate_arm(ctx: Context) -> tuple[list[str], str]:
    """The plan gate, run as pre-commit and CI run it: a failure when it did not pass, and
    its summary line for the proof block."""
    gate, gate_line = run.plan_gate(ctx.root)
    return ([f"plan gate: {gate_line}"] if gate else []), gate_line


def _duplicate_arm(ctx: Context, layout: Layout, runs: Path, after_pylint: list[dict],
                   seen: dict) -> list[str]:
    """Judge every duplicate-code pair between the parts; list the revealed ones in ``runs``,
    this prove's directory (``<stem>.r0801.txt``), and return a failure for each created
    one."""
    duplicates = similar.sort_pairs(layout, ctx.root, ctx.rules(), after_pylint,
                                    read_text(ctx.held(_SOURCE)))
    revealed = runs / f"{ctx.plan.stem}.r0801.txt"
    revealed.write_text("".join(
        f"{a} ~ {b}  (source lines {sa[0] + 1}-{sa[1]} ~ {sb[0] + 1}-{sb[1]})\n"
        for a, b, sa, sb in duplicates.revealed), encoding="utf-8")
    seen["revealed"] = (len(duplicates.revealed), revealed)
    return [f"duplicate-code CREATED by the split: {a} ~ {b}: {why}"
            for a, b, why in duplicates.created]


def _report_moves(ctx: Context, layout: Layout, idmap: Path, runs: Path, seen: dict
                  ) -> list[str]:
    """The proof block's head: the source, the evidence, the files, identity, collection and
    outcomes.  The evidence is the record's run directory, the one its token names, and
    ``runs``, this prove's, each relative to ``--into``: one record may have several proves
    beside it, and a commit body quotes the observations of one (the coordinator's ruling on
    3C-2's prove fork, (b), 2026-10-16)."""
    meta = ctx.meta
    package = ctx.root / layout.plan.package
    sizes = ", ".join(f"{p.name} {read_text(p).count(chr(10))}"
                      for p in sorted(package.glob("*.py")))
    counts = describe(layout)
    return [
        f"Proof (python -m tools.split.split prove, plan {ctx.plan.stem}.plan.json):",
        f"- source {ctx.plan.source} at {meta['head'][:9]}, sha256 {meta['sha256'][:16]}",
        f"- evidence, in --into: the record's run {meta['run']}, this prove's {runs.name}",
        f"- files (lines): {sizes}; every one under the line budget",
        f"- identity: {counts['whole_nodes']} whole nodes and {counts['split_members']} "
        "split-class members, each verbatim line for line (leading comments included) "
        "and ast.dump-identical; each held exactly once, in source order",
        f"- fixtures: {seen['fixtures']} set up by the source's tests; none scoped to a "
        "module, class or package and defined outside the source, none defined in another "
        "file and registered on it",
        f"- collection: {seen['collected']} ids after = the source's under the map "
        f"(total, one-to-one); map sha256 {run.sha256(idmap)}",
        f"- outcomes: identical for every id under the map, "
        f"{dynamic.outcome_counts(seen['outcomes'])}; identical with each part run alone",
    ]


def _report_lint(ctx: Context, after_pylint: list[dict], named: list[str]) -> list[str]:
    """The proof block's pylint lines: the source's counts, the parts', and each message on
    an importer's re-pointed import that the line did not hold before."""
    before = ctx.read(_BEFORE_PYLINT)
    return [
        f"- pylint: source {dict(sorted(dynamic.pylint_counts(before).items()))}; parts "
        f"{dict(sorted(dynamic.pylint_counts(after_pylint).items()))}; every part message "
        "answered by its origin",
        f"- importers re-pointed: {sorted(ctx.meta['importers'])}; every other line "
        f"unchanged and holding no new message; on the re-pointed imports: {named}",
    ]


def _report_duplicates(ctx: Context, seen: dict) -> list[str]:
    """The proof block's duplicate-code line: every pair between parts, pre-existing, and
    where they are listed, relative to ``--into``."""
    count, path = seen["revealed"]
    return [f"- duplicate-code: {count} R0801 pairs, pre-existing, revealed not created "
            f"(each mapped to two distinct source ranges pylint's own Symilar reports as a "
            f"pair; listed in {path.relative_to(ctx.into)})"]


def _report_pointers(gate_line: str, hits: list[dynamic.Hit]) -> list[str]:
    """The proof block's tail: the plan gate and every line still naming the old module, the
    package's own provenance counted apart."""
    live = [h for h in hits if not h.archived and not h.provenance]
    archived = sum(1 for h in hits if h.archived)
    return [
        f"- plan gate: {gate_line}",
        f"- census of the old module's name: {len(live)} live, {archived} archived, "
        f"{len(hits) - len(live) - archived} the package's own provenance (not counted)",
        *(f"    live {h.path}:{h.line}: {h.text[:120]}" for h in live),
    ]


def parser() -> argparse.ArgumentParser:
    """The command line."""
    top = argparse.ArgumentParser(prog="split", description=lines_of(__doc__)[0])
    top.add_argument("command", choices=("record", "apply", "prove"))
    top.add_argument("plan", type=Path)
    top.add_argument("--into", type=Path, required=True, help="the record directory")
    top.add_argument("--idmap", type=Path, help="where apply writes the node-id map")
    return top


def main(argv: list[str] | None = None, root: Path = REPO) -> int:
    """Run one command; 0 when it proved, 1 when it refused or a proof arm failed (the
    command line's own refusals exit 2, through :meth:`argparse.ArgumentParser.error`).

    The one place an :class:`OSError` is caught (the coordinator's ruling on commit 3b's
    (A), 2026-10-15): a file that cannot be read or written, a process that cannot start
    (pylint not installed), each names its file itself, so it is reported as a refusal like
    the rest, never a traceback; a decode, JSON or ini error is refused where it is read
    (:mod:`tools.split._source`), naming the file.  A failed git command
    (:class:`tools.ci.gitcmd.GitError`, as a repository with no commit gives ``record``) is
    caught here too, for every git call at once.

    While the command runs, ``SIGTERM`` and ``SIGHUP`` raise :class:`Signalled`, which an
    apply's unit undoes as it does Ctrl-C, and which stops a suite run through the wrapper's
    own teardown (:func:`tools.split._run._wrapper_run`); like Ctrl-C, nothing swallows it,
    so it ends the run with its traceback, an apply's carrying the undo's note (the
    coordinator's ruling on 3B-2's signal fork, 2026-10-15).  The handlers that were there
    before are put back
    however the command ends, so a caller in the same process (a test) keeps its own."""
    args = parser().parse_args(argv)
    if args.command != "record" and args.idmap is None:
        parser().error("apply and prove need --idmap")
    inside = [str(path) for path in (args.plan, args.into, args.idmap)
              if path is not None and path.resolve().is_relative_to(root.resolve())]
    if inside:
        parser().error(f"{inside} must lie outside the repository: the census reads every "
                       "file git does not ignore, and a plan or a record names the old module "
                       "throughout")
    previous = {signum: signal.signal(signum, _raise) for signum in _RAISED}
    try:
        return _command(args, root)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _command(args: argparse.Namespace, root: Path) -> int:
    """Run the command ``args`` names in ``root``, and report it (:func:`main`)."""
    try:
        ctx = Context(plan=load_plan(args.plan), root=root, into=args.into)
        if args.command == "record":
            failures = record(ctx)
        elif args.command == "apply":
            apply(ctx, args.idmap)
            failures = []
        else:
            failures = prove(ctx, args.idmap)
    except (PlanError, Refusal, run.RunError, GitError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    print(f"{args.command}: {'FAILED' if failures else 'OK'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
