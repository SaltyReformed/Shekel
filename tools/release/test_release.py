"""The ``release`` command over throwaway repositories under tmp_path: ``plan``'s report and
exit status, and ``census``'s listing.  The dev database listing is injected, so no test
calls docker."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run
from tools.release import release
from tools.release._carried import STEPS_PATH
from tools.release._census import COMPOSE
from tools.release.conftest import steps

MONEY = "Move the money. **MOVES MONEY**."
COMPOSED = ("services:\n  db:\n    container_name: dev-db\n    environment:\n"
            "      POSTGRES_USER: someone\n      POSTGRES_DB: budget\n")


def _two_movers(repo):
    """dev took A-1 (moves money) then B-1 (moves money): the base, the first merge, the
    second."""
    base = repo.plan((("balance:A-1", MONEY, "#1", "--"), ("balance:B-1", MONEY, "#2", "--")),
                     parents=())
    first = repo.merge(base, repo.commit("ship A-1", {STEPS_PATH: steps(
        ("balance:A-1", "Moved.", "SHIPPED", "`0123abcd`"),
        ("balance:B-1", MONEY, "#1", "--"))}, ships=("balance:A-1",), parents=(base,)))
    second = repo.merge(first, repo.commit("ship B-1", {STEPS_PATH: steps(
        ("balance:A-1", "Moved.", "SHIPPED", "`0123abcd`"),
        ("balance:B-1", "Moved.", "SHIPPED", "`4567abcd`"))}, ships=("balance:B-1",),
        parents=(first,)))
    return base, first, second


def test_plan_prints_what_refuses_the_release_and_the_cut_to_offer_and_exits_1(repo, capsys):
    """The report names each carried step, the rule, the cut offered, and the verdict."""
    base, first, second = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", second], root=repo.root) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"release plan: base {base}"
    assert out[1] == f"cut {second} 'Merge pull request'"
    assert out[2] == "steps carried (2):"
    assert out[3].startswith("  balance:A-1  moves money: yes  (claimed at ")
    assert out[4].startswith("  balance:B-1  moves money: yes  (claimed at ")
    assert out[5:] == [
        "migrations (0):",
        "REFUSED -- it carries 2 steps that move money (balance:A-1, balance:B-1); a release "
        "carries one at most",
        f"newest earlier cut that passes: {first} 'Merge pull request' (carries balance:A-1 "
        "[moves money]; exit 0)",
        "verdict: refused (exit 1)",
    ]


def test_plan_exits_0_on_a_clean_release(repo, capsys):
    """One mover alone: clean, no offer printed."""
    base, first, _ = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", first], root=repo.root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "verdict: clean (exit 0)"


def test_plan_exits_2_and_judges_nothing_on_a_name_that_names_nothing(repo, capsys):
    """A mistyped unrehearsed name is a stop, never a release read as rehearsed."""
    base, first, _ = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", first, "--unrehearsed", "typo"],
                        root=repo.root) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: unrehearsed typo")


def test_plan_prints_each_declaration_with_its_source_and_exits_3(repo, capsys):
    """A born-ticked step declared by the skill: its flag shows where it was read, and the
    approval question must name it."""
    base = repo.plan((), parents=())
    cut = repo.plan((("balance:B-1", "Did it.", "SHIPPED", "`0123abcd`"),), parents=(base,),
                    ships=("balance:B-1",))
    assert release.main(["plan", "--base", base, "--cut", cut, "--declare",
                         "balance:B-1=none@a924135ed"], root=repo.root) == 3
    out = capsys.readouterr().out.splitlines()
    assert out[3].startswith("  balance:B-1  moves money: no (declared from a924135ed)  ")
    assert out[-2:] == [
        "APPROVAL MUST NAME -- balance:B-1: no version of steps.md from base to the cut "
        "holds it open; the skill declared it moving no money, from a924135ed",
        "verdict: approval must name the above (exit 3)"]


@pytest.mark.parametrize("text", ["balance:B-1=none", "balance:B-1=maybe@x", "=none@x",
                                  "balance:B-1", "balance:B-1=none@ "])
def test_plan_refuses_a_declaration_without_a_value_or_a_source(repo, text):
    """A declaration names its value AND where it was read, or it is no declaration."""
    base, first, _ = _two_movers(repo)
    with pytest.raises(SystemExit) as exited:
        release.main(["plan", "--base", base, "--cut", first, "--declare", text],
                     root=repo.root)
    assert exited.value.code == 2


def test_plan_exits_2_on_a_cut_git_does_not_have(repo, capsys):
    """An unknown commit is an error, never an empty release."""
    base, _, _ = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", "0" * 40], root=repo.root) == 2
    assert "nothing was judged" in capsys.readouterr().err


# -- census ---------------------------------------------------------------------------------

@pytest.fixture(name="released")
def _released(repo, tmp_path):
    """A repository just released into ``origin/main``: its release branch (local and on
    origin), a peer's merged branch checked out in a worktree of its own, the kit's
    worktree, an unmerged branch, ``main`` and ``dev``, and the dev compose file."""
    tip = repo.commit("released", {COMPOSE: COMPOSED})
    later = repo.commit("not released", parents=(tip,))
    for ref in ("refs/remotes/origin/main", "refs/remotes/origin/dev", "refs/heads/main",
                "refs/heads/release/2026-10-14", "refs/remotes/origin/release/2026-10-14",
                "refs/heads/feat/peer"):
        run(repo.root, "update-ref", ref, tip)
    run(repo.root, "update-ref", "refs/heads/feat/unmerged", later)
    run(repo.root, "checkout", "--quiet", "--detach", tip)
    peer, kit = tmp_path / "trees-peer" / "wt_peer", tmp_path / "trees" / "wt_base"
    run(repo.root, "worktree", "add", "--quiet", str(peer), "feat/peer")
    run(repo.root, "worktree", "add", "--quiet", "--detach", str(kit), tip)
    return repo, tip, peer, kit


def test_census_marks_only_what_this_release_made(released, capsys):
    """MADE: the release branch, local and on origin; the kit's worktree; the kit's clones.
    Everything else is listed for its owner, a branch with the worktree it is checked
    out in; a peer's worktree in a sibling directory whose name starts the same as the
    kit's is not the kit's; main and dev are never listed; an unmerged branch is not
    listed."""
    repo, tip, peer, kit = released
    asked = []

    def listing(argv):
        asked.append(argv)
        return ["budget", "shekel_rel_1014_pristine", "shekel_rel_1014_a"]

    assert release.main(["census", "--release-branch", "release/2026-10-14",
                         "--kit-worktrees", str(kit.parent),
                         "--kit-databases", "shekel_rel_1014_"],
                        root=repo.root, listing=listing) == 0
    assert asked == [["docker", "exec", "dev-db", "psql", "-U", "someone", "-d", "budget",
                      "-Atc", "SELECT datname FROM pg_database WHERE NOT datistemplate "
                      "ORDER BY datname"]]
    assert capsys.readouterr().out.splitlines() == [
        f"census (read-only) after a release into {tip}",
        "worktrees (3):",
        f"  other {repo.root}  (detached at {tip[:12]})",
        f"  other {peer}  (refs/heads/feat/peer)",
        f"  MADE  {kit}  (detached at {tip[:12]})",
        f"branches whose tip {tip[:12]} holds (3):",
        f"  other refs/heads/feat/peer  ({peer})",
        "  MADE  refs/heads/release/2026-10-14",
        "  MADE  refs/remotes/origin/release/2026-10-14",
        "dev databases (3):",
        "  other budget",
        "  MADE  shekel_rel_1014_pristine",
        "  MADE  shekel_rel_1014_a",
    ]


def test_census_marks_nothing_when_told_nothing(released, capsys):
    """No release branch and no prefixes: everything is the owners'."""
    repo, _, _, _ = released
    assert release.main(["census"], root=repo.root, listing=lambda argv: ["budget"]) == 0
    assert not [line for line in capsys.readouterr().out.splitlines() if "MADE" in line]


@pytest.mark.parametrize("name", ["dev", "main", "release/", "feat/release/x"])
def test_census_refuses_a_release_branch_that_is_no_release_branch(released, name):
    """No branch outside ``release/<...>`` can be marked for the skill to delete."""
    repo, _, _, _ = released
    with pytest.raises(SystemExit) as exited:
        release.main(["census", "--release-branch", name], root=repo.root,
                     listing=lambda argv: [])
    assert exited.value.code == 2


def test_census_exits_2_when_the_compose_file_names_no_database_server(released, capsys):
    """The server's name is the compose file's; a file without it lists nothing."""
    repo, _, _, _ = released
    (repo.root / COMPOSE).write_text("services:\n  app: {}\n", encoding="utf-8")
    assert release.main(["census"], root=repo.root, listing=lambda argv: []) == 2
    assert "gives service 'db' no container_name" in capsys.readouterr().err


def test_census_exits_2_when_the_listing_fails(released, capsys):
    """A docker failure is an error, never an empty listing."""
    repo, _, _, _ = released

    def failing(argv):
        raise OSError("docker: not found")

    assert release.main(["census"], root=repo.root, listing=failing) == 2
    assert "docker: not found" in capsys.readouterr().err


@pytest.mark.parametrize("flags, says", [
    (["--kit-databases", ""], "--kit-databases '' would mark 'budget'"),
    (["--kit-databases", "bud"], "--kit-databases 'bud' would mark 'budget'"),
    (["--kit-databases", "post"], "--kit-databases 'post' would mark 'postgres'"),
    (["--kit-worktrees", ""], "--kit-worktrees is empty"),
    (["--kit-worktrees", "/"], "the repository's main worktree"),
    (["--kit-worktrees", "ROOT"], "the repository's main worktree"),
    (["--kit-worktrees", "PEER"], "which has refs/heads/feat/peer checked out"),
])
def test_census_refuses_inputs_that_would_mark_what_no_release_made(released, capsys, flags,
                                                                      says):
    """The skill deletes what is MADE, so an input that would mark the dev database, the
    server's own, the repository or a peer's checked-out worktree lists nothing."""
    repo, _, peer, _ = released
    named = {"PEER": str(peer.parent), "ROOT": str(repo.root)}
    flags = [named.get(flag, flag) for flag in flags]
    assert release.main(["census", *flags], root=repo.root,
                        listing=lambda argv: ["budget"]) == 2
    assert says in capsys.readouterr().err
