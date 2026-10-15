"""Split one test module into a package by a plan, and prove the move pure.

    python -m tools.split.split record <plan.json> --into <dir>
    python -m tools.split.split apply  <plan.json> --into <dir> --idmap <map.json>
    python -m tools.split.split prove  <plan.json> --into <dir> --idmap <map.json>

``record`` refuses the plan or measures the source BEFORE anything moves: a copy of it, its
collection and every test's outcome through ``./scripts/test.sh``, every fixture its tests set
up (refusing one whose reach the split would change), and pylint's messages for it and for
each module that imports it, whose text it fingerprints; its ``meta.json``, written last,
marks the record complete, and ``apply`` and ``prove`` refuse without one.  ``apply`` writes
the package, deletes the source, re-points the importers, writes the node-id map and runs
the static proof.  ``prove`` runs the static proof again and every run arm, and prints the
block a commit body quotes when every arm passed.
Exit status 0 means proved; 1 means refused or failed, with every reason printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.ci.arcs import REPO
from tools.ci.gitcmd import git
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
from tools.split._source import CollectRules, Refusal, collect_rules, read_module

#: The record directory's files.  ``meta.json`` is the record's completion token.
_SOURCE, _META = "source.py", "meta.json"
_BEFORE_COLLECT, _BEFORE_RUN = "before_collect.json", "before_run.json"
_BEFORE_PYLINT, _BEFORE_IMPORTERS = "before_pylint.json", "before_importers_pylint.json"

#: Every file a record holds, in the order ``record`` deletes them, the token first (each
#: observation with its processes' fixture-setup files: :func:`tools.split._run.discard`).
_RECORD = (_META, _SOURCE, _BEFORE_PYLINT, _BEFORE_IMPORTERS, _BEFORE_COLLECT, _BEFORE_RUN)


@dataclass(frozen=True)
class Context:
    """One invocation's inputs: the plan, the repository root, the record directory."""

    plan: Plan
    root: Path
    into: Path

    def rules(self) -> CollectRules:
        """pytest's collection rules, from the repository's ``pytest.ini``."""
        return collect_rules(self.root)

    def layout(self, source: Path) -> Layout:
        """The plan resolved against ``source`` (the live file, or the record's copy)."""
        rules = self.rules()
        return resolve(read_module(source, rules), self.plan, rules)

    def read(self, name: str) -> object:
        """A JSON file the record holds."""
        return json.loads((self.into / name).read_text(encoding="utf-8"))

    def meta(self) -> dict:
        """The record's completion token, which ``record`` writes last; refused, by name,
        when the directory holds none, as after a record that stopped part-way."""
        if not (self.into / _META).is_file():
            raise Refusal(f"{self.into}: no complete record; record again")
        return self.read(_META)

    def save(self, name: str, value: object) -> None:
        """Write a JSON file into the record, whole or not at all: written beside its name
        and renamed onto it, so a run stopped mid-write leaves no part of a file under the
        name, which is what lets the token's presence mean the record completed."""
        path = self.into / name
        partial = path.with_name(f"{name}.partial")
        partial.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
        partial.replace(path)


def _sha256(path: Path) -> str:
    """A file's sha256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head(root: Path) -> str:
    """The commit the record was taken at, read through :func:`tools.ci.gitcmd.git` so
    ``root`` alone is the repository read."""
    return git(root, "rev-parse", "HEAD").strip()


def _importer_record(rewrites: list[Rewrite], root: Path) -> dict[str, dict]:
    """Each importer the split re-points, by repository-relative path: its fingerprint and
    its rewrites, ``[line, last, old, new]``.  ``record`` keeps it, and ``apply`` refuses
    unless the tree still gives the same, so no importer is re-pointed that ``record`` did not
    fingerprint and lint (the branch's neutral review of this leaf, M1)."""
    importers: dict[str, dict] = {}
    for rewrite in rewrites:
        entry = importers.setdefault(str(rewrite.path.relative_to(root)), {
            "sha256": fingerprint(rewrite.path.read_text(encoding="utf-8")), "rewrites": []})
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
    setups, setups_ran = run.merged_setups(ctx.into / _BEFORE_RUN)
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

    A record is the files ONE complete run of this command wrote, and ``meta.json``, written
    last, is the token that the run completed.  It is deleted first, then every other file
    a record holds, so a run that stops part-way (a refusal, a process that wrote no
    observation, an interrupt) leaves no record at all, and never the last record's
    observations under this run's token.  A run that completes but observed no session of
    the tests is a complete record whose verdict fails, which ``apply`` refuses (the
    coordinator's ruling on the delta review's H-A, 2026-10-15: the token
    was written before pylint and the runs, so a record that stopped there reached ``apply``
    holding the previous source's observations)."""
    for name in _RECORD:
        run.discard(ctx.into / name)
    source = ctx.root / ctx.plan.source
    layout = ctx.layout(source)
    render_all(layout, line_limit(ctx.root))
    importers = _importer_record(importer_rewrites(layout, ctx.root), ctx.root)
    ctx.into.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, ctx.into / _SOURCE)
    meta = {"source": str(ctx.plan.source), "sha256": _sha256(source),
            "head": _head(ctx.root), "importers": importers}
    ctx.save(_BEFORE_PYLINT, run.pylint_messages(ctx.root, [str(ctx.plan.source)]))
    ctx.save(_BEFORE_IMPORTERS,
             run.pylint_messages(ctx.root, sorted(importers)) if importers else [])
    run.observe(ctx.root, [str(ctx.plan.source)], ctx.into / _BEFORE_COLLECT,
                collect_only=True)
    run.observe(ctx.root, [str(ctx.plan.source)], ctx.into / _BEFORE_RUN, collect_only=False)
    ctx.save(_META, meta)
    return _record_verdict(ctx, layout)[0]


def apply(ctx: Context, idmap: Path) -> list[str]:
    """Write the package, delete the source, re-point importers, write the id map, and run
    the static proof; refused, with nothing written, unless a complete record passed and the
    source and every importer are as it found them."""
    meta = ctx.meta()
    source = ctx.root / ctx.plan.source
    if _sha256(source) != meta["sha256"]:
        raise Refusal(f"{source} changed since it was recorded; record again")
    layout = ctx.layout(source)
    refused, _ = _record_verdict(ctx, layout)
    if refused:
        raise Refusal(f"the record did not pass ({len(refused)} failure(s), first: "
                      f"{refused[0]}); nothing is applied")
    texts = render_all(layout, line_limit(ctx.root))
    rewrites = importer_rewrites(layout, ctx.root)
    if _importer_record(rewrites, ctx.root) != meta["importers"]:
        raise Refusal("the modules importing the source, or their text, changed since the "
                      "record; record again")
    mapping = dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"])
    write(layout, ctx.root, texts, rewrites)
    payload = json.dumps(mapping, indent=1, sort_keys=True) + "\n"
    idmap.write_text(payload, encoding="utf-8")
    (ctx.into / "idmap.json").write_text(payload, encoding="utf-8")
    return prove_static(ctx.layout(ctx.into / _SOURCE), ctx.root, ctx.rules())


def _run_arms(ctx: Context, layout: Layout, mapping: dict[str, str]) -> tuple[list[str], dict]:
    """The collection, outcome and part-alone arms; their failures and what they saw.  Each
    run, like the record's, must be one pytest ran to its end over every test it was run on
    (:func:`tools.split._dynamic.run_failures`)."""
    package = str(layout.plan.package)
    before = ctx.read(_BEFORE_RUN)["outcomes"]
    collected = run.observe(ctx.root, [package], ctx.into / "after_collect.json", True)
    failures = dynamic.run_failures(layout, collected, "the split's collection")
    failures += dynamic.compare_collection(mapping, collected["collected"])
    if collected["deselected"]:
        failures.append(f"the split deselects {collected['deselected'][:5]}")
    ran = run.observe(ctx.root, [package], ctx.into / "after_run.json", False)
    failures += dynamic.run_failures(layout, ran, "the split's run")
    failures += dynamic.compare_outcomes(mapping, before, ran["outcomes"])
    for module in (p.module for p in layout.plan.parts):
        target = f"{package}/{module}.py"
        alone = run.observe(ctx.root, [target], ctx.into / f"alone_{module}.json", False)
        failures += dynamic.run_failures(layout, alone, f"{module} alone", module)
        scope = {old for old, new in mapping.items() if new.startswith(target + "::")}
        failures += [f"alone: {f}" for f in
                     dynamic.compare_outcomes(mapping, before, alone["outcomes"], scope)]
    return failures, {"collected": len(collected["collected"]), "outcomes": ran["outcomes"],
                      "summary": ran["summary"]}


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
    Refused without a complete record."""
    ctx.meta()
    layout = ctx.layout(ctx.into / _SOURCE)
    recorded = _record_verdict(ctx, layout)
    failures, origins = _static_arms(ctx, layout)
    failures = [f"record: {f}" for f in recorded[0]] + failures
    mapping = json.loads(idmap.read_text(encoding="utf-8"))
    if mapping != dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"]):
        failures.append(f"{idmap} is not the map this plan produces")
    arms, seen = _run_arms(ctx, layout, mapping)
    failures += arms
    seen["fixtures"] = recorded[1]
    arms, after_pylint = _pylint_arms(ctx, layout, idmap, origins, seen)
    failures += arms
    arms, named = _importer_arm(ctx)
    failures += arms
    gate, gate_line = run.plan_gate(ctx.root)
    if gate:
        failures.append(f"plan gate: {gate_line}")
    hits = dynamic.classify(ctx.root, run.census(ctx.root, layout.plan), origins)
    if not failures:
        print("\n".join(_report_moves(ctx, layout, idmap, seen)
                        + _report_lint(ctx, after_pylint, named)
                        + _report_duplicates(seen)
                        + _report_pointers(gate_line, hits)))
    return failures


def _pylint_arms(ctx: Context, layout: Layout, idmap: Path, origins: dict, seen: dict
                 ) -> tuple[list[str], list[dict]]:
    """Lint the parts in one run: the origin arm's and the duplicate-code arm's failures, and
    the parts' messages."""
    files = sorted(str(p.relative_to(ctx.root)) for p in (ctx.root / layout.plan.package).glob(
        "*.py"))
    after_pylint = run.pylint_messages(ctx.root, files)
    failures = [f"pylint: {f}" for f in dynamic.compare_pylint(
        ctx.read(_BEFORE_PYLINT), after_pylint, origins, import_block(layout))]
    return failures + _duplicate_arm(ctx, layout, idmap, after_pylint, seen), after_pylint


def _importer_arm(ctx: Context) -> tuple[list[str], list[str]]:
    """Judge the re-pointed importers' pylint messages by origin: the failures, and the
    messages on their re-pointed imports, named for the proof block."""
    recorded = ctx.read(_META)["importers"]
    if not recorded:
        return [], []
    repointed, unborne = importer_lines(ctx.root, recorded)
    failures, named = dynamic.compare_importers(
        ctx.read(_BEFORE_IMPORTERS), run.pylint_messages(ctx.root, sorted(recorded)),
        repointed)
    return [f"importers: {f}" for f in unborne + failures], named


def _duplicate_arm(ctx: Context, layout: Layout, idmap: Path, after_pylint: list[dict],
                   seen: dict) -> list[str]:
    """Judge every duplicate-code pair between the parts; list the revealed ones beside the
    id map (``<stem>.r0801.txt``) and return a failure for each created one."""
    duplicates = similar.sort_pairs(layout, ctx.root, ctx.rules(), after_pylint,
                                    (ctx.into / _SOURCE).read_text(encoding="utf-8"))
    revealed = idmap.with_name(f"{ctx.plan.stem}.r0801.txt")
    revealed.write_text("".join(
        f"{a} ~ {b}  (source lines {sa[0] + 1}-{sa[1]} ~ {sb[0] + 1}-{sb[1]})\n"
        for a, b, sa, sb in duplicates.revealed), encoding="utf-8")
    seen["revealed"] = (len(duplicates.revealed), revealed)
    return [f"duplicate-code CREATED by the split: {a} ~ {b}: {why}"
            for a, b, why in duplicates.created]


def _report_moves(ctx: Context, layout: Layout, idmap: Path, seen: dict) -> list[str]:
    """The proof block's head: the source, the files, identity, collection and outcomes."""
    meta = ctx.read(_META)
    package = ctx.root / layout.plan.package
    sizes = ", ".join(f"{p.name} {p.read_text(encoding='utf-8').count(chr(10))}"
                      for p in sorted(package.glob("*.py")))
    counts = describe(layout)
    return [
        f"Proof (python -m tools.split.split prove, plan {ctx.plan.stem}.plan.json):",
        f"- source {meta['source']} at {meta['head'][:9]}, sha256 {meta['sha256'][:16]}",
        f"- files (lines): {sizes}; every one under the line budget",
        f"- identity: {counts['whole_nodes']} whole nodes and {counts['split_members']} "
        "split-class members, each verbatim line for line (leading comments included) "
        "and ast.dump-identical; each held exactly once, in source order",
        f"- fixtures: {seen['fixtures']} set up by the source's tests; none scoped to a "
        "module, class or package and defined outside the source, none defined in another "
        "file and registered on it",
        f"- collection: {seen['collected']} ids after = the source's under the map "
        f"(total, one-to-one); map sha256 {_sha256(idmap)}",
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
        f"- importers re-pointed: {sorted(ctx.read(_META)['importers'])}; every other line "
        f"unchanged and holding no new message; on the re-pointed imports: {named}",
    ]


def _report_duplicates(seen: dict) -> list[str]:
    """The proof block's duplicate-code line: every pair between parts, pre-existing."""
    count, path = seen["revealed"]
    return [f"- duplicate-code: {count} R0801 pairs, pre-existing, revealed not created "
            f"(each mapped to two distinct source ranges pylint's own Symilar reports as a "
            f"pair; listed in {path.name})"]


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
    top = argparse.ArgumentParser(prog="split", description=__doc__.splitlines()[0])
    top.add_argument("command", choices=("record", "apply", "prove"))
    top.add_argument("plan", type=Path)
    top.add_argument("--into", type=Path, required=True, help="the record directory")
    top.add_argument("--idmap", type=Path, help="where apply writes the node-id map")
    return top


def main(argv: list[str] | None = None, root: Path = REPO) -> int:
    """Run one command; 0 when it proved, 1 when it refused or a proof arm failed."""
    args = parser().parse_args(argv)
    if args.command != "record" and args.idmap is None:
        parser().error("apply and prove need --idmap")
    inside = [str(path) for path in (args.plan, args.into, args.idmap)
              if path is not None and path.resolve().is_relative_to(root.resolve())]
    if inside:
        parser().error(f"{inside} must lie outside the repository: the census reads every "
                       "file git does not ignore, and a plan or a record names the old module "
                       "throughout")
    try:
        ctx = Context(plan=load_plan(args.plan), root=root, into=args.into)
        if args.command == "record":
            failures = record(ctx)
        elif args.command == "apply":
            failures = apply(ctx, args.idmap)
        else:
            failures = prove(ctx, args.idmap)
    except (PlanError, Refusal, run.RunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    print(f"{args.command}: {'FAILED' if failures else 'OK'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
