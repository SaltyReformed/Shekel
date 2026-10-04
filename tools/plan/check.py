"""The one check a card passes before it is written, whoever writes it.

The plan tool runs it before it sends a card (``plan file``) and refuses to
send what fails.  The tracker's Action (step X-cx's leaf L6) runs the SAME
function on a card the developer files or edits by hand, and comments on what
fails without reverting it -- so the two hold a card to one set of rules.  They
can differ in one input only: :attr:`Draft.owner_live`, which the plan tool
reads from git and the cards (``_state.is_live``) and the Action from whatever
it reads; the rule applied to it is the same.

What it holds a card to (rulings ``balance:R-BAL136``, ``R-BAL138``,
``R-BAL177``, ``R-BAL178``, ``R-BAL179`` and ``R-BAL180``):

- always: its type is one of the four kinds, and it carries exactly one arc
  label;
- a title the change writes is a short name: present, and at most
  :data:`TITLE_CAP` characters;
- a parent the change sets is the owner its kind needs: a finding's is a LIVE
  step (a finding names a live owner), a ruling's is a step, and a step's, if
  it has one, is the LIVE step it splits (a step already done with has no work
  left to split);
- a body the change writes is capped: a finding's is one sentence of at most
  400 characters, and a ruling's is :func:`ruling_body`'s shape -- the
  developer's question, then his answer -- at most 2,000 characters in all.
  That they are HIS words, word for word, is the filer's duty; no check can see
  it.

**Only what a change writes is graded** (``R-BAL136``: "rows nobody edits keep
their text until they close").  A caller names the parts it writes in
``changed``; a new card writes all of them (:data:`NEW_CARD`).  So a migrated
ruling that names no owner, or a finding whose owner has since shipped, is
not refused when only its labels change.

Real production figures are ALLOWED in the private tracker (``R-BAL172``), so
nothing here looks for them.  Nothing here reads GitHub: a caller describes
the card as a :class:`Draft`.  Until step X-cx's cutover (L8) deletes the plan
gate's registry arms, ``tools/plan_gate/_rulings.RULINGS_ROW_CAP`` spells
R-BAL136's 2,000 again, for a ``rulings.md`` row.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from setup_tracker import ARCS, ISSUE_TYPES

#: A NEW finding's text: one sentence of at most this many characters (R-BAL136).
FINDING_CAP = 400
#: A NEW ruling's text: question and answer, at most this many characters
#: (R-BAL136, made a hard cap by R-BAL138).
RULING_CAP = 2000
#: A title a change writes: a short name (R-BAL178) of at most this many
#: characters (R-BAL180).  GitHub's own limit, 1,024 bytes
#: (measured 2026-10-04: 1,024 ASCII characters taken, 1,025 refused), is far
#: above it.
TITLE_CAP = 100

#: The parts of a card a change can write.
TITLE, BODY, OWNER = "title", "body", "owner"
#: A new card writes every part.
NEW_CARD = frozenset({TITLE, BODY, OWNER})

_QUESTION, _ANSWER = "Question: ", "\n\nAnswer: "
#: Where a sentence may end: terminal punctuation (not the last dot of an
#: ellipsis), then any closing quote, bracket or Markdown mark.  A cell cut
#: mid-word fails it; a cut that lands just after a full stop passes, which no
#: punctuation test can see.
_END = r"(?<!\.\.)[.!?][)\"'`*_\]]*"
_ENDS = re.compile(_END + r"\Z")
#: A second sentence INSIDE the text: an end, whitespace, then a capital or an
#: opening mark (backtick, bold or italic, a quote, a bracket or parenthesis).
#: Not a digit: ``P.L. 119`` is one sentence.  Not the dot closing an initialism
#: of two letters or more (``U.S.``, ``e.g.``): a letter, a dot and a letter
#: just before it.  A single letter's dot (``plan A. The ...``) still ends one.
_BREAK = re.compile(r"(?<![A-Za-z]\.[A-Za-z])" + _END + r"\s+[A-Z`*_\"'(\[]")


@dataclass(frozen=True)
class Draft:
    """A card as it would stand after the change.

    ``owner_kind`` is the issue type of the card's parent (None when it has
    none).  ``owner_live`` is the caller's answer to whether that parent is
    still work: the plan tool asks git and the card, the Action the card.
    """

    kind: str | None
    title: str
    body: str | None
    labels: tuple[str, ...]
    owner_kind: str | None = None
    owner_live: bool = False


def ruling_body(question: str, answer: str) -> str:
    """A ruling card's body: the developer's question, then his answer, each word for word."""
    return f"{_QUESTION}{question.strip()}{_ANSWER}{answer.strip()}"


def ruling_question(body: str | None) -> str | None:
    """The question in a body of :func:`ruling_body`'s shape; None for any other body.

    A question card a conversion already rewrote (R-BAL186: the same command
    finishes a half-done conversion) holds its question in this shape, so the
    retry reads it back instead of wrapping it a second time.
    """
    parts = _ruling_parts(normalized(body))
    return parts[0] if parts else None


def _ruling_parts(text: str) -> tuple[str, str] | None:
    """``(question, answer)`` of a text in :func:`ruling_body`'s shape, each stripped;
    None when either mark is missing."""
    question, found, answer = text.removeprefix(_QUESTION).partition(_ANSWER)
    if not text.startswith(_QUESTION) or not found:
        return None
    return question.strip(), answer.strip()


def normalized(body: str | None) -> str:
    """A body as the rules read it: CRLF as a web form may send it read as LF, the
    space around it dropped, and GitHub's null for an empty body read as empty."""
    return (body or "").replace("\r\n", "\n").strip()


def _sentence_problems(text: str) -> list[str]:
    """Why ``text`` is not one sentence of at most :data:`FINDING_CAP` characters."""
    problems = []
    if "\n" in text:
        problems.append("a finding's text is one sentence, on one line")
    if not _ENDS.search(text):
        problems.append(
            f"a finding's text is one complete sentence, ending in '.', '!' or '?'; "
            f"it ends {text[-40:]!r}"
        )
    elif found := _BREAK.search(text):
        problems.append(
            f"a finding's text is ONE sentence; a second begins at "
            f"{text[found.end() - 1:][:40]!r}"
        )
    if len(text) > FINDING_CAP:
        problems.append(
            f"a finding's text is at most {FINDING_CAP} characters (R-BAL136); it is "
            f"{len(text)}"
        )
    return problems


def _ruling_problems(text: str) -> list[str]:
    """Why ``text`` is not a question and answer within :data:`RULING_CAP` characters."""
    problems = []
    parts = _ruling_parts(text)
    if parts is None or not all(parts):
        problems.append(
            "a ruling's text is the developer's question and his answer, word for word: "
            f"{_QUESTION.strip()!r} then a blank line and {_ANSWER.strip()!r}"
        )
    if len(text) > RULING_CAP:
        problems.append(
            f"a ruling's text is at most {RULING_CAP} characters (R-BAL136, R-BAL138); "
            f"it is {len(text)}"
        )
    return problems


def _owner_problems(draft: Draft) -> list[str]:
    """Why a card's parent is not the owner its kind needs."""
    parent = draft.owner_kind or "none"
    if draft.kind == "finding" and (draft.owner_kind != "step" or not draft.owner_live):
        if draft.owner_kind == "step":
            parent = "a step that is done"
        return [f"a finding is a sub-issue of a LIVE step, its owner (R-BAL177); "
                f"its parent is {parent}"]
    if draft.kind == "ruling" and draft.owner_kind != "step":
        return [f"a ruling is a sub-issue of a step, its owner (R-BAL177); its parent is {parent}"]
    if draft.kind == "step" and draft.owner_kind not in (None, "step"):
        return [f"a step's parent is the step it splits, not a {draft.owner_kind}"]
    if draft.kind == "step" and draft.owner_kind == "step" and not draft.owner_live:
        return ["a step's parent is the LIVE step it splits; its parent is a step that is done"]
    return []


def violations(draft: Draft, changed: Collection[str] = NEW_CARD) -> list[str]:
    """Every way ``draft`` breaks the plan's rules; empty when it may be written.

    ``changed``: the parts the change writes (:data:`TITLE`, :data:`BODY`,
    :data:`OWNER`); only those are graded beyond the type and arc label.
    """
    problems = []
    if draft.kind not in ISSUE_TYPES:
        problems.append(f"its type is {draft.kind!r}, not one of {', '.join(ISSUE_TYPES)}")
    arcs = sorted(label for label in draft.labels if label in ARCS)
    if len(arcs) != 1:
        problems.append(f"it carries exactly one arc label of {', '.join(ARCS)}; it has {arcs}")
    title = draft.title.strip()
    if TITLE in changed and not title:
        problems.append("its title is a short name, and it has none")
    elif TITLE in changed and len(title) > TITLE_CAP:
        problems.append(f"its title is a short name of at most {TITLE_CAP} characters; "
                        f"it is {len(title)}")
    if OWNER in changed:
        problems += _owner_problems(draft)
    body = normalized(draft.body)
    if BODY in changed and draft.kind == "finding":
        problems += _sentence_problems(body)
    elif BODY in changed and draft.kind == "ruling":
        problems += _ruling_problems(body)
    return problems
