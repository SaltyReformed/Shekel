"""The bench: a dump restored once and cloned per state, a detached worktree per state, and a
teardown whose census names anything left (step X-cx's K1a-2).

The cases marked ``cluster`` start a real private cluster (``scripts/private_cluster.py``) on
the daemon ``DOCKER_HOST`` names, and restore a dump planted by the module's fixture, so they
need docker, the test-db image and the host's ``pg_dump`` and ``pg_restore``; the conftest
says where they run.  The rest grade the bench's pure parts on planted inputs.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from tools.ci import arcs, gitcmd
from tools.rehearsal import bench
from tools.rehearsal.bench import Bench, BenchError


def test_the_major_a_dump_was_made_by_is_read_from_its_listing():
    """``pg_restore -l`` names the server a custom-format archive came from; its major is the
    first number, a distribution's suffix or not."""
    assert bench.dump_major(";\n;     Dumped from database version: 18.6\n") == 18
    assert bench.dump_major(";     Dumped from database version: 16.4 (Ubuntu 16.4-1)\n") == 16
    with pytest.raises(BenchError, match="not a pg_dump -Fc archive"):
        bench.dump_major("; Archive created at 2026-10-19\n")


def test_a_dump_from_another_major_than_the_cluster_s_is_refused_naming_both():
    """180006 is PostgreSQL 18.6, so an 18 dump passes and a 17 one is refused."""
    bench.refuse_another_major(18, 180006)
    with pytest.raises(BenchError, match="made by PostgreSQL 17 and the cluster runs "
                                         "PostgreSQL 18"):
        bench.refuse_another_major(17, 180006)


def test_states_are_name_revision_pairs_each_named_once():
    """A state's name becomes a database and a directory name, so it is held to both."""
    assert bench.parse_states(["base=origin/main", "cut=HEAD"]) == {
        "base": "origin/main", "cut": "HEAD"}
    for given in (["base"], ["=HEAD"], ["base="], ["Base=HEAD"], ["1st=HEAD"], ["a-b=HEAD"],
                  ["../x=HEAD"], ["a" * 31 + "=HEAD"]):
        with pytest.raises(BenchError, match="is not <name>=<revision>"):
            bench.parse_states(given)
    with pytest.raises(BenchError, match="given twice"):
        bench.parse_states(["base=HEAD", "base=HEAD~1"])
    with pytest.raises(BenchError, match="no state given"):
        bench.parse_states([])


@pytest.fixture(name="dump", scope="module")
def _dump(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A ``pg_dump -Fc`` archive of a planted database: one table, two rows of money."""
    path = tmp_path_factory.mktemp("dump") / "planted.dump"
    token = bench.cluster("token").strip()
    try:
        socket_dir, _container = bench.cluster(
            "start", "--", f"shekel-rehearsal-test-{os.getpid()}-{token[:8]}", token,
            bench.image_tag()).splitlines()
        with psycopg.connect(bench.cluster("url", socket_dir, "postgres").strip(),
                             autocommit=True) as connection:
            connection.execute("CREATE DATABASE planted")
        planted = bench.cluster("url", socket_dir, "planted").strip()
        with psycopg.connect(planted, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE ledger (id integer PRIMARY KEY, amount numeric(12, 2))")
            connection.execute("INSERT INTO ledger VALUES (1, 12.34), (2, 0.01)")
        subprocess.run(["pg_dump", "--format=custom", "--file", str(path), "--dbname", planted],
                       check=True, capture_output=True)
    finally:
        bench.cluster("stop", token)
    yield path


def _containers() -> str:
    """The ids of every container a bench of this process started, running or not."""
    return subprocess.run(["docker", "ps", "--all", "--quiet", "--filter",
                           f"name=shekel-rehearsal-{os.getpid()}-"],
                          check=True, capture_output=True, text=True).stdout


def _left_nothing(made: Bench) -> None:
    """Nothing the bench made is left: no container, socket directory, worktree or listing."""
    assert _containers() == ""
    assert made.socket_dir is not None and not made.socket_dir.exists()
    listed = gitcmd.git(arcs.REPO, "worktree", "list", "--porcelain")
    for state in made.made.values():
        assert not state.worktree.parent.exists(), state.worktree.parent
        assert f"worktree {state.worktree}\n" not in listed


@pytest.mark.cluster
def test_a_bench_restores_the_dump_clones_it_per_state_and_leaves_nothing(dump):
    """Each state's clone holds the dump's rows and its worktree the state's commit; after
    it, nothing is left, and every part was timed."""
    head = gitcmd.git(arcs.REPO, "rev-parse", "HEAD").strip()
    parent = gitcmd.git(arcs.REPO, "rev-parse", "HEAD~1").strip()
    made = Bench(dump, {"base": parent, "cut": head})
    with made:
        assert {name: state.commit for name, state in made.made.items()} == {
            "base": parent, "cut": head}
        for state in made.made.values():
            assert gitcmd.git(state.worktree, "rev-parse", "HEAD").strip() == state.commit
            assert gitcmd.git(state.worktree, "status", "--porcelain") == ""
            with psycopg.connect(state.url) as connection:
                assert connection.execute("SELECT id, amount FROM ledger ORDER BY id").fetchall() \
                    == [(1, Decimal("12.34")), (2, Decimal("0.01"))]
    _left_nothing(made)
    assert set(made.timings) == {"image", "start", "restore", "clone base", "worktree base",
                                 "clone cut", "worktree cut", "teardown"}


@pytest.mark.cluster
def test_a_ctrl_c_inside_the_bench_still_leaves_nothing(dump):
    """A KeyboardInterrupt in the caller's body tears the bench down all the same."""
    made = Bench(dump, {"cut": "HEAD"})
    with pytest.raises(KeyboardInterrupt), made:
        raise KeyboardInterrupt
    _left_nothing(made)


@pytest.mark.cluster
def test_a_state_that_cannot_be_made_tears_down_what_was(dump):
    """A revision git cannot resolve fails the bench by name, after the first state, the
    cluster and the restore were made; none of them is left."""
    made = Bench(dump, {"base": "HEAD", "cut": "no-such-revision"})
    with pytest.raises(BenchError, match="state cut's worktree at 'no-such-revision'"):
        with made:
            pass
    _left_nothing(made)


@pytest.mark.cluster
def test_a_worktree_the_teardown_could_not_remove_is_named(dump, monkeypatch):
    """A planted leftover, a worktree whose removal is skipped and the scratch directory kept,
    is named by the census, never passed as clean.  The case then removes both itself."""
    monkeypatch.setattr(Bench, "_remove_worktree", lambda self, worktree: None)
    monkeypatch.setattr(bench.shutil, "rmtree", lambda path, ignore_errors=False: None)
    made = Bench(dump, {"cut": "HEAD"})
    with pytest.raises(BenchError, match="the teardown left something") as refused:
        with made:
            pass
    monkeypatch.undo()
    worktree = made.made["cut"].worktree
    assert f"git still lists worktree {worktree}" in str(refused.value)
    assert f"{worktree.parent} survived teardown" in str(refused.value)
    gitcmd.git(arcs.REPO, "worktree", "remove", "--force", str(worktree))
    bench.shutil.rmtree(worktree.parent)
    _left_nothing(made)


@pytest.mark.cluster
def test_the_command_line_brings_a_bench_up_and_reports_each_part(dump, capsys):
    """``python -m tools.rehearsal.bench`` exits 0, naming each state and each part's time."""
    assert bench.main(["--dump", str(dump), "--state", "cut=HEAD"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("state cut: ") and out.endswith("teardown left nothing\n")
    assert "\nrestore: " in out and "\nclone cut: " in out
