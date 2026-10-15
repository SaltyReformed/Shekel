"""Split one test module into a package by a plan, and prove the move pure.

    python -m tools.split.split record <plan.json> --into <dir>
    python -m tools.split.split apply  <plan.json> --into <dir> --idmap <map.json>
    python -m tools.split.split prove  <plan.json> --into <dir> --idmap <map.json>

``record`` refuses the plan or measures the source BEFORE anything moves: a copy of it, its
collection and every test's outcome through ``./scripts/test.sh``, and pylint's messages for
it and for each module that imports it.  ``apply`` writes the package, deletes the source,
re-points the importers, writes the node-id map and runs the static proof.  ``prove`` runs
the static proof again and every run arm, and prints the block a commit body quotes.
Exit status 0 means proved; 1 means refused or failed, with every reason printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.ci.arcs import REPO
from tools.split import _dynamic as dynamic
from tools.split import _run as run
from tools.split._layout import Layout, resolve
from tools.split._model import Plan, PlanError, load_plan
from tools.split._prove import describe, prove_static
from tools.split._render import importer_rewrites, line_limit, render_all, write
from tools.split._source import CollectRules, Refusal, collect_rules, read_module

#: The record directory's files.
_SOURCE, _META = "source.py", "meta.json"
_BEFORE_COLLECT, _BEFORE_RUN = "before_collect.json", "before_run.json"
_BEFORE_PYLINT, _BEFORE_IMPORTERS = "before_pylint.json", "before_importers_pylint.json"


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

    def save(self, name: str, value: object) -> None:
        """Write a JSON file into the record."""
        (self.into / name).write_text(json.dumps(value, indent=1, sort_keys=True) + "\n",
                                      encoding="utf-8")


def _sha256(path: Path) -> str:
    """A file's sha256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head(root: Path) -> str:
    """The commit the record was taken at."""
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


def record(ctx: Context) -> list[str]:
    """Refuse the plan or measure the source before anything moves."""
    source = ctx.root / ctx.plan.source
    layout = ctx.layout(source)
    render_all(layout, line_limit(ctx.root))
    importers = sorted({str(r.path.relative_to(ctx.root))
                        for r in importer_rewrites(layout, ctx.root)})
    ctx.into.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, ctx.into / _SOURCE)
    ctx.save(_META, {"source": str(ctx.plan.source), "sha256": _sha256(source),
                     "head": _head(ctx.root), "importers": importers})
    ctx.save(_BEFORE_PYLINT, run.pylint_messages(ctx.root, [str(ctx.plan.source)]))
    ctx.save(_BEFORE_IMPORTERS, run.pylint_messages(ctx.root, importers) if importers else [])
    collected = run.observe(ctx.root, [str(ctx.plan.source)], ctx.into / _BEFORE_COLLECT,
                            collect_only=True)
    ran = run.observe(ctx.root, [str(ctx.plan.source)], ctx.into / _BEFORE_RUN,
                      collect_only=False)
    failures = []
    if collected["deselected"]:
        failures.append(f"the source run deselects {len(collected['deselected'])} tests; "
                        "the proof covers only what runs")
    if set(collected["collected"]) != set(ran["outcomes"]):
        failures.append("the source's collection and its run disagree on which tests exist")
    return failures


def apply(ctx: Context, idmap: Path) -> list[str]:
    """Write the package, delete the source, re-point importers, write the id map, and run
    the static proof."""
    meta = ctx.read(_META)
    source = ctx.root / ctx.plan.source
    if _sha256(source) != meta["sha256"]:
        raise Refusal(f"{source} changed since it was recorded; record again")
    layout = ctx.layout(source)
    texts = render_all(layout, line_limit(ctx.root))
    rewrites = importer_rewrites(layout, ctx.root)
    mapping = dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"])
    write(layout, ctx.root, texts, rewrites)
    payload = json.dumps(mapping, indent=1, sort_keys=True) + "\n"
    idmap.write_text(payload, encoding="utf-8")
    (ctx.into / "idmap.json").write_text(payload, encoding="utf-8")
    return prove_static(ctx.layout(ctx.into / _SOURCE), ctx.root, ctx.rules())


def _run_arms(ctx: Context, layout: Layout, mapping: dict[str, str]) -> tuple[list[str], dict]:
    """The collection, outcome and part-alone arms; their failures and what they saw."""
    package = str(layout.plan.package)
    before = ctx.read(_BEFORE_RUN)["outcomes"]
    collected = run.observe(ctx.root, [package], ctx.into / "after_collect.json", True)
    failures = dynamic.compare_collection(mapping, collected["collected"])
    if collected["deselected"]:
        failures.append(f"the split deselects {collected['deselected'][:5]}")
    ran = run.observe(ctx.root, [package], ctx.into / "after_run.json", False)
    failures += dynamic.compare_outcomes(mapping, before, ran["outcomes"])
    for module in (p.module for p in layout.plan.parts):
        target = f"{package}/{module}.py"
        alone = run.observe(ctx.root, [target], ctx.into / f"alone_{module}.json", False)
        scope = {old for old, new in mapping.items() if new.startswith(target + "::")}
        failures += [f"alone: {f}" for f in
                     dynamic.compare_outcomes(mapping, before, alone["outcomes"], scope)]
    return failures, {"collected": len(collected["collected"]), "outcomes": ran["outcomes"],
                      "summary": ran["summary"]}


def prove(ctx: Context, idmap: Path) -> list[str]:
    """Every arm of the proof; print the block a commit body quotes."""
    layout = ctx.layout(ctx.into / _SOURCE)
    failures = [f"static: {f}" for f in prove_static(layout, ctx.root, ctx.rules())]
    mapping = json.loads(idmap.read_text(encoding="utf-8"))
    if mapping != dynamic.id_map(layout, ctx.read(_BEFORE_COLLECT)["collected"]):
        failures.append(f"{idmap} is not the map this plan produces")
    arm_failures, seen = _run_arms(ctx, layout, mapping)
    failures += arm_failures
    files = sorted(str(p.relative_to(ctx.root)) for p in (ctx.root / layout.plan.package).glob(
        "*.py"))
    after_pylint = run.pylint_messages(ctx.root, files)
    failures += [f"pylint: {f}" for f in dynamic.compare_pylint(ctx.read(_BEFORE_PYLINT),
                                                               after_pylint)]
    importers = ctx.read(_META)["importers"]
    importer_kinds = dynamic.new_kinds(ctx.read(_BEFORE_IMPORTERS), run.pylint_messages(
        ctx.root, importers)) if importers else {}
    gate, gate_line = run.plan_gate(ctx.root)
    if gate:
        failures.append(f"plan gate: {gate_line}")
    hits = dynamic.classify(ctx.root, run.census(ctx.root, layout.plan))
    print("\n".join(_report_moves(ctx, layout, idmap, seen)
                    + _report_lint(ctx, after_pylint, importer_kinds)
                    + _report_pointers(gate_line, hits)))
    return failures


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
        f"- collection: {seen['collected']} ids after = the source's under the map "
        f"(total, one-to-one); map sha256 {_sha256(idmap)}",
        f"- outcomes: identical for every id under the map, "
        f"{dynamic.outcome_counts(seen['outcomes'])}; identical with each part run alone",
    ]


def _report_lint(ctx: Context, after_pylint: list[dict], importer_kinds: dict) -> list[str]:
    """The proof block's pylint lines: the source's counts, the parts', the importers'."""
    before = ctx.read(_BEFORE_PYLINT)
    return [
        f"- pylint: source {dict(sorted(dynamic.pylint_counts(before).items()))}; parts "
        f"{dict(sorted(dynamic.pylint_counts(after_pylint).items()))}",
        f"- importers re-pointed: {ctx.read(_META)['importers']}; kinds new to them: "
        f"{importer_kinds}",
    ]


def _report_pointers(gate_line: str, hits: list[dynamic.Hit]) -> list[str]:
    """The proof block's tail: the plan gate and every line still naming the old path."""
    live = [h for h in hits if not h.archived]
    return [
        f"- plan gate: {gate_line}",
        f"- census of the old path: {len(live)} live, {len(hits) - len(live)} archived",
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
