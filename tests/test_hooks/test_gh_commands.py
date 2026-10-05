"""The command reader both Bash guards share: what a line runs, read as bash reads it.

``scripts/hooks/_gh_commands.py`` is the one place the two PreToolUse guards
(``guard_tracker_writes.py``, ``guard_pr_main.py``) learn which ``gh`` commands a
session's Bash line will run.  A reader that misreads a line fails in one of two
directions, and both are graded here:

* **it invents a command or a doubt** -- a commit message quoting ``gh issue
  create``, an operator inside quotes, a comment, a heredoc inside a
  substitution read as unclosed -- and a guard refuses or questions a line that
  does nothing to GitHub;
* **it misses or misreads one** -- a ``gh`` call inside ``$(...)`` or an
  unquoted heredoc, a ``cd`` behind ``if``, ``GH_REPO`` set earlier in the line,
  arguments ``xargs`` appends, a ``$`` the shell will fill in -- and a guard
  lets through the command it exists to stop.

The quoting cases are the reason the reader is not :mod:`shlex`: shlex drops
quotes without a record of them, so it cannot tell GraphQL's literal
``'$id'`` from a shell variable ``"$BASE"``, and only the second is unreadable
before the line runs.  Every case that says "as bash does" was checked against
bash 5.3 on 2026-10-05.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.hooks import _gh_commands
from scripts.hooks._gh_arguments import HIDDEN_VERB, REPO_FLAGS, parse_arguments, subcommand
from scripts.hooks._gh_commands import (
    MAX_READ_BYTES,
    CommandUnreadable,
    GhInvocation,
    Word,
    gh_invocations,
)
from tests.test_hooks._command_shapes import SHAPES

START = Path("/start")
ENV = {"HOME": "/home/someone"}


def _read(command: str, environ: dict[str, str] | None = None) -> list[GhInvocation]:
    """Every gh command *command* runs, starting in :data:`START`."""
    return gh_invocations(command, START, ENV if environ is None else environ)


def _args(invocation: GhInvocation) -> list[str]:
    """An invocation's words as text, for compact assertions."""
    return [word.text for word in invocation.args]


class TestWhatIsACommand:
    """Text that is not a gh command is never read as one, and real ones are all found."""

    def test_a_heredoc_body_is_text_not_a_command(self):
        """A commit message quoting a gh write runs nothing; the command after it does."""
        found = _read(
            "git commit -F - <<'EOF'\n"
            "gh issue create -R saltyreformed-labs/shekel-plan\n"
            "EOF\n"
            "gh pr view 1"
        )
        assert [_args(invocation) for invocation in found] == [["pr", "view", "1"]]

    @pytest.mark.parametrize("command", SHAPES)
    def test_a_heredoc_inside_a_substitution_reads_whole(self, command):
        """The ``--body "$(cat <<'EOF' ... EOF)"`` shape, with every character that broke it.

        An apostrophe, a numbered list's ``)``, quotes and prose quoting gh
        commands: the body ends at its delimiter, the substitution at its
        ``)``, and nothing in the body is a command.
        """
        found = _read(command)
        assert [_args(invocation)[:2] for invocation in found] in ([], [["pr", "create"]],
                                                                   [["pr", "merge"]])

    def test_an_operator_inside_quotes_does_not_split_the_line(self):
        """``-m 'a; gh pr merge 3'`` is one argument, not a second command."""
        assert not _read("git commit -m 'stop; gh pr merge 3'")

    def test_a_comment_runs_nothing_but_a_hash_inside_a_word_is_kept(self):
        """``#`` starts a comment only at the start of a word, as in bash."""
        found = _read("echo plan#3 # gh issue close 3\ngh issue view 3")
        assert [_args(invocation) for invocation in found] == [["issue", "view", "3"]]

    def test_every_separator_starts_a_new_command(self):
        """``&&``, ``||``, ``;``, ``|`` and a newline each end a command."""
        found = _read("gh pr list && gh pr view 1 || gh pr view 2; gh pr view 3 | cat\n"
                      "gh pr view 4")
        assert [_args(invocation)[-1] for invocation in found] == ["list", "1", "2", "3", "4"]

    def test_a_command_substitution_is_read_as_its_own_line(self):
        """A gh call nested in ``$(...)`` or backticks is found, not hidden."""
        found = _read("echo $(gh issue close 3) `gh pr merge 2`")
        assert sorted(_args(invocation)[1] for invocation in found) == ["close", "merge"]

    def test_a_quoted_paren_does_not_close_a_substitution(self):
        """``$(echo ")")`` closes at the second ``)``, as bash reads it."""
        found = _read('gh pr view $(echo ")") --json number')
        assert _args(found[0])[-2:] == ["--json", "number"]

    def test_an_unquoted_heredoc_runs_its_substitutions(self):
        """Bash expands an unquoted body, so a ``$(gh ...)`` in it runs."""
        found = _read("cat <<EOF\n$(gh issue close 3)\nEOF")
        assert [_args(invocation) for invocation in found] == [["issue", "close", "3"]]

    def test_a_wrapper_does_not_hide_gh(self):
        """``env``, ``timeout`` or ``xargs`` before gh still leaves a gh command."""
        found = _read("timeout 30 /usr/bin/gh pr merge 5")
        assert [_args(invocation) for invocation in found] == [["pr", "merge", "5"]]

    def test_a_quoted_gh_phrase_is_one_word_and_not_a_command(self):
        """``grep 'gh pr create'`` searches for text; nothing runs gh."""
        assert not _read("grep -rn 'gh pr create' scripts/")

    def test_a_descriptor_number_is_not_an_argument(self):
        """``2>&1`` belongs to the redirection; the merge still names no pull request."""
        found = _read("gh pr merge --squash 2>&1")
        assert _args(found[0]) == ["pr", "merge", "--squash"]


class TestArgumentsAppendedAtRunTime:
    """A runner that appends arguments leaves gh's argument list incomplete."""

    @pytest.mark.parametrize("command", [
        "gh pr list --json number -q '.[].number' | xargs -n1 gh pr merge --squash",
        "printf '3\\n' | parallel gh issue close",
        "find . -name x -exec gh issue close {} \\;",
    ])
    def test_xargs_parallel_and_find_are_marked(self, command):
        """What gh will act on is not on the line."""
        assert not _read(command)[-1].args_complete

    def test_a_plain_wrapper_is_not(self):
        """``timeout`` and ``env`` pass the line's arguments through unchanged."""
        assert _read("env timeout 5 gh pr merge 5")[0].args_complete


class TestWhatTheShellFillsIn:
    """A word the shell rewrites is marked; a quoted ``$`` is not."""

    def test_a_single_quoted_dollar_is_literal(self):
        """GraphQL's ``$id`` in single quotes is sent as written: readable."""
        found = _read("gh api graphql -f query='mutation($id: ID!) { x }'")
        assert not any(word.expands for word in found[0].args)
        assert found[0].args[-1].text == "query=mutation($id: ID!) { x }"

    def test_an_ansi_c_quote_is_literal(self):
        """``$'main'`` is the literal word ``main``, as bash gives it."""
        assert _read("gh pr create --base $'main'")[0].args[-1] == Word("main")

    def test_a_double_quoted_variable_expands(self):
        """``"$BASE"`` is filled in when the line runs, so its value is unknown now."""
        found = _read('gh pr create --base "$BASE"')
        assert found[0].args[-1] == Word("$BASE", expands=True)

    def test_a_lone_dollar_is_a_literal_character(self):
        """A ``$`` that starts no expansion (``cost $ 5``) changes nothing."""
        found = _read("gh pr create --title 'cost' --body \"cost $ 5\"")
        assert not found[0].args[-1].expands


class TestTheStateALineCarries:
    """``cd`` and ``GH_REPO`` set earlier in a line apply to the gh command after them."""

    def test_cd_moves_the_directory(self):
        """``cd ~/x && gh ...`` runs gh in ``~/x``; a relative cd builds on it."""
        found = _read("cd ~/x && cd sub && gh pr merge")
        assert found[0].cwd == Path("/home/someone/x/sub")

    @pytest.mark.parametrize("command", [
        "if cd /wt; then gh pr merge --squash; fi",
        "{ cd /wt; } && gh pr merge --squash",
        "for x in 1; do cd /wt; gh pr merge --squash; done",
    ])
    def test_a_cd_behind_a_reserved_word_is_followed(self, command):
        """``if``, ``{`` and ``do`` stand before the ``cd``; it still moves the line."""
        assert _read(command)[0].cwd == Path("/wt")

    @pytest.mark.parametrize("command", [
        'cd "$DIR" && gh pr merge',
        "cd - && gh pr merge",
        "pushd /tmp && popd && gh pr merge",
    ])
    def test_a_cd_that_cannot_be_followed_leaves_the_directory_unknown(self, command):
        """A variable, ``cd -`` and ``popd`` cannot be known before the line runs."""
        assert _read(command)[0].cwd is None

    def test_a_subshell_puts_the_directory_back(self):
        """``(cd x && ...)`` does not move the commands after it."""
        found = _read("(cd /tmp && gh pr view 1); gh pr view 2")
        assert [invocation.cwd for invocation in found] == [Path("/tmp"), START]

    def test_gh_repo_from_a_prefix_an_export_or_the_environment(self):
        """All three ways gh can be handed ``GH_REPO`` are read, and ``unset`` clears it."""
        prefixed = _read("GH_REPO=a/b gh issue close 4")[0]
        exported = _read("export GH_REPO=c/d; gh issue close 4")[0]
        inherited = _read("gh issue close 4", {**ENV, "GH_REPO": "e/f"})[0]
        cleared = _read("unset GH_REPO; gh issue close 4", {**ENV, "GH_REPO": "e/f"})[0]
        assert [prefixed.gh_repo, exported.gh_repo, inherited.gh_repo] == [
            Word("a/b"), Word("c/d"), Word("e/f")]
        assert cleared.gh_repo is None


class TestWhatAGhCommandReads:
    """Standard input, read only on request, and None where the line does not show it."""

    def test_a_quoted_heredoc_feeds_the_command_it_follows(self):
        """The body is the command's stdin, literal, as bash delivers it."""
        found = _read("gh api graphql -F query=@- <<'EOF'\nmutation { x($id) }\nEOF")
        assert found[0].stdin.read() == "mutation { x($id) }\n"

    def test_an_unquoted_heredoc_that_expands_is_unseen(self):
        """``<<EOF`` with ``$Q`` in the body: bash fills it in, so it cannot be known."""
        assert _read("gh api graphql -F query=@- <<EOF\n$Q\nEOF")[0].stdin.read() is None

    def test_an_unquoted_heredoc_with_nothing_to_expand_is_its_text(self):
        """The control: an unquoted body with no expansion is read as written."""
        assert _read("gh api graphql -F query=@- <<EOF\nq\nEOF")[0].stdin.read() == "q\n"

    def test_a_tab_stripped_heredoc_strips_its_body_too(self):
        """``<<-`` strips leading tabs from every body line, not only the delimiter."""
        found = _read("gh api graphql --input - <<-EOF\n\tbody\n\tEOF\ngh pr view 1")
        assert found[0].stdin.read() == "body\n"
        assert _args(found[1]) == ["pr", "view", "1"]

    def test_a_here_string_is_its_word(self):
        """``<<< 'text'`` feeds the word and a newline; ``<<< "$Q"`` cannot be known."""
        assert _read("gh api graphql -F query=@- <<< 'q'")[0].stdin.read() == "q\n"
        assert _read('gh api graphql -F query=@- <<< "$Q"')[0].stdin.read() is None

    def test_cat_into_a_pipe_passes_its_heredoc_through(self):
        """``cat <<EOF | gh ...`` is a common way to feed a query; it is readable."""
        found = _read("cat <<'EOF' | gh api graphql --input -\n{\"query\": \"q\"}\nEOF")
        assert found[0].stdin.read() == '{"query": "q"}\n'

    def test_cat_of_a_file_into_a_pipe_is_that_file(self, tmp_path):
        """``cat FILE | gh ...`` feeds the file's text, read from the command's directory."""
        (tmp_path / "q.graphql").write_text("mutation { x }", encoding="utf-8")
        found = gh_invocations("cat q.graphql | gh api graphql --input -", tmp_path, ENV)
        assert found[0].stdin.read() == "mutation { x }"

    def test_any_other_pipe_is_unseen(self):
        """What another command writes into a pipe cannot be known before it runs."""
        assert _read("generate | gh api graphql --input -")[0].stdin.read() is None

    def test_a_redirected_file_is_read(self, tmp_path):
        """``< query.graphql`` is the file's text, read from the command's directory."""
        (tmp_path / "query.graphql").write_text("mutation { x }", encoding="utf-8")
        found = gh_invocations("gh api graphql --input - < query.graphql", tmp_path, ENV)
        assert found[0].stdin.read() == "mutation { x }"

    def test_nothing_redirected_is_empty_not_unseen(self):
        """With no redirection the command reads nothing, which is known."""
        assert _read("gh pr view 1")[0].stdin.read() == ""


class TestOnlySafeFilesAreRead:
    """A device, a FIFO or a huge file is never read -- not even for a command with no gh."""

    def test_a_line_redirecting_from_a_device_reads_nothing(self):
        """``tr ... < /dev/null`` runs no gh, so its input is never read.

        ``/dev/null``, never a device that streams: see the next case.  Whether
        anything is read at all is graded without a device by
        :meth:`test_input_is_read_only_when_a_guard_asks`.
        """
        started = time.monotonic()
        assert not _read("tr -dc A-Za-z < /dev/null | head -c 32")
        assert time.monotonic() - started < 5

    def test_input_is_read_only_when_a_guard_asks(self, monkeypatch, tmp_path):
        """Reading the line opens no file; asking for one command's input opens its file only."""
        opened: list[Path | None] = []
        monkeypatch.setattr(_gh_commands, "read_text",
                            lambda path: opened.append(path) or "query { x }")
        found = gh_invocations("tr -d x < a.txt; gh api graphql --input - < b.graphql",
                               tmp_path, ENV)
        assert not opened
        assert found[0].stdin.read() == "query { x }"
        assert opened == [tmp_path / "b.graphql"]

    def test_a_device_is_never_read(self):
        """A device is not a regular file, so it reads as unseen.

        ``/dev/null`` on purpose, not ``/dev/zero``: reading this device ends at
        once, empty, so a regression that drops the guard fails here cleanly
        (``""`` instead of None).  The same regression against ``/dev/zero``
        would read forever and take the host's memory with it -- measured
        2026-10-05, when a mutation run of exactly that kind exhausted memory on
        a host that also runs the production database.
        """
        found = _read("gh api graphql --input - < /dev/null")
        assert found[0].stdin.read() is None

    def test_a_fifo_is_never_opened(self, tmp_path):
        """Opening a FIFO with no writer blocks; the reader declines it instead."""
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        found = gh_invocations("gh api graphql --input - < pipe", tmp_path, ENV)
        assert found[0].stdin.read() is None

    def test_a_file_over_the_cap_is_unseen(self, tmp_path):
        """No hand-written query is a mebibyte; a larger file is not read."""
        (tmp_path / "big").write_text("x" * (MAX_READ_BYTES + 1), encoding="utf-8")
        found = gh_invocations("gh api graphql --input - < big", tmp_path, ENV)
        assert found[0].stdin.read() is None


class TestALineThatDoesNotLex:
    """An unclosed quote or substitution raises; a guard fails closed on it."""

    @pytest.mark.parametrize("command", [
        "gh pr create --title 'unclosed",
        'gh pr create --title "unclosed',
        'gh pr merge "$(echo 3"',
        "gh pr merge `echo 3",
    ])
    def test_it_raises(self, command):
        """Bash would refuse each of these lines too."""
        with pytest.raises(CommandUnreadable):
            _read(command)


class TestGhsOwnArguments:
    """Flags and their values sorted the way gh's parser (pflag) sorts them."""

    FLAGS = {"-B": "base", "--base": "base", **REPO_FLAGS}

    @pytest.mark.parametrize("spelling", [
        ["--base", "main"], ["--base=main"], ["-B", "main"], ["-Bmain"], ["-B=main"],
        ["-dB", "main"], ["-dBmain"],
    ])
    def test_every_spelling_of_a_value_lands_alike(self, spelling):
        """Long, ``=``, short, attached, and a value flag inside a cluster (``-dB``)."""
        arguments = parse_arguments([Word(text) for text in ["create", *spelling]], self.FLAGS)
        assert arguments.last("base") == Word("main")
        assert arguments.positionals == (Word("create"),)

    def test_an_empty_long_value_is_empty(self):
        """``--base=`` gives the empty value; it does not swallow the next word."""
        words = [Word(text) for text in ["--base=", "5"]]
        arguments = parse_arguments(words, self.FLAGS)
        assert arguments.last("base") == Word("")
        assert arguments.positionals == (Word("5"),)

    def test_the_last_value_wins(self):
        """gh keeps the last of a repeated flag, so the guard reads that one."""
        words = [Word(text) for text in ["--base", "dev", "--base", "main"]]
        assert parse_arguments(words, self.FLAGS).last("base") == Word("main")

    def test_a_value_is_never_a_positional(self):
        """``-R owner/repo 5``: the pull request is ``5``, not the repository."""
        words = [Word(text) for text in ["-R", "owner/repo", "5", "--squash"]]
        assert parse_arguments(words, self.FLAGS).positionals == (Word("5"),)

    def test_the_verb_is_found_after_a_repo_flag(self):
        """``gh issue -R o/r close 3``: gh allows the group's ``-R`` before the verb."""
        words = [Word(text) for text in ["-R", "o/r", "close", "3"]]
        assert subcommand(words, REPO_FLAGS) == ("close", 3)


class TestWhatTheSecondReviewFound:
    """The shapes the second review measured misread, each beside the neighbour read right."""

    def test_a_case_inside_a_substitution_is_unreadable(self):
        """Its pattern's ``)`` would end the ``$(...)``; the line raises rather than misread."""
        with pytest.raises(CommandUnreadable, match="case"):
            _read('z="$(case $y in a) gh issue close 3;; esac)"')

    def test_the_word_case_as_an_argument_is_read(self):
        """The control: ``case`` that does not start a command, or is quoted, is a word."""
        assert _args(_read("x=$(echo case) && gh pr view 1")[0]) == ["pr", "view", "1"]
        assert _args(_read("x=$('case' a) && gh pr view 1")[0]) == ["pr", "view", "1"]

    @pytest.mark.parametrize("command", [
        "PR=${PR:-$(gh pr create --base main --fill)}",
        'echo "${PR:-`gh pr create --base main --fill`}"',
        "n=$(( $(gh pr create --base main --fill | wc -l) + 1 ))",
        "v=$((cd /wt) && gh pr create --base main --fill)",
        'v="$((cd /wt) && gh pr create --base main --fill)"',
    ])
    def test_a_gh_call_inside_an_expansion_is_found(self, command):
        """Inside ``${...}``, inside arithmetic, and in ``$((`` that bash reads as ``$( (``."""
        assert [_args(invocation)[:2] for invocation in _read(command)] == [["pr", "create"]]

    @pytest.mark.parametrize("command", [
        'y="${x:-"}"}" && gh pr view 2',
        "y=\"${x:-'}'}\" && gh pr view 2",
        "y=${x:-\\}} && gh pr view 2",
        "echo $((1+(2))) && gh pr view 2",
    ])
    def test_a_brace_or_paren_inside_quotes_or_arithmetic_closes_nothing(self, command):
        """As bash 5.3 reads them: quotes inside ``${...}`` quote, and arithmetic's parens nest."""
        assert [_args(invocation) for invocation in _read(command)] == [["pr", "view", "2"]]

    def test_a_cd_into_a_directory_named_gh_is_a_cd(self):
        """``cd ~/clones/gh`` moves the line; it does not run gh."""
        found = _read("cd /clones/gh && gh issue create --title x")
        assert [(_args(invocation)[:2], invocation.cwd) for invocation in found] == [
            (["issue", "create"], Path("/clones/gh"))]

    @pytest.mark.parametrize(("command", "environ", "seen"), [
        ("GH_REPO=a/b; gh issue close 3", ENV, None),
        ("echo GH_REPO=a/b; gh issue close 3", ENV, None),
        ("GH_REPO=a/b; export GH_REPO; gh issue close 3", ENV, "a/b"),
        ("export GH_REPO; GH_REPO=a/b; gh issue close 3", ENV, "a/b"),
        ("declare -x GH_REPO=a/b; gh issue close 3", ENV, "a/b"),
        ("set -a; GH_REPO=a/b; gh issue close 3", ENV, "a/b"),
        ("set -a; set +a; GH_REPO=a/b; gh issue close 3", ENV, None),
        ("GH_REPO=a/b; gh issue close 3", {**ENV, "GH_REPO": "e/f"}, "a/b"),
        ("export -n GH_REPO; gh issue close 3", {**ENV, "GH_REPO": "e/f"}, None),
    ])
    def test_gh_repo_reaches_gh_only_when_exported(self, command, environ, seen):
        """A plain ``GH_REPO=x;`` is the shell's own until exported, as bash keeps it."""
        gh_repo = _read(command, environ)[0].gh_repo
        assert (gh_repo.text if gh_repo is not None else None) == seen

    @pytest.mark.parametrize(("command", "seen"), [
        ("env -u GH_REPO gh pr merge 3", None),
        ("env -uGH_REPO gh pr merge 3", None),
        ("env --unset=GH_REPO gh pr merge 3", None),
        ("env --unset GH_REPO gh pr merge 3", None),
        ("env -i PATH=/usr/bin gh pr merge 3", None),
        ("env -u GH_REPO GH_REPO=c/d gh pr merge 3", "c/d"),
        ("env -u HOME gh pr merge 3", "e/f"),
    ])
    def test_env_can_take_gh_repo_away(self, command, seen):
        """``env -u`` and ``env -i`` run gh without the line's ``GH_REPO``; other names keep it."""
        gh_repo = _read(command, {**ENV, "GH_REPO": "e/f"})[0].gh_repo
        assert (gh_repo.text if gh_repo is not None else None) == seen

    def test_fd_exec_is_marked(self):
        """``fd -x`` appends each found path to the command it runs, as ``find -exec`` does."""
        assert not _read("fd -e url -x gh issue close")[0].args_complete

    @pytest.mark.parametrize(("words", "verb"), [
        (["--title", "view", "create"], HIDDEN_VERB),
        (["--web", "view"], HIDDEN_VERB),
        (["-R", "o/r", "close"], "close"),
        (["-Ro/r", "close"], "close"),
        (["-R=o/r", "close"], "close"),
        (["--repo=o/r", "close"], "close"),
    ])
    def test_a_flag_before_the_verb_hides_it(self, words, verb):
        """gh's parser drops a flag AND the word after it before finding the verb.

        ``gh issue --title view create --help`` prints CREATE's help (gh 2.102,
        2026-10-05); only the repository flag, in any spelling, may stand first.
        """
        assert subcommand([Word(text) for text in words], REPO_FLAGS)[0] == verb
