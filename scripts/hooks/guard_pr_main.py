"""PreToolUse guard: ask the developer before a pull request goes into ``main``, and only then.

``main`` is production: ``docker-publish.yml`` signs ``:latest`` on every push
to it, and the deploy pipeline ships that image.  After the tracker cutover
(plan step L8) every session opens and merges its own pull requests into
``dev``, and the one path to ``main`` is a release (the ``/release`` skill, run
on the developer's word).  So this guard turns three commands into a question
for the developer:

* ``gh pr create`` (or its alias ``gh pr new``) whose ``--base`` is ``main`` --
  or names no base at all, which counts as ``main`` (card ``plan#3``): gh then
  uses the branch's configured merge base if one is set, and otherwise the
  repository's default branch, which is ``main``;
* ``gh pr edit --base main``, which makes an existing pull request one into
  ``main`` (an application of ruling ``balance:R-BAL207``: retargeting is the
  other way a pull request comes to be one into ``main``);
* ``gh pr merge`` of a pull request whose base is ``main``.  The base is a fact
  GitHub holds, so the guard asks it -- ``gh pr view <the same pull request>
  --json baseRefName``, in the same repository and directory -- and asks the
  developer whenever that read fails, rather than guessing.  All of a line's
  reads share one budget (:data:`BASE_READ_BUDGET_SECONDS`): a hook that times
  out does not block the tool call (Claude Code's hooks guide), so a line of
  slow merges must run out of budget, and ask, before any timeout L8 registers.

It asks too when it cannot tell: a base or a pull request named through a
shell variable, arguments ``xargs``, ``find`` or ``fd`` append at run time, a
``cd`` it could not follow before a merge, a flag before the verb (which hides
the verb from a reader: ``gh pr --title x create``), a line the reader cannot
follow that runs ``gh pr`` with a gated verb.  A guard that cannot see its
target fails closed.  It does not ask about a pull request it can read as going
into another branch, nor about a command that only prints its help.

**What it deliberately does not do** (each an application of ``R-BAL207``):

* **Gate a push to ``main``.**  ``main``'s branch protection requires a pull
  request and green checks, and L8 turns on "include administrators", after
  which GitHub itself refuses every direct push to ``main`` from every session
  (force pushes and deletion are already refused).  A hook arm over a door the
  server has shut would be a fence the design doctrine says not to grow, and
  the hardest arm to get right (bare ``git push``, ``HEAD``, ``--all``,
  ``refs/heads/main``, aliases).  The condition: L8 turns on "include
  administrators" no later than it registers this hook.
* **Honour ``SHEKEL_PR_COORDINATOR``.**  The variable exempts the coordinator
  session from today's guard (``guard-pr-actions.sh``); L8 deletes it with the
  coordinator role, and after L8 no session merges into ``main`` without the
  developer.
* **Read ``gh api`` or curl.**  Like the guard it replaces, this is advisory,
  not a security boundary; the branch protection above is the boundary.

**Switched off until L8.**  Nothing registers this hook, and
``tests/test_hooks/test_tracker_hooks_switched_off.py`` pins that: until L8 the
coordinator opens and merges every pull request into ``dev``, and today's
``guard-pr-actions.sh`` asks about all of them outside the coordinator.  L8
turns this on by registering it in ``.claude/settings.json`` in place of
``guard-pr-actions.sh``, which it deletes; there is no switch in the code.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

# The hook's own checkout on sys.path, so 'scripts' resolves to the tree this
# file belongs to, whatever directory the session stands in.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Pylint: wrong-import-position -- this import must follow the sys.path
# bootstrap above; 'scripts' is only importable once the checkout root is on
# the path.
from scripts.hooks._gh_arguments import (  # pylint: disable=wrong-import-position
    HIDDEN_VERB,
    REPO_FLAGS,
    VERB_FLAGS,
    Arguments,
    parse_arguments,
    prints_help,
    subcommand,
)
# Pylint: wrong-import-position -- as above, after the sys.path bootstrap.
from scripts.hooks._gh_commands import (  # pylint: disable=wrong-import-position
    GH_REPO,
    CommandUnreadable,
    GhInvocation,
    Word,
    gh_invocations,
    run_guard,
)

#: The production branch: whatever lands here is built and deployed.
PRODUCTION_BRANCH = "main"
_PRODUCTION_REFS = frozenset({PRODUCTION_BRANCH, f"refs/heads/{PRODUCTION_BRANCH}"})

_CREATE = frozenset({"create", "new"})
_EDIT = "edit"
_MERGE = "merge"
_GATED = _CREATE | {_EDIT, _MERGE}
#: A line that may run one of the gated verbs: asked about when the reader
#: cannot follow it, and let through to bash otherwise.
_MAY_BE_GATED = re.compile(
    r"\bgh\b[\s\S]*\bpr\b[\s\S]*\b(?:"
    + "|".join(sorted(_GATED)) + r")\b")

#: Every flag that takes a value, per verb, so a value is never mistaken for
#: the pull request a merge names (one home: :data:`_gh_arguments.VERB_FLAGS`).
_CREATE_FLAGS = VERB_FLAGS[("pr", "create")].values
_EDIT_FLAGS = VERB_FLAGS[("pr", "edit")].values
_MERGE_FLAGS = VERB_FLAGS[("pr", "merge")].values

#: How long ALL of one line's base reads may take together before the guard
#: asks instead.  A hook that times out lets the command run, so the budget
#: must end first: Claude Code's default command-hook timeout is 10 minutes
#: (hooks guide, read 2026-10-05), and L8 must not register this hook with a
#: timeout shorter than this budget.  It also keeps a session from stalling.
BASE_READ_BUDGET_SECONDS = 20

_REASON = (
    "This command {action}. Whatever lands on {branch} is built and deployed to "
    "production, so the only path there is a release (the /release skill, on your "
    "word). Approve only if this is that release; otherwise deny. ({detail})"
)
_UNSURE = (
    "The guard could not tell whether this command sends a pull request into {branch}: "
    "{detail}. It asks rather than guess. Approve only if it does not go into {branch}, "
    "or if this is a release."
)


def question(command: str, cwd: Path, environ: Mapping[str, str]) -> str | None:
    """What to ask the developer before *command* runs, or None to let it run.

    Args:
        command: The Bash tool's command line.
        cwd: The directory it starts in.
        environ: The environment it starts with.

    Returns:
        The question's text, or None.
    """
    try:
        invocations = gh_invocations(command, cwd, environ)
    except CommandUnreadable as exc:
        if _MAY_BE_GATED.search(command) is None:
            return None
        return _unsure(f"the guard cannot follow the line ({exc})")
    deadline = time.monotonic() + BASE_READ_BUDGET_SECONDS
    for invocation in invocations:
        asked = _question(invocation, environ, deadline)
        if asked is not None:
            return asked
    return None


def _question(invocation: GhInvocation, environ: Mapping[str, str],
              deadline: float) -> str | None:
    """The question for one gh command, or None: it routes ``gh pr``'s three gated verbs."""
    args = invocation.args
    if not args or args[0].text != "pr":
        return None
    pr_args = args[1:]
    verb, after = subcommand(pr_args, REPO_FLAGS)
    if prints_help(pr_args, verb != HIDDEN_VERB) or verb not in _GATED | {HIDDEN_VERB}:
        return None
    if verb == HIDDEN_VERB or not invocation.args_complete:
        return _unsure(
            "a flag stands before the verb of `gh pr`, and gh's parser then skips the word "
            "after it too, so which verb runs cannot be read; put the flags after the verb"
            if verb == HIDDEN_VERB else
            f"`gh pr {verb}` is run by xargs, parallel, find or fd, which append its arguments "
            "when it runs")
    if verb in _CREATE:
        return _create_question(verb, pr_args)
    if verb == _EDIT:
        return _edit_question(pr_args)
    return _merge_question(parse_arguments(pr_args[after:], _MERGE_FLAGS),
                           parse_arguments(pr_args[:after - 1], REPO_FLAGS),
                           invocation, environ, deadline)


def _create_question(verb: str, pr_args: Sequence[Word]) -> str | None:
    """``gh pr create``: asked when the base is main, or unstated and so counted as main."""
    base = parse_arguments(pr_args, _CREATE_FLAGS).last("base")
    if base is None:
        return _into_main("opens a pull request",
                          f"`gh pr {verb}` names no --base, so gh uses the branch's configured "
                          f"merge base or else the default branch, {PRODUCTION_BRANCH}")
    return _base_question("opens a pull request", f"`gh pr {verb}`", base)


def _edit_question(pr_args: Sequence[Word]) -> str | None:
    """``gh pr edit``: asked only when it moves a pull request's base to main."""
    base = parse_arguments(pr_args, _EDIT_FLAGS).last("base")
    if base is None:
        return None
    return _base_question("retargets a pull request", "`gh pr edit`", base)


def _base_question(action: str, command: str, base: Word) -> str | None:
    """Asked when a stated base is main, or is filled in by the shell and so unknown."""
    if base.expands:
        return _unsure(f"{command} takes its base from {base.text}, which the shell fills in "
                       "when it runs")
    if base.text in _PRODUCTION_REFS:
        return _into_main(action, f"{command} --base {base.text}")
    return None


def _merge_question(arguments: Arguments, before_verb: Arguments, invocation: GhInvocation,
                    environ: Mapping[str, str], deadline: float) -> str | None:
    """``gh pr merge``: asked when GitHub says the pull request goes into main, or will not say.

    Args:
        arguments: The words after ``merge``.
        before_verb: The words between ``pr`` and ``merge`` (a ``-R`` may stand there).
        invocation: The whole command, for its directory and ``GH_REPO``.
        environ: The environment the line starts with.
        deadline: When the line's base-read budget runs out (``time.monotonic``).
    """
    selector = arguments.positionals[0] if arguments.positionals else None
    repo = arguments.last("repo") or before_verb.last("repo")
    hidden = next((word.text for word in (selector, repo, invocation.gh_repo)
                   if word is not None and word.expands), None)
    if hidden is not None:
        return _unsure(f"`gh pr merge` names its pull request through {hidden}, which the "
                       "shell fills in when it runs")
    if invocation.cwd is None:
        return _unsure("a `cd` before `gh pr merge` could not be followed, so the guard "
                       "cannot tell which repository and branch it runs in")
    named = f"pull request {selector.text}" if selector else "this branch's pull request"
    base = read_base(selector, repo, invocation, environ, deadline)
    if base is None:
        return _unsure(f"GitHub would not say in time which branch {named} goes into")
    if base in _PRODUCTION_REFS:
        return _into_main("merges a pull request", f"{named} goes into {base}")
    return None


def read_base(selector: Word | None, repo: Word | None, invocation: GhInvocation,
              environ: Mapping[str, str], deadline: float) -> str | None:
    """Ask GitHub which branch the pull request a merge names goes into.

    The same pull request ``gh pr merge`` would act on: the same selector (a
    number, URL or branch; the current branch's when none), the same ``-R``,
    the same ``GH_REPO`` -- set, or unset, as the line leaves it -- and the same
    directory.

    Returns:
        The base branch's name, or None when the read fails, or the line's
        budget is spent, for any reason.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    command = ["gh", "pr", "view"]
    if selector is not None:
        command.append(selector.text)
    if repo is not None:
        command += ["--repo", repo.text]
    command += ["--json", "baseRefName", "--jq", ".baseRefName"]
    child = {name: value for name, value in environ.items() if name != GH_REPO}
    if invocation.gh_repo is not None:
        child[GH_REPO] = invocation.gh_repo.text
    try:
        done = subprocess.run(command, cwd=invocation.cwd, env=child, capture_output=True,
                              text=True, timeout=remaining, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    base = done.stdout.strip()
    return base if done.returncode == 0 and base else None


def _into_main(action: str, detail: str) -> str:
    """The question for a command that sends a pull request into main."""
    return _REASON.format(action=f"{action} into {PRODUCTION_BRANCH}",
                          branch=PRODUCTION_BRANCH, detail=detail)


def _unsure(detail: str) -> str:
    """The question for a command the guard cannot place."""
    return _UNSURE.format(branch=PRODUCTION_BRANCH, detail=detail)


def main() -> int:
    """Ask before the Bash command on stdin sends a pull request into main (module docstring).

    Returns:
        The hook's exit status (:func:`_gh_commands.run_guard`).
    """
    return run_guard("guard-pr-main", "ask",
                     lambda command, cwd: question(command, cwd, os.environ))


if __name__ == "__main__":
    sys.exit(main())
