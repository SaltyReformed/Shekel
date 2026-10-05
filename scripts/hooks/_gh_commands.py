"""Read a Bash tool command the way the shell will, and find the ``gh`` commands in it.

Two PreToolUse guards decide from a session's Bash command, BEFORE it runs,
whether it may run: ``guard_tracker_writes.py`` (a raw write to the plan
tracker) and ``guard_pr_main.py`` (a pull request into ``main``).  Both need the
same answer -- which ``gh`` commands this line runs, with which words, in which
directory, under which ``GH_REPO``, fed which standard input -- so one reader
gives it and the two guards cannot disagree about what a command says.  The
hook plumbing they share -- reading the payload, printing the decision, failing
closed when the payload cannot be read -- is :func:`run_guard`, for the same
reason.

**Why a lexer of its own rather than** :mod:`shlex`.  shlex removes quotes and
keeps no record of what they were, and a guard needs exactly that record: in
``-f query='mutation($id: ID!) {...}'`` the ``$id`` is GraphQL, sent as written,
while in ``--base "$BASE"`` the shell substitutes the value before gh sees it.
One is readable and the other is not, and only the quoting says which.  shlex
also splits an operator out of a quoted word (``-m ';'``), cannot see a heredoc,
and reads ``#`` inside ``plan#3`` as a comment.  The lexer below follows bash's
own rules for the subset a session's command line uses:

* quoting: single quotes, double quotes, backslash escapes, ``$'...'``, and
  backslash-newline continuation;
* operators, recognised only outside quotes: ``&&``, ``||``, ``;``, ``|``,
  ``&``, newline, ``(`` and ``)``, and the redirections;
* heredocs: the body is read from the lines after the command, as bash reads
  it, and becomes the standard input of the command it feeds.  Under a QUOTED
  delimiter (``<<'EOF'``) the body is literal text, so a commit message quoting
  ``gh issue create`` runs nothing.  Under an unquoted one bash expands it: a
  ``$VAR`` makes the body unknowable, and a ``$(...)`` in it runs, so it is
  read as a command line like any other.  ``<<-`` strips leading tabs from the
  body and the delimiter, as bash does;
* expansions: ``$NAME``, ``${...}``, ``$(...)``, ``$((...))`` and backticks
  mark their word as one the shell rewrites (:attr:`Word.expands`).  The
  inside of a ``$(...)`` is read by this same lexer -- quotes, heredocs and
  nested substitutions included -- so the ``--body "$(cat <<'EOF' ... EOF)"``
  shape sessions use for every pull request reads correctly, and a ``gh`` call
  nested in one is found, as is one inside a ``${X:-$(...)}`` or an arithmetic
  ``$(( $(...) ))``.  ``$((`` is arithmetic only where bash takes it so;
  ``$((cd x) && gh ...)`` is a substitution;
* comments: an unquoted ``#`` that starts a word runs to the end of its line.

**State the walk follows across a line**: ``cd`` and ``pushd`` with a literal
path (``~`` included) -- behind ``if``, ``{``, ``!`` or ``do`` too -- ``popd``
(after which the directory is unknown), and ``GH_REPO`` as gh will see it:
inherited, ``export``-ed or ``declare -x``-ed, set by a plain ``GH_REPO=x;`` only
once exported (or under ``set -a``), cleared by ``unset``, and per command by a
``GH_REPO=x`` prefix, ``env -u GH_REPO`` or ``env -i``.  A subshell's
``( ... )`` puts all of it back when it closes.
A ``cd`` it cannot follow (``cd "$DIR"``, ``cd -``) leaves the directory
unknown, and a caller fails closed on that rather than guessing.

**Standard input is read only when a guard asks for it** (:class:`Stdin`), and
then only from regular files no larger than :data:`MAX_READ_BYTES`: a line such
as ``tr -dc A-Za-z < /dev/urandom`` is never read, whatever it runs.

**How a ``gh`` command is found**: the first unexpanded word whose file name is
``gh`` starts it, whatever stands before it, so a wrapper (``env``, ``command``,
``time``, ``timeout``) cannot hide one -- except a ``cd``, ``pushd`` or
``popd``, whose argument is a directory.  A runner that APPENDS arguments at run
time -- ``xargs``, ``parallel``, ``find -exec``, ``fd -x`` -- is found too, and the
invocation is marked :attr:`GhInvocation.args_complete` False, because what gh
will act on is not on the line.  The cost of matching anywhere is a false
match on an unquoted ``echo gh pr create``, which can only make a guard ask or
refuse a command that does nothing.

**What it cannot read, and why both guards are advisory.**  Text the shell runs
as a program -- ``bash -c '...'``, ``eval``, a script file -- is not read, nor
is gh reached under another name (``$(which gh)``, ``"$GH"``, ``g\\h``, a
symlink) or a ``gh`` alias the developer defines.  A construct the lexer does
not follow (a ``case`` starting a command inside ``$(...)``, whose patterns'
``)`` would end the substitution) makes a line UNREADABLE, which each guard
treats as fail-closed for lines that could run what it gates.  Nothing here
is a security boundary: curl, python or an MCP tool reach GitHub without
``gh`` at all.  The guards exist to stop the
ordinary command, and say so in their own headers.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: The environment variable gh reads for the repository a command targets.
GH_REPO = "GH_REPO"

#: The largest file the hook reads to see what a command sends.  A GraphQL
#: query or a request body a session writes is a few kilobytes; anything
#: larger is treated as unseen rather than read.
MAX_READ_BYTES = 1 << 20

#: Runners that append arguments to the command they run, at run time.
_APPENDING_RUNNERS = frozenset({"xargs", "parallel", "find", "fd"})
#: Builtins whose arguments are directories: ``cd ~/clones/gh`` is a ``cd``, never a gh call.
_DIRECTORY_BUILTINS = frozenset({"cd", "pushd", "popd"})
#: Builtins that set a variable's attributes: with ``-x`` they export it, as ``export`` does.
_DECLARE = frozenset({"declare", "typeset"})
#: Words that may stand before a command without being one (bash's reserved
#: words, and the two builtins that run the next word as a command).
_COMMAND_PREFIXES = frozenset({
    "!", "{", "}", "if", "then", "elif", "else", "fi", "do", "done", "while", "until",
    "time", "builtin", "command",
})

#: Every operator, LONGEST FIRST, so ``&&`` is never read as two ``&``.
_OPERATORS = (
    "&>>", "<<<", "<<-", ";;&",
    ";;", "&&", "||", "|&", "<<", ">>", ">&", "<&", "<>", ">|", "&>",
    ";", "&", "|", "(", ")", "<", ">",
)
#: The characters that start an operator outside quotes (``-`` only ever
#: continues one, in ``<<-``).
_OPERATOR_START = frozenset(";&|()<>")
_BLANKS = frozenset(" \t\r")

#: Operators that end one simple command and start the next.
_SEPARATORS = frozenset({"&&", "||", ";", ";;", ";;&", "&", "|", "|&", "\n"})
#: Separators that feed the previous command's output to the next one.
_PIPES = frozenset({"|", "|&"})
#: Redirections whose next word is a file or a descriptor, never an argument.
_REDIRECTS = frozenset({"<", ">", ">>", ">&", "&>", "&>>", "<&", "<>", ">|"})
_HEREDOCS = frozenset({"<<", "<<-"})
#: A heredoc delimiter written with any of these is quoted: its body is literal.
_QUOTING = frozenset("'\"\\")

#: ``NAME=value`` as the shell reads an assignment word.
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)
#: The characters a ``$NAME`` expansion takes.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: The one-character parameters (``$1``, ``$@``, ``$?`` ...).
_SPECIAL_PARAMETERS = frozenset("0123456789@*#?$!-")
#: Characters a backslash escapes inside double quotes and unquoted heredoc
#: bodies; before any other the backslash is kept (bash's rule).
_DOUBLE_QUOTE_ESCAPES = frozenset('$`"\\\n')


class CommandUnreadable(ValueError):
    """The reader cannot follow the line: an unclosed quote, or a construct it does not read.

    A guard fails closed on it, for lines that could run what it gates: a line
    the reader cannot follow is one it cannot vouch for.  It can be valid bash
    (see the module docstring), so each guard narrows the refusal to lines that
    mention what it gates.
    """


@dataclass(frozen=True)
class Word:
    """One shell word, quotes removed.

    Attributes:
        text: The word as the command receives it, except that an expansion is
            kept as written (``$BASE``), because its value does not exist until
            the shell runs the line.
        expands: True when the shell rewrites part of the word before the
            command sees it -- a variable, ``${...}``, ``$(...)`` or backticks,
            unquoted or in double quotes.  Single-quoted ``$`` is literal and
            does not count.
        substitutions: The text inside each ``$(...)`` or backtick in the word,
            which the walk reads as a command line of its own.
    """

    text: str
    expands: bool = False
    substitutions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stdin:
    """What a command reads on standard input, read only when a guard asks.

    Attributes:
        text: Text the line itself supplies -- a heredoc body, a here-string --
            or ``""`` when nothing feeds the command.
        files: Files whose text it is instead (``< query.graphql``,
            ``cat a b | ...``), resolved to absolute paths and not yet read.
        seen: False when the line cannot show it: another command's output in
            a pipe, a heredoc body or here-string the shell expands, a file
            named through a variable or relative to an unknown directory.
    """

    text: str = ""
    files: tuple[Path, ...] = ()
    seen: bool = True

    def read(self) -> str | None:
        """The text, or None when it is unseen or a file cannot be read safely."""
        if not self.seen:
            return None
        if not self.files:
            return self.text
        texts = [read_text(path) for path in self.files]
        if any(text is None for text in texts):
            return None
        return "".join(text for text in texts if text is not None)


#: Standard input the line cannot show.
UNSEEN = Stdin(seen=False)


@dataclass(frozen=True)
class GhInvocation:
    """One ``gh`` command a Bash command line runs.

    Attributes:
        args: The words after ``gh``, e.g. ``pr``, ``merge``, ``5``.
        gh_repo: The ``GH_REPO`` gh will read, or None when nothing sets it.
        cwd: The directory it runs in, or None when a ``cd`` before it could not
            be followed.
        stdin: What it reads on standard input (:class:`Stdin`).
        args_complete: False when a runner (``xargs``, ``parallel``, ``find``)
            appends arguments at run time, so :attr:`args` is not all gh gets.
    """

    args: tuple[Word, ...]
    gh_repo: Word | None
    cwd: Path | None
    stdin: Stdin
    args_complete: bool = True


def run_guard(name: str, decision: str, decide: Callable[[str, Path], str | None]) -> int:
    """Run a PreToolUse guard over the Bash payload on stdin.

    Args:
        name: The hook's name, for its error message.
        decision: The permission decision it gives (``"deny"`` or ``"ask"``).
        decide: Given the command line and the directory it starts in, the
            decision's reason, or None to let the command run.

    Returns:
        The hook's exit status: 0, or 2 when the payload cannot be read -- the
        harness then blocks the command and shows the message, because a gate
        fails closed, never silently open.
    """
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError(f"tool_input.command is {type(command).__name__}, not a string")
    except (ValueError, KeyError, TypeError) as exc:
        print(f"{name}: could not read the Bash command from the hook payload ({exc}); "
              "failing closed.", file=sys.stderr)
        return 2
    reason = decide(command, Path(payload.get("cwd") or os.getcwd()))
    if reason is not None:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }))
    return 0


def resolve(path: str, cwd: Path | None) -> Path | None:
    """Where a file a command names lives, or None when its directory is unknown.

    The path is taken as written, relative to *cwd* unless absolute; the
    caller expands a leading ``~`` where the shell would.
    """
    if os.path.isabs(path):
        return Path(path)
    return None if cwd is None else cwd / path


def read_text(path: Path | None) -> str | None:
    """The text of a REGULAR file of at most :data:`MAX_READ_BYTES`, or None.

    Never a device, a FIFO or a socket -- reading ``/dev/urandom`` would never
    end, and a FIFO would block the hook -- and never a file too large to be a
    hand-written query or body.
    """
    if path is None:
        return None
    try:
        status = os.stat(path)
        if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_READ_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def gh_invocations(command: str, cwd: Path, environ: Mapping[str, str]) -> list[GhInvocation]:
    """Every ``gh`` command *command* runs, in order.

    Args:
        command: The Bash tool's command line.
        cwd: The directory the line starts in (the hook payload's ``cwd``).
        environ: The environment it starts with, for ``GH_REPO`` and ``HOME``.

    Returns:
        One :class:`GhInvocation` per ``gh`` command, nested substitutions
        included.

    Raises:
        CommandUnreadable: The reader cannot follow the line.
    """
    gh_repo = Word(environ[GH_REPO]) if GH_REPO in environ else None
    walk = _Walk(environ.get("HOME"))
    walk.walk(_Lexer(command).tokens(), _Scope(cwd, gh_repo, exported=gh_repo is not None))
    return walk.found


@dataclass(frozen=True)
class _Operator:
    """An operator token.

    A heredoc's carries its body (None when the shell expands it into
    something unknowable) and the command substitutions the body runs.
    """

    text: str
    heredoc: str | None = None
    substitutions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _OpenHeredoc:
    """A heredoc whose body starts after the current line."""

    index: int
    delimiter: str
    strip_tabs: bool
    quoted: bool


class _Lexer:
    """Split one command line into :class:`Word` and :class:`_Operator` tokens.

    A lexer started with ``nested=True`` reads the inside of a ``$(...)`` and
    stops at the ``)`` that closes it, leaving :attr:`end` there.
    """

    def __init__(self, text: str, start: int = 0, nested: bool = False) -> None:
        """Prepare to lex *text* from *start*."""
        self._text = text
        self._at = start
        self._nested = nested
        self._depth = 0
        self._tokens: list[Word | _Operator] = []
        self._open_heredocs: list[_OpenHeredoc] = []
        self.end = len(text)

    def tokens(self) -> list[Word | _Operator]:
        """Lex the line (or, nested, up to the closing ``)``)."""
        text = self._text
        while self._at < len(text):
            char = text[self._at]
            if char in _BLANKS:
                self._at += 1
            elif text.startswith("\\\n", self._at):
                self._at += 2
            elif char == "\n":
                self._at += 1
                self._tokens.append(_Operator("\n"))
                self._read_heredoc_bodies()
            elif char == "#":
                end = text.find("\n", self._at)
                self._at = len(text) if end < 0 else end
            elif char == ")" and self._nested and self._depth == 0:
                self.end = self._at
                return self._tokens
            elif char in _OPERATOR_START:
                self._operator()
            else:
                self._word()
        if self._nested:
            raise CommandUnreadable("a $(...) substitution is never closed")
        self._read_heredoc_bodies()
        return self._tokens

    def _operator(self) -> None:
        """Read the operator at the cursor; a heredoc's also reads its delimiter."""
        text = self._text
        operator = next(op for op in _OPERATORS if text.startswith(op, self._at))
        self._at += len(operator)
        if operator == "(":
            self._depth += 1
        elif operator == ")":
            self._depth -= 1
        if operator not in _HEREDOCS:
            self._tokens.append(_Operator(operator))
            return
        while self._at < len(text) and text[self._at] in _BLANKS:
            self._at += 1
        start = self._at
        delimiter = self._read_word()
        quoted = any(char in _QUOTING for char in text[start:self._at])
        self._open_heredocs.append(
            _OpenHeredoc(len(self._tokens), delimiter.text, operator == "<<-", quoted))
        self._tokens.append(_Operator("<<"))

    def _read_heredoc_bodies(self) -> None:
        """Read the body of each heredoc opened on the line just ended, in order."""
        text = self._text
        for heredoc in self._open_heredocs:
            lines: list[str] = []
            while self._at < len(text):
                end = text.find("\n", self._at)
                end = len(text) if end < 0 else end
                line = text[self._at:end]
                self._at = end + 1
                if heredoc.strip_tabs:
                    line = line.lstrip("\t")
                if line == heredoc.delimiter:
                    break
                lines.append(line)
            body = "".join(line + "\n" for line in lines)
            if heredoc.quoted:
                self._tokens[heredoc.index] = _Operator("<<", body)
            else:
                expands, substitutions = _Lexer(body).expanded_body()
                self._tokens[heredoc.index] = _Operator(
                    "<<", None if expands else body, tuple(substitutions))
        self._open_heredocs.clear()

    def expanded_body(self) -> tuple[bool, list[str]]:
        """Read the whole text as an unquoted heredoc body: what expands, what runs.

        Bash expands an unquoted body as it does a double-quoted string, with
        no closing quote: ``$``-expansions and backticks, escaped by a
        backslash before ``$``, a backtick, a backslash or a newline.
        """
        text = self._text
        substitutions: list[str] = []
        expands = False
        while self._at < len(text):
            char = text[self._at]
            if char == "\\" and text[self._at + 1:self._at + 2] in _DOUBLE_QUOTE_ESCAPES:
                self._at += 2
            elif char in "$`":
                expands |= self._expansion(substitutions)[1]
            else:
                self._at += 1
        return expands, substitutions

    def _word(self) -> None:
        """Read the word at the cursor and keep it, unless it is a redirection's descriptor.

        Inside a ``$(...)`` an unquoted ``case`` that starts a command raises
        :class:`CommandUnreadable`: each of its patterns ends in a ``)`` this
        lexer would take for the substitution's end, so the arms after it would
        go unread.  Not one of 7,566 recorded Bash calls containing ``gh`` uses
        it (all of this project's session transcripts, 2026-10-05).
        """
        start = self._at
        word = self._read_word()
        text = self._text
        if self._nested and text[start:self._at] == "case" and self._starts_command():
            raise CommandUnreadable("a `case` inside $(...) is not read: its patterns' `)` "
                                    "would end the substitution early")
        # A descriptor number written against its redirection (``2>&1``) is
        # part of the operator to bash, not an argument of the command.
        if (not word.expands and word.text.isdigit()
                and self._at < len(text) and text[self._at] in "<>"):
            return
        self._tokens.append(word)

    def _starts_command(self) -> bool:
        """Whether the word being read stands where a command name does."""
        if not self._tokens:
            return True
        previous = self._tokens[-1]
        if isinstance(previous, Word):
            return not previous.expands and previous.text in _COMMAND_PREFIXES
        return previous.text not in _REDIRECTS | _HEREDOCS | {"<<<"}

    def _read_word(self) -> Word:
        """Read one word from the cursor, following bash's quoting."""
        text = self._text
        parts: list[str] = []
        substitutions: list[str] = []
        expands = False
        while self._at < len(text):
            char = text[self._at]
            if char in _BLANKS or char == "\n" or char in _OPERATOR_START:
                break
            if char == "\\":
                escaped = text[self._at + 1:self._at + 2]
                self._at += 2
                if escaped != "\n":
                    parts.append(escaped)
            elif char == "'":
                end = text.find("'", self._at + 1)
                if end < 0:
                    raise CommandUnreadable("a single quote is never closed")
                parts.append(text[self._at + 1:end])
                self._at = end + 1
            elif text.startswith("$'", self._at):
                parts.append(self._ansi_c_quoted())
            elif text.startswith('$"', self._at):
                self._at += 1
            elif char == '"':
                expands |= self._double_quoted(parts, substitutions)
            elif char in "$`":
                raw, rewritten = self._expansion(substitutions)
                parts.append(raw)
                expands |= rewritten
            else:
                parts.append(char)
                self._at += 1
        return Word("".join(parts), expands, tuple(substitutions))

    def _ansi_c_quoted(self) -> str:
        """``$'...'``: literal to the shell's expansions; escapes are kept as written."""
        text = self._text
        at = self._at + 2
        while at < len(text) and text[at] != "'":
            at += 2 if text[at] == "\\" else 1
        if at >= len(text):
            raise CommandUnreadable("a $'...' quote is never closed")
        body = text[self._at + 2:at]
        self._at = at + 1
        return body

    def _double_quoted(self, parts: list[str], substitutions: list[str]) -> bool:
        """Read ``"..."`` into *parts*; return whether anything in it expands."""
        text = self._text
        self._at += 1
        expands = False
        while self._at < len(text):
            char = text[self._at]
            if char == '"':
                self._at += 1
                return expands
            if char == "\\" and text[self._at + 1:self._at + 2] in _DOUBLE_QUOTE_ESCAPES:
                escaped = text[self._at + 1]
                self._at += 2
                if escaped != "\n":
                    parts.append(escaped)
            elif char in "$`":
                raw, rewritten = self._expansion(substitutions)
                parts.append(raw)
                expands |= rewritten
            else:
                parts.append(char)
                self._at += 1
        raise CommandUnreadable("a double quote is never closed")

    def _expansion(self, substitutions: list[str]) -> tuple[str, bool]:
        """Read the expansion at the cursor (a ``$`` or a backtick).

        Returns:
            Its text as written, and whether the shell rewrites it -- a ``$``
            that starts nothing (``$`` before a blank) is a literal dollar.
        """
        text = self._text
        start = self._at
        if text[start] == "`":
            end = _closing_backtick(text, start + 1)
            substitutions.append(text[start + 1:end])
            self._at = end + 1
            return text[start:self._at], True
        following = text[start + 1:start + 2]
        if following == "(":
            if not (text.startswith("$((", start) and self._arithmetic(substitutions)):
                inner = _Lexer(text, start + 2, nested=True)
                inner.tokens()
                substitutions.append(text[start + 2:inner.end])
                self._at = inner.end + 1
        elif following == "{":
            self._braced(substitutions)
        elif following and _NAME.match(following):
            self._at = _NAME.match(text, start + 1).end()
        elif following and following in _SPECIAL_PARAMETERS:
            self._at = start + 2
        else:
            self._at = start + 1
            return "$", False
        return text[start:self._at], True

    def _arithmetic(self, substitutions: list[str]) -> bool:
        """Read the ``$((...))`` at the cursor, if bash reads it as arithmetic.

        Bash takes ``$((`` as arithmetic only when the ``)`` closing its inner
        parenthesis is followed at once by a second ``)``.  Otherwise -- as in
        ``$((cd x) && gh pr create ...)`` -- it is a command substitution whose
        command opens a subshell, and this returns False with the cursor where
        it was.  A substitution inside arithmetic runs, so it is kept.
        """
        text = self._text
        start = self._at
        inner: list[str] = []
        depth = 0
        self._at = start + 3
        while self._at < len(text):
            char = text[self._at]
            if char == "\\":
                self._at += 2
            elif char in "$`":
                self._expansion(inner)
            elif char == "(" or (char == ")" and depth):
                depth += 1 if char == "(" else -1
                self._at += 1
            elif char == ")":
                if not text.startswith("))", self._at):
                    self._at = start
                    return False
                self._at += 2
                substitutions.extend(inner)
                return True
            else:
                self._at += 1
        raise CommandUnreadable("a $((...)) expansion is never closed")

    def _braced(self, substitutions: list[str]) -> None:
        """Read the ``${...}`` at the cursor, keeping the substitutions inside it.

        ``${PR:-$(gh pr create ...)}`` runs the gh call when ``PR`` is empty,
        so a parameter expansion's word is read like any other.  Inside the
        braces, as bash 5.3 reads them (checked 2026-10-05): a backslash
        escapes the next character, single and double quotes quote -- inside
        double quotes too -- expansions nest, and the first ``}`` outside all
        of those closes it; a ``{`` opens nothing.
        """
        text = self._text
        self._at += 2
        while self._at < len(text):
            char = text[self._at]
            if char == "}":
                self._at += 1
                return
            if char == "\\":
                self._at += 2
            elif char == "'":
                end = text.find("'", self._at + 1)
                if end < 0:
                    raise CommandUnreadable("a single quote is never closed")
                self._at = end + 1
            elif char == '"':
                self._double_quoted([], substitutions)
            elif char in "$`":
                self._expansion(substitutions)
            else:
                self._at += 1
        raise CommandUnreadable("a ${...} expansion is never closed")


def _closing_backtick(text: str, at: int) -> int:
    """Index of the backtick that closes the one before *at*."""
    while at < len(text):
        if text[at] == "\\":
            at += 2
        elif text[at] == "`":
            return at
        else:
            at += 1
    raise CommandUnreadable("a backtick substitution is never closed")


def _gh_repo_for(before: Sequence[Word], scope: _Scope) -> Word | None:
    """The ``GH_REPO`` gh gets: the line's exported value, as the words before ``gh`` change it.

    A ``GH_REPO=value`` before the command sets it for that command alone;
    ``env -u GH_REPO`` (``-uGH_REPO``, ``--unset[=]GH_REPO``) and ``env -i``
    take it away.
    """
    gh_repo = scope.gh_repo if scope.exported else None
    in_env = False
    index = 0
    while index < len(before):
        word = before[index]
        index += 1
        assignment = _ASSIGNMENT.fullmatch(word.text)
        if assignment:
            if assignment.group(1) == GH_REPO:
                gh_repo = Word(assignment.group(2), word.expands)
        elif os.path.basename(word.text) == "env":
            in_env = True
        elif in_env and word.text in ("-i", "--ignore-environment", "-"):
            gh_repo = None
        elif in_env and word.text in ("-u", "--unset"):
            if index < len(before) and before[index].text == GH_REPO:
                gh_repo = None
            index += 1
        elif in_env and word.text in (f"-u{GH_REPO}", f"--unset={GH_REPO}"):
            gh_repo = None
    return gh_repo


def _allexport(args: Sequence[Word], current: bool) -> bool:
    """Whether ``set`` with *args* leaves ``set -a`` (``-o allexport``) on."""
    texts = [word.text for word in args]
    for index, text in enumerate(texts):
        if text[:1] in "-+" and text[1:2] != text[:1] and "a" in text[1:]:
            current = text[0] == "-"
        elif text in ("-o", "+o") and texts[index + 1:index + 2] == ["allexport"]:
            current = text == "-o"
    return current


def _assigned(name: str, words: Sequence[Word], scope: _Scope) -> _Scope:
    """The ``GH_REPO`` state after a command that may assign or export it.

    ``export`` and ``declare -x`` export (``export -n`` un-exports).  A command
    of nothing but assignments sets the SHELL's variables, and one already
    exported, or assigned under ``set -a``, reaches gh too; assignments before
    any other command are that command's alone and leave the line unchanged.
    """
    if name == "export" or (name in _DECLARE and any(
            word.text.startswith("-") and "x" in word.text for word in words[1:])):
        exported = not (name == "export" and any(word.text == "-n" for word in words[1:]))
        for word in words[1:]:
            assignment = _ASSIGNMENT.fullmatch(word.text)
            if word.text == GH_REPO:
                scope = dataclasses.replace(scope, exported=exported)
            elif assignment and assignment.group(1) == GH_REPO:
                scope = dataclasses.replace(scope, exported=exported,
                                            gh_repo=Word(assignment.group(2), word.expands))
        return scope
    if all(_ASSIGNMENT.fullmatch(word.text) for word in words):
        for word in words:
            assignment = _ASSIGNMENT.fullmatch(word.text)
            if assignment and assignment.group(1) == GH_REPO:
                scope = dataclasses.replace(scope, gh_repo=Word(assignment.group(2), word.expands),
                                            exported=scope.exported or scope.allexport)
    return scope


@dataclass(frozen=True)
class _Scope:
    """What a command line has changed so far: its directory and ``GH_REPO``.

    Attributes:
        cwd: The directory, or None when a ``cd`` could not be followed.
        gh_repo: The shell's ``GH_REPO``, or None when it is unset.
        exported: Whether it is exported, so gh sees it: inherited, or exported
            by ``export`` or ``declare -x``.  A plain ``GH_REPO=x;`` stays the
            shell's own unless it was exported already or ``set -a`` is on.
        allexport: Whether ``set -a`` exports every assignment.
    """

    cwd: Path | None
    gh_repo: Word | None
    exported: bool = False
    allexport: bool = False


@dataclass
class _Command:
    """The simple command being read: its words and its standard input."""

    stdin: Stdin
    words: list[Word] = dataclasses.field(default_factory=list)


class _Walk:
    """Walk the tokens, following the line's state, and collect the gh commands."""

    def __init__(self, home: str | None) -> None:
        """Start a walk; *home* is where ``cd`` and ``~`` go."""
        self._home = home
        self.found: list[GhInvocation] = []

    def walk(self, tokens: Sequence[Word | _Operator], scope: _Scope) -> _Scope:
        """Walk *tokens* starting in *scope*; return the scope the line ends in."""
        command = _Command(stdin=Stdin())
        outer: list[tuple[_Command, _Scope]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if isinstance(token, Word):
                command.words.append(token)
                continue
            operator = token.text
            if operator in _SEPARATORS:
                scope = self._finish(command, scope)
                piped = self._piped(command, scope) if operator in _PIPES else Stdin()
                command = _Command(stdin=piped)
            elif operator == "<<":
                for substitution in token.substitutions:
                    self.walk(_Lexer(substitution).tokens(), scope)
                command.stdin = UNSEEN if token.heredoc is None else Stdin(text=token.heredoc)
            elif operator == "<<<" or operator in _REDIRECTS:
                target = tokens[index] if index < len(tokens) else None
                if isinstance(target, Word):
                    index += 1
                    command.stdin = self._redirected(operator, target, scope, command.stdin)
            elif operator == "(":
                # A subshell -- or a process substitution: either way the inner
                # commands run in a COPY of the state, and the command around
                # them resumes when it closes.
                outer.append((command, scope))
                command = _Command(stdin=Stdin())
            elif operator == ")":
                self._finish(command, scope)
                command, scope = outer.pop() if outer else (_Command(stdin=Stdin()), scope)
        scope = self._finish(command, scope)
        while outer:
            command, scope = outer.pop()
            scope = self._finish(command, scope)
        return scope

    def _path(self, word: Word, scope: _Scope) -> Path | None:
        """Where a file word points, with a leading ``~`` expanded as bash does."""
        if word.expands:
            return None
        path = word.text
        if path == "~" or path.startswith("~/"):
            if not self._home:
                return None
            path = self._home + path[1:]
        return resolve(path, scope.cwd)

    def _redirected(self, operator: str, target: Word, scope: _Scope, current: Stdin) -> Stdin:
        """A command's standard input after a redirection to *target*."""
        if operator == "<<<":
            return UNSEEN if target.expands else Stdin(text=target.text + "\n")
        if operator != "<":
            return current
        path = self._path(target, scope)
        return UNSEEN if path is None else Stdin(files=(path,))

    def _piped(self, command: _Command, scope: _Scope) -> Stdin:
        """What a command writes into a pipe, when it is ``cat`` and the line shows it.

        ``cat <<'EOF' | gh api graphql --input -`` is a common way to feed a
        long query, so the guard can read it; any other command's output is
        unseen.
        """
        words = command.words
        if not words or words[0].text != "cat":
            return UNSEEN
        files = words[1:]
        if not files or [word.text for word in files] == ["-"]:
            return command.stdin
        if any(word.text.startswith("-") for word in files):
            return UNSEEN
        paths = [self._path(word, scope) for word in files]
        if any(path is None for path in paths):
            return UNSEEN
        return Stdin(files=tuple(path for path in paths if path is not None))

    def _finish(self, command: _Command, scope: _Scope) -> _Scope:
        """Record the command if it runs gh; return the scope after it."""
        words = command.words
        for word in words:
            for substitution in word.substitutions:
                self.walk(_Lexer(substitution).tokens(), scope)
        name = next((word.text for word in words if word.text not in _COMMAND_PREFIXES), None)
        gh_at = None if name in _DIRECTORY_BUILTINS else next(
            (i for i, word in enumerate(words)
             if not word.expands and os.path.basename(word.text) == "gh"), None)
        if gh_at is None:
            return self._follow(words, scope)
        appended = any(os.path.basename(word.text) in _APPENDING_RUNNERS for word in words[:gh_at])
        gh_repo = _gh_repo_for(words[:gh_at], scope)
        self.found.append(GhInvocation(tuple(words[gh_at + 1:]), gh_repo, scope.cwd,
                                       command.stdin, not appended))
        return scope

    def _follow(self, words: Sequence[Word], scope: _Scope) -> _Scope:
        """The state a command that is not gh leaves behind it."""
        words = list(words)
        while words and words[0].text in _COMMAND_PREFIXES:
            words.pop(0)
        if not words:
            return scope
        name = words[0].text
        if name in ("cd", "pushd"):
            return dataclasses.replace(scope, cwd=self._cd(words[1:], scope.cwd))
        if name == "popd":
            return dataclasses.replace(scope, cwd=None)
        if name == "unset" and any(word.text == GH_REPO for word in words[1:]):
            return dataclasses.replace(scope, gh_repo=None, exported=False)
        if name == "set":
            return dataclasses.replace(scope, allexport=_allexport(words[1:], scope.allexport))
        return _assigned(name, words, scope)

    def _cd(self, args: Sequence[Word], cwd: Path | None) -> Path | None:
        """Where ``cd`` with *args* leaves the line, or None when it cannot be known."""
        targets = [word for word in args if word.text == "-" or not word.text.startswith("-")]
        if not targets:
            return Path(self._home) if self._home else None
        target = targets[0]
        if target.expands or target.text == "-":
            return None
        path = target.text
        if path == "~" or path.startswith("~/"):
            if not self._home:
                return None
            path = self._home + path[1:]
        elif path.startswith("~"):
            return None
        if os.path.isabs(path):
            return Path(os.path.normpath(path))
        return None if cwd is None else Path(os.path.normpath(cwd / path))
