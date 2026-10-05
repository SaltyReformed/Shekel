"""The raw-write refusal: a ``gh`` write to the plan tracker is refused; reads are not.

``scripts/hooks/guard-tracker-writes.sh`` (deciding in ``guard_tracker_writes.py``)
reads a session's Bash command before it runs and refuses one that writes to
the private tracker ``saltyreformed-labs/shekel-plan`` -- or its board -- through
``gh``, pointing the session at the plan tool, which checks every write before
it sends it.  The hook ships UNREGISTERED until the cutover (plan step L8);
these cases drive it directly, through the shell wrapper, with the JSON payload
the harness sends, so the pre-filter, the reader and the decision are graded
together as they will run.

**Every rule is graded in both directions**: the write it must refuse, and the
neighbouring command it must let through -- the same verb as a read, the same
write to another repository, the same mutation without a tracker feature.  A
refusal that only ever fires is as wrong as one that never does: the first
blocks the code repository's own work, the second waves the tracker's through.

The tracker's name comes from ``tools/plan/setup_tracker.py`` (rule 14), so the
cases spell it as a literal on purpose: if that module's name ever changed,
these would fail rather than follow it silently.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_hooks._command_shapes import SHAPES

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "guard-tracker-writes.sh"
TRACKER = "saltyreformed-labs/shekel-plan"
#: A remote of the code repository, for the checkouts ordinary work runs in.
CODE_REMOTE = "https://github.com/SaltyReformed/Shekel.git"
#: A hook that hangs would hit this rather than the suite's per-test timeout.
HOOK_SECONDS = 20


def _decide(command: str, cwd: Path, hook: Path = HOOK,
            environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Run the hook on *command*; return (decision, reason), or None when it allows it.

    The environment is built from nothing but a PATH holding this interpreter
    (whose packages ``setup_tracker`` imports), so no ``GH_REPO`` from the
    machine running the suite leaks in; ``CLAUDE_PROJECT_DIR`` points where no
    ``.venv`` is, so the hook library leaves that PATH alone.
    """
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(cwd),
        "CLAUDE_PROJECT_DIR": str(cwd),
        **(environ or {}),
    }
    payload = json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    done = subprocess.run(["bash", str(hook)], input=payload, env=env, capture_output=True,
                          text=True, check=False, timeout=HOOK_SECONDS)
    assert done.returncode == 0, done.stderr
    if not done.stdout.strip():
        return None
    output = json.loads(done.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    return output["permissionDecision"], output["permissionDecisionReason"]


def _refused(command: str, cwd: Path, **kwargs) -> str:
    """Assert *command* is refused; return the reason."""
    decided = _decide(command, cwd, **kwargs)
    assert decided is not None, f"not refused: {command}"
    decision, reason = decided
    assert decision == "deny"
    assert "python tools/plan/plan.py" in reason
    return reason


def _allowed(command: str, cwd: Path, **kwargs) -> None:
    """Assert *command* runs without a decision."""
    decided = _decide(command, cwd, **kwargs)
    assert decided is None, f"refused: {command}: {decided}"


def _checkout(path: Path, remote: str) -> Path:
    """A real git checkout at *path* whose ``origin`` is *remote*."""
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


class TestTheCommandFamilies:
    """``gh issue``, ``gh label``, ``gh project``: reads pass, writes to the tracker do not."""

    @pytest.mark.parametrize("command", [
        f"gh issue list -R {TRACKER}",
        f"gh issue ls -R {TRACKER} --state all",
        f"gh issue view 5 -R {TRACKER}",
        f"gh issue status -R {TRACKER}",
        f"gh label list -R {TRACKER}",
        "gh project item-list 1 --owner saltyreformed-labs",
        "gh project field-list 1 --owner saltyreformed-labs",
        "gh project view 1 --owner saltyreformed-labs",
        "gh project list --owner saltyreformed-labs",
        "gh project ls --owner saltyreformed-labs",
        f"gh issue close 3 -R {TRACKER} --help",
    ])
    def test_a_read_of_the_tracker_runs(self, command, tmp_path):
        """Reading the tracker with gh stays allowed (the card's spec); so does help."""
        _allowed(command, tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh issue close 5 -R {TRACKER}",
        f"gh issue create --repo={TRACKER} --title x",
        "gh issue create -R SaltyReformed-Labs/Shekel-Plan --title x",
        f"gh issue comment https://github.com/{TRACKER}/issues/3 --body x",
        f"gh issue -R {TRACKER} edit 3 --add-label x",
        f"gh issue transfer 7 {TRACKER}",
        f"gh label create filing -R {TRACKER}",
        f"gh label clone SaltyReformed/Shekel -R {TRACKER}",
        f"cd /tmp && gh issue reopen 4 -R {TRACKER}",
    ])
    def test_a_write_to_the_tracker_is_refused(self, command, tmp_path):
        """Each spelling of the target -- flag, ``=``, case, URL, destination -- is seen."""
        reason = _refused(command, tmp_path)
        assert f"writes to {TRACKER}" in reason

    def test_a_verb_gh_adds_later_is_a_write(self, tmp_path):
        """Reads are an allowlist: a verb not on it is refused, not let through."""
        _refused(f"gh issue frobnicate 3 -R {TRACKER}", tmp_path)

    @pytest.mark.parametrize("command", [
        "gh issue close 5 -R SaltyReformed/Shekel",
        'gh issue create -R SaltyReformed/Shekel --title x --body "$(cat notes.md)"',
        "gh label create bug -R SaltyReformed/Shekel",
        f"gh label clone {TRACKER} -R SaltyReformed/Shekel",
    ])
    def test_a_write_to_another_repository_runs(self, command, tmp_path):
        """The same verbs aimed at the code repository are not this guard's business.

        The second keeps a body the shell fills in: a body cannot move a write,
        so it does not make the target unreadable.  The fourth copies labels
        FROM the tracker into the code repository, which writes only the latter.
        """
        _allowed(command, tmp_path)


class TestEveryGroupThatNamesTheTracker:
    """GO-ALL: any gh command naming the tracker must be a known read, whatever its group."""

    @pytest.mark.parametrize("command", [
        f"gh repo edit {TRACKER} --visibility public --accept-visibility-change-consequences",
        f"gh secret set APP_KEY -R {TRACKER} --body x",
        f"gh workflow run sync.yml -R {TRACKER}",
        f"gh pr merge 5 -R {TRACKER}",
        f"gh repo set-default {TRACKER}",
        f"gh repo delete {TRACKER} --yes",
        f"gh rs view 1 -R {TRACKER} && gh ruleset delete 1 -R {TRACKER}",
    ])
    def test_a_write_in_any_group_is_refused(self, command, tmp_path):
        """The four the first review measured passing, and their neighbours."""
        _refused(command, tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh repo view {TRACKER}",
        f"gh repo clone {TRACKER} /tmp/plan-copy",
        f"gh repo autolink list -R {TRACKER}",
        f"gh secret list -R {TRACKER}",
        f"gh variable get X -R {TRACKER}",
        f"gh run list -R {TRACKER}",
        f"gh workflow view sync.yml -R {TRACKER}",
        f"gh pr list -R {TRACKER}",
        f"gh rs list -R {TRACKER}",
        f"gh browse -R {TRACKER}",
        f"gh search issues --repo {TRACKER} filing",
    ])
    def test_a_read_in_any_group_runs(self, command, tmp_path):
        """The allowlist's reads, aliases (``rs``) and nested groups (``autolink``) pass."""
        _allowed(command, tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh pr comment 5 --body 'Filed as {TRACKER}#3.'",
        f"gh api -X POST repos/SaltyReformed/Shekel/issues/1/comments -f body='see {TRACKER}#3'",
        f"gh release create v1 --notes 'Plan moved to {TRACKER}.'",
    ])
    def test_text_that_only_mentions_the_tracker_runs(self, command, tmp_path):
        """A body, a field or release notes naming the tracker do not aim the write at it.

        Run in a checkout of the code repository, where a session's work runs.
        """
        _allowed(command, _checkout(tmp_path / "code", CODE_REMOTE))

    def test_a_non_content_flag_naming_the_tracker_is_refused(self, tmp_path):
        """The control: ``--repo`` on a board link names where the write lands."""
        _refused(f"gh project link 1 --owner @me --repo {TRACKER}", tmp_path)

    def test_a_hidden_value_outside_the_plan_groups_runs(self, tmp_path):
        """The fail-closed rule for hidden targets is the plan groups' alone."""
        _allowed('gh gist create "$NOTES_FILE"', tmp_path)


class TestACheckoutOfTheTracker:
    """A write naming no repository acts on the checkout it runs in."""

    @pytest.mark.parametrize("command", [
        "gh issue create --title x",
        "gh repo edit --visibility public --accept-visibility-change-consequences",
        "gh api -X POST repos/{owner}/{repo}/labels -f name=x",
    ])
    def test_in_a_clone_of_the_tracker_it_is_refused(self, command, tmp_path):
        """A clone, or any checkout with a remote naming the tracker, is the tracker."""
        clone = _checkout(tmp_path / "clone", f"git@github.com:{TRACKER}.git")
        _refused(command, clone)

    @pytest.mark.parametrize("command", [
        "gh issue create --title x",
        "gh api -X POST repos/{owner}/{repo}/labels -f name=x",
    ])
    def test_in_a_checkout_of_the_code_repository_it_runs(self, command, tmp_path):
        """The control: the same commands in a checkout of the code repository."""
        code = _checkout(tmp_path / "code", CODE_REMOTE)
        _allowed(command, code)

    def test_a_plan_write_in_a_checkout_it_cannot_place_is_refused(self, tmp_path):
        """After a ``cd`` it cannot follow, a bare issue write has no known target."""
        _refused('cd "$WORKTREE" && gh issue create --title x', tmp_path)


class TestGhRepoNamesTheTarget:
    """``GH_REPO`` is where gh sends a command that names no ``-R``."""

    def test_a_prefix_an_export_and_the_environment_all_count(self, tmp_path):
        """All three ways the variable reaches gh are refused when it is the tracker."""
        _refused(f"GH_REPO={TRACKER} gh issue close 3", tmp_path)
        _refused(f"export GH_REPO={TRACKER}; gh issue close 3", tmp_path)
        _refused("gh issue close 3", tmp_path, environ={"GH_REPO": TRACKER})

    def test_another_repository_in_gh_repo_runs(self, tmp_path):
        """The control: the same command with the code repository in ``GH_REPO``."""
        _allowed("GH_REPO=SaltyReformed/Shekel gh issue close 3", tmp_path)


class TestATargetTheHookCannotRead:
    """In the plan groups, a write whose target is hidden fails closed."""

    @pytest.mark.parametrize("command", [
        'gh issue close 3 -R "$REPO"',
        'gh issue close "$ISSUE_URL"',
        'gh issue delete --yes "$ISSUE_URL"',
        'gh issue edit --remove-milestone "$ISSUE_URL"',
        'gh project item-add 1 --owner "$OWNER" --url u',
        'GH_REPO="$REPO" gh issue close 3',
        f"gh issue list -R {TRACKER} --json url -q '.[].url' | xargs -n1 gh issue close",
        "find . -name '*.url' -exec gh issue close {} \\;",
    ])
    def test_it_is_refused(self, command, tmp_path):
        """A variable, a word after a boolean flag, ``GH_REPO``, or xargs-appended arguments."""
        reason = _refused(command, tmp_path)
        assert "cannot" in reason

    @pytest.mark.parametrize("command", [
        'gh issue view 3 -R "$REPO"',
        f"gh issue list -R {TRACKER} --json url -q '.[].url' | xargs -n1 gh issue view",
        'gh issue close 3 -R SaltyReformed/Shekel --comment "$(cat note.md)"',
    ])
    def test_a_read_or_a_hidden_body_runs(self, command, tmp_path):
        """Reads need no target; a comment cannot move a write."""
        _allowed(command, tmp_path)


class TestTheBoard:
    """``gh project`` writes reach a board by its owner, or by node id with no owner."""

    @pytest.mark.parametrize("command", [
        "gh project item-add 1 --owner saltyreformed-labs --url u",
        "gh project item-edit --id X --project-id P --text y",
        "gh project field-create 1 --owner=SaltyReformed-Labs --name n --data-type TEXT",
        "gh project copy 1 --source-owner @me --target-owner saltyreformed-labs --title t",
    ])
    def test_a_write_to_the_trackers_board_is_refused(self, command, tmp_path):
        """The organization's board, a write naming no owner, a copy INTO the organization."""
        _refused(command, tmp_path)

    @pytest.mark.parametrize("command", [
        "gh project item-add 1 --owner @me --url u",
        "gh project delete 1 --owner SaltyReformed",
        "gh project copy 1 --source-owner saltyreformed-labs --target-owner @me --title t",
    ])
    def test_a_write_to_another_owners_board_runs(self, command, tmp_path):
        """Plan step L10 deletes the developer's demo board with the second command."""
        _allowed(command, tmp_path)


class TestRestCalls:
    """``gh api``: the method decides read or write, the path decides the target."""

    @pytest.mark.parametrize("command", [
        f"gh api repos/{TRACKER}/issues",
        f"gh api -X GET repos/{TRACKER}/issues -f state=open",
        f"gh api --method HEAD repos/{TRACKER}",
    ])
    def test_a_get_runs(self, command, tmp_path):
        """GET and HEAD read; ``-X GET`` with fields sends them as a query string."""
        _allowed(command, tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh api repos/{TRACKER}/issues -f title=x",
        f"gh api repos/{TRACKER}/issues --input body.json",
        f"gh api -X PATCH repos/{TRACKER}/issues/3 -f state=closed",
        f"gh api --method=DELETE /repos/{TRACKER}/labels/x",
        f"gh api -iX DELETE repos/{TRACKER}/labels/x",
        f"gh api -XPOST https://api.github.com/repos/{TRACKER}/issues --input body.json",
        f"GH_REPO={TRACKER} gh api -X POST repos/{{owner}}/{{repo}}/labels -f name=x",
        "gh api -X POST orgs/saltyreformed-labs/issue-types -f name=x",
        "gh api -X POST orgs/saltyreformed-labs/projectsV2/1/items -f id=x",
    ])
    def test_a_write_to_the_tracker_is_refused(self, command, tmp_path):
        """Implied POST (a field, or an ``--input`` body), every method spelling, clusters."""
        _refused(command, tmp_path)

    def test_a_write_to_another_repository_runs(self, tmp_path):
        """The control: the same POST to the code repository."""
        _allowed("gh api -X POST repos/SaltyReformed/Shekel/statuses/abc -f state=success",
                 tmp_path)

    def test_a_write_whose_path_the_shell_fills_in_is_refused(self, tmp_path):
        """The hook cannot see where ``$ENDPOINT`` points, so it fails closed."""
        reason = _refused('gh api -X POST "$ENDPOINT" -f x=y', tmp_path)
        assert "$ENDPOINT" in reason


class TestGraphQL:
    """A mutation is the write; what it touches decides whether it is the tracker's."""

    def test_a_query_runs_even_one_about_the_board(self, tmp_path):
        """A read of the board, with GraphQL variables written as ``$name``."""
        _allowed("gh api graphql -f query='query($o: String!) { organization(login: $o) "
                 "{ projectV2(number: 1) { title } } }' -F o=saltyreformed-labs", tmp_path)

    @pytest.mark.parametrize("mutation", [
        'addSubIssue(input: {issueId: "a", subIssueId: "b"}) { clientMutationId }',
        'updateProjectV2ItemPosition(input: {projectId: "p", itemId: "i"}) { clientMutationId }',
        'addBlockedBy(input: {issueId: "a", blockingIssueId: "b"}) { clientMutationId }',
        'updateIssueIssueType(input: {issueId: "a", issueTypeId: "t"}) { clientMutationId }',
    ])
    def test_a_mutation_of_a_tracker_feature_is_refused(self, mutation, tmp_path):
        """Boards, sub-issues, blocked-by links, issue types: the code repository uses none."""
        reason = _refused(f"gh api graphql -f query='mutation {{ {mutation} }}'", tmp_path)
        assert "the code repository does not use" in reason

    def test_a_mutation_naming_the_tracker_is_refused(self, tmp_path):
        """A mutation whose variables name the tracker repository."""
        _refused("gh api graphql -f query='mutation($r: String!) { x(repo: $r) { y } }' "
                 "-f r=shekel-plan", tmp_path)

    def test_another_mutation_runs(self, tmp_path):
        """The control: a comment on a code-repository pull request, by node id."""
        _allowed("gh api graphql -f query='mutation { addComment(input: {subjectId: \"PR_1\", "
                 "body: \"x\"}) { clientMutationId } }'", tmp_path)

    @pytest.mark.parametrize("command", [
        "gh api graphql -F query=@- <<'EOF'\nmutation { addSubIssue(input: {}) { x } }\nEOF",
        "gh api graphql -F query=@- <<< 'mutation { addSubIssue(input: {}) { x } }'",
        "cat <<'EOF' | gh api graphql --input -\n{\"query\": \"mutation { addSubIssue }\"}\nEOF",
    ])
    def test_a_mutation_fed_on_stdin_is_read(self, command, tmp_path):
        """A heredoc, a here-string, or cat of a heredoc into a pipe."""
        _refused(command, tmp_path)

    def test_a_mutation_in_a_file_is_read(self, tmp_path):
        """``-F query=@file`` is read from the command's own directory."""
        (tmp_path / "q.graphql").write_text("mutation { removeSubIssue(input: {}) { x } }",
                                            encoding="utf-8")
        _refused("gh api graphql -F query=@q.graphql", tmp_path)

    @pytest.mark.parametrize("command", [
        "generate-query | gh api graphql -F query=@-",
        'gh api graphql -f query="$QUERY"',
        "gh api graphql -F query=@missing.graphql",
        'gh api graphql -F query=@- <<< "$QUERY"',
        "gh api graphql -F query=@- <<EOF\n$QUERY\nEOF",
        "echo q | xargs gh api graphql -f",
    ])
    def test_a_query_the_hook_cannot_read_is_refused(self, command, tmp_path):
        """A pipe, a variable, a missing file, an expanding heredoc or here-string, xargs."""
        reason = _refused(command, tmp_path)
        assert "cannot read" in reason


class TestWhatIsNotACommand:
    """Text that mentions a tracker write runs nothing, and is not refused."""

    @pytest.mark.parametrize("command", [
        f"git commit -F - <<'EOF'\nThe hook refuses gh issue close 3 -R {TRACKER}.\nEOF",
        f"grep -rn 'gh issue close 3 -R {TRACKER}' docs/",
        "ls -la",
        "gh pr list",
        "echo \"it's a high-entropy value",
    ])
    def test_it_runs(self, command, tmp_path):
        """A commit message, a search, unrelated commands, an unreadable line with no gh write."""
        _allowed(command, tmp_path)

    @pytest.mark.parametrize("command", SHAPES)
    def test_the_shapes_sessions_send_run(self, command, tmp_path):
        """Every heredoc-in-substitution shape the first review replayed (made-up bodies).

        Run in a checkout of the code repository, where a session's work runs
        (``~`` is the directory the hook starts in, see :func:`_decide`).
        """
        _allowed(command, _checkout(tmp_path / "code", CODE_REMOTE))

    def test_a_write_inside_a_substitution_is_refused(self, tmp_path):
        """The nested command runs, so it is read like any other."""
        _refused(f"echo $(gh issue close 3 -R {TRACKER})", tmp_path)

    def test_a_write_inside_an_unquoted_heredoc_is_refused(self, tmp_path):
        """Bash runs a ``$(...)`` in an unquoted heredoc body, so it is read too."""
        _refused(f"cat <<EOF\n$(gh issue close 3 -R {TRACKER})\nEOF", tmp_path)

    @pytest.mark.parametrize("command", [
        "gh issue close 3 --comment 'unclosed",
        f"gh pr merge 5 -R {TRACKER} --subject 'unclosed",
    ])
    def test_an_unreadable_line_that_could_write_is_refused(self, command, tmp_path):
        """A plan group, or the tracker's name, in a line the reader cannot follow."""
        reason = _refused(command, tmp_path)
        assert "could not read this command" in reason


class TestTheHookStaysSafeAndFast:
    """It never reads a device or a FIFO, whatever the line runs."""

    def test_a_device_redirect_is_never_read(self, tmp_path):
        """The usual way to make a secret, beside a word holding "gh": no read, no hang.

        ``/dev/null``, never a device that streams: a regression that read it
        must fail, not exhaust the host (the 2026-10-05 mutation run did).
        """
        _allowed("tr -dc 'A-Za-z0-9' < /dev/null | head -c 32  # high entropy", tmp_path)

    def test_a_fifo_is_never_opened(self, tmp_path):
        """A query "fed" from a FIFO is unseen, so refused at once rather than blocking."""
        os.mkfifo(tmp_path / "pipe")
        _refused("gh api graphql --input - < pipe", tmp_path)


class TestTheTrackersNameHasOneHome:
    """The name is read from ``tools/plan/setup_tracker.py``, and only for a write."""

    @pytest.fixture(name="hook_without_plan_tools")
    def _hook_without_plan_tools(self, tmp_path):
        """A copy of the hooks in a tree with no ``tools/plan``: the name cannot load."""
        hooks = tmp_path / "tree" / "scripts" / "hooks"
        shutil.copytree(ROOT / "scripts" / "hooks", hooks,
                        ignore=shutil.ignore_patterns("__pycache__"))
        return hooks / HOOK.name

    def test_a_write_is_refused_when_the_name_cannot_load(self, hook_without_plan_tools,
                                                          tmp_path):
        """Fail closed: a write is never waved through because the check broke."""
        reason = _refused(f"gh issue close 3 -R {TRACKER}", tmp_path,
                          hook=hook_without_plan_tools)
        assert "could not read the tracker's name" in reason

    def test_a_read_never_loads_it(self, hook_without_plan_tools, tmp_path):
        """Reads decide without the name, so a broken import cannot block them."""
        _allowed(f"gh issue list -R {TRACKER}", tmp_path, hook=hook_without_plan_tools)
        _allowed(f"gh api repos/{TRACKER}/issues", tmp_path, hook=hook_without_plan_tools)


def test_an_unreadable_payload_fails_closed(tmp_path):
    """A payload with no command blocks (exit 2) with a message, never passes silently."""
    done = subprocess.run(["bash", str(HOOK)], input='{"tool_input": {}, "gh": "api"}',
                          env={"PATH": os.environ["PATH"], "CLAUDE_PROJECT_DIR": str(tmp_path)},
                          capture_output=True, text=True, check=False, timeout=HOOK_SECONDS)
    assert done.returncode == 2
    assert "failing closed" in done.stderr


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
    payload = json.dumps({"tool_input": {"command": f"gh issue close 3 -R {TRACKER}"},
                          "cwd": str(tmp_path)})
    done = subprocess.run(["bash", str(HOOK)], input=payload,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin",
                               "CLAUDE_PROJECT_DIR": str(tmp_path)},
                          capture_output=True, text=True, check=False, timeout=HOOK_SECONDS)
    assert done.returncode == 2
    assert "crashed (exit 1); failing closed" in done.stderr


class TestWhatTheSecondReviewFound:
    """Each shape the second review measured passing, beside the neighbour that must still run."""

    @pytest.mark.parametrize("command", [
        f"gh issue close 3 -R{TRACKER}",
        f"gh issue close 3 -R={TRACKER}",
        f"gh pr merge 3 -dR{TRACKER}",
        f"gh pr merge -d https://github.com/{TRACKER}/pull/3",
        f"gh pr close -d https://github.com/{TRACKER}/pull/3",
        f"gh issue develop -c https://github.com/{TRACKER}/issues/3",
        f"gh repo edit {TRACKER} -h https://example.com --visibility public "
        "--accept-visibility-change-consequences",
        f"gh issue --title view create -R {TRACKER}",
    ])
    def test_a_spelling_that_hid_the_tracker_is_refused(self, command, tmp_path):
        """An attached ``-R``, a boolean sharing a content flag's letter, ``-h`` as homepage.

        And a flag before the verb, which makes gh run ``create``, not ``view``.
        Run in a checkout of the code repository, so the refusal must come from
        reading the tracker's name, not from an unknown checkout.
        """
        reason = _refused(command, _checkout(tmp_path / "code", CODE_REMOTE))
        assert f"writes to {TRACKER}" in reason

    @pytest.mark.parametrize("command", [
        f"gh pr comment 5 --body='Filed as {TRACKER}#3.'",
        f"gh pr comment 5 -b 'Filed as {TRACKER}#3.'",
        f"gh pr comment 5 -b='Filed as {TRACKER}#3.'",
        f"gh pr comment 5 '-bFiled as {TRACKER}#3.'",
        f"gh pr merge 5 --squash -t 'Moves the plan to {TRACKER}'",
        f"gh label create x -R SaltyReformed/Shekel -d 'mirrors {TRACKER}'",
        f"gh issue close 3 -R {TRACKER} -h",
        f"gh issue -R {TRACKER} view 3",
    ])
    def test_text_in_every_spelling_and_help_run(self, command, tmp_path):
        """A content flag's value in each spelling; ``-h`` where it is help; ``-R`` before a read."""
        _allowed(command, _checkout(tmp_path / "code", CODE_REMOTE))

    @pytest.mark.parametrize("command", [
        "gh issue create --title x",
        "gh repo edit --visibility public --accept-visibility-change-consequences",
    ])
    def test_a_clone_made_on_the_same_line_is_refused(self, command, tmp_path):
        """The clone does not exist when the hook runs, so git cannot place it: refused."""
        _refused(f"gh repo clone {TRACKER} d && cd d && {command}", tmp_path)

    @pytest.mark.parametrize("command", ["gh gist create notes.md", "gh auth status"])
    def test_a_group_that_acts_on_no_repository_runs_anywhere(self, command, tmp_path):
        """The control: gists and auth never act on a checkout, so none is looked up."""
        _allowed(command, tmp_path)

    def test_a_named_repository_skips_the_checkout(self, tmp_path):
        """In a tracker clone, a write naming the code repository acts on it alone."""
        clone = _checkout(tmp_path / "clone", f"git@github.com:{TRACKER}.git")
        _allowed("gh issue create -R SaltyReformed/Shekel --title x", clone)
        _allowed("GH_REPO=SaltyReformed/Shekel gh issue create --title x", clone)

    def test_an_unexported_gh_repo_does_not_name_the_repository(self, tmp_path):
        """``GH_REPO=x;`` is the shell's own, so gh acts on the tracker clone it runs in."""
        clone = _checkout(tmp_path / "clone", f"git@github.com:{TRACKER}.git")
        _refused("GH_REPO=SaltyReformed/Shekel; gh issue create --title x", clone)

    def test_a_cd_into_a_clone_named_gh_is_followed(self, tmp_path):
        """``cd .../gh`` is a ``cd``: the write after it runs in that tracker clone."""
        clone = _checkout(tmp_path / "gh", f"git@github.com:{TRACKER}.git")
        _refused(f"cd {clone} && gh issue create --title x", tmp_path)

    @pytest.mark.parametrize(("command", "remote"), [
        ("gh api -X POST repos/:owner/:repo/labels -f name=x", TRACKER),
        ("gh api graphql -F n='{repo}' -f query='mutation($n: String!) { x(name: $n) { y } }'",
         TRACKER),
    ])
    def test_a_placeholder_gh_fills_from_the_tracker_is_refused(self, command, remote, tmp_path):
        """``:owner``/``:repo`` in a path, and ``{repo}`` in a ``-F`` value, as gh fills them."""
        _refused(command, _checkout(tmp_path / "clone", f"git@github.com:{remote}.git"))

    @pytest.mark.parametrize("command", [
        "gh api -X POST repos/:owner/:repo/labels -f name=x",
        "gh api graphql -F n='{repo}' -f query='mutation($n: String!) { x(name: $n) { y } }'",
    ])
    def test_the_same_placeholder_in_the_code_repository_runs(self, command, tmp_path):
        """The control: filled from a checkout of the code repository."""
        _allowed(command, _checkout(tmp_path / "code", CODE_REMOTE))

    def test_an_unseen_query_says_how_to_write_it(self, tmp_path):
        """Design answer #6: an unseen query may be a mutation, so it stays refused."""
        reason = _refused('gh api graphql -f query="$Q"', tmp_path)
        assert "write the query in single quotes and pass values with -F name=value" in reason

    def test_a_hidden_tail_after_a_written_out_repository_runs(self, tmp_path):
        """``repos/OWNER/REPO/`` is written out, so the variable after it cannot move the call."""
        _allowed('gh api -X DELETE "repos/SaltyReformed/Shekel/rulesets/$PROBE"', tmp_path)

    def test_a_hidden_tail_after_the_tracker_is_refused(self, tmp_path):
        """The same shape aimed at the tracker."""
        _refused(f'gh api -X DELETE "repos/{TRACKER}/labels/$LABEL"', tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh api -X PUT orgs/saltyreformed-labs/teams/t/repos/{TRACKER}",
        "gh api -X PATCH repositories/123/issues/1 -f state=closed",
    ])
    def test_a_rest_path_naming_the_tracker_anywhere_or_by_id_is_refused(self, command, tmp_path):
        """A team's access to the tracker; a repository by numeric id, which cannot be attributed."""
        _refused(command, tmp_path)

    @pytest.mark.parametrize("command", [
        f"gh pr co 5 -R {TRACKER}",
        f"gh co 5 -R {TRACKER}",
        f"gh at verify x.tgz -R {TRACKER}",
        f"gh agent-task list -R {TRACKER}",
        f"gh cs list -R {TRACKER}",
        f"gh issue develop 3 --list -R {TRACKER}",
    ])
    def test_a_read_by_an_alias_or_a_read_flag_runs(self, command, tmp_path):
        """gh's aliases of known reads, and ``issue develop --list``."""
        _allowed(command, tmp_path)

    def test_issue_develop_without_list_is_a_write(self, tmp_path):
        """The control: without ``--list`` it creates a branch for the issue."""
        _refused(f"gh issue develop 3 -R {TRACKER}", tmp_path)

    def test_a_query_piped_from_cat_of_a_file_is_read(self, tmp_path):
        """``cat FILE | gh api graphql --input -`` is read from the file, never taken as empty.

        The reason names the feature: an empty or unseen body would be refused
        too, but as unreadable.
        """
        (tmp_path / "q.json").write_text('{"query": "mutation { addSubIssue(input: {}) { x } }"}',
                                         encoding="utf-8")
        reason = _refused("cat q.json | gh api graphql --input -", tmp_path)
        assert "the code repository does not use" in reason


class TestEveryIssueMutation:
    """Design answer #13: a mutation whose name contains ``Issue`` is the tracker's, by any node id."""

    @pytest.mark.parametrize("mutation", [
        'closeIssue(input: {issueId: "I_1"}) { clientMutationId }',
        'deleteIssue(input: {issueId: "I_1"}) { clientMutationId }',
        'transferIssue(input: {issueId: "I_1", repositoryId: "R_1"}) { clientMutationId }',
        'createIssue(input: {repositoryId: "R_1", title: "t"}) { issue { number } }',
        'a: reopenIssue(input: {issueId: "I_1"}) { clientMutationId }',
    ])
    def test_it_is_refused(self, mutation, tmp_path):
        """By node id, under an alias, whatever repository the id belongs to."""
        reason = _refused(f"gh api graphql -f query='mutation {{ {mutation} }}'", tmp_path)
        assert "changes an issue" in reason
        assert "stated condition" in reason

    def test_one_sent_as_an_input_body_is_refused(self, tmp_path):
        """The request's JSON is parsed for its query."""
        (tmp_path / "body.json").write_text(
            '{"query": "mutation($i: ID!) { closeIssue(input: {issueId: $i}) { x } }", '
            '"variables": {"i": "I_1"}}', encoding="utf-8")
        _refused("gh api graphql --input body.json", tmp_path)

    @pytest.mark.parametrize("mutation", [
        'addComment(input: {subjectId: "PR_1", body: "fixes the issue"}) { clientMutationId }',
        'resolveReviewThread(input: {threadId: "T_1"}) { thread { isResolved } }\n'
        '# an issue in a comment\n',
    ])
    def test_issue_in_a_string_or_a_comment_runs(self, mutation, tmp_path):
        """The control: only the mutation's own name counts, never its text."""
        _allowed(f"gh api graphql -f query='mutation {{ {mutation} }}'", tmp_path)

    def test_a_mutation_that_does_not_parse_is_refused(self, tmp_path):
        """A string that never closes: the hook cannot tell which mutation it is."""
        reason = _refused("gh api graphql -f query='mutation { addComment(input: {body: \"x }'",
                          tmp_path)
        assert "cannot parse" in reason
