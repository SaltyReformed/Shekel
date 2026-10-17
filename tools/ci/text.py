"""The translation of line endings and the splitter of text into lines that :mod:`tools.split`
reads by (its ``test_reader.py`` holds that no other module of that package reads text, or
splits it with :meth:`str.splitlines`, itself), and the ONE decoder of a process's bytes into
text for a message, by which :mod:`tools.split` and :mod:`tools.testrecord` both show a
refusal.

In the bottom layer, so the two packages reach one home: ``tools/split`` imports
``tools/testrecord``, never the reverse, and each imports this package (the coordinator's
ruling on the lane review's M-B, 2026-10-17: move the leaf).  Standard library only.

Every consumer imports from here.  The names' two old homes, :mod:`tools.split._source`
and :mod:`tools.testrecord.record`, bind this module rather than its names, so importing a
name from either is an ImportError; another module that imports a name here could still pass
it on, and only review holds that (the coordinator's ruling on the lane review's L-2,
2026-10-17).
"""
from __future__ import annotations

import io


def universal(text: str) -> io.StringIO:
    """``text`` behind :mod:`io`'s own universal newlines, the ONE translation of line endings
    the tool reads by: ``\\r\\n`` and a lone ``\\r`` read as ``\\n``, as Python's tokenizer
    reads them."""
    return io.StringIO(text, newline=None)


def lines_of(text: str, keepends: bool = False) -> list[str]:
    """``text``'s lines exactly as Python numbers them, so the line the syntax tree calls
    ``n`` is item ``n - 1``: line endings translated (:func:`universal`), then each line
    ended by ``\\n`` alone, the empty remainder after a final ``\\n`` no line; ``keepends``
    keeps each line's ``\\n``.

    The ONE splitter of text into lines (the coordinator's ruling on commit 3b-1's first
    report, 2026-10-15): :meth:`str.splitlines` also ends a line at ``\\x0b``, ``\\x0c``,
    ``\\x1c``-``\\x1e``, ``\\x85``, ``\\u2028`` and ``\\u2029``, where Python ends none, so a
    string literal holding one would shift every later line off its number."""
    kept = universal(text).readlines()
    return kept if keepends else [line.removesuffix("\n") for line in kept]


def shown(data: bytes) -> str:
    """Bytes a process wrote, made text for a MESSAGE alone (a byte that is not UTF-8 shown
    escaped), so a refusal's diagnostics never stop it naming why; never read as data."""
    return data.decode("utf-8", "backslashreplace")
