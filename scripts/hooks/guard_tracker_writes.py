"""PreToolUse guard: refuse a raw ``gh`` write to the plan tracker; point the session at ``plan``.

The plan lives in a private GitHub repository's Issues and one Project board
(ruling ``balance:R-BAL170``).  Every session writes it through the ``plan``
tool (``tools/plan/plan.py``), which checks a write before sending it -- an
owner step for every finding and ruling, a finding one sentence long, a ruling
the developer's words exactly -- and writes as the plan tool's GitHub App, so a
check can tell a tool write from the developer's own edit.  The tracker's
configuration -- labels, settings, the board, the App -- has its own one home,
``tools/plan/setup_tracker.py``.  A raw ``gh`` command skips both.  This guard
reads each Bash command before it runs and refuses one that writes to the
tracker through ``gh``; KNOWN reads stay allowed.  A request the hook cannot
read is not a known read: a GraphQL query it cannot see may be a mutation, so
it is refused, and the reason says how to write it so it can be read.

**Not a security boundary.**  curl, python or an MCP tool reach GitHub without
``gh``, and ``bash -c`` or a script hides a ``gh`` call from any reader of the
command line (:mod:`_gh_commands` lists what it cannot read).  The
after-the-fact backstop is the tracker's own Action, which re-runs the plan
tool's check on every edit the App did not make (plan step L6).  This guard
stops the ordinary command and says where the right door is.

**Switched off until the cutover (plan step L8).**  Nothing registers this
hook, and ``tests/test_hooks/test_tracker_hooks_switched_off.py`` pins that.
Until L8 the coordinator and the tracker lanes still write the tracker by hand
where the plan tool has no verb yet, and every session -- the coordinator
included -- runs whatever the settings register.  L8 turns it on by adding it
to ``.claude/settings.json``; there is no switch in the code to remove after.

What it refuses (each an application of ruling ``balance:R-BAL207``, the
developer's standing rule that a session decides what he cannot see by the
best from-scratch design, under the strict, fail-closed posture he set for
security tradeoffs):

* **Any gh command that names the tracker and is not a known read**, in any
  command group: a ``-R``/``--repo`` value, ``GH_REPO``, a URL or a positional
  argument naming ``org/repo``, in every spelling gh's parser accepts
  (``-Rorg/repo``, ``-dRorg/repo``) -- a title, body or note that only
  MENTIONS it does not count, where the verb's own help says the flag takes
  text (:data:`_gh_arguments.VERB_FLAGS`; ``-d`` is a description in one verb
  and ``--delete-branch`` in another).  Reads are an allowlist per group
  (:data:`READS`), so a verb gh adds later is refused by default, and so is a
  verb a flag before it hides (``gh issue --title view create``).  This
  covers the card's ``gh issue``, ``gh project`` and ``gh api``, and also
  ``gh label`` (plan content), ``gh repo edit --visibility`` (the tracker may
  hold real figures, R-BAL172), ``gh secret set``, ``gh workflow run`` and
  ``gh pr merge`` aimed at it -- each measured passing an earlier draft by its
  review.  The tracker's settings and secrets are changed through
  ``setup_tracker.py`` or the developer's own terminal, which no hook gates.
* **A write that names no repository, run in a checkout whose remotes name the
  tracker**: gh then acts on that checkout's repository, so a clone of the
  tracker, or a remote added for it, is the tracker.  So is a checkout the hook
  cannot place -- after a ``cd`` it could not follow, or in a directory that is
  not a git checkout yet (``gh repo clone <tracker> d && cd d && ...``) -- in
  every group that acts on a repository (:data:`_NO_REPOSITORY` lists the
  groups that never do).
* **A board write**: a ``gh project`` write whose ``--owner`` (or
  ``--target-owner``) is the tracker's organization, or which names no owner
  at all -- the node-id forms -- since the guard cannot tell whose board a node
  id is.  Another owner's board passes, so plan step L10's
  ``gh project delete 1 --owner SaltyReformed`` still runs.
* **A REST write** to the tracker's paths -- named anywhere in the path, so a
  team's access to it counts -- or its organization's board and issue types,
  ``{owner}/{repo}`` (and ``:owner``/``:repo``) filled as gh fills them; or to
  a repository named by numeric id (``repositories/<id>``), which the hook
  cannot attribute.  The path is the whole of the target, and gh sends POST
  when a field or an input body is given and no method is.
* **A GraphQL mutation** that names the tracker, fills ``{owner}``/``{repo}``
  into a ``-F`` value from the tracker, uses a feature the code repository does
  not use -- boards, sub-issues, blocked-by links, issue types
  (:data:`TRACKER_FEATURES`) -- or calls any mutation whose name contains
  ``Issue`` (:func:`mutation_fields`), by whatever node id.  STATED CONDITION
  (coordinator, 2026-10-05): the code repository uses no issues and no board
  (0 and 0, measured read-only 2026-10-05), so every one of these is the
  tracker's; revisit both if it ever does.  The cost is known: a pull
  request's conversation comments are issue comments to GitHub, so
  ``updateIssueComment`` is refused even on one; the REST path and
  ``gh pr comment --edit-last`` still edit them.  The developer's demo board
  is the one other board until L10 deletes it, and a mutation of it is
  refused too.
* **In the plan-content groups (issue, label, project, api) only, a write the
  hook cannot read whole**: a target the shell fills in (``-R "$REPO"``, an
  issue URL in a variable; a title or a body in one cannot move the write),
  arguments ``xargs``, ``find`` or ``fd`` append at run time, a GraphQL query
  held in a variable or fed from a pipe or a file the hook cannot open.  A
  REST path the shell fills in is read up to its first expansion: a hidden
  tail after a written-out ``repos/OWNER/REPO/`` cannot move the call.  An
  advisory guard that cannot see such a write's
  target fails CLOSED, and its reason says how to spell the command so it can
  be read.  Outside those groups a hidden value is let through, so
  ``gh gist create "$F"`` runs.
* **A line the reader cannot follow** that mentions ``gh`` with a plan-content
  group or the tracker's name.

What it lets through by design: every known read; a write to any other
repository or board; and a GraphQL mutation by node id that is not an issue
mutation and uses no tracker feature, which no command line can attribute to a
repository without asking GitHub (the L6 Action's case).

The tracker's name is read from ``tools/plan/setup_tracker.py`` -- its one
home -- and only once a write needs a target decided: importing that module
cost 56-95 ms in measurements on 2026-10-04 and 2026-10-05, and a read never
pays it.  If the import fails the write is refused with the error, never waved
through.
"""
from __future__ import annotations

import functools
import importlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
    VerbFlags,
    flag_value,
    parse_arguments,
    prints_help,
    subcommand,
)
# Pylint: wrong-import-position -- as above, after the sys.path bootstrap.
from scripts.hooks._gh_commands import (  # pylint: disable=wrong-import-position
    CommandUnreadable,
    GhInvocation,
    Word,
    gh_invocations,
    read_text,
    resolve,
    run_guard,
)

#: Where the plan tools live, relative to the checkout.
_PLAN_DIR = Path("tools") / "plan"
#: The tracker's name is read from THIS checkout's plan tools, never from
#: whatever tree the session stands in.
_PLAN_TOOLS = Path(__file__).resolve().parents[2] / _PLAN_DIR
PLAN_TOOL = f"python {_PLAN_DIR / 'plan.py'}"

#: Every gh command group's verbs that only READ, by group name and alias
#: (``gh rs`` is ``gh ruleset``).  A command that names the tracker and is not
#: on this list is refused, in any group.
READS: Mapping[str, frozenset[str]] = {
    "issue": frozenset({"list", "ls", "status", "view"}),
    "label": frozenset({"list", "ls"}),
    "project": frozenset({"field-list", "item-list", "list", "ls", "view"}),
    "pr": frozenset({"checkout", "checks", "co", "diff", "list", "ls", "status", "view"}),
    "repo": frozenset({"autolink list", "autolink ls", "autolink view", "clone",
                       "deploy-key list", "deploy-key ls", "gitignore", "license", "list",
                       "ls", "read-dir", "read-file", "view"}),
    "release": frozenset({"download", "list", "ls", "verify", "verify-asset", "view"}),
    "run": frozenset({"download", "list", "ls", "view", "watch"}),
    "workflow": frozenset({"list", "ls", "view"}),
    "secret": frozenset({"list", "ls"}),
    "variable": frozenset({"get", "list", "ls"}),
    "cache": frozenset({"list", "ls"}),
    "ruleset": frozenset({"check", "list", "ls", "view"}),
    "rs": frozenset({"check", "list", "ls", "view"}),
    "discussion": frozenset({"list", "ls", "view"}),
    "attestation": frozenset({"download", "trusted-root", "verify"}),
    "at": frozenset({"download", "trusted-root", "verify"}),
    "agent-task": frozenset({"list", "view"}),
    "agent-tasks": frozenset({"list", "view"}),
    "agent": frozenset({"list", "view"}),
    "agents": frozenset({"list", "view"}),
    "codespace": frozenset({"list", "ls", "view"}),
    "cs": frozenset({"list", "ls", "view"}),
}
#: Verbs that only read when a flag says so: ``gh issue develop --list``
#: lists an issue's branches, and without it creates one.
_READ_FLAGS: Mapping[tuple[str, str], frozenset[str]] = {
    ("issue", "develop"): frozenset({"-l", "--list"}),
}
#: Groups every invocation of which only reads (or only prints).  ``co`` is
#: gh's own alias for ``gh pr checkout`` (``gh alias list``, 2026-10-05).
READ_ONLY_GROUPS = frozenset({"browse", "co", "completion", "help", "licenses", "search",
                              "status", "version"})
#: The flags with which gh prints and does nothing else, in place of a group.
_ROOT_PRINTS = frozenset({"--help", "-h", "--version"})
#: Nested groups whose verb is the next word (``gh repo autolink list``).
_NESTED = frozenset({"autolink", "deploy-key"})
#: The groups whose writes are plan CONTENT -- cards, labels, the board, raw
#: API calls -- where a write the hook cannot read whole is refused too.
PLAN_CONTENT = frozenset({"issue", "label", "project", "api"})
#: Groups no command of which acts on a repository: the checkout a write runs
#: in is never its target, so it is not looked up (gh 2.102's command list,
#: 2026-10-05).  A group not here -- one gh adds later included -- acts on
#: the checkout's repository when it names none.
_NO_REPOSITORY = frozenset({"alias", "auth", "completion", "config", "copilot", "extension",
                            "gist", "gpg-key", "org", "preview", "ssh-key"})
_OWNER_FLAGS = {"--owner": "owner", "--target-owner": "target-owner"}

#: Every ``gh api`` flag that takes a value, by canonical name.
_API_FLAGS = {
    "-X": "method", "--method": "method",
    "-f": "raw-field", "--raw-field": "raw-field",
    "-F": "field", "--field": "field",
    "--input": "input",
    "-H": "header", "--header": "header",
    "-q": "jq", "--jq": "jq",
    "-t": "template", "--template": "template",
    "-p": "preview", "--preview": "preview",
    "--hostname": "hostname",
    "--cache": "cache",
}
#: The flags whose presence turns gh api's default method from GET to POST.
_API_BODY = ("raw-field", "field", "input")
_READ_METHODS = frozenset({"GET", "HEAD"})
_API_HOST = re.compile(r"^https?://[^/]+/")
#: The placeholders gh fills from GH_REPO or the checkout, in a REST path and
#: in a ``-F`` field's value, in both spellings (measured with GET requests on
#: gh 2.102.0, 2026-10-05; ``-f`` values are sent raw).
_PLACEHOLDER = re.compile(r"\{(?:owner|repo)\}|:(?:owner|repo)\b")
#: A REST path naming a repository by its numeric id, which no command line
#: can attribute to a repository without asking GitHub.
_BY_ID = re.compile(r"^repositories/")
#: The ``repos/OWNER/REPO/`` that starts a path, when its owner and repository
#: are written out: a hidden tail after it cannot move the call elsewhere.
_REPO_PREFIX = re.compile(r"^repos/[^/]+/[^/]+/")
_GRAPHQL = "graphql"
_MUTATION = re.compile(r"\bmutation\b")
#: GraphQL's tokens as its specification lexes them: block strings, strings,
#: comments, the spread, names, numbers, punctuators, and the ignored blanks
#: and commas.  A ``"`` standing alone is a string that never closes.
_GRAPHQL_TOKEN = re.compile(r'"""(?:\\"""|[\s\S])*?"""|"(?:\\.|[^"\\\n])*"|#[^\n]*|\.\.\.'
                            r"|[_A-Za-z]\w*|-?\d[\w.+-]*|[\s,\ufeff]+|.")
_GRAPHQL_IGNORED = " \t\r\n,\ufeff"
_GRAPHQL_NAME = re.compile(r"[_A-Za-z]\w*")
#: A line that runs gh with a plan-content group: refused when it cannot be read.
_MAY_WRITE_PLAN = re.compile(
    r"\bgh\b[\s\S]*\b(?:" + "|".join(sorted(PLAN_CONTENT)) + r")\b")

#: The GraphQL features the code repository does not use and the tracker
#: does: its board (``ProjectV2``), sub-issues, blocked-by links and issue
#: types.  Matched in the query text, so a mutation GitHub adds for any of
#: them later is caught by its name.
TRACKER_FEATURES = re.compile(r"projectv2|subissue|blockedby|issuetype", re.IGNORECASE)

#: How long the check of a checkout's remotes may take before the guard
#: treats the checkout as unknown.
GIT_TIMEOUT_SECONDS = 5

_REASON = (
    "Writes to the plan tracker go through the plan tool ({tool}), which checks "
    "each write before it sends it, and its settings go through "
    "tools/plan/setup_tracker.py; a raw gh write skips both. Run `{tool} --help` "
    "for its commands. Reading the tracker with gh is fine. "
    "Why this command was stopped: {detail}."
)


@dataclass(frozen=True)
class Tracker:
    """The plan tracker's identity: its organization and repository."""

    org: str
    repo: str

    def named_in(self, text: str) -> bool:
        """Whether *text* names the tracker repository (``org/repo``, any case).

        Covers a ``-R`` value, ``HOST/org/repo``, an issue URL and an ``ssh`` or
        ``.git`` remote; not a longer name that merely starts the same.
        """
        pattern = rf"(?<![\w.-]){re.escape(self.org)}/{re.escape(self.repo)}(?![\w-])"
        return re.search(pattern, text, re.IGNORECASE) is not None

    def repo_named_in(self, text: str) -> bool:
        """Whether *text* names the tracker repository by its bare name (GraphQL)."""
        pattern = rf"(?<![\w.-]){re.escape(self.repo)}(?![\w-])"
        return re.search(pattern, text, re.IGNORECASE) is not None

    def owns(self, login: str) -> bool:
        """Whether *login* is the tracker's organization (logins ignore case)."""
        return login.casefold() == self.org.casefold()

    def owns_path(self, endpoint: str) -> bool:
        """Whether a REST path is the tracker or its organization's board or issue types."""
        org = re.escape(self.org)
        repo = re.escape(self.repo)
        pattern = rf"^(?:repos/{org}/{repo}(?:/|$)|orgs/{org}/(?:projects|issue-types))"
        return re.match(pattern, endpoint, re.IGNORECASE) is not None


class TrackerUnknown(RuntimeError):
    """The tracker's name could not be read from ``tools/plan/setup_tracker.py``."""


def load_tracker() -> Tracker:
    """Read the tracker's name from its one home, ``tools/plan/setup_tracker.py``.

    Raises:
        TrackerUnknown: The module would not import, or no longer defines it.
    """
    sys.path.insert(0, str(_PLAN_TOOLS))
    try:
        module = importlib.import_module("setup_tracker")
        return Tracker(org=module.ORG, repo=module.REPO)
    except (ImportError, AttributeError) as exc:
        raise TrackerUnknown(
            f"could not read the tracker's name from {_PLAN_TOOLS / 'setup_tracker.py'} ({exc})"
        ) from exc


def refusal(command: str, cwd: Path, environ: Mapping[str, str],
            tracker: Callable[[], Tracker]) -> str | None:
    """Why *command* may not run, or None when it may.

    Args:
        command: The Bash tool's command line.
        cwd: The directory it starts in.
        environ: The environment it starts with.
        tracker: Returns the tracker's identity; called only when a write, or
            an unreadable line, needs a target decided.

    Returns:
        A sentence for the refusal's reason, or None.
    """
    try:
        try:
            invocations = gh_invocations(command, cwd, environ)
        except CommandUnreadable as exc:
            if _MAY_WRITE_PLAN.search(command) or (
                    re.search(r"\bgh\b", command) and tracker().repo_named_in(command)):
                return (f"the hook could not read this command ({exc}), so it cannot "
                        "tell whether it writes to the tracker")
            return None
        for invocation in invocations:
            detail = _refusal(invocation, tracker)
            if detail is not None:
                return detail
    except TrackerUnknown as exc:
        return f"it may write through gh, and {exc}"
    return None


def _refusal(invocation: GhInvocation, tracker: Callable[[], Tracker]) -> str | None:
    """Why one gh command may not run, or None: it routes to its group's rules."""
    args = invocation.args
    if not args or args[0].text in READ_ONLY_GROUPS | _ROOT_PRINTS:
        return None
    group = args[0].text
    if group == "api":
        return _api_refusal(args[1:], invocation, tracker)
    if group == "project":
        return _project_refusal(args[1:], invocation, tracker)
    return _group_refusal(group, args[1:], invocation, tracker)


def _verb(args: Sequence[Word]) -> str | None:
    """The group's verb, two words deep for a nested group (``autolink list``)."""
    verb, after = subcommand(args, REPO_FLAGS)
    if verb in _NESTED:
        inner, _ = subcommand(args[after:], REPO_FLAGS)
        return f"{verb} {inner}" if inner is not None else verb
    return verb


def _reads(group: str, verb: str, args: Sequence[Word]) -> bool:
    """Whether the verb only reads: on its group's allowlist, or made a read by a flag."""
    if verb in READS.get(group, frozenset()):
        return True
    read_flags = _READ_FLAGS.get((group, verb), frozenset())
    return any(word.text in read_flags for word in args)


def _short_h_is_help(verb: str | None, flags: VerbFlags | None) -> bool:
    """Whether ``-h`` asks for help in this verb (:func:`_gh_arguments.prints_help`)."""
    return verb != HIDDEN_VERB and (flags is None or "-h" not in flags.values)


def _group_refusal(group: str, args: Sequence[Word], invocation: GhInvocation,
                   tracker: Callable[[], Tracker]) -> str | None:
    """Any group but ``api`` and ``project``: a write that names, or runs in, the tracker."""
    verb = _verb(args)
    if verb is None:
        return None
    flags = VERB_FLAGS.get((group, verb))
    if _reads(group, verb, args) or prints_help(args, _short_h_is_help(verb, flags)):
        return None
    command = f"`gh {group} {verb}`"
    target = tracker()
    repositories = _repositories(args, flags)
    named = repositories if (group, verb) == ("label", "clone") else (
        *_target_words(args, flags), *repositories)
    if _names_tracker(named, invocation.gh_repo, target):
        return f"{command} writes to {target.org}/{target.repo}"
    in_checkout = _checkout_refusal(command, group, bool(repositories), invocation, target)
    if in_checkout is not None:
        return in_checkout
    if group not in PLAN_CONTENT:
        return None
    return _plan_write_unreadable(command, args, invocation, flags)


def _checkout_refusal(command: str, group: str, names_repository: bool,
                      invocation: GhInvocation, target: Tracker) -> str | None:
    """A write naming no repository acts on its checkout: refused when that is the tracker.

    A checkout the hook cannot place is refused too, in every group that acts
    on a repository (all but :data:`_NO_REPOSITORY`): after a ``cd`` it could
    not follow, or in a directory that is not a git checkout when the line
    starts -- ``gh repo clone <tracker> d && cd d && gh issue create`` runs in
    a clone that does not exist yet.
    """
    gh_repo = invocation.gh_repo
    if (group in _NO_REPOSITORY or (gh_repo is not None and (gh_repo.expands or gh_repo.text))
            or names_repository):
        return None
    checkout = _checkout_is_tracker(invocation.cwd, target)
    if checkout:
        return (f"{command} names no repository, so gh acts on the checkout it runs "
                f"in, and that checkout's remotes are {target.org}/{target.repo}")
    if checkout is None:
        return (f"{command} names no repository, so gh acts on the checkout it runs in, and "
                "the hook cannot tell which repository that is (a `cd` it could not follow, "
                "or a directory that is not a git checkout yet); name the repository with -R, "
                "or run it from a checkout the hook can read")
    return None


def _plan_write_unreadable(command: str, args: Sequence[Word], invocation: GhInvocation,
                           flags: VerbFlags | None) -> str | None:
    """A plan-content write the hook cannot read whole: appended or hidden arguments."""
    if not invocation.args_complete:
        return _appended(command)
    return _unreadable(command, _hidden_target(args, invocation.gh_repo, flags))


def _names_tracker(named: Sequence[Word], gh_repo: Word | None, target: Tracker) -> bool:
    """Whether the words that may name a command's target, or its ``GH_REPO``, name the tracker.

    The caller gathers *named*: every :func:`_target_words` word and every
    ``-R`` value -- for ``gh label clone SOURCE``, only the ``-R`` side, since
    it reads labels FROM its positional and writes them to its ``-R``.
    """
    if gh_repo is not None and target.named_in(gh_repo.text):
        return True
    return any(target.named_in(word.text) for word in named)


def _repositories(args: Sequence[Word], flags: VerbFlags | None) -> tuple[Word, ...]:
    """Every ``-R``/``--repo`` value, in each spelling gh's parser accepts.

    ``-Rorg/repo`` and ``-dRorg/repo`` hold the value in the flag's own word,
    where no pattern over the word can tell it from a longer name; the parsed
    value can.  The verb's own value flags are parsed beside it, so a content
    flag earlier in a cluster takes the rest of the word as gh's parser does.
    """
    value_flags = {**REPO_FLAGS, **(flags.values if flags is not None else {})}
    return parse_arguments(args, value_flags).values.get("repo", ())


def _target_words(args: Sequence[Word], flags: VerbFlags | None) -> tuple[Word, ...]:
    """Every word that may name where a write lands: all but a content flag and its value.

    The content flags are the verb's own (:data:`_gh_arguments.VERB_FLAGS`, read
    from gh's help per verb), so ``-d`` exempts a label's description but not
    the word after ``gh pr merge -d``, where it is the boolean
    ``--delete-branch``.  A verb with no entry exempts nothing.  The value is
    skipped in every spelling gh's parser accepts: ``--body x``, ``--body=x``,
    ``-b x``, ``-bx``, ``-b=x`` and ``-db x``.
    """
    if flags is None:
        return tuple(args)
    words: list[Word] = []
    skip_next = False
    for index, word in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        text = word.text
        if text == "--":
            words.extend(args[index + 1:])
            break
        if text.startswith("-") and len(text) > 1:
            name, value = flag_value(text, flags.values)
            if name in flags.content:
                skip_next = value is None
                continue
        words.append(word)
    return tuple(words)


def _checkout_is_tracker(cwd: Path | None, target: Tracker) -> bool | None:
    """Whether the checkout a bare gh command acts on is the tracker; None if unknown.

    gh resolves a command that names no repository from the git remotes of the
    directory it runs in.  Unknown: a ``cd`` the reader could not follow, or a
    directory git cannot read as a checkout -- one the line itself clones or
    creates, which does not exist yet when the hook runs.
    """
    if cwd is None:
        return None
    try:
        done = subprocess.run(["git", "-C", str(cwd), "remote", "-v"], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode:
        return None
    return target.named_in(done.stdout)


def _project_refusal(args: Sequence[Word], invocation: GhInvocation,
                     tracker: Callable[[], Tracker]) -> str | None:
    """``gh project``: a write to a board the tracker's organization owns, or one unnamed."""
    verb, _ = subcommand(args, ())
    if verb is None or verb in READS["project"] or prints_help(args, verb != HIDDEN_VERB):
        return None
    command = f"`gh project {verb}`"
    target = tracker()
    if any(target.named_in(word.text) for word in (*args, *_repositories(args, None))):
        return f"{command} names {target.org}/{target.repo}"
    arguments = parse_arguments(args, _OWNER_FLAGS)
    owned = _owner_refusal(command, arguments.last("target-owner") or arguments.last("owner"),
                           target)
    if owned is not None:
        return owned
    return None if invocation.args_complete else _appended(command)


def _owner_refusal(command: str, owner: Word | None, target: Tracker) -> str | None:
    """A board write is the tracker's when its owner is the organization, or is unknown."""
    if owner is None:
        return (f"{command} names no --owner, so it reaches a board by node id and the "
                "hook cannot tell whose board that is; name the owner")
    if owner.expands:
        return _unreadable(command, owner.text)
    if target.owns(owner.text):
        return f"{command} writes to a board owned by {target.org}, which is the tracker's"
    return None


def _api_refusal(args: Sequence[Word], invocation: GhInvocation,
                 tracker: Callable[[], Tracker]) -> str | None:
    """``gh api``: a REST write to the tracker's paths, or a GraphQL mutation of it."""
    if prints_help(args, short_h_is_help=True):
        return None
    arguments = parse_arguments(args, _API_FLAGS)
    if not arguments.positionals:
        return None
    endpoint_word = arguments.positionals[0]
    endpoint = _API_HOST.sub("", endpoint_word.text).lstrip("/")
    if endpoint == _GRAPHQL:
        return _graphql_refusal(args, arguments, invocation, tracker)
    method = arguments.last("method")
    if method is None:
        sends_body = any(arguments.values.get(name) for name in _API_BODY)
        method = Word("POST" if sends_body else "GET")
    if not method.expands and method.text.upper() in _READ_METHODS:
        return None
    command = f"`gh api` sends {method.text.upper()} to {endpoint}"
    return _rest_write_refusal(command, endpoint_word, invocation, tracker())


def _rest_write_refusal(command: str, endpoint_word: Word, invocation: GhInvocation,
                        target: Tracker) -> str | None:
    """A REST write: refused when its path is the tracker's, or cannot be read.

    The path is the whole of a REST call's target; a field that mentions the
    tracker (a comment's body) does not aim the call at it.  The tracker named
    ANYWHERE in the path counts (``orgs/ORG/teams/T/repos/<tracker>`` grants a
    team access to it).  A path the shell fills in is read up to its first
    expansion: after a written-out ``repos/OWNER/REPO/`` the rest cannot move
    the call to another repository.  ``{owner}``/``{repo}`` (or ``:owner``/
    ``:repo``) are the tracker's when gh would fill them from it.
    """
    endpoint = _API_HOST.sub("", endpoint_word.text).lstrip("/")
    if endpoint_word.expands:
        written = _REPO_PREFIX.match(re.split(r"[$`]", endpoint, maxsplit=1)[0])
        if written is None:
            if not invocation.args_complete:
                return _appended(command)
            return _unreadable(command, endpoint_word.text)
        endpoint = written.group(0)
    if target.owns_path(_filled(endpoint, invocation.gh_repo)) or target.named_in(endpoint):
        return f"{command}, which is the tracker"
    if _BY_ID.match(endpoint):
        return (f"{command}, which names a repository by its numeric id, so the hook cannot "
                "tell whether it is the tracker; name it as repos/OWNER/REPO")
    if _PLACEHOLDER.search(endpoint):
        filled = _placeholder_refusal(command, invocation, target)
        if filled is not None:
            return filled
    return None if invocation.args_complete else _appended(command)


def _filled(endpoint: str, gh_repo: Word | None) -> str:
    """*endpoint* with its placeholders filled from a written-out ``GH_REPO``, as gh fills them."""
    if gh_repo is None or gh_repo.expands or "/" not in gh_repo.text:
        return endpoint
    owner, repo = gh_repo.text.split("/")[-2:]
    filled = re.sub(r"\{owner\}|:owner\b", owner, endpoint)
    return re.sub(r"\{repo\}|:repo\b", repo, filled)


def _placeholder_refusal(command: str, invocation: GhInvocation, target: Tracker) -> str | None:
    """A request gh fills ``{owner}``/``{repo}`` into: refused when they are the tracker's.

    Refused too when the hook cannot tell whose they are.
    """
    filled = _default_repository_is_tracker(invocation, target)
    if filled is None:
        return (f"{command}, whose {{owner}}/{{repo}} gh fills in from GH_REPO or the checkout "
                "it runs in, which the hook cannot read; write the repository out")
    if filled:
        return f"{command}, whose {{owner}}/{{repo}} gh fills in as {target.org}/{target.repo}"
    return None


def _default_repository_is_tracker(invocation: GhInvocation, target: Tracker) -> bool | None:
    """Whether the repository gh falls back to -- GH_REPO's, else the checkout's -- is the tracker.

    None when it cannot be told: GH_REPO filled in by the shell, or a checkout
    the hook cannot place.
    """
    gh_repo = invocation.gh_repo
    if gh_repo is not None and (gh_repo.expands or gh_repo.text):
        return None if gh_repo.expands else target.named_in(gh_repo.text)
    return _checkout_is_tracker(invocation.cwd, target)


def _graphql_refusal(args: Sequence[Word], arguments: Arguments, invocation: GhInvocation,
                     tracker: Callable[[], Tracker]) -> str | None:
    """``gh api graphql``: a mutation the tracker owns, or one naming the tracker."""
    request = _graphql_request(arguments, invocation) if invocation.args_complete else None
    if request is None:
        return ("it sends a GraphQL request whose query the hook cannot read (a pipe, a shell "
                "variable, xargs, or a file it cannot open), so it cannot tell a read from a "
                "write; write the query in single quotes and pass values with -F name=value")
    query, variables = request
    if _MUTATION.search(query) is None:
        return None
    command = "`gh api graphql` sends a mutation"
    owned = _tracker_mutation(command, query, variables)
    if owned is not None:
        return owned
    target = tracker()
    if target.repo_named_in(query) or target.repo_named_in(variables) or any(
            target.named_in(word.text) or target.repo_named_in(word.text) for word in args):
        return f"{command} that names {target.org}/{target.repo}"
    if any(_PLACEHOLDER.search(word.text) for word in arguments.values.get("field", ())):
        return _placeholder_refusal(command, invocation, target)
    return None


def _tracker_mutation(command: str, query: str, variables: str) -> str | None:
    """A mutation only the tracker can be the target of: a tracker feature, or an issue.

    The code repository uses no board and no issues (the stated condition in
    the module docstring), so whichever node id such a mutation names, it is
    the tracker's.
    """
    if TRACKER_FEATURES.search(query) or TRACKER_FEATURES.search(variables):
        return (f"{command} that changes a board, a sub-issue, a blocked-by link or an issue "
                "type, which the code repository does not use and the tracker does")
    fields = mutation_fields(query)
    if fields is None:
        return (f"{command} the hook cannot parse, so it cannot tell which mutation it is; "
                "write the query in single quotes and pass values with -F name=value")
    issue = next((field for field in fields if "issue" in field.casefold()), None)
    if issue is not None:
        return (f"{command}, {issue}, that changes an issue; the code repository uses no "
                "issues, so every issue mutation is the tracker's (a stated condition, to "
                "revisit if the code repository ever uses issues)")
    return None


def mutation_fields(query: str) -> list[str] | None:
    """The fields each ``mutation`` operation in *query* calls, or None when unreadable.

    ``mutation M($id: ID!) { a: closeIssue(input: {issueId: $id}) { issue { id } } }``
    calls ``closeIssue``: what counts is the top-level fields of a mutation's
    selection set -- never an alias, an argument, a string's text, a comment or
    a field selected from the result -- so a body that says "issue" is not an
    issue mutation.  A string that never closes, or a fragment spread at that
    level, makes the query unreadable.
    """
    tokens = [token for token in _GRAPHQL_TOKEN.findall(query)
              if token.strip(_GRAPHQL_IGNORED) and not token.startswith("#")]
    if '"' in tokens:
        return None
    fields: list[str] = []
    depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        elif depth == 0 and token == "mutation":
            start = next((at for at, parens in _parens(tokens, index + 1)
                          if tokens[at] == "{" and parens == 0), None)
            selected = None if start is None else _top_fields(tokens, start)
            if selected is None:
                return None
            names, index = selected
            fields.extend(names)
            continue
        index += 1
    return fields


def _parens(tokens: Sequence[str], start: int):
    """Each index from *start* with the depth of ``(``/``[`` it stands at."""
    parens = 0
    for at in range(start, len(tokens)):
        if tokens[at] in ("(", "["):
            parens += 1
        elif tokens[at] in (")", "]"):
            parens -= 1
        yield at, parens


def _top_fields(tokens: Sequence[str], start: int) -> tuple[list[str], int] | None:
    """The fields of the selection set opening at ``tokens[start]``, and the index after it."""
    names: list[str] = []
    depth = 0
    for at, parens in _parens(tokens, start):
        token = tokens[at]
        if parens or token in (")", "]"):
            continue
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return names, at + 1
        elif depth == 1 and token == "...":
            return None
        elif (depth == 1 and _GRAPHQL_NAME.fullmatch(token) and tokens[at - 1] != "@"
              and tokens[at + 1:at + 2] != [":"]):
            names.append(token)
    return None


def _graphql_request(arguments: Arguments, invocation: GhInvocation) -> tuple[str, str] | None:
    """The request's query, and everything else it sends; None when the query is unseen.

    A variable the shell fills in (``-F id="$ID"``) is kept as written: it is a
    node id or a value, and a literal node id is no more attributable to a
    repository than a hidden one.  The QUERY is what says read or write, so a
    query the hook cannot see makes the whole request unseen.  An ``--input``
    body is the request's JSON, ``{"query": ..., "variables": ...}``; a body
    that is not such an object is unseen (GitHub refuses it anyway).
    """
    queries: list[str] = []
    others: list[str] = []
    for name in ("raw-field", "field"):
        for value in arguments.values.get(name, ()):
            key, _, source = value.text.partition("=")
            if name == "field" and source.startswith("@"):
                text = _sent_file(source[1:], value, invocation)
            elif value.expands and key == "query":
                text = None
            else:
                text = source
            if text is None:
                return None
            (queries if key == "query" else others).append(text)
    for value in arguments.values.get("input", ()):
        body = _json_object(_sent_file(value.text, value, invocation))
        if body is None or not isinstance(body.get("query"), str):
            return None
        queries.append(body["query"])
        others.append(json.dumps(body.get("variables")))
    return "\n".join(queries), "\n".join(others)


def _json_object(text: str | None) -> dict | None:
    """*text* as a JSON object, or None when it is not one."""
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sent_file(path: str, word: Word, invocation: GhInvocation) -> str | None:
    """What gh sends for a ``@path`` field or an ``--input`` body; ``-`` is standard input.

    gh reads the path as written: no shell expands a ``~`` in the middle of
    ``query=@~/q.graphql``, so neither does this.
    """
    if word.expands:
        return None
    if path == "-":
        return invocation.stdin.read()
    return read_text(resolve(path, invocation.cwd))


def _hidden_target(args: Sequence[Word], gh_repo: Word | None,
                   flags: VerbFlags | None) -> str | None:
    """The first word the shell fills in that could name a plan write's target.

    Only the value of the verb's CONTENT flag (:func:`_target_words`: a title,
    a body, a label name) is known not to move the write, so ``--body "$(cat
    notes.md)"`` stays readable; a hidden word anywhere else -- a positional,
    the value of ``-R``, or a word after a boolean flag such as ``--yes`` --
    may be the target, and is reported.
    """
    hidden = next((word.text for word in _target_words(args, flags) if word.expands), None)
    if hidden is None and gh_repo is not None and gh_repo.expands:
        hidden = f"GH_REPO={gh_repo.text}"
    return hidden


def _appended(command: str) -> str:
    """Refuse a plan write whose arguments a runner appends when it runs."""
    return (f"{command}, run by xargs, parallel or find, which append its arguments when it "
            "runs, so the hook cannot tell whether the target is the tracker; write each "
            "command out, or use the plan tool")


def _unreadable(command: str, hidden: str | None) -> str | None:
    """Refuse a write whose target the hook cannot read; None when it can read it."""
    if hidden is None:
        return None
    return (f"{command}, and the hook cannot read {hidden} before the shell fills it in, "
            "so it cannot tell whether the target is the tracker; write the value out, "
            "or use the plan tool")


def _deny_reason(command: str, cwd: Path) -> str | None:
    """The refusal's full text for *command*, or None to let it run."""
    detail = refusal(command, cwd, os.environ, functools.cache(load_tracker))
    return None if detail is None else _REASON.format(tool=PLAN_TOOL, detail=detail)


def main() -> int:
    """Refuse the Bash command on stdin when it writes to the tracker raw (module docstring).

    Returns:
        The hook's exit status (:func:`_gh_commands.run_guard`).
    """
    return run_guard("guard-tracker-writes", "deny", _deny_reason)


if __name__ == "__main__":
    sys.exit(main())
