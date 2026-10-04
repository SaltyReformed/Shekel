"""Recorded GitHub exchanges: kept by a session that calls GitHub once, replayed by the tests.

The plan tool's tests never call GitHub (the build plan, L2), yet a test fed a
response its author imagined grades the author's imagination.  So the
exchanges the tests replay were RECORDED: :class:`Recorder` wraps the session
a real :class:`_github.GitHub` sends through and keeps every request and
GitHub's answer, and :class:`Replay` answers the same requests with the same
answers, in the order they were recorded, refusing any request it never saw.

A recording is JSON: a list of ``{"method", "url", "body", "status",
"answer"}``.  Nothing secret is in one: the token rides in a header, and
headers are not kept.

**Nothing private is in one either.**  A recording lives in the PUBLIC code
repository, and the tracker it reads is private: real production figures are
allowed there (``R-BAL172``).  So :meth:`Recorder.save` keeps the text -- every
title, body and saved edit -- of SCRATCH cards only, the cards a recording
session files to measure on (titles beginning :data:`SCRATCH`), and redacts the
text of every other card it read, whatever read it.  A text it cannot tie to a
card is redacted too.  And it refuses to save a recording that WROTE text to a
card that is not a scratch card, since a request is replayed as it was sent.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

import requests

#: Where the recordings the tests replay are kept.
RECORDINGS = Path(__file__).resolve().parent / "recorded"
#: Every card whose text a recording may keep has a title beginning with this.
SCRATCH = "[L2 measurement] scratch"
#: What stands in a recording for a card's text.
REDACTED = "[redacted: not a scratch card]"
#: The parts of an answer that hold a card's text.
_TEXT = ("title", "body", "diff")
#: GitHub's node-id prefix for a board (a ProjectV2).  A board's own title is not a
#: card's text but the tracker's configuration, already in this repository
#: (``setup_tracker.PROJECT_TITLE``), and the plan tool finds its board by it.
_BOARD_NODE = "PVT_"
_ISSUE_URL = re.compile(r"/issues/([0-9]+)(?:/|$)")


def _named_card(exchange: dict) -> int | None:
    """The card a request names: in its URL, or in its GraphQL variables."""
    found = _ISSUE_URL.search(exchange["url"])
    if found:
        return int(found.group(1))
    variables = (exchange["body"] or {}).get("variables") or {}
    return variables.get("number")


def _scratch_cards(exchanges: list[dict]) -> set[int]:
    """Every card any answer shows with a scratch card's title."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            title = value.get("title")
            if isinstance(title, str) and title.startswith(SCRATCH) and "number" in value:
                found.add(value["number"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for exchange in exchanges:
        walk(exchange["answer"])
    return found


def _redact(value, card: int | None, scratch: set[int]):
    """``value`` with the text of every card not in ``scratch`` redacted; ``card`` is the
    card the enclosing answer is about (None: not known, so its text is redacted)."""
    if isinstance(value, list):
        return [_redact(item, card, scratch) for item in value]
    if not isinstance(value, dict):
        return value
    own = value.get("number", card) if "title" in value else card
    board = str(value.get("id", "")).startswith(_BOARD_NODE)
    return {
        key: (REDACTED if key in _TEXT and isinstance(item, str) and own not in scratch
              and not (board and key == "title")
              else _redact(item, None if board else own, scratch))
        for key, item in value.items()
    }


def redacted(exchanges: list[dict]) -> list[dict]:
    """``exchanges`` with every card's text redacted but a scratch card's.

    Raises:
        ValueError: when a request wrote text to a card that is not a scratch
            card (a request cannot be redacted: the replay matches it as sent).
    """
    scratch = _scratch_cards(exchanges)
    kept = []
    for exchange in exchanges:
        card = _named_card(exchange)
        body = exchange["body"] or {}
        if any(key in body for key in _TEXT):
            target = card if card is not None else (exchange["answer"] or {}).get("number")
            if target not in scratch:
                raise ValueError(f"a recording would keep text written to card {target}: "
                                 f"{exchange['method']} {exchange['url']}")
        kept.append({**exchange, "answer": _redact(exchange["answer"], card, scratch)})
    return kept


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
    """A ``requests`` session that keeps every exchange it sends."""

    def __init__(self) -> None:
        """Send through a real session; keep each exchange."""
        self._session = requests.Session()
        self.exchanges: list[dict] = []

    def request(self, method, url, **kwargs):
        """Send the request to GitHub; keep it and the answer."""
        response = self._session.request(method, url, **kwargs)
        self.exchanges.append({
            "method": method, "url": url, "body": kwargs.get("json"),
            "status": response.status_code,
            "answer": response.json() if response.content else None,
        })
        return response

    def save(self, name: str, directory: Path = RECORDINGS) -> Path:
        """Write the exchanges to ``<directory>/<name>.json`` (``recorded/`` unless a
        test names another), every card's text redacted but a scratch card's
        (:func:`redacted`); refuse one holding a token (an installation token is
        minted through a client this never wraps)."""
        held = [e["url"] for e in self.exchanges if isinstance(e["answer"], dict)
                and "token" in e["answer"]]
        if held:
            raise ValueError(f"a recording would keep a token from {held}")
        kept = redacted(self.exchanges)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
        return path


class Replay:
    """A ``requests`` session answering only what a recording holds, in its order."""

    def __init__(self, name: str) -> None:
        """Load ``recorded/<name>.json``."""
        self._answers: dict[str, deque] = defaultdict(deque)
        for exchange in json.loads((RECORDINGS / f"{name}.json").read_text(encoding="utf-8")):
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
