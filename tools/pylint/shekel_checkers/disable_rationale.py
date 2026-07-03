"""Disable-rationale checker: ``shekel-disable-rationale`` (W9903).

Flags any ``# pylint: disable=`` that lacks a standard ``Pylint:`` why-comment
in the mandated location (the docstring for a def/class-scoped directive, a
comment immediately above otherwise). Keeps every suppression auditable with one
grep.
"""

import io
import re
import tokenize

from astroid import nodes

from pylint.checkers import BaseRawFileChecker

# Marker that prefixes every disable rationale.  Greppable via
# ``grep -rn "Pylint:" app/``; capitalized so it can never collide with the
# lowercase pragma prefix pylint parses -- that prefix is matched
# case-sensitively, so the capitalized marker is invisible to pylint's own
# inline-option parser.
_RATIONALE_MARKER = "Pylint:"
# Captures the comma-separated rule list from an inline disable directive -- the
# lowercase "disable" pragma pylint honors anywhere inside a comment token.  The
# "enable" and "disable-next" pragmas are intentionally not matched; the codebase
# uses the plain "disable" form only.  The leading ``#.*?`` (rather than
# ``#\s*``) means a directive behind prefix text in the same comment -- the
# historical "noqa code, then a disable pragma" combined form -- cannot evade the
# rationale gate, because pylint honors the pragma anywhere in the comment and
# the checker must see everything pylint sees.
_DISABLE_RE = re.compile(r"#.*?pylint:\s*disable=([\w,\-]+)")


class ShekelDisableRationaleChecker(BaseRawFileChecker):
    """Require every ``# pylint: disable=`` to carry a standard why-comment.

    ``docs/coding-standards.md`` mandates a rationale in a fixed location and
    format so every suppression is auditable with one grep (``Pylint:``):

    * Definition-scoped (the directive sits on a ``def``/``class`` line -- the
      ``too-many-*`` and ``too-many-instance-attributes`` smells): the rationale
      is a ``Pylint:`` note in that symbol's docstring.
    * Statement-scoped (any other line -- ``broad-except``, ``protected-access``,
      ``import-outside-toplevel``): the rationale is a ``# Pylint:`` comment
      immediately above the disabled line.

    Either way the rationale must name every rule the directive disables.  This
    checker enforces marker presence, location, and rule-naming; the
    ``(<count>/<limit>)`` shape is a documented convention, not machine-checked.
    """

    name = "shekel-disable-rationale"
    msgs = {
        "W9903": (
            "pylint disable has no standard rationale: add a ``Pylint:`` note "
            "naming %s %s",
            "shekel-disable-rationale",
            "Every ``# pylint: disable=`` must carry a why-comment in the "
            "standard location and format (docs/coding-standards.md): a "
            "``Pylint:`` note in the docstring when the directive is on a "
            "def/class line, or a ``# Pylint:`` comment immediately above the "
            "line otherwise, naming each disabled rule. One grep for ``Pylint:`` "
            "must then surface a justification for every suppression.",
        ),
    }

    def process_module(self, node: nodes.Module) -> None:
        """Flag every disable directive in the module lacking a standard rationale.

        ``node`` is the astroid module.  :meth:`_scan_comments` tokenizes the
        source to locate comment tokens -- so a directive written inside a string
        literal is never matched -- and :meth:`_definition_docstrings` maps each
        def/class line to its docstring, so a definition-scoped directive is
        checked against the right symbol's docstring.
        """
        content = self._read_source(node)
        if content is None:
            return
        scanned = self._scan_comments(content)
        if scanned is None:
            return
        comment_only, disables = scanned
        if not disables:
            return

        def_lines = self._definition_docstrings(node)
        for lineno, rules in disables:
            if lineno in def_lines:
                text = def_lines[lineno] or ""
                hint = "in the docstring"
            else:
                text = self._comment_block_above(comment_only, lineno)
                hint = "in a comment immediately above"
            if self._rationale_ok(text, rules):
                continue
            self.add_message(
                "shekel-disable-rationale",
                line=lineno,
                args=(", ".join(rules), hint),
            )

    @staticmethod
    def _read_source(node: nodes.Module) -> str | None:
        """Return the module's UTF-8 source text, or ``None`` if unreadable.

        Reads through the astroid module's own byte stream so the checker sees
        exactly what pylint parsed; an OS error or a non-UTF-8 decode yields
        ``None`` and the caller skips the module rather than raising.
        """
        try:
            with node.stream() as stream:
                return stream.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _scan_comments(
        content: str,
    ) -> tuple[dict[int, str], list[tuple[int, list[str]]]] | None:
        """Tokenize ``content`` into comment-only lines and disable directives.

        Returns a ``(comment_only, disables)`` pair: ``comment_only`` maps each
        line whose only content is a comment to that comment's text (the lookup
        the statement-scoped rationale search walks upward through), and
        ``disables`` lists every ``(lineno, [rules])`` a disable directive names.
        Tokenizing -- rather than scanning raw text -- means a directive written
        inside a string literal is never a COMMENT token and so is never matched.
        Returns ``None`` on a tokenizer error (an unterminated construct), so the
        caller skips the module.
        """
        comment_only: dict[int, str] = {}
        disables: list[tuple[int, list[str]]] = []
        try:
            for tok_type, tok_str, start, _end, line in tokenize.generate_tokens(
                io.StringIO(content).readline,
            ):
                if tok_type != tokenize.COMMENT:
                    continue
                lineno, col = start
                if line[:col].strip() == "":
                    comment_only[lineno] = tok_str
                match = _DISABLE_RE.search(tok_str)
                if match:
                    disables.append(
                        (lineno, [r for r in match.group(1).split(",") if r]),
                    )
        except tokenize.TokenError:
            return None
        return comment_only, disables

    @staticmethod
    def _definition_docstrings(node: nodes.Module) -> dict[int, str | None]:
        """Map each def/class signature line to its docstring (or ``None``).

        Keyed by ``fromlineno`` -- the ``def``/``class`` line (decorators
        excluded), which is where a definition-scoped disable directive sits.
        """
        defs = node.nodes_of_class(
            (nodes.FunctionDef, nodes.AsyncFunctionDef, nodes.ClassDef),
        )
        return {
            definition.fromlineno: (
                definition.doc_node.value if definition.doc_node is not None else None
            )
            for definition in defs
        }

    @staticmethod
    def _comment_block_above(comment_only: dict[int, str], lineno: int) -> str:
        """Join the contiguous comment-only lines immediately above ``lineno``.

        Stops at the first non-comment or blank line, so only an adjacent
        rationale block counts -- a comment separated from the directive by code
        or a blank line does not.
        """
        collected: list[str] = []
        cursor = lineno - 1
        while cursor in comment_only:
            collected.append(comment_only[cursor])
            cursor -= 1
        return "\n".join(collected)

    @staticmethod
    def _rationale_ok(text: str, rules: list[str]) -> bool:
        """Return True if ``text`` carries the marker and names every rule."""
        if _RATIONALE_MARKER not in text:
            return False
        return all(rule in text for rule in rules)
