"""The tracker's two hooks ship SWITCHED OFF, and today's PR guard is untouched.

Plan step L3 (balance:X-cx, card ``plan#3``) builds two PreToolUse hooks for the
tracker cutover: ``guard-tracker-writes.sh`` (refuse a raw ``gh`` write to the
plan tracker) and ``guard-pr-main.sh`` (ask only before a pull request goes into
``main``).  Neither may take effect when L3 merges.  Until the cutover (plan
step L8) the coordinator session opens and merges every pull request into
``dev`` and today's ``guard-pr-actions.sh`` asks about all of them outside it,
and the tracker is still written by hand where the plan tool has no verb yet.
Hooks reach EVERY session -- the coordinator included -- the moment a checkout
carrying them is what a session runs.

**Registration is the switch.**  There is no flag, sentinel file or variable
in the hooks themselves: a hook nothing registers in ``.claude/settings.json``
never runs, and a hook L8 registers needs nothing removed afterwards.  So this
file pins the switch in its OFF position from both sides -- the settings name
neither new hook, and the PR guard every session runs behaves exactly as it
did before L3.  **L8 rewrites this file** when it registers the two hooks and
deletes ``guard-pr-actions.sh`` (and must turn on ``main``'s "include
administrators" no later than that, the condition ``guard_pr_main.py``'s header
records for not gating pushes).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
SETTINGS = CLAUDE / "settings.json"
TODAYS_GUARD = ROOT / "scripts" / "hooks" / "guard-pr-actions.sh"
NEW_HOOKS = ("guard-tracker-writes.sh", "guard-pr-main.sh")
#: Every name either hook could be registered by: its wrapper or its decision.
NEW_HOOK_NAMES = NEW_HOOKS + ("guard_tracker_writes", "guard_pr_main")
#: Where Claude Code reads hooks from a checkout: settings files, and the
#: ``hooks:`` frontmatter of agents, skills and commands.
_FRONTMATTER_DIRS = ("agents", "skills", "commands")


def _registered_commands() -> list[tuple[str, str | None, str]]:
    """Every hook command ``.claude/settings.json`` registers: (event, matcher, command)."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [
        (event, group.get("matcher"), hook["command"])
        for event, groups in settings["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    ]


def _registration_sources() -> list[tuple[Path, str]]:
    """The text of every place a hook can be registered: (file, text).

    Every ``settings*.json`` under ``.claude`` (a tracked or local one), and the
    YAML frontmatter of every agent, skill and command.  Never ``.claude/
    worktrees``, where other sessions keep whole checkouts.
    """
    sources = [(path, path.read_text(encoding="utf-8"))
               for path in sorted(CLAUDE.glob("settings*.json"))]
    for directory in _FRONTMATTER_DIRS:
        for path in sorted((CLAUDE / directory).rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            if text.startswith("---"):
                sources.append((path, text.split("---", 2)[1]))
    return sources


class TestTheSettingsRegisterNeitherHook:
    """The switch itself: ``.claude/settings.json``."""

    @pytest.mark.parametrize("name", NEW_HOOK_NAMES)
    def test_the_new_hook_is_registered_nowhere(self, name):
        """Not in any settings file, nor any agent, skill or command's frontmatter.

        By its wrapper's name or its python decision's, under any event or
        matcher.
        """
        assert not [path for path, text in _registration_sources() if name in text]

    def test_the_sources_searched_include_the_settings(self):
        """The control: the search above reads ``.claude/settings.json`` itself."""
        assert SETTINGS in [path for path, _ in _registration_sources()]

    def test_the_new_hooks_exist_to_be_registered(self):
        """The control: the scripts L8 registers are really there and executable.

        Without it the two cases above would also pass for a hook that was never
        written, or renamed so L8's registration would point at nothing.
        """
        for hook in NEW_HOOKS:
            path = ROOT / "scripts" / "hooks" / hook
            assert path.is_file()
            assert os.access(path, os.X_OK)

    def test_every_bash_call_still_runs_todays_guard_alone(self):
        """The Bash PreToolUse hooks are exactly the ones before L3."""
        bash = [command for event, matcher, command in _registered_commands()
                if event == "PreToolUse" and matcher == "Bash"]
        assert bash == ["$CLAUDE_PROJECT_DIR/scripts/hooks/guard-pr-actions.sh"]


def _todays_guard(command: str, coordinator: bool = False) -> str | None:
    """Run ``guard-pr-actions.sh`` on *command*; return its decision, or None."""
    env = {"PATH": os.environ["PATH"]}
    if coordinator:
        env["SHEKEL_PR_COORDINATOR"] = "1"
    payload = json.dumps({"tool_input": {"command": command}})
    done = subprocess.run(["bash", str(TODAYS_GUARD)], input=payload, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    if not done.stdout.strip():
        return None
    return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"]


class TestTodaysGuardIsUnchanged:
    """``guard-pr-actions.sh`` behaves as it did before L3 -- the side every session sees."""

    @pytest.mark.parametrize("command", [
        "gh pr create --base dev --fill",
        "gh pr merge 8 --squash",
    ])
    def test_every_pr_into_any_branch_still_asks_outside_the_coordinator(self, command):
        """Not narrowed to main: a pull request into ``dev`` still asks."""
        assert _todays_guard(command) == "ask"

    def test_the_coordinator_is_still_exempt(self):
        """The launch variable still lets the coordinator open and merge unasked."""
        assert _todays_guard("gh pr merge 8 --squash", coordinator=True) is None

    @pytest.mark.parametrize("command", [
        "git push origin main",
        "gh issue close 3 -R saltyreformed-labs/shekel-plan",
        "ls -la",
    ])
    def test_nothing_new_is_gated(self, command):
        """No push gate and no tracker refusal: those arrive with L8, not with L3."""
        assert _todays_guard(command) is None
