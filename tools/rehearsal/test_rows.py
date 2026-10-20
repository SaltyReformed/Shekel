"""The rows (step X-cx's K1b-1): the noise gate, the planted controls, the rollback and the
verdict.  The side's environment and its probes are :mod:`.test_probes`'s, and the row
grader is :mod:`.test_rowdiff`'s.

The cases marked ``cluster`` start a real private cluster through a :class:`bench.Bench` on the
conftest's ``dump``; the last of them deploy both sides by production's own step, so they also
need each side's venv (cached after its first build).  The rest grade the pure parts on planted
inputs.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from tools.rehearsal import bench, rows
from tools.rehearsal.rowdiff import Moved


def test_a_control_is_found_only_when_it_is_the_one_row_and_column_found(moved_rows):
    """Exactly the planted row and column; a second row, another key or column, or a
    removal read as a change, is not found."""
    planted = ("budget.accounts", "[3]", "name")
    assert rows.found([moved_rows[0]], planted) is False
    one = Moved("budget.accounts", "[3]", {"id": 3, "name": "A"}, {"id": 3, "name": "B"})
    assert rows.found([one], planted) is True
    assert rows.found([one, moved_rows[3]], planted) is False
    assert rows.found([one], ("budget.accounts", "[4]", "name")) is False
    assert rows.found([one], planted, removed=True) is False
    gone = Moved("budget.accounts", "[6]", {"id": 6}, None)
    assert rows.found([gone], ("budget.accounts", "[6]", ""), removed=True) is True



def test_a_revision_resolves_only_by_a_migration_script_named_for_it(tmp_path):
    """As ``shekel-deploy.sh``'s ``image_resolves_revisions`` reads the image's listing: a
    ``<revision>_*.py`` file for every revision, matched up to its ``_`` (a revision that is
    another's prefix does not resolve), a file not ``.py`` is not a script, and none to
    resolve resolves."""
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    for name in ("abc123_add_x.py", "def456_y.py", "fed789_z.txt"):
        (versions / name).write_text("", encoding="utf-8")
    assert rows.base_resolves(tmp_path, ["abc123"]) is True
    assert rows.base_resolves(tmp_path, ["abc123", "def456"]) is True
    assert rows.base_resolves(tmp_path, []) is True
    assert rows.base_resolves(tmp_path, ["abc12"]) is False
    assert rows.base_resolves(tmp_path, ["fed789"]) is False
    assert rows.base_resolves(tmp_path, ["abc123", "0ff0ff"]) is False


class _Failing:
    """A bench whose making fails as ``error`` does: a file or the database, not the kit."""

    def __init__(self, error: Exception) -> None:
        """Fail with ``error``."""
        self.error = error

    def __call__(self, *_given: object) -> _Failing:
        """Stand in for :class:`bench.Bench`'s constructor."""
        return self

    def __enter__(self) -> bench.Bench:
        """Fail as the making would."""
        raise self.error

    def __exit__(self, *_given: object) -> None:
        """Nothing was made."""


@pytest.mark.parametrize("error", [OSError(2, "No such file or directory"),
                                   psycopg.OperationalError("the cluster went away")])
def test_a_run_that_fails_itself_exits_2_never_the_1_that_says_rows_differ(monkeypatch,
                                                                         capsys, error):
    """A file the run cannot write or a database error ends it measuring nothing (exit 2),
    named, where Python's own exit status for it would be 1, the verdict that rows differ."""
    monkeypatch.setattr(bench, "Bench", _Failing(error))
    assert rows.main(["--dump", "d", "--base", "b", "--cut", "c", "--report", "r"]) == 2
    assert capsys.readouterr().err == f"[rehearsal] {error}\n"


#: A line before every stamp the tests write, so each reads as written by a deploy.
_LONG_AGO = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
#: A line after every stamp the tests write, so each reads exactly.
_FAR_AHEAD = datetime(2100, 1, 1, tzinfo=ZoneInfo("UTC"))


@pytest.mark.cluster
def test_each_control_is_planted_in_one_row_and_found_exactly(clock, planted):
    """The cent in the first money column, the byte in the first text column (its length
    kept under a length limit), the removal of the first keyed row, and the first stamp moved
    a microsecond before both itself and the line, whether the line is after it or before it
    (a stamp a deploy wrote): each moves its one row, no trigger writing an audit event, and
    the comparison finds it and nothing else."""
    base = planted.made["base"]
    line = clock(base.url)
    for name, plant, removed, expected in (
            ("cent", rows.plant_cent, False, ("public.ledger", "[1]", "amount")),
            ("byte", rows.plant_byte, False, ("public.note", "[1, 1]", "body")),
            ("removal", rows.plant_removal, True, ("public.ledger", "[1]", ""))):
        url = planted.clone(base.database, name)
        assert plant(url) == expected
        moved = rows.compare(base.url, url, line)
        assert rows.found(moved, expected, removed), moved
    assert moved[0].before == {"id": 1, "amount": Decimal("12.34")}
    assert not rows.compare(base.url, planted.clone(base.database, "untouched"), line)
    for name, at in (("earlier", line), ("earlier_than_a_deploy_s", _LONG_AGO)):
        url = planted.clone(base.database, name)
        planted_at = rows.plant_earlier(url, at)
        assert planted_at == ("public.note", "[1, 1]", "created_at")
        moved = rows.compare(base.url, url, at)
        assert rows.found(moved, planted_at), (name, moved)


@pytest.mark.cluster
@pytest.mark.parametrize("case", ["read alike", "not later than the line", "the same value"])
def test_a_stamp_the_clock_wrote_on_two_clones_in_turn_is_not_found(clock, planted, monkeypatch,
                                                                     case):
    """The fifth control: two values the clock wrote, each later than the line, read alike.
    Two values no later than the line, or one value on both, refuse, since the control would
    then measure nothing."""
    base = planted.made["base"]
    line = {"read alike": clock(base.url), "not later than the line": _FAR_AHEAD,
            "the same value": _LONG_AGO}[case]
    if case == "the same value":
        monkeypatch.setattr(rows, "plant_now", lambda url: rows.plant_earlier(url, _FAR_AHEAD))
    wrong = rows.rewritten_twice(planted, base, line)
    if case == "read alike":
        assert wrong is None
    else:
        assert wrong is not None and wrong.startswith(
            "a stamp the clock wrote on two clones in turn planted at public.note [1, 1] "
            "created_at and public.note [1, 1] created_at wrote ")
        assert wrong.endswith(f"not two moments each later than the line {line.isoformat()}, "
                              "so it measures nothing")


@pytest.mark.cluster
def test_every_control_runs_and_a_wrong_one_is_named(clock, planted, monkeypatch):
    """The four found exactly pass on the planted copy, and what the fifth says is wrong is
    named among what is wrong."""
    base = planted.made["base"]
    monkeypatch.setattr(rows, "rewritten_twice", lambda *_given: "the fifth, planted")
    assert rows.controls(planted, base, clock(base.url)) == ["the fifth, planted"]


@pytest.mark.cluster
def test_a_control_no_row_can_take_is_refused_by_name(planted):
    """A copy with no text column outside a key refuses the byte by name."""
    base = planted.made["base"]
    url = planted.clone(base.database, "bare")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP TABLE note; DROP TABLE tally; DROP SCHEMA system CASCADE")
    with pytest.raises(bench.BenchError, match=r"no row of the copy could take a control in "
                                               r"a column of \['text', 'character varying'\]"):
        rows.plant_byte(url)


def _rehearsal(dump: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
               ) -> tuple[int, str, str, str]:
    """``python -m tools.rehearsal.rows`` on the dump, base and cut both HEAD: its status,
    stdout, stderr and report."""
    out = tmp_path / "report.txt"
    status = rows.main(["--dump", str(dump), "--base", "HEAD", "--cut", "HEAD",
                        "--report", str(out)])
    said = capsys.readouterr()
    return status, said.out, said.err, out.read_text(encoding="utf-8") if out.exists() else ""


def _planting(database: str, plant: Callable[[str], object]) -> Callable[..., str | None]:
    """The deploy step, then ``plant`` on the copy, for the one deploy of ``database``."""
    deploy = rows.deploy

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        said = deploy(state, url, secret_key, moment)
        if state.database == database and said is None:
            plant(url)
        return said
    return deployed


def _executing(statement: str) -> Callable[[str], None]:
    """A plant that runs ``statement`` on the copy, triggers and foreign-key checks off."""
    def run(url: str) -> None:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(statement)
    return run


def _refusing(database: str) -> Callable[..., str | None]:
    """The deploy step, except for ``database``'s, which refuses having written nothing, as
    the step's one transaction leaves a refused copy."""
    deploy = rows.deploy

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        if state.database == database:
            return "exit 1:\nplanted"
        return deploy(state, url, secret_key, moment)
    return deployed


#: What a run whose every control read as planted prints of them.
_CONTROLS_LINE = ("controls: a cent, a byte, a removed row, a stamp moved earlier, each found "
                  "exactly; a stamp the clock wrote on two clones in turn, not found\n")


@pytest.mark.cluster
def test_two_sides_that_store_the_same_rows_exit_0_saying_what_was_not_compared(
        dump, tmp_path, capsys):
    """Each side deployed by production's step on the dump (a first deploy, here), base
    twice the same, every control found: exit 0, and the stated limits printed."""
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, err, report) == (0, "", "")
    assert re.search(r"^clock pinned at \d{4}-\d{2}-\d{2}T12:00:00-0[45]:00$", out, re.MULTILINE)
    assert re.search(r"^line: \d{4}-\d{2}-\d{2}T[0-9:.]+\+00:00, the cluster's clock just before "
                     r"the first deploy; a stamp later than it reads as 'deploy'$", out,
                     re.MULTILINE)
    assert "deploy base: " in out and "deploy base, again: " in out and "deploy cut: " in out
    assert "noise gate: " in out and ", 0 rows differ\n" in out
    assert _CONTROLS_LINE in out
    assert rows.NOT_COMPARED + "\n" in out
    assert ("compared, though the server's clock fills them: the days "
            "budget.transaction_entries.purchased_on, each the server's real day where a "
            "deploy inserts a row without one, so deploys either side of midnight in the "
            "server's zone differ by it (a false difference, never a false pass)\n") in out
    assert "compared as multisets: system.audit_log\n" in out
    assert "base against cut: " in out and "teardown: " in out
    assert re.search(r"^rollback: base's image knows the migration version cut's copy is "
                     r"stamped at \([0-9a-z]+\); base boots on cut's deployed copy, and base's "
                     r"deploy step, run again on it, exits 0 and moves no row$", out, re.MULTILINE)


@pytest.mark.cluster
def test_a_row_cut_s_deploy_stores_differently_exits_1_naming_it(dump, tmp_path, capsys,
                                                                 monkeypatch):
    """A cent cut's copy holds that base's does not is the one row differing: named in the
    report, summed on stdout, exit 1."""
    planted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(rows, "deploy", _planting(
        "rehearsal_cut", lambda url: planted.append(rows.plant_cent(url))))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    table, key, column = planted[0]
    assert status == 1
    assert report.startswith(f"{table} {key}: {column} ") and report.count("\n") == 1
    assert f"{table}: 1 rows differ (0 only on base, 0 only on cut, 1 changed)\n" \
        f"  {column}: 1 rows, total change +0.01\n" in out


@pytest.mark.cluster
def test_a_stamp_of_the_dump_cut_s_deploy_moves_earlier_exits_1_naming_it(dump, tmp_path,
                                                                          capsys, monkeypatch):
    """R-BAL331's own case: cut's deploy moves a stamp the dump holds a second earlier (a tie
    it breaks would flip); stamps are compared, so it is the one row differing, named in the
    report, summed on stdout, exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_cut", _executing(
        "UPDATE stamped SET created_at = created_at - interval '1 second'")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert status == 1
    assert report.startswith("public.stamped [1]: created_at ") and report.count("\n") == 1
    assert ("public.stamped: 1 rows differ (0 only on base, 0 only on cut, 1 changed)\n"
            "  created_at: 1 rows\n") in out


@pytest.mark.cluster
def test_stamps_each_deploy_writes_at_its_own_moment_read_alike(dump, tmp_path, capsys,
                                                                monkeypatch):
    """Every deploy, base's twice, cut's and the rollback's, writing the server's clock into
    the dump's stamp at its own moment: each stamp reads as a deploy's, so base deployed
    twice reads the same, nothing differs and the rollback moves no row, exit 0."""
    deploy = rows.deploy
    stamp = _executing("UPDATE stamped SET created_at = now()")

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        said = deploy(state, url, secret_key, moment)
        if said is None:
            stamp(url)
        return said
    monkeypatch.setattr(rows, "deploy", deployed)
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, err, report) == (0, "", "")
    assert "noise gate: " in out and ", 0 rows differ\n" in out
    assert "base against cut: " in out and ", 0 rows differ (each in " in out


@pytest.mark.cluster
def test_base_deployed_twice_reading_differently_measures_nothing(dump, tmp_path, capsys,
                                                                  monkeypatch):
    """A byte planted in base's second copy turns the noise gate: exit 2, named, no verdict."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_base_again", rows.plant_byte))
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (2, "")
    assert "noise gate: " in out and ", 1 rows differ\n" in out
    assert err.startswith("[rehearsal] base deployed twice did not read the same")
    assert "1 rows differ (0 only on base, 0 only on base again, 1 changed)" in err


@pytest.mark.cluster
def test_base_s_deploy_refusing_the_dump_measures_nothing(dump, tmp_path, capsys, monkeypatch):
    """Base's deploy refusing production's data leaves nothing to compare: exit 2, the
    refusal quoted."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_base"))
    status, _, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (2, "")
    assert err.startswith("[rehearsal] base's deploy refused the dump, so nothing is compared "
                          "(an app defect): exit 1:\nplanted")


@pytest.mark.cluster
def test_cut_s_deploy_refusing_what_base_s_took_is_a_difference(dump, tmp_path, capsys,
                                                                monkeypatch):
    """Cut's deploy refusing the copy base's deployed is a difference: exit 1, the refusal
    quoted, after the gates ran."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_cut"))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert _CONTROLS_LINE in out
    assert "cut's deploy refused the copy base's deployed: exit 1:\nplanted\n" in out


_AGAIN = "ROLLBACK FAILS: base's deploy step, run again on cut's deployed copy,"


@pytest.mark.cluster
def test_a_copy_stamped_where_base_s_tree_has_no_script_is_a_restore(dump, tmp_path, capsys,
                                                                     monkeypatch):
    """Cut's copy stamped at a revision base's tree carries no script for: shekel-deploy.sh
    would refuse the rollback, so the run says rolling back is a restore and runs neither the
    step nor the boot; the stamp is a row that differs, so exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_cut", _executing(
        "UPDATE public.alembic_version SET version_num = 'feedface0000'")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert status == 1 and "public.alembic_version" in report
    assert ("rollback: base's image does not know the migration version cut's copy is stamped "
            "at (feedface0000), so shekel-deploy.sh would refuse the rollback and name the dump "
            "to restore: rolling back past this release is a restore\n") in out
    assert "ROLLBACK FAILS" not in out


@pytest.mark.cluster
def test_base_s_step_refusing_cut_s_copy_fails_the_rollback_and_nothing_boots(
        dump, tmp_path, capsys, monkeypatch):
    """Base's deploy step, run again on cut's deployed copy, refusing it: production's
    container dies at step 3, so the rollback fails, quoted, exit 1, though no row differs."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_rollback"))
    monkeypatch.setattr(rows, "boot", lambda *_given: pytest.fail("the boot ran"))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert f"{_AGAIN} refused: exit 1:\nplanted\n" in out
    assert "\nrollback: " not in out


@pytest.mark.cluster
def test_base_s_step_moving_a_row_of_cut_s_copy_fails_the_rollback(dump, tmp_path, capsys,
                                                                   monkeypatch):
    """A cent base's step, run again on cut's deployed copy, moves: the rollback fails, the
    row summed, exit 1, though base and cut store the same rows; base still boots."""
    planted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(rows, "deploy", _planting(
        "rehearsal_rollback", lambda url: planted.append(rows.plant_cent(url))))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    table, _, column = planted[0]
    assert (status, report) == (1, "")
    assert (f"{_AGAIN} moved 1 rows:\n{table}: 1 rows differ (0 only on cut, 0 only on rolled "
            f"back, 1 changed)\n  {column}: 1 rows, total change +0.01\n") in out
    assert "cannot boot" not in out and "\nrollback: " not in out


@pytest.mark.cluster
def test_base_that_cannot_boot_on_cut_s_copy_fails_the_rollback(dump, tmp_path, capsys,
                                                                 monkeypatch):
    """Cut's deployed copy without the table base's boot queries (empty, so no row differs):
    base's step runs again and moves nothing, but base cannot boot, quoted, exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_rollback", _executing(
        "DROP TABLE salary.pay_stubs CASCADE")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert "ROLLBACK FAILS: base cannot boot on cut's deployed copy: exit 1:\n" in out
    assert 'relation "salary.pay_stubs" does not exist' in out
    assert _AGAIN not in out and "\nrollback: " not in out
