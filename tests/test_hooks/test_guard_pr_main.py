"""The narrowed PR guard: a pull request into ``main`` asks the developer; nothing else does.

``scripts/hooks/guard-pr-main.sh`` (deciding in ``guard_pr_main.py``) replaces
today's ``guard-pr-actions.sh`` at the tracker cutover (plan step L8), when
every session starts opening and merging its own pull requests into ``dev``.
It asks before a command opens, retargets or merges a pull request into
``main`` -- production -- and stays silent for every other branch.  It ships
UNREGISTERED; these cases drive it directly through the shell wrapper.

**The suite never calls GitHub.**  A merge's base is a fact GitHub holds, so
the guard runs ``gh pr view ... --json baseRefName``; here a fake ``gh`` stands
first on PATH, answers from a table the case writes, and logs every call, so a
case can assert WHICH pull request, repository and directory the guard asked
about -- the same ones the merge would act on -- and that a read the guard has
no business making was never made.

**Both directions per rule**, as in the tracker guard's tests: the ``main``
case that must ask and its neighbour that must not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.hooks import guard_pr_main
from tests.test_hooks._command_shapes import SHAPES

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "guard-pr-main.sh"

#: The fake ``gh``: logs each call, then answers ``pr view`` from the table --
#: keyed by the pull request named, or by ``cwd:<dir>`` when none is -- and
#: fails as gh does when the pull request is not in it.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd(),
                          "gh_repo": os.environ.get("GH_REPO")}) + "\\n")
with open(os.environ["FAKE_GH_BASES"], encoding="utf-8") as table:
    bases = json.load(table)
named = args[2] if len(args) > 2 and not args[2].startswith("-") else None
key = named if named is not None else "cwd:" + os.getcwd()
if args[:2] != ["pr", "view"] or key not in bases:
    print("no pull requests found", file=sys.stderr)
    sys.exit(1)
print(bases[key])
"""


class Guard:
    """The hook, run against a fake ``gh`` whose answers and calls the case controls."""

    def __init__(self, tmp_path: Path) -> None:
        """Lay out the fake ``gh``, its answer table and its call log under *tmp_path*."""
        self.home = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text(FAKE_GH, encoding="utf-8")
        fake.chmod(0o755)
        self.log = tmp_path / "gh-calls.jsonl"
        self.table = tmp_path / "bases.json"
        self.bases({})
        self.env = {
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_BASES": str(self.table),
        }

    def bases(self, table: dict[str, str]) -> None:
        """Set what the fake ``gh`` says each pull request's base is."""
        self.table.write_text(json.dumps(table), encoding="utf-8")

    def calls(self) -> list[dict[str, object]]:
        """Every call the guard made to ``gh``, in order."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def decide(self, command: str, cwd: Path | None = None,
               environ: dict[str, str] | None = None) -> str | None:
        """Run the hook on *command*; return the question, or None when it lets it run."""
        payload = json.dumps({"tool_input": {"command": command},
                              "cwd": str(cwd or self.home)})
        done = subprocess.run(["bash", str(HOOK)], input=payload,
                              env={**self.env, **(environ or {})}, capture_output=True,
                              text=True, check=False, timeout=60)
        assert done.returncode == 0, done.stderr
        if not done.stdout.strip():
            return None
        output = json.loads(done.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        return output["permissionDecisionReason"]


@pytest.fixture(name="guard")
def _guard(tmp_path):
    """A fresh guard and fake ``gh`` per case."""
    return Guard(tmp_path)


def _asks_into_main(question: str | None) -> None:
    """The question names main and the one path there."""
    assert question is not None
    assert "into main" in question
    assert "/release" in question


class TestOpeningAPullRequest:
    """``gh pr create`` / ``gh pr new``: main, or no base at all, asks."""

    @pytest.mark.parametrize("command", [
        "gh pr create --base main --title x",
        "gh pr create -B main --title x",
        "gh pr create -Bmain --fill",
        "gh pr create --base=main --fill",
        "gh pr create --base refs/heads/main --fill",
        "gh pr new --base main --fill",
        "gh pr create --base dev --base main --fill",
    ])
    def test_a_pull_request_into_main_asks(self, command, guard):
        """Every spelling of the base, the alias, and the last of a repeated flag."""
        _asks_into_main(guard.decide(command))

    def test_one_with_no_base_asks_because_github_defaults_to_main(self, guard):
        """A bare ``gh pr create`` counts as main (the card): gh's fallback is the default branch."""
        question = guard.decide("gh pr create --title x --body y")
        _asks_into_main(question)
        assert "names no --base" in question

    @pytest.mark.parametrize("command", [
        "gh pr create --base dev --fill",
        "gh pr new -B feat/plan-tracker-l3 --fill",
        "gh pr create --base main-old --fill",
        "gh pr create --fill -dB dev",
        "gh pr create --fill -dB=dev",
    ])
    def test_a_pull_request_into_another_branch_runs(self, command, guard):
        """``dev``, a feature branch, a name that starts with main, a base in a flag cluster."""
        assert guard.decide(command) is None

    def test_a_base_in_a_flag_cluster_is_read(self, guard):
        """``-dB main`` is a draft into main, as gh's flag parser reads it."""
        _asks_into_main(guard.decide("gh pr create --fill -dB main"))

    def test_a_base_the_shell_fills_in_asks(self, guard):
        """``--base "$BASE"`` cannot be known before the line runs."""
        question = guard.decide('gh pr create --base "$BASE" --fill')
        assert question is not None
        assert "$BASE" in question


class TestRetargeting:
    """``gh pr edit --base main`` makes an existing pull request one into main."""

    def test_retargeting_to_main_asks(self, guard):
        """The retarget is the other way a pull request comes to be one into main."""
        _asks_into_main(guard.decide("gh pr edit 5 --base main"))

    @pytest.mark.parametrize("command", [
        "gh pr edit 5 --title x",
        "gh pr edit 5 -B dev",
    ])
    def test_any_other_edit_runs(self, command, guard):
        """An edit that does not move the base, or moves it elsewhere."""
        assert guard.decide(command) is None


class TestMerging:
    """``gh pr merge`` asks when GitHub says the pull request goes into main."""

    def test_a_merge_into_main_asks(self, guard):
        """The guard asks GitHub about the very pull request the merge names."""
        guard.bases({"7": "main"})
        _asks_into_main(guard.decide("gh pr merge 7 --merge"))
        assert guard.calls()[0]["args"][:3] == ["pr", "view", "7"]

    def test_a_merge_into_dev_runs(self, guard):
        """The control: the same merge of a pull request into dev."""
        guard.bases({"8": "dev"})
        assert guard.decide("gh pr merge 8 --squash") is None

    def test_a_merge_github_will_not_describe_asks(self, guard):
        """A failed read never lets a merge through: it asks instead (fail closed)."""
        question = guard.decide("gh pr merge 9 --squash")
        assert question is not None
        assert "would not say" in question

    @pytest.mark.parametrize("command", [
        "gh pr merge --match-head-commit abc123 8 --squash",
        "gh pr merge -t subject 8",
        "gh pr merge --subject=subject 8",
    ])
    def test_a_flag_value_is_never_taken_for_the_pull_request(self, command, guard):
        """The pull request is ``8``; ``abc123`` and ``subject`` are flag values."""
        guard.bases({"8": "dev"})
        assert guard.decide(command) is None
        assert guard.calls()[0]["args"][2] == "8"

    @pytest.mark.parametrize("command", [
        "gh pr merge -R SaltyReformed/Shekel 8",
        "gh pr -R SaltyReformed/Shekel merge 8",
    ])
    def test_the_repository_is_passed_through(self, command, guard):
        """``-R`` after the verb or before it reaches the read, as it reaches the merge."""
        guard.bases({"8": "dev"})
        assert guard.decide(command) is None
        assert guard.calls()[0]["args"][3:5] == ["--repo", "SaltyReformed/Shekel"]

    def test_gh_repo_is_passed_through(self, guard):
        """``GH_REPO`` reaches the read, as it reaches the merge."""
        guard.bases({"8": "dev"})
        assert guard.decide("GH_REPO=o/r gh pr merge 8") is None
        assert guard.calls()[0]["gh_repo"] == "o/r"

    def test_a_merge_naming_no_pull_request_is_read_where_it_runs(self, guard, tmp_path):
        """After ``cd``, the current branch's pull request is the one in that directory."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        guard.bases({f"cwd:{worktree}": "dev"})
        assert guard.decide(f"cd {worktree} && gh pr merge --squash") is None
        assert guard.calls()[0]["cwd"] == str(worktree)

    @pytest.mark.parametrize("command", [
        'gh pr merge "$PR" --squash',
        'cd "$WORKTREE" && gh pr merge --squash',
    ])
    def test_a_merge_it_cannot_place_asks_without_asking_github(self, command, guard):
        """A pull request or directory the shell fills in: unknown, so it asks."""
        assert guard.decide(command) is not None
        assert not guard.calls()

    @pytest.mark.parametrize("prefix", [
        "if cd {wt}; then ",
        "{{ cd {wt}; }} && ",
    ])
    def test_a_cd_behind_a_reserved_word_is_followed(self, prefix, guard, tmp_path):
        """``if cd ...; then`` and ``{ cd ...; }`` move the merge as bash moves it."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        guard.bases({f"cwd:{worktree}": "main"})
        command = prefix.format(wt=worktree) + "gh pr merge --squash" + (
            "; fi" if prefix.startswith("if") else "")
        _asks_into_main(guard.decide(command))
        assert guard.calls()[0]["cwd"] == str(worktree)

    def test_an_unset_gh_repo_is_not_passed_to_the_read(self, guard):
        """``unset GH_REPO`` before the merge: the read must not inherit the old value."""
        guard.bases({"8": "dev"})
        assert guard.decide("unset GH_REPO; gh pr merge 8",
                            environ={"GH_REPO": "someone/else"}) is None
        assert guard.calls()[0]["gh_repo"] is None

    def test_merges_xargs_feeds_ask_without_asking_github(self, guard):
        """The pull requests come from a pipe, so which ones is unknown: it asks."""
        question = guard.decide("gh pr list --base main --json number -q '.[].number' "
                                "| xargs -n1 gh pr merge --squash")
        assert question is not None
        assert "xargs" in question
        assert not guard.calls()

    def test_the_coordinator_variable_changes_nothing(self, guard):
        """``SHEKEL_PR_COORDINATOR`` is today's guard's exemption, deleted at L8."""
        guard.bases({"7": "main"})
        _asks_into_main(guard.decide("gh pr merge 7", environ={"SHEKEL_PR_COORDINATOR": "1"}))


class TestWhatItLeavesAlone:
    """Everything that does not send a pull request into main runs unasked."""

    @pytest.mark.parametrize("command", [
        "gh pr view 7",
        "gh pr list --base main",
        "gh pr checks 7",
        "git commit -F - <<'EOF'\nThe guard asks before gh pr merge 7.\nEOF",
        'echo "an unclosed quote about merges and gh',
    ])
    def test_it_runs_without_asking_github(self, command, guard):
        """Reads, a commit message, and an unreadable line that cannot be a gated command."""
        guard.bases({"7": "main"})
        assert guard.decide(command) is None
        assert not guard.calls()

    def test_a_push_to_main_is_not_this_guards(self, guard):
        """Deliberately not gated (ruling ``R-BAL207``, recorded in the hook's header).

        ``main``'s branch protection refuses a direct push from every session once
        L8 turns on "include administrators", the condition this design rests
        on; a hook arm over that door would be a fence around a shut door.
        """
        assert guard.decide("git push origin main") is None

    def test_an_unreadable_gated_line_asks(self, guard):
        """An unclosed quote in what may be a merge: it cannot be read, so it asks."""
        question = guard.decide("gh pr merge 7 --subject 'unclosed")
        assert question is not None
        assert "cannot follow the line" in question

    @pytest.mark.parametrize("command", SHAPES)
    def test_the_shapes_sessions_send_ask_nothing(self, command, guard):
        """Every heredoc-in-substitution shape the first review replayed (made-up bodies).

        The pull request in them goes into ``dev``, and pull request 8 is into
        ``dev`` too, so none of them may ask.
        """
        guard.bases({"8": "dev"})
        assert guard.decide(command) is None


class TestTheReadBudget:
    """All of a line's base reads share one budget, so a slow GitHub ends in a question."""

    def test_a_spent_budget_asks_without_reading(self, guard, monkeypatch):
        """With no budget left the read is skipped, and the merge asks rather than runs."""
        monkeypatch.setattr(guard_pr_main, "BASE_READ_BUDGET_SECONDS", 0)
        guard.bases({"8": "dev"})
        question = guard_pr_main.question("gh pr merge 8", guard.home, guard.env)
        assert question is not None
        assert "in time" in question
        assert not guard.calls()

    def test_a_budget_left_reads(self, guard):
        """The control: the same call in-process, with the budget as shipped."""
        guard.bases({"8": "dev"})
        assert guard_pr_main.question("gh pr merge 8", guard.home, guard.env) is None
        assert guard.calls()[0]["args"][:3] == ["pr", "view", "8"]


class TestWhatTheSecondReviewFound:
    """Each shape the second review measured, beside the neighbour that must not ask."""

    @pytest.mark.parametrize("command", [
        "gh pr merge 7 --help",
        "gh pr create --base main -h",
        "gh pr --help",
    ])
    def test_help_asks_nothing(self, command, guard):
        """gh prints help and does nothing else, as the tracker guard already lets it."""
        guard.bases({"7": "main"})
        assert guard.decide(command) is None
        assert not guard.calls()

    def test_a_flag_before_the_verb_asks(self, guard):
        """gh drops ``--title x`` and runs ``create``; the reader cannot see which verb runs."""
        question = guard.decide("gh pr --title x create --base main")
        assert question is not None
        assert "before the verb" in question

    def test_a_repository_before_the_verb_is_read(self, guard):
        """The control: ``-R`` may stand first, and the verb after it is read as usual."""
        assert guard.decide("gh pr -R SaltyReformed/Shekel create --base dev --fill") is None

    def test_a_create_inside_a_parameter_expansion_asks(self, guard):
        """``${PR:-$(gh pr create ...)}`` runs the create when ``PR`` is empty."""
        _asks_into_main(guard.decide("PR=${PR:-$(gh pr create --base main --fill)}"))

    def test_env_unset_reaches_the_read(self, guard):
        """``env -u GH_REPO`` runs the merge without it, so the read runs without it too."""
        guard.bases({"8": "dev"})
        assert guard.decide("env -u GH_REPO gh pr merge 8",
                            environ={"GH_REPO": "someone/else"}) is None
        assert guard.calls()[0]["gh_repo"] is None

    def test_one_budget_covers_every_read_on_the_line(self, guard, monkeypatch):
        """Two merges share the line's budget: the second, read after it ran out, asks.

        The clock moves 15 s per reading against the 20 s budget, so the first
        read has 5 s left and the second none; a budget per merge would give
        each its own 20 s and let both through.
        """
        ticks = iter(range(0, 10_000, 15))
        monkeypatch.setattr(guard_pr_main, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        guard.bases({"8": "dev", "9": "dev"})
        question = guard_pr_main.question("gh pr merge 8 && gh pr merge 9", guard.home, guard.env)
        assert question is not None
        assert "in time" in question
        assert [call["args"][2] for call in guard.calls()] == ["8"]


def test_a_crashed_decision_fails_closed(tmp_path):
    """A decision that dies (exit 1) blocks the command (exit 2) with a message.

    On any exit but 0 or 2 Claude Code decides from what the hook printed --
    for a crash, nothing -- and runs the command, so the wrapper turns a crash
    into a block.  The crash is simulated by a ``python3`` that exits 1, first
    on PATH.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    crashing = bin_dir / "python3"
    crashing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    crashing.chmod(0o755)
    payload = json.dumps({"tool_input": {"command": "gh pr merge 7"}, "cwd": str(tmp_path)})
    done = subprocess.run(["bash", str(HOOK)], input=payload,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin",
                               "CLAUDE_PROJECT_DIR": str(tmp_path)},
                          capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 2
    assert "crashed (exit 1); failing closed" in done.stderr
