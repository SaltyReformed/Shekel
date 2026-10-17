"""Where a record is judged (the coordinator's ruling on delta review 2, REVISED (B)): record
and apply refuse a working tree that is not ``HEAD``'s, judged by its files whatever the
index or a machine's own rules say (delta review 3's H-1), and record a source ``HEAD`` does
not track; apply and prove refuse another ``HEAD``, environment or plan than the record's
(the plan: the fourteenth unit correction); prove refuses a tree that differs from ``HEAD``
anywhere but at exactly the paths apply writes; record and prove refuse a change made
while their runs ran (delta review 3's M-1); and record and prove refuse another day than
the one the record's runs began on, prove before its runs and both after them (the
coordinator's ruling on M-1, 2026-10-17)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split._render import importer_rewrites, line_limit, render_all
from tools.split.conftest import (
    IMPORTER,
    PACKAGE,
    SOURCE,
    applied,
    committed,
    display_home,
    everything,
    layout_of,
    plan_bytes,
    plan_dict,
)
from tools.split.split import _META

#: The tracked file most of the cases below change.
TRACKED = "tests/shared.py"

#: A conftest pytest loads for the source's tests, which the sample does not hold.
AREA_CONFTEST = "tests/test_area/conftest.py"


def _edited(root: Path) -> None:
    """A tracked file edited."""
    path = root / TRACKED
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")


def _importer_added(root: Path) -> None:
    """An untracked module importing the source."""
    (root / "tests/test_late.py").write_text("from tests.test_area.test_sample import _make\n",
                                             encoding="utf-8")


def _flagged_edit(flag: str, path: str):
    """``path`` flagged in the index with ``flag``, then edited: a change ``git status``
    never compares (delta review 3's P3, and its P7 for the source)."""
    def change(root: Path) -> None:
        scratch.run(root, "update-index", flag, path)
        target = root / path
        target.write_text(target.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    return change


def _excluded_conftest(root: Path) -> None:
    """An untracked conftest pytest would load, named in ``.git/info/exclude``, a rule kept
    per machine (delta review 3's P3b)."""
    (root / AREA_CONFTEST).write_text('"""Area."""\n', encoding="utf-8")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text(f"{AREA_CONFTEST}\n", encoding="utf-8")


def _hiding_gitignore(root: Path) -> None:
    """An untracked ``.gitignore`` holding ``*`` beside an untracked conftest, hiding both
    (the thirteenth unit correction)."""
    (root / AREA_CONFTEST).write_text('"""Area."""\n', encoding="utf-8")
    (root / AREA_CONFTEST).with_name(".gitignore").write_text("*\n", encoding="utf-8")


def _staged(root: Path) -> None:
    """A new file added to the index and not committed."""
    (root / "notes.txt").write_text("staged\n", encoding="utf-8")
    scratch.run(root, "add", "notes.txt")


#: Each way the working tree can differ from HEAD: the one path it differs at, and the change.
DIFFERENCES = {
    "a tracked file edited": (TRACKED, _edited),
    "a tracked file deleted": (TRACKED, lambda root: (root / TRACKED).unlink()),
    "a tracked file's mode changed": (TRACKED, lambda root: (root / TRACKED).chmod(0o755)),
    "an untracked module importing the source": ("tests/test_late.py", _importer_added),
    "a new file staged": ("notes.txt", _staged),
    "a tracked file edited under --skip-worktree": (
        TRACKED, _flagged_edit("--skip-worktree", TRACKED)),
    "a tracked file edited under --assume-unchanged": (
        TRACKED, _flagged_edit("--assume-unchanged", TRACKED)),
    "the source edited under --skip-worktree": (SOURCE, _flagged_edit("--skip-worktree",
                                                                      SOURCE)),
    "an untracked conftest named in .git/info/exclude": (AREA_CONFTEST, _excluded_conftest),
    "an untracked .gitignore hiding an untracked conftest": ("tests/test_area/.gitignore",
                                                             _hiding_gitignore),
}


#: What each command says it did not do.
DONE = {"record": "recorded", "apply": "applied"}


@pytest.mark.parametrize("difference", DIFFERENCES)
@pytest.mark.parametrize("command", ["record", "apply"])
def test_a_tree_that_is_not_heads_is_refused(split, capsys, command, difference):
    """record and apply each refuse a working tree that differs from HEAD, naming where,
    before anything is written or discarded: the earlier record, the tree and the id maps are
    as they were (the coordinator's ruling on delta review 2, REVISED (B) (1)).  A file the
    index flags, one a machine's own rule hides and an untracked ``.gitignore`` are each a
    difference (delta review 3's H-1 and the thirteenth unit correction): the source edited
    under ``--skip-worktree`` after record was refused only by apply's source hash check,
    which this made unnecessary and deleted."""
    assert split("record") == 0
    path, change = DIFFERENCES[difference]
    change(split.root)
    before = everything(split)
    capsys.readouterr()
    assert split(command) == 1
    err = capsys.readouterr().err
    assert f"REFUSED: the working tree differs from HEAD at 1 path(s), e.g. ['{path}']" in err
    assert err.endswith(f"nothing is {DONE[command]}\n")
    assert everything(split) == before


def _moved(split, _monkeypatch) -> None:
    """A commit after the record: HEAD moves, and the tree is HEAD's."""
    (split.root / "README.md").write_text("A commit after the record.\n", encoding="utf-8")
    committed(split.root, "after the record")


def _another_environment(_split, monkeypatch) -> None:
    """The process runs with another interpreter, distributions or git than record's."""
    monkeypatch.setattr(run, "environment", lambda _root: "0" * 64)


def _without(key: str):
    """A record an earlier version of the tool wrote, holding no ``key``."""
    def change(split, _monkeypatch) -> None:
        path = split.into / _META
        meta = json.loads(path.read_text(encoding="utf-8"))
        del meta[key]
        path.write_text(json.dumps(meta), encoding="utf-8")
    return change


#: The module the other plan names the sample plan's first part.
RENAMED = "test_opening"


def other_plan() -> dict:
    """The sample plan with its first part named otherwise, which re-points every importer
    as the sample plan does (the premise, tested below)."""
    plan = plan_dict()
    plan["parts"][0]["module"] = RENAMED
    return plan


def _another_plan(split, _monkeypatch) -> None:
    """The plan file holds another plan."""
    split.plan.write_bytes(plan_bytes(other_plan()))


def _reindented_plan(split, _monkeypatch) -> None:
    """The plan file holds the same plan in other bytes: a record keeps the sha256 of the
    plan's BYTES, never of a parsed form, so even this is another plan (the coordinator's
    ruling on 3C-2's report, which rejected a canonical form's hash)."""
    split.plan.write_bytes(json.dumps(plan_dict(), indent=1).encode("utf-8"))


#: How a refusal names a record taken with another environment, and under another plan.
ANOTHER_ENVIRONMENT = ("the record was taken with another interpreter, other installed "
                       "distributions or another git, or by a version of this tool that did "
                       "not record them")
ANOTHER_PLAN = ("the record was taken under another plan, or by a version of this tool that "
                "did not record its plan")
NO_START = ("the record was taken by a version of this tool that did not record when its "
            "runs began")

#: Each state a record is not judged in, as a change made after it, and how the refusal
#: names it (None: the two commits, which the case reads).
ELSEWHERE = {
    "HEAD moved": (_moved, None),
    "another environment": (_another_environment, ANOTHER_ENVIRONMENT),
    "no environment kept": (_without("environment"), ANOTHER_ENVIRONMENT),
    "another plan": (_another_plan, ANOTHER_PLAN),
    "the same plan in other bytes": (_reindented_plan, ANOTHER_PLAN),
    "no plan kept": (_without("plan"), ANOTHER_PLAN),
    "no start instant kept": (_without("started"), NO_START),
}

#: What each command's refusal ends with.
TAILS = {"apply": "record again; nothing is applied",
         "prove": "a split is proved where it was recorded, before it is committed"}


@pytest.mark.parametrize("state", ELSEWHERE)
@pytest.mark.parametrize("command", ["apply", "prove"])
def test_a_record_is_judged_only_where_it_was_taken(split, capsys, monkeypatch, command,
                                                    state):
    """apply and prove each refuse unless HEAD, the environment and the plan are the
    record's, and the tree, the record and the id maps are byte-identical after; a record
    holding no environment, or no plan, is another version's (REVISED (B) (1) and (2); the
    plan, the fourteenth unit correction), and so is one holding no instant its runs began
    at (the coordinator's ruling on M-1's build, (3), 2026-10-17).  For prove, HEAD moved by
    committing the split itself, the tree then HEAD's.  Before the plan was kept, another
    plan was refused by name nowhere: apply wrote the other plan's split, and prove refused
    the tree as other than the plan's writes."""
    assert split("record") == 0
    if command == "prove":
        assert split("apply") == 0
    taken = scratch.run(split.root, "rev-parse", "HEAD")
    change, said = ELSEWHERE[state]
    change(split, monkeypatch)
    before = everything(split)
    capsys.readouterr()
    assert split(command) == 1
    if said is None:
        said = (f"HEAD is {scratch.run(split.root, 'rev-parse', 'HEAD')[:9]}, and the record "
                f"was taken at {taken[:9]}")
    assert f"REFUSED: {said}; {TAILS[command]}" in capsys.readouterr().err
    assert everything(split) == before


def test_the_other_plan_differs_from_the_sample_in_its_parts_alone(repo):
    """The premise of the other plan's case: it writes another package and re-points every
    importer exactly as the sample plan does, so only the plan's identity tells a record
    taken under one from the other (the coordinator's ruling on 3C-2's report)."""
    sample, other = layout_of(repo), layout_of(repo, other_plan())
    assert importer_rewrites(other, repo) == importer_rewrites(sample, repo)
    assert render_all(other, line_limit(repo)).keys() != render_all(sample,
                                                                     line_limit(repo)).keys()


def _package_only(split) -> None:
    """An apply killed once the package was written: the source and importer as before."""
    scratch.run(split.root, "checkout", "--quiet", "--", SOURCE, IMPORTER)


def _part_of_the_package(split) -> None:
    """An apply killed while the package was written."""
    _package_only(split)
    for name in ("test_first.py", "test_big_late.py"):
        (split.root / PACKAGE / name).unlink()


#: Each tree prove refuses after an apply, and the two lists its refusal names.
NOT_THE_WRITES = {
    "killed once the package was written": (_package_only, [SOURCE, IMPORTER], []),
    "killed while the package was written": (_part_of_the_package, [
        SOURCE, f"{PACKAGE}/test_big_late.py", f"{PACKAGE}/test_first.py", IMPORTER], []),
    "a change beside the split": (
        lambda split: (split.root / "notes.txt").write_text("x\n", encoding="utf-8"),
        [], ["notes.txt"]),
}


@pytest.mark.parametrize("shape", NOT_THE_WRITES)
def test_prove_refuses_a_tree_other_than_what_apply_wrote(split, capsys, shape):
    """prove refuses unless the tree differs from HEAD at EXACTLY the paths apply writes: a
    partial write set (an apply ``SIGKILL`` ended) is never proved, and a change beside the
    split is judged by no arm (the coordinator's ruling on 3B-2's signal fork, (d))."""
    applied(split)
    change, missing, beside = NOT_THE_WRITES[shape]
    change(split)
    capsys.readouterr()
    assert split("prove") == 1
    assert ("REFUSED: the working tree must differ from HEAD at exactly the paths apply "
            f"writes: {len(missing)} written by apply and not different, e.g. {missing}; "
            f"{len(beside)} different and not written by apply, e.g. {beside}"
            in capsys.readouterr().err)


def _committed_beside(split, _monkeypatch) -> None:
    """A file beside the split committed: ``HEAD`` moves, and the tree's differences stay."""
    (split.root / "README.md").write_text("A commit while the runs ran.\n", encoding="utf-8")
    scratch.run(split.root, "add", "README.md")
    scratch.run(split.root, "commit", "--quiet", "-m", "while the runs ran")


#: Each change made while a command's runs run, and how its refusal names it.
DURING = {
    "a tracked file edited": (lambda split, _monkeypatch: _edited(split.root),
                              f"the working tree changed at 1 path(s), e.g. ['{TRACKED}']"),
    "a file written beside the split": (
        lambda split, _monkeypatch: (split.root / "notes.txt").write_text(
            "x\n", encoding="utf-8"),
        "the working tree changed at 1 path(s), e.g. ['notes.txt']"),
    "a commit": (_committed_beside, "HEAD moved from "),
    "another environment": (_another_environment, "the environment changed"),
    "the display home broken": (
        lambda split, _monkeypatch: display_home(split.root, "DISPLAY_TIMEZONE = (\n"),
        "the working tree changed at 1 path(s), e.g. ['app/utils/dates.py']"),
}

#: What each command's refusal of a change while its runs ran ends with.
WITHHELD = {"record": "nothing is recorded", "prove": "no block is printed"}


@pytest.mark.parametrize("change", DURING)
@pytest.mark.parametrize("command", ["record", "prove"])
def test_a_change_while_the_runs_ran_is_refused(split, capsys, monkeypatch, command, change):
    """record and prove judge HEAD, the environment and the tree again after their runs: a
    change made while they ran is refused, naming it, and record writes no token, prove
    prints no block (the coordinator's ruling on delta review 3's M-1, 2026-10-16: each was
    judged only at its start, and a run is minutes long).  A display home broken while they
    ran is named as that change to the tree: the zone was read once, before the runs (the
    lane review of M-1, M-2: read again after them, the refusal named the home alone)."""
    if command == "prove":
        applied(split)
    make, said = DURING[change]
    stub, made = run.observe, []

    def observe(*args, **kwargs):
        if not made:
            made.append(make(split, monkeypatch))
        return stub(*args, **kwargs)
    monkeypatch.setattr(run, "observe", observe)
    capsys.readouterr()
    assert split(command) == 1
    out, err = capsys.readouterr()
    assert f"REFUSED: {said}" in err
    assert err.endswith(f" while the runs ran, so no observation was made in the state judged; "
                        f"{WITHHELD[command]}\n")
    assert out == ""
    if command == "record":
        assert not (split.into / _META).exists()


#: What a refusal of a date that moved ends with: the cure for its own command, at apply and
#: prove before they write or run anything, and at record and prove after the runs (the
#: coordinator's rulings on M-1, condition 1, and on its lane review, (B), 2026-10-17).
PROVE_CURE = "revert the apply, record into a new --into, apply and prove on one day"
BEFORE_CURES = {"apply": "record again, then apply and prove on one day; nothing is applied",
                "prove": PROVE_CURE}
CURES = {"record": "record again on one day; nothing is recorded",
         "prove": f"{PROVE_CURE}; no block is printed"}

#: Each day an apply or a prove can run on that is not the record's: the instant the record's
#: runs began, the command's, and each date its refusal names, worked by hand (the sample's
#: display zone is Asia/Tokyo, UTC+9, and each instant's own offset is the local zone's).
ANOTHER_DAY = {
    "a day later": ("2026-10-16T12:00:00-04:00", "2026-10-17T12:00:00-04:00",
                    "in UTC from 2026-10-16 to 2026-10-17, in Asia/Tokyo from 2026-10-17 to "
                    "2026-10-18, in the local zone from 2026-10-16 to 2026-10-17"),
    "past midnight in the display zone alone": (
        "2026-10-16T10:00:00-04:00", "2026-10-16T11:30:00-04:00",
        "in Asia/Tokyo from 2026-10-16 to 2026-10-17"),
    "past midnight in UTC alone": ("2026-10-16T19:00:00-04:00", "2026-10-16T20:30:00-04:00",
                                   "in UTC from 2026-10-16 to 2026-10-17"),
    "another local date under another TZ": (
        "2026-10-17T09:00:00+14:00", "2026-10-16T16:00:00-04:00",
        "in the local zone from 2026-10-17 to 2026-10-16"),
}


def _observed(monkeypatch, then=None) -> list:
    """Each run :func:`tools.split._run.observe` is asked for from here on, ``then`` called
    before the first."""
    stub, asked = run.observe, []

    def observe(*args, **kwargs):
        if not asked and then is not None:
            then()
        asked.append(args)
        return stub(*args, **kwargs)
    monkeypatch.setattr(run, "observe", observe)
    return asked


@pytest.mark.parametrize("day", ANOTHER_DAY)
@pytest.mark.parametrize("command", ["apply", "prove"])
def test_a_command_on_another_day_is_refused_before_it_writes_or_runs(split, capsys,
                                                                      monkeypatch, command,
                                                                      day):
    """apply and prove each refuse, before they write or run anything, a day that is not the
    record's in UTC, in the display zone or in the local zone, naming both instants, each
    zone whose date moved and the cure for the command, with the tree, the record and the id
    maps as they were, no run asked for and no directory made for a prove (the
    coordinator's rulings on M-1, R2 and condition 1, and on its lane review, (B),
    2026-10-17: before (B), an apply on another day wrote the split).  The display zone is
    the one the repository's own home names (:func:`tools.split._run.display_zone`): the
    sample's is not the app's, so a tool reading its own checkout's home misses the midnight
    in Asia/Tokyo.  The last case is R1's: the record's process ran under UTC+14 (``TZ``),
    and the command's under UTC-4 on the same UTC day, a day before in the local zone alone;
    a record keeping its instant in UTC re-reads it in the command's zone and sees no
    difference."""
    recorded, proved, said = ANOTHER_DAY[day]
    split.clock.at = datetime.fromisoformat(recorded)
    assert split("record") == 0
    if command == "prove":
        assert split("apply") == 0
    split.clock.at = datetime.fromisoformat(proved)
    asked = _observed(monkeypatch)
    before = everything(split)
    capsys.readouterr()
    assert split(command) == 1
    out, err = capsys.readouterr()
    assert err == (f"REFUSED: the date moved {said} since the record's runs began at "
                   f"{recorded} (it is now {proved}), and a record is judged only on the day "
                   f"its runs began; {BEFORE_CURES[command]}\n")
    assert (out, asked, list(split.into.glob("prove-*"))) == ("", [], [])
    assert everything(split) == before


#: A display home naming a zone whose offset changes: Europe/London leaves summer time (UTC+1)
#: for UTC at 01:00 UTC on 2026-10-25.
SUMMER_TIME_HOME = ('from zoneinfo import ZoneInfo\n\n'
                    'DISPLAY_TIMEZONE = ZoneInfo("Europe/London")\n')


def test_the_display_date_is_read_at_each_instants_own_offset(split, capsys):
    """The record's runs began at 00:30 on 2026-10-25 in London's summer time, and the prove
    runs at 23:30 that day in its winter time: the same London date, though one read at the
    record's offset (UTC+1) would be the next day, so only UTC's date moved (the lane review
    of M-1, nit 2: the sample's zone keeps one offset, so no case read a change of it)."""
    display_home(split.root, SUMMER_TIME_HOME)
    committed(split.root, "a display home with summer time")
    recorded, proved = "2026-10-25T00:30:00+01:00", "2026-10-25T23:30:00+00:00"
    split.clock.at = datetime.fromisoformat(recorded)
    applied(split)
    split.clock.at = datetime.fromisoformat(proved)
    capsys.readouterr()
    assert split("prove") == 1
    assert capsys.readouterr().err == (
        f"REFUSED: the date moved in UTC from 2026-10-24 to 2026-10-25 since the record's runs "
        f"began at {recorded} (it is now {proved}), and a record is judged only on the day its "
        f"runs began; {PROVE_CURE}\n")


@pytest.mark.parametrize("command", ["record", "prove"])
def test_a_midnight_passed_while_the_runs_ran_is_refused(split, capsys, monkeypatch,
                                                         command):
    """record and prove judge the day again after their runs: the date in the display zone
    moved while they ran (Asia/Tokyo's midnight is 11:00 in UTC-4), so record writes no
    token and prove prints no block, the refusal naming the record's start, this instant
    and the cure for its command (the coordinator's ruling on M-1, R2, 2026-10-17).  prove
    started on the record's day, so its check before the runs passed."""
    began = "2026-10-16T10:00:00-04:00" if command == "prove" else "2026-10-16T10:30:00-04:00"
    split.clock.at = datetime.fromisoformat(began)
    if command == "prove":
        applied(split)
        split.clock.at = datetime.fromisoformat("2026-10-16T10:30:00-04:00")

    def midnight() -> None:
        split.clock.at = datetime.fromisoformat("2026-10-16T11:30:00-04:00")
    _observed(monkeypatch, midnight)
    capsys.readouterr()
    assert split(command) == 1
    out, err = capsys.readouterr()
    assert err == ("REFUSED: the date moved in Asia/Tokyo from 2026-10-16 to 2026-10-17 since "
                   f"the record's runs began at {began} (it is now 2026-10-16T11:30:00-04:00) "
                   "while the runs ran, so no observation was made in the state judged; "
                   f"{CURES[command]}\n")
    assert out == ""
    if command == "record":
        assert not (split.into / _META).exists()


def test_a_change_and_a_midnight_while_the_runs_ran_are_named_together(split, capsys,
                                                                        monkeypatch):
    """A tracked file edited and a midnight passed while record's runs ran: one refusal names
    both, and the day's cure before what was not done (the lane review of M-1, nit 3: a day
    judged only when nothing else moved went unseen)."""
    split.clock.at = datetime.fromisoformat("2026-10-16T10:30:00-04:00")

    def both() -> None:
        _edited(split.root)
        split.clock.at = datetime.fromisoformat("2026-10-16T11:30:00-04:00")
    _observed(monkeypatch, both)
    capsys.readouterr()
    assert split("record") == 1
    assert capsys.readouterr().err == (
        f"REFUSED: the working tree changed at 1 path(s), e.g. ['{TRACKED}']; the date moved in "
        "Asia/Tokyo from 2026-10-16 to 2026-10-17 since the record's runs began at "
        "2026-10-16T10:30:00-04:00 (it is now 2026-10-16T11:30:00-04:00) while the runs ran, "
        f"so no observation was made in the state judged; {CURES['record']}\n")


def test_a_display_home_that_does_not_load_is_refused_before_anything_runs(split, capsys,
                                                                           monkeypatch):
    """A display home that does not load is a refused line naming its path, never a
    traceback, before any other refusal and before anything runs or is written: no run asked
    for, no record directory (the coordinator's ruling on M-1, condition 2, as amended by the
    ruling on M-1's build, (2), and the ruling on its lane review, (B), 2026-10-17: read at
    each judgment, record refused it only after every run)."""
    path = display_home(split.root, "DISPLAY_TIMEZONE = (\n")
    committed(split.root, "a display home that does not compile")
    asked = _observed(monkeypatch)
    capsys.readouterr()
    assert split("record") == 1
    out, err = capsys.readouterr()
    assert err.startswith(f"REFUSED: {path} did not load (SyntaxError: ")
    assert (out, asked, split.into.exists()) == ("", [], False)


def _untracked(root: Path, ignored: bool) -> None:
    """The source taken out of ``HEAD`` and left on disk untracked, named by the committed
    ``.gitignore`` too when ``ignored``."""
    if ignored:
        (root / ".gitignore").write_text(f"{SOURCE}\n", encoding="utf-8")
        scratch.run(root, "add", ".gitignore")
    scratch.run(root, "rm", "--cached", "--quiet", SOURCE)
    scratch.run(root, "commit", "--quiet", "-m", "the source untracked")


@pytest.mark.parametrize("ignored,said", [
    (False, f"the working tree differs from HEAD at 1 path(s), e.g. ['{SOURCE}']"),
    (True, f"{SOURCE} is not a file HEAD tracks, and a record is judged only against HEAD"),
], ids=["untracked", "untracked and ignored"])
def test_record_refuses_a_source_head_does_not_track(split, capsys, ignored, said):
    """A record is judged only against HEAD, so its source must be a file HEAD tracks: an
    untracked source is a difference, and one the committed rules also ignore is no
    difference, so it is refused by name (the coordinator's ruling on 3B-2b's REVISED (B)
    consequences, item 2, which deletes apply's source and importer re-checks)."""
    _untracked(split.root, ignored)
    capsys.readouterr()
    assert split("record") == 1
    assert f"REFUSED: {said}" in capsys.readouterr().err
    assert not (split.into / _META).exists()
