"""Which plan steps a release carries, whether each moves money, and which deploy-together
group each is a leaf of: the release tool's pre-cutover arm (step X-cx's L9a, T-a a1).

**What a release carries is git's answer**: the steps claimed by a ``Ships: <arc>:<id>``
trailer on a commit in ``<base>..<cut>``, read by the one trailer walk
(:func:`tools.ci.trailers.history`).  A step claimed on ``base``'s history is RELEASED.

**A step moves money when ANY version of ``steps.md`` that holds its row OPEN says so**:
every version in ``<base>..<cut>`` (every commit that changes the file, on every line of
history) and ``base`` itself, each row read through the one producer the migration
writes a step card's labels with (:func:`tools.quill.door.step_labels`).  Not the
cut's row: every carried step is ticked by the cut (the plan gate's unticked-leaf arm
makes the tick ride in the same push), and a tick rewrites the sentence in the past
tense, which has dropped the ``MOVES MONEY`` marker (``recurrence:R20`` carried it at
``969d5c141^`` and lost it at its tick ``969d5c141``; measured 2026-10-13).  Not one
"newest" open row either: in a branching history a lane that forked before a marker was
written holds an older open row, and which version is newest is no fact about the step.
Any open version carrying the marker counts, so the rule errs toward refusing, and its
one stated over-refusal is a step re-planned in the window to stop moving money, which
still reads as a money mover (none in this history, measured 2026-10-13).  Decided by
the coordinator under ruling ``balance:R-BAL207``, 2026-10-13, after a neutral review.

**A step no version holds open has no readable flag**: a row first written already
SHIPPED, as a lane does when it splits or files a step in its tick.  Its flag is the
skill's judgment, DECLARED with the source it was read from (:class:`Declaration`); an
undeclared one refuses the release, and a declaration for a step some version holds
open is refused, so a declaration only ever fills a value no home holds (R-BAL207,
2026-10-13).  The cutover makes every flag a card's label and deletes this arm.

**A group is a container the producer labels deploy-together**, read at the cut, and its
leaves are the plan gate's one derivation (``decomposition_leaf_keys``), a leaf that is
itself split counting as its own leaves.  A release carrying one of them must carry or
have released every other: a leaf neither carried nor released is MISSING.  A leaf
shipped before ruling ``R-BAL134`` gave a step its trailer reads MISSING, so the rule
errs toward refusing.  A deploy-together key that names a row in the window but none at
the cut (a container renamed or removed) refuses the release: its group cannot be read.

**Before the cutover only.**  The registries are read through the plan gate's door
(:mod:`tools.plan_gate.registries`); a cut whose tree holds no ``steps.md`` is after the
cutover, and its reader is :mod:`tools.release._cards` (the release's cut chooses, in
:class:`tools.release._plan.Judge`).  The cutover deletes this module with the registries.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tools.ci import trailers
from tools.ci.gitcmd import git
from tools.plan_gate.registries import (
    StepRow,
    decomposition_leaf_keys,
    identity_class,
    step_rows,
)
from tools.quill.door import step_labels
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release._model import (
    STEPS_PATH,
    Carried,
    Group,
    ReleaseError,
    Step,
    steps_blob,
)


@dataclass(frozen=True)
class Declaration:
    """The skill's judgment of a step whose flag no version of ``steps.md`` holds: whether
    it moves money, and where that was read (a commit or a handoff's path)."""

    key: str
    money: bool
    source: str


class Registry:
    """``steps.md`` as the commits of one repository hold it, each version parsed once."""

    def __init__(self, root: Path) -> None:
        """Read the repository at ``root``; nothing is parsed until a version is asked for."""
        self.root = root
        self._parsed: dict[str, dict[str, StepRow]] = {}

    def rows_at(self, commit: str) -> dict[str, StepRow] | None:
        """``{key: row}`` for every step ``steps.md`` holds at ``commit``; None when the
        commit's tree holds no ``steps.md``.

        Raises:
            ReleaseError: when that version does not parse.
        """
        blob = steps_blob(self.root, commit)
        if blob is None:
            return None
        if blob not in self._parsed:
            text = git(self.root, "cat-file", "blob", blob)
            try:
                parsed = step_rows(text=text)
            except AssertionError as malformed:
                # The plan gate's reader refuses a malformed table by assertion.
                raise ReleaseError(f"{STEPS_PATH} at {commit[:12]} does not parse, so what it "
                                   f"says cannot be read: {malformed}") from malformed
            self._parsed[blob] = {row.key: row for row in parsed}
        return self._parsed[blob]

    def versions(self, base: str, cut: str) -> list[str]:
        """Every commit in ``<base>..<cut>`` that changes ``steps.md``, on every line of
        history (``--full-history`` keeps a side branch a merge took nothing from), and
        ``base``."""
        out = git(self.root, "rev-list", "--full-history", f"{base}..{cut}", "--", STEPS_PATH)
        return [*out.split(), base]


def released(root: Path, base: str) -> frozenset[str]:
    """The key of every step a ``Ships:`` trailer on ``base``'s history claims."""
    return frozenset(claim.key for claim in trailers.history(root, base).old_form)


def _class_rows(rows: dict[str, StepRow], key: str) -> list[StepRow]:
    """Every row of one version that is the step ``key`` names: its own row and each row
    the plan gate's one derivation (``identity_class``) makes the same step, so a step
    filed under two names is read under both."""
    named = [row for row in rows.values() if row.key == key or key in row.alias_keys()]
    every = list(rows.values())
    return list({member.key: member for row in named
                 for member in identity_class(row, every)}.values())


def _money(registry: Registry, versions: list[str], key: str) -> bool | None:
    """Whether any version holding ``key``'s step open, under any of its names, labels it a
    money mover; None when no version holds it open."""
    opened = [row for version in versions
              for row in _class_rows(registry.rows_at(version) or {}, key)
              if not row.shipped]
    if not opened:
        return None
    return any(MOVES_MONEY_LABEL in step_labels(row) for row in opened)


def _work_leaves(container: StepRow, rows: dict[str, StepRow]) -> list[str]:
    """``container``'s leaves, a leaf that is itself split counting as its own leaves (a
    leaf's id extends its container's, so the descent ends)."""
    found: list[str] = []
    for key in decomposition_leaf_keys(container, list(rows.values())):
        row = rows.get(key)
        if row is not None and row.is_decomposed_parent:
            found += _work_leaves(row, rows)
        else:
            found.append(key)
    return list(dict.fromkeys(found))


def _groups(rows: dict[str, StepRow]) -> dict[str, list[str]]:
    """``{container key: its leaves}`` for every container the producer labels
    deploy-together at the cut."""
    return {key: _work_leaves(row, rows) for key, row in rows.items()
            if DEPLOY_TOGETHER_LABEL in step_labels(row)}


def _unreadable(found: trailers.History, lost: list[str]) -> list[str]:
    """Each thing this reader cannot read: a card trailer, whose labels are the tracker's
    before the cutover; a value naming nothing; and each deploy-together key ``lost``,
    which names a row in the window and none at the cut."""
    return ([f"{t.sha[:12]} carries `{t.key}: plan#{t.card}`: before the cutover a card's "
             "labels are the tracker's, which this reader does not read"
             for t in found.trailers]
            + [f"{line} (nor the old <arc>:<id> form)" for line in found.malformed]
            + [f"{key} is a deploy-together group (R-BAL240) whose row the window holds and "
               f"the cut does not, so whether this release splits it cannot be read"
               for key in lost])


def _touched(groups: Mapping[str, list[str]], claims: dict[str, list[str]],
             shipped: frozenset[str]) -> tuple[Group, ...]:
    """Each group one of whose leaves ``claims`` names, its leaves sorted three ways."""
    return tuple(
        Group(group,
              tuple(leaf for leaf in leaves if leaf in claims),
              tuple(leaf for leaf in leaves if leaf not in claims and leaf in shipped),
              tuple(leaf for leaf in leaves if leaf not in claims and leaf not in shipped))
        for group, leaves in groups.items() if any(leaf in claims for leaf in leaves))


@dataclass(frozen=True)
class Knowledge:
    """What the release's cut knows, read once and applied to the cut and to every earlier
    cut offered in its place, so an offered cut is judged by everything the cut knows:
    each claimed step's flag (read over the cut's whole window, else declared, else None)
    and the source of its declaration; each deploy-together group's leaves at the cut;
    and each deploy-together container the window holds and the cut does not."""

    flags: Mapping[str, tuple[bool | None, str | None]]
    groups: Mapping[str, list[str]]
    lost: tuple[str, ...]


def knowledge(registry: Registry, base: str, cut: str,
              declared: Mapping[str, Declaration]) -> Knowledge:
    """What ``cut`` knows of the release ``<base>..<cut>``; ``declared`` holds the skill's
    declarations, by key, which fill only a flag no version holds.

    Raises:
        ReleaseError: when the cut's tree holds no ``steps.md`` (after the cutover), or a
            version of it in the window does not parse.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    at_cut = registry.rows_at(cut)
    if at_cut is None:
        raise ReleaseError(
            f"{cut[:12]} holds no {STEPS_PATH}, so it is after the cutover, whose reader "
            "is the cards' (tools.release._cards), never this one")
    versions = registry.versions(base, cut)
    flags: dict[str, tuple[bool | None, str | None]] = {}
    for claim in trailers.history(registry.root, f"{base}..{cut}").old_form:
        if claim.key not in flags:
            money = _money(registry, versions, claim.key)
            declaration = declared.get(claim.key) if money is None else None
            flags[claim.key] = ((declaration.money, declaration.source) if declaration
                                else (money, None))
    containers = {row.key for version in versions
                  for row in (registry.rows_at(version) or {}).values()
                  if DEPLOY_TOGETHER_LABEL in step_labels(row)}
    return Knowledge(flags, _groups(at_cut), tuple(sorted(containers - set(at_cut))))


def carried(root: Path, base: str, cut: str, known: Knowledge,
            shipped: frozenset[str]) -> Carried:
    """What ``<base>..<cut>`` carries, judged with ``known``, the release's cut's
    :class:`Knowledge` (``cut`` is that cut or an earlier one); ``shipped`` is
    :func:`released`'s answer for ``base``.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    found = trailers.history(root, f"{base}..{cut}")
    claims: dict[str, list[str]] = {}
    for claim in found.old_form:
        claims.setdefault(claim.key, []).append(claim.sha)
    steps = []
    for key, shas in sorted(claims.items()):
        money, source = known.flags.get(key, (None, None))
        steps.append(Step(key, tuple(shas), money,
                          tuple(group for group, leaves in known.groups.items()
                                if key in leaves), source))
    return Carried(tuple(steps), _touched(known.groups, claims, shipped),
                   tuple(_unreadable(found, list(known.lost))))
