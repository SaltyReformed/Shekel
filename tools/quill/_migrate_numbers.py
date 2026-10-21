"""X-cx's migration, its NUMBERS: each bare ``#N`` a card's text holds, decided one by one
(ruling ``balance:R-BAL277``).

GitHub links every bare ``#N`` in a card's Markdown to the tracker's card N, and leaves a
"mentioned this" line on that card.  Some numbers the cards carry mean a tracker card
(R-BAL244's "card #8"), some an app pull request (N-87's "PR #47"), and most a rank, an
installment or an id (R-BAL133's "#149 of about 180").  The input file decides each one
(:attr:`_migrate_input.Decisions.numbers`), and :func:`link`, the ONE transform, writes
each decision into its card's text, which every phase then files and grades
(``migrate.prepare``: built, its census taken, then linked here):

- ``card``: left as written, a link to the tracker's card N;
- ``pr``: ``[#N](https://github.com/<organization>/Shekel/pull/N)``, a link to the app's
  pull request N (:data:`APP_PULLS`), reading ``#N``;
- ``plain``: ``#<!-- -->N``, which GitHub stores as sent and renders as ``#N``, linking
  nothing (the rehearsal's probe M6-3, 2026-10-12).

Every card reads word for word as its sources do.  A card's decisions are written for
its text as built (:func:`decided_from`, hashed), so a card whose text changed since has
them refused until they are decided and reviewed again, as a stale name is.

**Which numbers GitHub links** (measured 2026-10-21 through GitHub's Markdown API, rendering
in the plan tracker's context; the probe and its answers are in the X-cx L8 lane's
``plan-tracker-2026-10-21/`` records): ``#`` and digits of an issue that EXISTS (``#149``
links nothing while the tracker holds no card 149, so every probe used card 2), after
anything but a letter or a digit and before anything but a letter, a digit or ``_``, as
GitHub reads the text once Markdown has taken its marks, outside code -- after ``/``,
``#``, ``\\``, a quote, a bracket or a dash, inside emphasis (``_#2_``, whose ``_`` are
marks), ``#2.5`` and ``#02`` included; and where a character reference stands for the
``#`` or a digit (``&#35;2``, ``&#x23;2``, ``&num;2``, ``#&#50;``), which GitHub reads as
that character (``&NUM;2`` and ``&num2``, no references, are read as written).
:data:`CANDIDATE` takes every such ``#N`` in the source and more (``a_#2`` and ``#2_x``,
whose ``_`` may be a mark; a number in an indented code block or a link's text, which
GitHub does not link): a candidate needs a decision whether GitHub would link it or not,
and a marker or a link where GitHub would not link reads the same, so taking more costs a
decision and never a wrong link.

**What is refused, never rewritten** (each a refusal naming the card), being a place the
transform was not measured in, where a marker or a link could change what the card reads:

- a candidate in a TITLE, which is not Markdown and takes no marker: the name is reworded;
- a ``#`` or a digit written as a character reference outside code, which can link with
  no ``#N`` in the source for :data:`CANDIDATE` to find;
- a candidate after a character outside :data:`MEASURED_BEFORE` (a URL's ``/`` among
  them), or on a line indented four spaces or more, which Markdown may read as code,
  where a marker or a link prints as written;
- a ``pr`` decision inside an open ``[`` on its line, where its link would nest inside a
  link's text.

**What it does not grade**: whether a decision is RIGHT (``card #149`` linking whatever card
ends up numbered 149, say); the input file's reviewer grades that, as it grades a name.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.ci.arc_steps import fenced_lines
from tools.quill._migrate_bodies import Built
from tools.quill._migrate_input import TREATMENTS, Numbered
from tools.quill._migrate_source import source_hash
from tools.quill.setup_tracker import ORG

#: The treatments, by the names the input file writes them in (one home: its
#: :data:`_migrate_input.TREATMENTS`, which this unpacking holds to exactly three).
CARD, PR, PLAIN = TREATMENTS
#: A ``#N`` GitHub may link (the module docstring): its number is group 1.  An ``_`` beside
#: it is no letter here, as Markdown may take it as a mark.
CANDIDATE = re.compile(r"(?<![A-Za-z0-9])#(\d+)(?![A-Za-z0-9])")
#: A character reference, decimal, hex or named, as CommonMark bounds it (seven decimal
#: digits, six hex), which GitHub reads as the character it stands for
#: (:func:`html.unescape`'s; a name it does not know is read as written, and holds no ``#``).
_REFERENCE = re.compile(r"&(?:#\d{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]*);")
#: An inline code span: a run of backticks, then text, then a run of as many.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")
#: What may precede a candidate the transform rewrites: each rendered by GitHub with each
#: treatment, reading as written (2026-10-21); "" is a line's start.
MEASURED_BEFORE = frozenset({"", " ", "(", "[", "'", '"', "-", "*"})
#: Indentation at which Markdown may read a line as code (an indented code block).
_CODE_INDENT = 4
#: Where a ``pr`` number links: the app's pull requests, in the organization it moved into
#: at L5 (PR #583), by its own name, so no link leans on GitHub's redirect from the old one.
APP_PULLS = f"https://github.com/{ORG}/Shekel/pull/"
#: What each treatment writes in place of ``#N``, given N's digits.
_WRITES = {
    CARD: lambda digits: f"#{digits}",
    PR: lambda digits: f"[#{digits}]({APP_PULLS}{int(digits)})",
    PLAIN: lambda digits: f"#<!-- -->{digits}",
}
#: The parts of a card's text a candidate may sit in and be rewritten.
_PARTS = ("body", "comment")
#: The count a report gives the candidates of a card refused, decided or not.
UNDECIDED = "undecided"


@dataclass(frozen=True)
class Number:
    """One candidate in a card's text: the part it sits in, its line's index there, its
    span on that line, its text (``#47``) and the character before it ("" at a line's
    start)."""

    part: str
    line: int
    start: int
    stop: int
    text: str
    before: str


@dataclass(frozen=True)
class Linked:
    """Every card's text with its numbers written as decided (a refused card's as built),
    how many candidates each treatment took (:data:`UNDECIDED` for a refused card's), and
    what is refused."""

    built: Mapping[str, Built]
    counts: Counter
    refusals: tuple[str, ...]


def _lines(text: str) -> list[str]:
    """``text``'s lines, split at ``\\n`` alone, so joining them with it gives ``text`` back
    whatever else it holds (``str.splitlines`` splits at more)."""
    return text.split("\n")


def _blanked(found: re.Match) -> str:
    """What ``found`` is read as where it is no candidate's: spaces, as many."""
    return " " * len(found.group(0))


def _readable(line: str) -> str:
    """``line`` with each code span blanked, the same length: what GitHub reads for links
    on it, at the same positions."""
    return _CODE_SPAN.sub(_blanked, line)


def _unfenced(text: str) -> list[tuple[int, str]]:
    """Each line of ``text`` outside every fenced block, with its index."""
    lines = _lines(text)
    return [(index, line) for index, (line, fenced) in enumerate(zip(lines, fenced_lines(lines)))
            if not fenced]


def _numeric(found: re.Match) -> bool:
    """Whether the character reference ``found`` reads as ``#`` or a digit."""
    read = html.unescape(found.group(0))
    return read == "#" or read.isdigit()


def _references(line: str) -> list[str]:
    """Each character reference on ``line`` outside code that reads as ``#`` or a digit."""
    return [found.group(0) for found in _REFERENCE.finditer(_readable(line)) if _numeric(found)]


def numbers(built: Built) -> list[Number]:
    """Every candidate in ``built``'s body and comment, in reading order: what its decisions
    are listed in.  A character reference is read as the character it stands for, never
    as its own digits (``&#65;`` is ``A``; one reading as ``#`` or a digit is refused
    whole, :func:`_problems`)."""
    found = []
    for part in _PARTS:
        for index, line in _unfenced(getattr(built, part) or ""):
            readable = _REFERENCE.sub(_blanked, _readable(line))
            found += [Number(part, index, match.start(), match.end(), match.group(0),
                             line[match.start() - 1] if match.start() else "")
                      for match in CANDIDATE.finditer(readable)]
    return found


def decided_from(built: Built) -> str:
    """The hash a card's decisions record (:func:`_migrate_source.source_hash`): of its body's
    and its comment's lines outside fenced blocks, where its candidates sit.  Its As filed
    block, fenced, is left out, so a cell or a commit it quotes moves no decision."""
    return source_hash("\n".join(line for part in _PARTS
                                 for _, line in _unfenced(getattr(built, part) or "")))


def link(built: Mapping[str, Built], decided: Mapping[str, Numbered]) -> Linked:
    """Every card's text with its candidates written as ``decided`` (the module docstring),
    and what is refused: a refused card is left as built, its candidates counted
    :data:`UNDECIDED`.

    Args:
        built: Each card's text as :func:`_migrate_bodies.build` makes it, by key.
        decided: Each card's decisions as the input file states them well
            (:func:`_migrate_input.grade`), by key.

    Returns:
        The :class:`Linked`.
    """
    linked, counts, refusals = {}, Counter(), []
    for key, card in built.items():
        found = numbers(card)
        problems = _problems(card, found, decided.get(key))
        refusals += [f"numbers: {key}: {problem}" for problem in problems]
        if problems:
            linked[key] = card
            counts[UNDECIDED] += len(found)
            continue
        if not found:
            linked[key] = card
            continue
        treatments = [treatment for _, treatment in decided[key].each]
        linked[key] = _written(card, found, treatments)
        counts.update(treatments)
    return Linked(linked, counts, tuple(refusals))


def _problems(card: Built, found: Sequence[Number], decision: Numbered | None) -> list[str]:
    """Why ``card``'s candidates ``found`` cannot be written as ``decision`` (None: the input
    file decides none of them): its title's candidates and its character references first,
    then each candidate's context, then the decision against the text."""
    problems = [f"its title holds {match.group(0)}, which no marker can stop in a title: "
                "reword its name" for match in CANDIDATE.finditer(card.title)]
    problems += [f"its text writes {html.unescape(reference)!r} as the character reference "
                 f"{reference!r}, which GitHub reads as that character, so it can link where "
                 "no #N stands: write the character itself in its source"
                 for part in _PARTS for _, line in _unfenced(getattr(card, part) or "")
                 for reference in _references(line)]
    problems += [problem for number in found for problem in _context(card, number)]
    if decision is None:
        return problems + ([
            f"its card holds {len(found)} bare #N, none decided: "
            + "; ".join(f"{number.text} at {_shown(card, number)!r}" for number in found)
            + f"; its text's hash is {decided_from(card)}"] if found else [])
    return problems + _against(card, found, decision)


def _context(card: Built, number: Number) -> list[str]:
    """Why ``number``'s place on ``card`` is one the transform was not measured in."""
    line = _lines(getattr(card, number.part))[number.line]
    problems = []
    if number.before not in MEASURED_BEFORE:
        problems.append(f"{number.text} follows {number.before!r}, after which no rewrite of it "
                        f"was measured (_migrate_numbers.MEASURED_BEFORE): reword its source")
    indent = len(line) - len(line.lstrip(" "))
    if indent >= _CODE_INDENT:
        problems.append(f"{number.text} sits on a line indented {indent} spaces, which Markdown "
                        "may read as code, where a marker or a link prints as written: reword "
                        "or re-indent its source")
    return problems


def _against(card: Built, found: Sequence[Number], decision: Numbered) -> list[str]:
    """Why ``decision`` does not decide ``card``'s candidates ``found``: its hash is not the
    card's text's, or it decides other numbers, or a ``pr`` would land in a link's text."""
    now = decided_from(card)
    if not found:
        return [f"decides {len(decision.each)} number(s), and its card holds no bare #N: "
                "drop its entry"]
    if decision.source != now:
        return [f"its text changed since its numbers were decided (hash {decision.source}, "
                f"now {now}): decide and review them again"]
    given = [text for text, _ in decision.each]
    if given != [number.text for number in found]:
        return [f"decides {', '.join(given)}, and its card holds "
                f"{', '.join(number.text for number in found)}, in that order"]
    return [f"{number.text} at {_shown(card, number)!r} is decided pr inside an open '[', "
            "where its link would nest in a link's text: decide it otherwise or reword its "
            "source" for number, (_, treatment) in zip(found, decision.each)
            if treatment == PR and _bracketed(card, number)]


def _bracketed(card: Built, number: Number) -> bool:
    """Whether ``number`` sits inside a ``[`` its line opens before it and has not closed."""
    head = _readable(_lines(getattr(card, number.part))[number.line])[:number.start]
    return head.count("[") > head.count("]")


def _shown(card: Built, number: Number) -> str:
    """``number`` with the text around it on its line, as a refusal quotes it."""
    line = _lines(getattr(card, number.part))[number.line]
    return line[max(0, number.start - 40):number.stop + 20]


def _written(card: Built, found: Sequence[Number], treatments: Sequence[str]) -> Built:
    """``card`` with each candidate ``found`` written as its treatment, part by part and
    line by line, each line's last first so the spans before it hold."""
    texts = {part: _lines(getattr(card, part) or "") for part in _PARTS}
    for number, treatment in sorted(zip(found, treatments), key=lambda pair: (
            pair[0].part, pair[0].line, -pair[0].start)):
        lines = texts[number.part]
        line = lines[number.line]
        lines[number.line] = (line[:number.start] + _WRITES[treatment](number.text[1:])
                              + line[number.stop:])
    return Built(card.title, "\n".join(texts["body"]),
                 None if card.comment is None else "\n".join(texts["comment"]))
