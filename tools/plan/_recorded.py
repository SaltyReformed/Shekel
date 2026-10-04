"""Recorded GitHub exchanges: kept by a session that calls GitHub once, replayed by the tests.

The plan tool's tests never call GitHub (the build plan, L2), yet a test fed a
response its author imagined grades the author's imagination.  So the
exchanges the tests replay were RECORDED: :class:`Recorder` wraps the session
a real :class:`_github.GitHub` sends through and keeps every request and
GitHub's answer, and :class:`Replay` answers the same requests with the same
answers, in the order they were recorded, refusing any request it never saw.

A recording is JSON: ``{"scratch": [card numbers], "exchanges": [{"method",
"url", "body", "status", "answer"}, ...]}``.  Nothing secret is in one: the
token rides in a header, and headers are not kept.

**Nothing private is in one either, by construction rather than by search.**
A recording lives in the PUBLIC code repository, and the tracker it reads is
private: real production figures are allowed there (``R-BAL172``).  So:

- **The scratch cards are fixed before anything is sent**: the cards the
  recording session has checked are scratch cards (titles beginning
  :data:`SCRATCH`), plus each card it files with such a title.  Nothing an
  answer says changes the set.
- **Only a scratch card is written to** (:func:`refusal`, asked of every
  request before it is sent): a REST write naming any other card, text written
  anywhere but to a scratch card, a retitle that would take a card out of the
  set, and every GraphQL mutation but the board's three are refused, unsent.
- **Every string an answer holds is redacted** (:data:`REDACTED`) unless its
  key is one of :data:`_KEPT` -- ids, states, names of labels, types and
  repositories, dates, error messages, which hold no card's text -- or it
  belongs to a scratch card, or it is a board's own title (the tracker's
  configuration, already in this repository as
  ``setup_tracker.PROJECT_TITLE``).  A string belongs to the card whose
  ``number`` its object carries; an object with no number that carries a
  string of its own belongs to the card the REQUEST names (its URL, or a
  ``number`` variable), never to the object around it; an object carrying
  neither shares its enclosing object's card.

What a recording can still hold: a string under a :data:`_KEPT` key, of any
card, so a key enters that set only when no card text can be stored under it;
and a scratch card's object nesting an object of ANOTHER card that carries
that card's number nowhere and only :data:`_KEPT` strings of its own (none of
the plan tool's queries reads one).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path

import requests

from setup_tracker import ORG, REPO

#: Where the recordings the tests replay are kept.
RECORDINGS = Path(__file__).resolve().parent / "recorded"
#: Every card a recording may write to, or keep text of, has a title beginning with this.
SCRATCH = "[L2 measurement] scratch"
#: What stands in a recording for a string it may not keep.
REDACTED = "[redacted: not a scratch card]"
#: The keys whose strings are kept whichever card they belong to: none holds a
#: card's text.
_KEPT = frozenset({
    "__typename", "authoredDate", "createdAt", "date", "editedAt", "endCursor",
    "fullDatabaseId", "id", "login", "merged_at", "message", "name", "nameWithOwner",
    "node_id", "path", "ref", "sha", "state", "stateReason", "state_reason", "type",
})
#: GitHub's node-id prefix for a board (a ProjectV2), whose own title is kept.
_BOARD_NODE = "PVT_"
#: The only GraphQL mutations a recording sends: the board's, which write no text.
_BOARD_MUTATIONS = frozenset({
    "addProjectV2ItemById", "deleteProjectV2Item", "updateProjectV2ItemPosition"})
#: The fields of a REST write that hold a card's text.
_TEXT_FIELDS = ("title", "body")
_ISSUE_URL = re.compile(r"/issues/([0-9]+)(?:/|$)")
_CREATE_URL = f"/repos/{ORG}/{REPO}/issues"
#: A GraphQL mutation's fields: every GitHub mutation takes one ``input``.
_MUTATION_FIELD = re.compile(r"(\w+)\s*\(\s*input\s*:")


def _named_card(url: str, body) -> int | None:
    """The card a request names: in its URL, or in its GraphQL variables."""
    found = _ISSUE_URL.search(url)
    if found:
        return int(found.group(1))
    return ((body or {}).get("variables") or {}).get("number")


def _is_create(method: str, url: str) -> bool:
    """Whether a request files a card in the tracker."""
    return method == "POST" and url.endswith(_CREATE_URL)


def _mutation_refusal(query: str) -> str | None:
    """Why a recording may not send this GraphQL request: a mutation but the board's."""
    fields = set(_MUTATION_FIELD.findall(query))
    if not query.lstrip().startswith("mutation") or (fields and fields <= _BOARD_MUTATIONS):
        return None
    return (f"a recording sends no GraphQL mutation but the board's "
            f"({', '.join(sorted(_BOARD_MUTATIONS))}): {query[:80]!r}")


def _rest_refusal(method: str, url: str, body: dict, scratch: set[int]) -> str | None:
    """Why a recording may not send this REST write: to a card not scratch, text anywhere
    else but a scratch card's filing, or a title that leaves the scratch cards."""
    card = _named_card(url, body)
    if card is None:
        texts = any(key in body for key in _TEXT_FIELDS)
        filed = _is_create(method, url) and str(body.get("title", "")).startswith(SCRATCH)
        return (None if not texts or filed
                else f"a recording writes text only to a scratch card: {method} {url}")
    if card not in scratch:
        return (f"a recording writes only to scratch cards, and plan#{card} is not one: "
                f"{method} {url}")
    if "title" in body and not str(body["title"]).startswith(SCRATCH):
        return f"retitling plan#{card} {body['title']!r} would take it out of the scratch cards"
    return None


def refusal(method: str, url: str, body, scratch: set[int]) -> str | None:
    """Why a recording may not send this request; None when it may.

    A recording writes to scratch cards only, so what it keeps of every write
    is a scratch card's: a request cannot be redacted, since the replay
    matches it as sent.
    """
    body = body or {}
    if isinstance(body.get("query"), str):
        return _mutation_refusal(body["query"])
    return None if method == "GET" else _rest_refusal(method, url, body, scratch)


def _redact(value, key: str | None, card: int | None, named: int | None, scratch: set[int]):
    """``value`` (held under ``key``, belonging to ``card``) with every string redacted that
    is neither a scratch card's nor under a :data:`_KEPT` key; ``named`` is the card the
    request names."""
    if isinstance(value, list):
        return [_redact(item, key, card, named, scratch) for item in value]
    if isinstance(value, str):
        return value if key in _KEPT or card in scratch else REDACTED
    if not isinstance(value, dict):
        return value
    if "number" in value:
        card = value["number"]
    elif any(isinstance(item, str) and name not in _KEPT for name, item in value.items()):
        card = named
    board = str(value.get("id", "")).startswith(_BOARD_NODE)
    return {
        name: (item if board and name == "title" and isinstance(item, str)
               else _redact(item, name, card, named, scratch))
        for name, item in value.items()
    }


def redacted(exchanges: list[dict], scratch: set[int]) -> list[dict]:
    """``exchanges`` with every string redacted but what a scratch card or a kept key holds.

    Raises:
        ValueError: when a request is one a recording may not send
            (:func:`refusal`).
    """
    kept = []
    for exchange in exchanges:
        why = refusal(exchange["method"], exchange["url"], exchange["body"], scratch)
        if why:
            raise ValueError(why)
        named = _named_card(exchange["url"], exchange["body"])
        kept.append({**exchange, "answer": _redact(exchange["answer"], None, named, named,
                                                   scratch)})
    return kept


def recording(name: str, directory: Path = RECORDINGS) -> dict:
    """The recording ``<directory>/<name>.json``: its scratch cards and its exchanges."""
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def _key(method: str, url: str, body) -> str:
    """One request, as the replay matches it: method, URL and body, keys sorted."""
    return json.dumps([method, url, body], sort_keys=True)


class _Answer:
    """The slice of ``requests.Response`` :class:`_github.GitHub` reads."""

    def __init__(self, status: int, answer) -> None:
        """Hold one recorded status and JSON answer (None: an empty body)."""
        self.status_code = status
        self.content = b"" if answer is None else json.dumps(answer).encode()
        self.text = self.content.decode()
        self._answer = answer

    def json(self):
        """The recorded answer."""
        return self._answer


class Recorder:
    """A ``requests`` session that keeps every exchange it sends, and sends only what a
    recording may (:func:`refusal`)."""

    def __init__(self, scratch: Iterable[int], session=None) -> None:
        """Send through ``session`` (a real ``requests`` session unless a test passes one);
        ``scratch``: the cards the caller has checked are scratch cards, the only ones it
        may write to."""
        self._session = session or requests.Session()
        self.scratch = set(scratch)
        self.exchanges: list[dict] = []

    def request(self, method, url, **kwargs):
        """Send the request to GitHub, unless it is refused; keep it and the answer.

        Raises:
            ValueError: for a request a recording may not send, before it is sent.
        """
        body = kwargs.get("json")
        why = refusal(method, url, body, self.scratch)
        if why:
            raise ValueError(f"not sent: {why}")
        response = self._session.request(method, url, **kwargs)
        answer = response.json() if response.content else None
        self.exchanges.append({"method": method, "url": url, "body": body,
                               "status": response.status_code, "answer": answer})
        if _is_create(method, url) and response.status_code < 400:
            self.scratch.add(answer["number"])
        return response

    def save(self, name: str, directory: Path = RECORDINGS) -> Path:
        """Write the scratch cards and the exchanges to ``<directory>/<name>.json``
        (``recorded/`` unless a test names another), redacted (:func:`redacted`); refuse
        one holding a token (an installation token is minted through a client this
        never wraps)."""
        held = [e["url"] for e in self.exchanges if isinstance(e["answer"], dict)
                and "token" in e["answer"]]
        if held:
            raise ValueError(f"a recording would keep a token from {held}")
        kept = {"scratch": sorted(self.scratch),
                "exchanges": redacted(self.exchanges, self.scratch)}
        path = directory / f"{name}.json"
        path.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
        return path


class Replay:
    """A ``requests`` session answering only what a recording holds, in its order."""

    def __init__(self, name: str) -> None:
        """Load ``recorded/<name>.json``."""
        self._answers: dict[str, deque] = defaultdict(deque)
        for exchange in recording(name)["exchanges"]:
            key = _key(exchange["method"], exchange["url"], exchange["body"])
            self._answers[key].append((exchange["status"], exchange["answer"]))
        self.sent: list[tuple[str, str, object]] = []

    def request(self, method, url, **kwargs):
        """The next recorded answer to this exact request; an unrecorded one is an error."""
        body = kwargs.get("json")
        self.sent.append((method, url, body))
        queue = self._answers.get(_key(method, url, body))
        if not queue:
            raise AssertionError(f"no recorded answer left for {method} {url} {body}")
        return _Answer(*queue.popleft())

    def unused(self) -> int:
        """How many recorded answers no request has taken yet."""
        return sum(len(queue) for queue in self._answers.values())
