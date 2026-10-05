"""gh's own argument grammar: which words of a ``gh`` command are flags, values and verbs.

:mod:`_gh_commands` reads a Bash line the way the shell will and hands each
guard the words of every ``gh`` command in it.  What those words MEAN to gh is
this module's: gh's flag parser (pflag) sorts flags from values from
positionals (:func:`parse_arguments`), its command parser (cobra) finds a
group's verb (:func:`subcommand`), and each verb's own flags say which values
are text and which may name a target (:data:`VERB_FLAGS`).  Both guards take
all three from here, so they cannot disagree about what a command says.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from scripts.hooks._gh_commands import Word

#: gh's flag naming the repository a command group acts on.
REPO_FLAGS = {"-R": "repo", "--repo": "repo"}


@dataclass(frozen=True)
class Arguments:
    """A gh command's words sorted into flag values and positional arguments.

    Attributes:
        positionals: The words that are not flags or flag values, in order.
        values: Every value given to each value-taking flag, keyed by the
            flag's canonical name and in the order given.
    """

    positionals: tuple[Word, ...]
    values: Mapping[str, tuple[Word, ...]]

    def last(self, name: str) -> Word | None:
        """The value gh uses for flag *name*: the last one given, or None."""
        given = self.values.get(name, ())
        return given[-1] if given else None


@dataclass(frozen=True)
class VerbFlags:
    """The flags of one gh verb that take a value, as the verb's own help lists them.

    Attributes:
        values: Every spelling of every value-taking flag, mapped to its long
            name (``{"-t": "title", "--title": "title"}``).  A flag not here is
            read as a boolean, as gh's parser reads a flag it knows takes none.
        content: The long names whose value is TEXT -- a title, a body, a label
            name, a file of notes -- and so can never name where the write lands.
    """

    values: Mapping[str, str]
    content: frozenset[str]


def _verb_flags(content: str, other: str = "", repo: bool = True) -> VerbFlags:
    """A :class:`VerbFlags` written as gh's help lists the flags.

    Args:
        content: The text flags, space separated, each ``-t/--title`` or ``--attach``.
        other: The verb's other value flags, written the same way.
        repo: Whether the verb takes ``-R``/``--repo`` (every ``gh repo`` and
            ``gh gist`` verb does not).
    """
    values: dict[str, str] = dict(REPO_FLAGS) if repo else {}
    names: set[str] = set()
    for spec, is_content in ((content, True), (other, False)):
        for flag in spec.split():
            spellings = flag.split("/")
            name = spellings[-1].lstrip("-")
            values.update(dict.fromkeys(spellings, name))
            if is_content:
                names.add(name)
    return VerbFlags(values, frozenset(names))


_PR_CREATE = _verb_flags("-a/--assignee --attach -b/--body -F/--body-file -l/--label "
                         "-m/--milestone -p/--project --recover -r/--reviewer -T/--template "
                         "-t/--title", "-B/--base -H/--head")
_ISSUE_CREATE = _verb_flags("-a/--assignee --attach -b/--body -F/--body-file -l/--label "
                            "-m/--milestone -p/--project --recover -T/--template -t/--title "
                            "--type", "--blocked-by --blocking --parent")
_RELEASE_CREATE = _verb_flags("--discussion-category -n/--notes -F/--notes-file -t/--title",
                              "--notes-start-tag --target")
_REPO_CREATE = _verb_flags("-d/--description -g/--gitignore -h/--homepage -l/--license -t/--team",
                           "-r/--remote -s/--source -p/--template", repo=False)
_GIST_CREATE = _verb_flags("-d/--desc -f/--filename", repo=False)

#: The value flags of the gh verbs a session writes TEXT with, keyed by
#: ``(group, verb)``, aliases included (``gh pr new`` is ``gh pr create``).
#: Read from each verb's ``--help`` on gh 2.102.0, 2026-10-05.  A flag's
#: letter means different things in different verbs -- ``-d`` is a description
#: in ``gh label create`` and the boolean ``--delete-branch`` in ``gh pr merge``
#: -- which is why this is per verb and never one list.  A verb not here has no
#: known content flags, so every word of it may name its target.
VERB_FLAGS: Mapping[tuple[str, str], VerbFlags] = {
    ("pr", "create"): _PR_CREATE,
    ("pr", "new"): _PR_CREATE,
    ("pr", "edit"): _verb_flags(
        "--add-assignee --add-label --add-project --add-reviewer --attach -b/--body "
        "-F/--body-file -m/--milestone --remove-assignee --remove-label --remove-project "
        "--remove-reviewer -t/--title", "-B/--base"),
    ("pr", "merge"): _verb_flags(
        "-A/--author-email -b/--body -F/--body-file --match-head-commit -t/--subject"),
    ("pr", "comment"): _verb_flags("--attach -b/--body -F/--body-file"),
    ("pr", "review"): _verb_flags("-b/--body -F/--body-file"),
    ("pr", "close"): _verb_flags("-c/--comment"),
    ("issue", "create"): _ISSUE_CREATE,
    ("issue", "new"): _ISSUE_CREATE,
    ("issue", "edit"): _verb_flags(
        "--add-assignee --add-label --add-project --attach -b/--body -F/--body-file "
        "-m/--milestone --remove-assignee --remove-label --remove-project -t/--title --type",
        "--add-blocked-by --add-blocking --add-sub-issue --parent --remove-blocked-by "
        "--remove-blocking --remove-sub-issue"),
    ("issue", "comment"): _verb_flags("--attach -b/--body -F/--body-file"),
    ("issue", "close"): _verb_flags("-c/--comment -r/--reason", "--duplicate-of"),
    ("release", "create"): _RELEASE_CREATE,
    ("release", "new"): _RELEASE_CREATE,
    ("release", "edit"): _verb_flags("--discussion-category -n/--notes -F/--notes-file -t/--title",
                                     "--tag --target"),
    ("label", "create"): _verb_flags("-c/--color -d/--description"),
    ("label", "edit"): _verb_flags("-c/--color -d/--description -n/--name"),
    ("repo", "create"): _REPO_CREATE,
    ("repo", "new"): _REPO_CREATE,
    ("repo", "edit"): _verb_flags(
        "--add-topic -d/--description -h/--homepage --remove-topic --squash-merge-commit-message",
        "--default-branch --visibility", repo=False),
    ("gist", "create"): _GIST_CREATE,
    ("gist", "new"): _GIST_CREATE,
    ("gist", "edit"): _verb_flags("-a/--add -d/--desc -f/--filename -r/--remove", repo=False),
    ("discussion", "create"): _verb_flags("-b/--body -F/--body-file -c/--category -l/--label "
                                          "-t/--title"),
    ("discussion", "comment"): _verb_flags("-b/--body -F/--body-file"),
    ("discussion", "edit"): _verb_flags("--add-label -b/--body -F/--body-file -c/--category "
                                        "--remove-label -t/--title"),
}

#: What :func:`subcommand` returns when a flag stands before the verb: gh's
#: parser then drops the flag AND the word after it before it looks for the
#: verb (``gh issue --title view create`` runs ``create``), so which verb runs
#: cannot be read safely.
HIDDEN_VERB = "(a verb behind a flag)"


def prints_help(args: Sequence[Word], short_h_is_help: bool) -> bool:
    """Whether gh prints help for these words and does nothing else.

    ``--help`` always does.  ``-h`` does too, except in the two verbs that take
    ``-h`` as a flag of their own -- ``gh repo create`` and ``gh repo edit``,
    where it sets the homepage (every verb's help on gh 2.102.0, 2026-10-05)
    -- and in a verb a flag hides, which may be one of them.

    Args:
        args: The words after ``gh``'s group name.
        short_h_is_help: False for a verb whose own flags include ``-h``, or
            whose verb is :data:`HIDDEN_VERB`.
    """
    return any(word.text == "--help" or (short_h_is_help and word.text == "-h")
               for word in args)


def parse_arguments(args: Sequence[Word], value_flags: Mapping[str, str]) -> Arguments:
    """Sort *args* the way gh's flag parser (pflag) does.

    Args:
        args: The words to sort, e.g. everything after ``gh pr merge``.
        value_flags: Every spelling of every flag that takes a value, mapped to
            one canonical name (``{"-B": "base", "--base": "base"}``).  Any other
            flag is read as a boolean, which is what gh does with one it knows.

    Returns:
        The sorted :class:`Arguments`.  Every spelling pflag accepts lands
        alike: ``--base main``, ``--base=main``, ``-B main``, ``-Bmain``,
        ``-B=main``, and a value flag inside a cluster of short flags
        (``-dB main``, ``-iX DELETE``), which takes the rest of the cluster or,
        when nothing is left, the next word.
    """
    positionals: list[Word] = []
    values: dict[str, list[Word]] = {}
    index = 0
    while index < len(args):
        word = args[index]
        index += 1
        text = word.text
        if text == "--":
            positionals.extend(args[index:])
            break
        if not text.startswith("-") or text == "-":
            positionals.append(word)
            continue
        name, rest = flag_value(text, value_flags)
        if name is None:
            continue
        if rest is None:
            value = args[index] if index < len(args) else Word("")
            index += 1
        else:
            value = dataclasses.replace(word, text=rest)
        values.setdefault(name, []).append(value)
    return Arguments(tuple(positionals), {name: tuple(given) for name, given in values.items()})


def flag_value(text: str, value_flags: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The value flag a flag word sets, and its value when the word holds it.

    Args:
        text: One word starting with ``-``: ``--base=main``, ``-Bmain``, ``-dB``.
        value_flags: As for :func:`parse_arguments`.

    Returns:
        (canonical name, the value in the word), the value None meaning "the
        next word"; or (None, None) when the word sets no value flag.
    """
    if text.startswith("--"):
        flag, equals, attached = text.partition("=")
        name = value_flags.get(flag)
        return (name, attached if equals else None) if name is not None else (None, None)
    return _short_cluster_value(text, value_flags)


def _short_cluster_value(text: str,
                         value_flags: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The value flag in a short-flag cluster (``-dB...``) and its value in the word.

    Returns:
        (canonical name, the rest of the word after the flag and an optional
        ``=``), with None for the value meaning "the value is the next word";
        or (None, None) when every letter is a boolean flag.
    """
    for at in range(1, len(text)):
        short = "-" + text[at]
        if short in value_flags:
            rest = text[at + 1:]
            if rest.startswith("="):
                return value_flags[short], rest[1:]
            return value_flags[short], rest or None
    return None, None


def subcommand(args: Sequence[Word], repo_flags: Collection[str]) -> tuple[str | None, int]:
    """The verb of a gh command group (``issue``, ``pr``) and where it sits.

    gh lets the group's ``-R``/``--repo`` stand BEFORE the verb (``gh issue -R
    owner/repo close 3``), in any of its spellings, so the verb is the first
    word that is neither that flag nor its value.  Any OTHER flag before the
    verb hides it (:data:`HIDDEN_VERB`).

    Args:
        args: The words after the group's name.
        repo_flags: The spellings of the flag whose value to step over.

    Returns:
        The verb -- :data:`HIDDEN_VERB` when another flag stands before it, or
        None when the group is run with no verb (gh prints its help) -- and
        the index of the word after it.
    """
    index = 0
    while index < len(args):
        text = args[index].text
        index += 1
        if text in repo_flags:
            index += 1
        elif not text.startswith("-"):
            return text, index
        elif not _attached_value(text, repo_flags):
            return HIDDEN_VERB, index
    return None, index


def _attached_value(text: str, flags: Collection[str]) -> bool:
    """Whether *text* is one of *flags* with its value in the same word (``-Rx``, ``--repo=x``)."""
    if text.startswith("--"):
        return text.partition("=")[0] in flags
    return text[:2] in flags and len(text) > 2
