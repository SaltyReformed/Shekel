"""Recorded GitHub exchanges: kept by a session that calls GitHub once, replayed by the tests.

The plan tool's tests never call GitHub (the build plan, L2), yet a test fed a
response its author imagined grades the author's imagination.  So the
exchanges the tests replay were RECORDED: :class:`Recorder` wraps the session
a real :class:`_github.GitHub` sends through and keeps every request and
GitHub's answer, and :class:`Replay` answers the same requests with the same
answers, in the order they were recorded, refusing any request it never saw.

A recording is JSON: ``{"scratch": {...}, "exchanges": [{"method", "url",
"body", "status", "answer"}, ...]}``.  Nothing secret is in one: the token
rides in a header, and headers are not kept.

**A recording session writes to scratch cards only, and keeps the text of
scratch cards only.**  A recording lives in the PUBLIC code repository, and the
tracker it reads is private: real production figures are allowed there
(``R-BAL172``).  So:

- **The scratch cards are fixed before anything is sent** (:class:`Scratch`):
  the cards the recording session has checked carry a scratch title
  (:data:`SCRATCH`), plus each card it files with one.  Nothing an answer says
  changes the set.
- **Only a known write to a scratch card is sent** (:func:`refusal`, asked of
  every request before it goes out): a REST write takes one of the routes in
  :data:`_ROUTES` on the tracker, with every card it names -- in its path, by
  its REST id, or as a claim -- a scratch card; a GraphQL write is one of the
  board's four, word for word, moving a scratch card's board item.  Anything
  else is refused, unsent.
- **Every string an answer holds is redacted** (:data:`REDACTED`) unless its
  key is one of :data:`_KEPT` -- ids, states, names of labels, types and
  repositories, dates, error messages -- or it belongs to a scratch card, or it
  is the plan board's own title (:data:`setup_tracker.PROJECT_TITLE`, already
  in this repository).  An object carrying a ``number`` is that card of the
  tracker, unless it names another repository (then it is no card); an object
  with no number that carries a string of its own belongs to NO card -- except
  the answer itself, which is the card the request's URL names, and a card's
  own edit history; any other object shares its enclosing object's card.  An
  answer to a request about another repository holds no card's text.

What a recording can still hold: a string under a :data:`_KEPT` key, of any
card (so a key enters that set only when no card's text can be stored under
it); everything on the REQUEST side, kept as sent because the replay matches it
-- read URLs, search terms, GraphQL variables, a claim commit's message -- so a
recording session puts no private text in a request; and a scratch card's own
text, including any edit history from before it carried a scratch title (the
recording sessions file their scratch cards as scratch).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

from _github import API
from _tracker import BOARD_ADD, BOARD_AFTER, BOARD_REMOVE, BOARD_TOP, TRACKER
from setup_tracker import ORG, PROJECT_TITLE, REPO

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
    "fullDatabaseId", "full_name", "id", "login", "merged_at", "message", "name",
    "nameWithOwner", "node_id", "path", "ref", "repository_url", "sha", "state",
    "stateReason", "state_reason", "type",
})
#: The keys under which an object with no number is its enclosing card's own content.
_OWN_CONTENT = frozenset({"userContentEdits"})
#: GitHub's node-id prefix for a board (a ProjectV2).
_BOARD_NODE = "PVT_"
_REPO_URL = f"{API}/repos/{TRACKER}"
_GRAPHQL_URL = f"{API}/graphql"
#: Each GraphQL write a recording may send, and the variable naming what it moves: the
#: card's node id for an add, its board item for the rest.
_BOARD_WRITES = {BOARD_ADD: "c", BOARD_REMOVE: "i", BOARD_TOP: "i", BOARD_AFTER: "i"}
_MUTATION = re.compile(r"\bmutation\b")
_REPOSITORY_ARGUMENTS = re.compile(r"\brepository\s*\(([^)]*)\)")
_THE_TRACKER = re.compile(rf'\s*owner:\s*"{ORG}"\s*,\s*name:\s*"{REPO}"\s*')
_CLAIM = re.compile(r"refs/claims/(?:([0-9]+)|recording-[a-z0-9-]+)")


@dataclass
class Scratch:
    """The cards a recording may write to and keep the text of -- each by its number, its
    REST id and its node id -- and the board items added for them."""

    numbers: set[int] = field(default_factory=set)
    ids: set[int] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)
    items: set[str] = field(default_factory=set)

    def take(self, issue: dict) -> None:
        """Count a card a REST answer describes (``number``, ``id``, ``node_id``) as scratch."""
        self.numbers.add(issue["number"])
        self.ids.add(issue["id"])
        self.nodes.add(issue["node_id"])

    def as_json(self) -> dict:
        """The set as a recording keeps it."""
        return {"numbers": sorted(self.numbers), "ids": sorted(self.ids),
                "nodes": sorted(self.nodes), "items": sorted(self.items)}

    @classmethod
    def from_json(cls, kept: dict) -> Scratch:
        """The set a recording kept."""
        return cls(set(kept["numbers"]), set(kept["ids"]), set(kept["nodes"]), set(kept["items"]))


def _scratch_claim(ref: str, scratch: Scratch) -> bool:
    """Whether ``ref`` claims a scratch card, or is a recording's own stray claim ref."""
    found = _CLAIM.fullmatch(ref)
    return bool(found) and (found[1] is None or int(found[1]) in scratch.numbers)


_Allows = Callable[[re.Match, dict, Scratch], bool]
#: Every REST write a recording may send, under the tracker's repository: its method,
#: its path, and whether the cards it names are scratch cards.
_ROUTES: tuple[tuple[str, re.Pattern, _Allows], ...] = tuple(
    (method, re.compile(path), allows) for method, path, allows in (
        ("POST", r"/issues",
         lambda _m, body, _s: str(body.get("title", "")).startswith(SCRATCH)),
        ("PATCH", r"/issues/([0-9]+)",
         lambda m, body, s: int(m[1]) in s.numbers
         and str(body.get("title", SCRATCH)).startswith(SCRATCH)),
        ("POST", r"/issues/([0-9]+)/comments", lambda m, _body, s: int(m[1]) in s.numbers),
        ("POST", r"/issues/([0-9]+)/sub_issues",
         lambda m, body, s: int(m[1]) in s.numbers and body.get("sub_issue_id") in s.ids),
        ("POST", r"/issues/([0-9]+)/dependencies/blocked_by",
         lambda m, body, s: int(m[1]) in s.numbers and body.get("issue_id") in s.ids),
        ("DELETE", r"/issues/([0-9]+)/dependencies/blocked_by/([0-9]+)",
         lambda m, _body, s: int(m[1]) in s.numbers and int(m[2]) in s.ids),
        ("POST", r"/git/commits", lambda _m, _body, _s: True),
        ("POST", r"/git/refs", lambda _m, body, s: _scratch_claim(str(body.get("ref")), s)),
        ("DELETE", r"/git/refs/(claims/.+)",
         lambda m, _body, s: _scratch_claim(f"refs/{m[1]}", s)),
    )
)


def _rest_allows(method: str, url: str, body: dict, scratch: Scratch) -> bool:
    """Whether a REST write takes a route of :data:`_ROUTES` to scratch cards only.  A route
    is matched on the path under the tracker's URL, so a URL of any other repository, whose
    prefix stays on, matches none."""
    path = url.removeprefix(_REPO_URL)
    return any(method == route and (found := pattern.fullmatch(path)) is not None
               and allows(found, body, scratch) for route, pattern, allows in _ROUTES)


def _graphql_allows(body: dict, scratch: Scratch) -> bool:
    """Whether a GraphQL request is a read, or one of the board's writes moving a scratch
    card's item."""
    query = str(body.get("query", ""))
    if query in _BOARD_WRITES:
        moved = (body.get("variables") or {}).get(_BOARD_WRITES[query])
        return moved in (scratch.nodes if query == BOARD_ADD else scratch.items)
    return not _MUTATION.search(query)


def refusal(method: str, url: str, body, scratch: Scratch) -> str | None:
    """Why a recording may not send this request; None when it may.

    A recording writes to scratch cards only, so what it keeps of every write
    is a scratch card's: a request cannot be redacted, since the replay
    matches it as sent.
    """
    body = body or {}
    if url == _GRAPHQL_URL:
        allowed = _graphql_allows(body, scratch)
    else:
        allowed = method == "GET" or _rest_allows(method, url, body, scratch)
    return None if allowed else (f"a recording sends only reads and known writes to scratch "
                                 f"cards: {method} {url} {json.dumps(body)[:200]}")


def _about_the_tracker(url: str, body) -> bool:
    """Whether a request reads nothing but the tracker, so a number in its answer that names
    no repository names a card of the tracker."""
    if url == _GRAPHQL_URL:
        return all(_THE_TRACKER.fullmatch(arguments) for arguments in
                   _REPOSITORY_ARGUMENTS.findall(str((body or {}).get("query", ""))))
    return not url.startswith(f"{API}/repos/") or url.startswith(_REPO_URL + "/")


def _named_card(url: str) -> int | None:
    """The card a REST request about the tracker names in its URL."""
    found = re.match(rf"{re.escape(_REPO_URL)}/issues/([0-9]+)(?:/|$)", url)
    return int(found[1]) if found else None


def _card_of(value: dict) -> int | None:
    """The tracker card an object carrying a ``number`` is; None when it names another
    repository."""
    repository = value.get("repository")
    named = ((repository.get("nameWithOwner") or repository.get("full_name"))
             if isinstance(repository, dict) else None)
    url = value.get("repository_url")
    if named not in (None, TRACKER) or url not in (None, _REPO_URL):
        return None
    return value["number"]


@dataclass(frozen=True)
class _Owner:
    """Whose text a part of an answer holds: ``card``; the card the request names; whether
    the part is a card's own content (its edit history)."""

    card: int | None
    named: int | None
    own: bool


def _redact(value, key: str | None, owner: _Owner, scratch: set[int], top: bool = False):
    """``value``, held under ``key``, with every string redacted that is neither a scratch
    card's nor under a :data:`_KEPT` key."""
    if isinstance(value, list):
        return [_redact(item, key, owner, scratch) for item in value]
    if isinstance(value, str):
        return value if key in _KEPT or owner.card in scratch else REDACTED
    if not isinstance(value, dict):
        return value
    if "number" in value:
        owner = _Owner(_card_of(value), owner.named, False)
    elif any(isinstance(item, str) and name not in _KEPT for name, item in value.items()):
        card = owner.named if top else owner.card if owner.own else None
        owner = _Owner(card, owner.named, owner.own)
    board = (str(value.get("id", "")).startswith(_BOARD_NODE)
             and value.get("title") == PROJECT_TITLE)
    return {
        name: (item if board and name == "title"
               else _redact(item, name, _Owner(owner.card, owner.named,
                                               owner.own or name in _OWN_CONTENT), scratch))
        for name, item in value.items()
    }


def redacted(exchanges: list[dict], scratch: Scratch) -> list[dict]:
    """``exchanges`` with every string redacted but what a scratch card or a kept key holds.

    Raises:
        ValueError: when a request is one a recording may not send
            (:func:`refusal`).
    """
    kept = []
    for exchange in exchanges:
        url, body = exchange["url"], exchange["body"]
        why = refusal(exchange["method"], url, body, scratch)
        if why:
            raise ValueError(why)
        tracker = _about_the_tracker(url, body)
        named = _named_card(url) if tracker else None
        answer = _redact(exchange["answer"], None, _Owner(named, named, False),
                         scratch.numbers if tracker else set(), top=True)
        kept.append({**exchange, "answer": answer})
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

    def __init__(self, scratch: Scratch, session=None) -> None:
        """Send through ``session`` (a real ``requests`` session unless a test passes one);
        ``scratch``: the cards the caller has checked are scratch cards, the only ones it
        may write to."""
        self._session = session or requests.Session()
        self.scratch = scratch
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
        if response.status_code < 400:
            self._take(method, url, body, answer)
        return response

    def _take(self, method: str, url: str, body, answer) -> None:
        """Count what a write just made for a scratch card as scratch: a card filed with a
        scratch title, and a scratch card's board item."""
        if method == "POST" and url == f"{_REPO_URL}/issues":
            self.scratch.take(answer)
        elif url == _GRAPHQL_URL and (body or {}).get("query") == BOARD_ADD:
            self.scratch.items.add(answer["data"]["addProjectV2ItemById"]["item"]["id"])

    def save(self, name: str, directory: Path = RECORDINGS) -> Path:
        """Write the scratch cards and the exchanges to ``<directory>/<name>.json``
        (``recorded/`` unless a test names another), redacted (:func:`redacted`); refuse
        one holding a token (an installation token is minted through a client this
        never wraps)."""
        held = [e["url"] for e in self.exchanges if isinstance(e["answer"], dict)
                and "token" in e["answer"]]
        if held:
            raise ValueError(f"a recording would keep a token from {held}")
        kept = {"scratch": self.scratch.as_json(),
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
