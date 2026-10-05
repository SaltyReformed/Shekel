"""The tracker's cards, board order and claims, read and written as the plan tool's App.

Every card write goes through the plan tool's GitHub App (ruling
``balance:R-BAL170``), a separate identity from the developer's login, so a
reader can tell the tool's write from his own edit and never act on his.  This
module is the only place the plan tool speaks to the tracker; it decides
nothing about the plan (``_state`` does), it only reads and writes.

**What each read can see, measured on the tracker 2026-10-04:**

- A claim is the git reference ``refs/claims/<card>``.  REST
  ``git/matching-refs/claims/`` lists claims; GraphQL ``refs()`` lists NONE of
  them, but GraphQL reads a claim's commit by its id.  So two calls read every
  claim, whatever their number.
- A claim's commit is one the App makes with no parent and the default
  branch's tree, whose message names the branch that will ship the card; its
  author date is when the card was claimed.  GitHub refuses to create a
  reference that exists (422 "Reference already exists"), so exactly one
  session wins a claim -- the listing is display, the refusal is the lock.
- A card's REST id (what the sub-issue and blocked-by calls take) is its
  GraphQL ``fullDatabaseId``.
- The board's order is ``items(orderBy: {field: POSITION})``, and it LAGS a
  write (L1 measured 7 of 9 just-added cards listed, all 9 about 20 seconds
  later), so a write that places a card reads the board back until it shows
  the card where it was put (:data:`BOARD_WAIT_SECONDS`).
- GitHub silently drops an issue's ``type`` when the writer lacks push access,
  so every write that sets one reads it back.

**A link to an issue outside the tracker is carried, never followed**
(ruling ``balance:R-BAL188``).  GitHub lets a parent, sub-issue or blocked-by
link cross repositories in one organization (Shekel itself joins it at X-cx's
L5); read by number alone, such a link would name the tracker's card of that
number instead.  So every link and every board item is asked which repository
it names (:func:`_is_tracker_issue`, the one test), and a card keeps its
outside links apart (:attr:`Card.outside`) for the plan to report.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import quote

from _github import (
    GitHub,
    GitHubError,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
)
from setup_tracker import FILING, ORG, REPO, find_board

_BASE = f"/repos/{ORG}/{REPO}"
#: The tracker, as GitHub names a repository in a link (``nameWithOwner``).
TRACKER = f"{ORG}/{REPO}"
CLAIM_PREFIX = "refs/claims/"
#: How long a write waits for the board to show what it placed: three times
#: the ~20 seconds L1 measured, so a slow read is waited out and a stuck one
#: is reported rather than waited on forever.
BOARD_WAIT_SECONDS = 60
_POLL_SECONDS = 5
#: A connection GitHub caps per card (a parent holds at most 100 sub-issues).
_PAGE = 100

_CARD_FIELDS = """
  id number fullDatabaseId title state stateReason
  issueType { name }
  labels(first: 100) { totalCount nodes { name } }
  parent { number repository { nameWithOwner } }
  subIssues(first: 100) { totalCount nodes {
    number state issueType { name } repository { nameWithOwner } } }
  blockedBy(first: 100) { totalCount nodes { number repository { nameWithOwner } } }
  projectItems(first: 100) { totalCount nodes { id project { id } } }
  timelineItems(itemTypes: [CLOSED_EVENT, REOPENED_EVENT], last: 1) { nodes {
    __typename
    ... on ClosedEvent { actor { login } }
    ... on ReopenedEvent { actor { login } } } }
"""

_OPEN_CARDS = (
    """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
    % (ORG, REPO, _CARD_FIELDS)
)

#: Every card carrying the :data:`setup_tracker.FILING` mark, open or closed: a
#: ruling's filing closes it before its last write removes the mark.
_MARKED_CARDS = (
    """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, labels: ["%s"], states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
    % (ORG, REPO, FILING, _CARD_FIELDS)
)

_BOARD = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { id content { ... on Issue { number repository { nameWithOwner } } } } } } } }"""

_EDITS = (
    """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number body createdAt author { login }
    userContentEdits(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id editedAt editor { login } diff } } } } }"""
    % (ORG, REPO)
)


#: The board's writes, the only GraphQL mutations the plan tool sends (a recording
#: sends no other: ``_recorded.refusal``).  ``$p`` is the board, ``$c`` a card's node
#: id, ``$i`` a board item, ``$a`` the item it goes after.
BOARD_ADD = ("mutation($p: ID!, $c: ID!) { addProjectV2ItemById(input: {projectId: $p, "
             "contentId: $c}) { item { id } } }")
BOARD_REMOVE = ("mutation($p: ID!, $i: ID!) { deleteProjectV2Item(input: {projectId: $p, "
                "itemId: $i}) { deletedItemId } }")
BOARD_TOP = ("mutation($p: ID!, $i: ID!) { updateProjectV2ItemPosition(input: "
             "{projectId: $p, itemId: $i}) { clientMutationId } }")
BOARD_AFTER = ("mutation($p: ID!, $i: ID!, $a: ID!) { updateProjectV2ItemPosition(input: "
               "{projectId: $p, itemId: $i, afterId: $a}) { clientMutationId } }")


class TrackerError(RuntimeError):
    """The tracker refused a write or answered something the plan tool cannot read."""


class ClaimTaken(TrackerError):
    """Another session already holds the claim (GitHub refused to create its reference)."""


@dataclass(frozen=True)
class Child:
    """A sub-issue as its parent lists it."""

    number: int
    kind: str | None
    is_open: bool


@dataclass(frozen=True)
class OutsideLink:
    """A card's link to an issue outside the tracker: ``what`` the link is (``parent``,
    ``sub-issue`` or ``blocker``) and ``issue``, ``owner/name#number``."""

    what: str
    issue: str


@dataclass(frozen=True)
class Card:  # pylint: disable=too-many-instance-attributes
    """One card, as the plan reads it.

    Pylint: too-many-instance-attributes (15/7) -- **fifteen because a card
    states fifteen facts the plan reads**, each its own GitHub field, and no
    subset travels apart from the others.

    ``parent``, ``children`` and ``blocked_by`` name cards of the tracker only;
    ``outside`` holds every link to an issue anywhere else (R-BAL188).
    ``closed_by_tool``: the card is closed, and the last time anyone closed or
    reopened it, it was the plan tool -- so its state is the tool's display of
    git, not a person's decision.  ``touched_by_hand``: the last close or reopen
    was a person's.
    """

    number: int
    id: int
    node_id: str
    title: str
    kind: str | None
    labels: tuple[str, ...]
    is_open: bool
    state_reason: str | None
    parent: int | None
    children: tuple[Child, ...]
    blocked_by: tuple[int, ...]
    board_item: str | None
    closed_by_tool: bool
    touched_by_hand: bool
    outside: tuple[OutsideLink, ...]

    @property
    def leaves(self) -> tuple[int, ...]:
        """The steps this card splits into, the one spelling (R-BAL177: the findings and
        rulings it owns decide nothing)."""
        return tuple(child.number for child in self.children if child.kind == "step")

    @property
    def is_container(self) -> bool:
        """A step split into steps (a card that is not a step is no container, whatever
        hangs under it)."""
        return self.kind == "step" and bool(self.leaves)


@dataclass(frozen=True)
class Claim:
    """Who is building a card: the branch that will ship it, and since when."""

    card: int
    branch: str | None
    made: str
    sha: str


@dataclass(frozen=True)
class Edit:
    """One saved version of a card's body, in full."""

    edit_id: str | None
    edited_at: str
    editor: str | None
    body: str


def _counted(connection: dict, what: str, number: int) -> list:
    """A connection's nodes, refusing one GitHub truncated (a silent partial read)."""
    if connection["totalCount"] > len(connection["nodes"]):
        raise TrackerError(
            f"plan#{number} has {connection['totalCount']} {what}; the read holds "
            f"{len(connection['nodes'])}"
        )
    return connection["nodes"]


def _is_tracker_issue(node: dict) -> bool:
    """Whether a link or a board item's content names an issue of the tracker: the one
    test, for a parent, a sub-issue, a blocker and the board alike."""
    return (node.get("repository") or {}).get("nameWithOwner") == TRACKER


def _outside(node: dict, what: str) -> OutsideLink:
    """A link to an issue outside the tracker."""
    return OutsideLink(what, f"{node['repository']['nameWithOwner']}#{node['number']}")


def card_from(node: dict, board_id: str, app_login: str) -> Card:
    """A :class:`Card` from one GraphQL issue node (:data:`_CARD_FIELDS`)."""
    number = node["number"]
    items = [
        item["id"] for item in _counted(node["projectItems"], "board items", number)
        if item["project"]["id"] == board_id
    ]
    events = node["timelineItems"]["nodes"]
    last_actor = ((events[-1].get("actor") or {}).get("login")) if events else None
    is_open = node["state"] == "OPEN"
    links = [(node["parent"], "parent")] if node["parent"] else []
    links += [(child, "sub-issue") for child in _counted(node["subIssues"], "sub-issues", number)]
    links += [(blocker, "blocker")
              for blocker in _counted(node["blockedBy"], "blockers", number)]
    inside = [(link, what) for link, what in links if _is_tracker_issue(link)]
    return Card(
        number=number,
        id=int(node["fullDatabaseId"]),
        node_id=node["id"],
        title=node["title"],
        kind=(node["issueType"] or {}).get("name"),
        labels=tuple(label["name"] for label in _counted(node["labels"], "labels", number)),
        is_open=is_open,
        state_reason=node["stateReason"],
        parent=next((link["number"] for link, what in inside if what == "parent"), None),
        children=tuple(
            Child(link["number"], (link["issueType"] or {}).get("name"), link["state"] == "OPEN")
            for link, what in inside if what == "sub-issue"
        ),
        blocked_by=tuple(link["number"] for link, what in inside if what == "blocker"),
        board_item=items[0] if items else None,
        closed_by_tool=not is_open and last_actor == app_login,
        touched_by_hand=bool(events) and last_actor != app_login,
        outside=tuple(_outside(link, what) for link, what in links
                      if not _is_tracker_issue(link)),
    )


_CLAIM_BRANCH = re.compile(r"^branch: (.+)$", re.MULTILINE)


def claim_message(card: int, branch: str) -> str:
    """The message of a claim's commit: the card, and the branch that will ship it."""
    return f"claim plan#{card}\n\nbranch: {branch}\n"


class Board:
    """The board: the steps and questions in the developer's drag order (R-BAL177).

    ``sleep`` waits out the board's lag; tests pass one that does not wait.
    """

    def __init__(self, github: GitHub, board_id: str,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Read and write the board ``board_id`` through ``github``."""
        self.github = github
        self.board_id = board_id
        self._sleep = sleep

    def order(self) -> list[tuple[int, str]]:
        """The board's cards in their drag order: ``(card number, board item id)``."""
        order, after = [], None
        while True:
            items = self.github.graphql(_BOARD, id=self.board_id, after=after)["node"]["items"]
            order += [
                (item["content"]["number"], item["id"]) for item in items["nodes"]
                if _is_tracker_issue(item["content"] or {})
            ]
            if not items["pageInfo"]["hasNextPage"]:
                return order
            after = items["pageInfo"]["endCursor"]

    def add(self, card: Card) -> str:
        """Put a card on the board (GitHub adds it at the bottom); its board item id."""
        answer = self.github.graphql(BOARD_ADD, p=self.board_id, c=card.node_id)
        return answer["addProjectV2ItemById"]["item"]["id"]

    def remove(self, item: str) -> None:
        """Take a card off the board (the card itself is untouched)."""
        self.github.graphql(BOARD_REMOVE, p=self.board_id, i=item)

    def place(self, item: str, after: str | None) -> bool:
        """Move a board item to just after ``after`` (the top when None); whether the
        board shows it there within :data:`BOARD_WAIT_SECONDS`."""
        if after is None:
            self.github.graphql(BOARD_TOP, p=self.board_id, i=item)
        else:
            self.github.graphql(BOARD_AFTER, p=self.board_id, i=item, a=after)
        return self.shows(item, after)

    def shows(self, item: str, after: str | None) -> bool:
        """Read the board until ``item`` sits right after ``after`` (first when None),
        for at most :data:`BOARD_WAIT_SECONDS`."""
        waited = 0
        while True:
            items = [item_id for _, item_id in self.order()]
            if item in items:
                index = items.index(item)
                if (after is None and index == 0) or (index and items[index - 1] == after):
                    return True
            if waited >= BOARD_WAIT_SECONDS:
                return False
            self._sleep(_POLL_SECONDS)
            waited += _POLL_SECONDS


class Tracker:  # pylint: disable=too-many-public-methods
    """The tracker as the App sees it: its cards and claims, and its :class:`Board`.

    Pylint: ``too-many-public-methods`` (22/20) -- ``connect``, then **one
    method per read or write the plan tool makes of the tracker** (most one
    request, ``claim`` four; this module is the one place it speaks to
    GitHub), the board's own writes already apart in :class:`Board`.  Two of
    them, the filing mark's read and its removal (R-BAL202), took it past 20.
    The claims' three (``claims``, ``claim``, ``release``: git references, not
    cards) could stand apart the same way; that split is not this change's.

    ``github`` is any object with :class:`_github.GitHub`'s ``rest``,
    ``graphql`` and ``graphql_lookup``.
    """

    def __init__(self, github: GitHub, board: Board, app_login: str) -> None:
        """Read and write through ``github`` as ``app_login``; ``board`` is the plan's."""
        self.github = github
        self.board = board
        self.board_id = board.board_id
        self.app_login = app_login

    @classmethod
    def connect(cls) -> Tracker:
        """The tracker, through a fresh installation token of the plan tool's App."""
        client_id, key = app_credentials()
        jwt = app_jwt(client_id, key, int(time.time()))
        login = GitHub(jwt).rest("GET", "/app")["slug"]
        github = GitHub(installation_token(app_installation(ORG, jwt)["id"], jwt))
        board_id = find_board(github)
        if board_id is None:
            raise TrackerError("the tracker has no board; run tools/plan/setup_tracker.py")
        return cls(github, Board(github, board_id), login)

    # -- reads ---------------------------------------------------------------

    def _listing(self, query: str) -> dict[int, Card]:
        """Every card a listing ``query`` (:data:`_OPEN_CARDS`, :data:`_MARKED_CARDS`)
        holds, by number, read page by page."""
        cards, after = {}, None
        while True:
            page = self.github.graphql(query, after=after)["repository"]["issues"]
            for node in page["nodes"]:
                cards[node["number"]] = card_from(node, self.board_id, self.app_login)
            if not page["pageInfo"]["hasNextPage"]:
                return cards
            after = page["pageInfo"]["endCursor"]

    def open_cards(self) -> dict[int, Card]:
        """Every open card, by number."""
        return self._listing(_OPEN_CARDS)

    def marked(self) -> dict[int, Card]:
        """Every card carrying the :data:`setup_tracker.FILING` mark, open or closed, by
        number: each a filing the plan tool began and has not finished (R-BAL202)."""
        return self._listing(_MARKED_CARDS)

    def cards(self, numbers: Iterable[int]) -> dict[int, Card]:
        """The cards numbered ``numbers``, open or closed; one that does not exist is
        absent from the answer (a trailer can name a number nobody filed)."""
        wanted = sorted(set(numbers))
        found = {}
        for start in range(0, len(wanted), _PAGE // 2):
            batch = wanted[start:start + _PAGE // 2]
            fields = " ".join(f"c{n}: issue(number: {n}) {{ {_CARD_FIELDS} }}" for n in batch)
            answer = self.github.graphql_lookup(
                f'query {{ repository(owner: "{ORG}", name: "{REPO}") {{ {fields} }} }}'
            )["repository"]
            for number in batch:
                if answer[f"c{number}"] is not None:
                    found[number] = card_from(answer[f"c{number}"], self.board_id,
                                              self.app_login)
        return found

    def body(self, number: int) -> str:
        """A card's body as it stands."""
        return self.github.rest("GET", f"{_BASE}/issues/{number}")["body"] or ""

    def claims(self) -> dict[int, Claim]:
        """Every claim, by card: one REST listing, then one read of their commits."""
        refs = self.github.rest("GET", f"{_BASE}/git/matching-refs/claims/") or []
        shas = {}
        for ref in refs:
            number = ref["ref"].removeprefix(CLAIM_PREFIX)
            if number.isdigit():
                shas[int(number)] = ref["object"]["sha"]
        if not shas:
            return {}
        fields = " ".join(
            f'c{n}: object(oid: "{sha}") {{ ... on Commit {{ message authoredDate }} }}'
            for n, sha in shas.items()
        )
        answer = self.github.graphql_lookup(
            f'query {{ repository(owner: "{ORG}", name: "{REPO}") {{ {fields} }} }}'
        )["repository"]
        claims = {}
        for number, sha in shas.items():
            commit = answer[f"c{number}"] or {}
            branch = _CLAIM_BRANCH.search(commit.get("message") or "")
            claims[number] = Claim(number, branch.group(1).strip() if branch else None,
                                   commit.get("authoredDate") or "", sha)
        return claims

    def edits(self, number: int) -> tuple[str, list[Edit]]:
        """A card's body as it stands and every saved version of it, oldest first.

        GitHub keeps one entry per saved edit, each holding the FULL body (not a
        diff; measured 2026-10-04), and on the first edit adds one for the body
        as filed.  A card never edited has none: its body is the filed text.
        """
        versions, after = [], None
        while True:
            issue = self.github.graphql(_EDITS, number=number, after=after)["repository"]["issue"]
            if issue is None:
                raise TrackerError(f"plan#{number} does not exist")
            page = issue["userContentEdits"]
            versions += [
                Edit(node["id"], node["editedAt"], (node["editor"] or {}).get("login"),
                     node["diff"] or "")
                for node in page["nodes"]
            ]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        body = issue["body"] or ""
        if not versions:
            versions = [Edit(None, issue["createdAt"], (issue["author"] or {}).get("login"), body)]
        return body, sorted(versions, key=lambda edit: edit.edited_at)

    def merged_into_dev(self, repository: str, sha: str) -> set[str]:
        """The head branches of the merged pull requests into ``dev`` that carried
        ``sha`` -- the branch that SHIPPED it, which git alone does not record.

        ``repository`` is the code repository (``owner/name``); it is public, so
        the App reads its pull requests (measured 2026-10-04).
        """
        pulls = self.github.rest("GET", f"/repos/{repository}/commits/{sha}/pulls")
        return {pull["head"]["ref"] for pull in pulls
                if pull.get("merged_at") and pull["base"]["ref"] == "dev"}

    def find_titles(self, text: str) -> list[tuple[int, str]]:
        """Cards whose title holds ``text`` (GitHub's search: words, not characters)."""
        query = quote(f'repo:{ORG}/{REPO} in:title "{text}"', safe="")
        found = self.github.rest("GET", f"/search/issues?q={query}&per_page=100")
        return [(item["number"], item["title"]) for item in found["items"]
                if "pull_request" not in item]

    # -- writes --------------------------------------------------------------

    def create(self, kind: str, title: str, body: str, labels: Iterable[str]) -> int:
        """File a card; its number, once GitHub's answer shows the type and labels sent."""
        labels = sorted(labels)
        issue = self.github.rest("POST", f"{_BASE}/issues",
                                 {"title": title, "body": body, "type": kind, "labels": labels})
        got = ((issue.get("type") or {}).get("name"), sorted(l["name"] for l in issue["labels"]))
        if got != (kind, labels):
            raise TrackerError(
                f"plan#{issue['number']} was filed as {got}, not {(kind, labels)}: fix it by hand"
            )
        return issue["number"]

    def retype(self, number: int, kind: str) -> None:
        """Change a card's kind, reading it back."""
        issue = self.github.rest("PATCH", f"{_BASE}/issues/{number}", {"type": kind})
        if (issue.get("type") or {}).get("name") != kind:
            raise TrackerError(f"GitHub did not make plan#{number} a {kind}")

    def retitle(self, number: int, title: str) -> None:
        """Rename a card."""
        self.github.rest("PATCH", f"{_BASE}/issues/{number}", {"title": title})

    def set_body(self, number: int, body: str) -> None:
        """Replace a card's body (its spec, for a step)."""
        self.github.rest("PATCH", f"{_BASE}/issues/{number}", {"body": body})

    def comment(self, number: int, text: str) -> None:
        """Add a comment to a card."""
        self.github.rest("POST", f"{_BASE}/issues/{number}/comments", {"body": text})

    def close(self, number: int, reason: str) -> None:
        """Close a card: ``completed`` (it shipped) or ``not_planned`` (it was dropped)."""
        self.github.rest("PATCH", f"{_BASE}/issues/{number}",
                         {"state": "closed", "state_reason": reason})

    def reopen(self, number: int) -> None:
        """Reopen a card."""
        self.github.rest("PATCH", f"{_BASE}/issues/{number}", {"state": "open"})

    def unmark(self, number: int) -> None:
        """Remove a card's :data:`setup_tracker.FILING` mark: its filing's last write."""
        self.github.rest("DELETE", f"{_BASE}/issues/{number}/labels/{FILING}")

    def add_child(self, parent: int, child: Card) -> None:
        """Make ``child`` a sub-issue of ``parent``."""
        self.github.rest("POST", f"{_BASE}/issues/{parent}/sub_issues",
                         {"sub_issue_id": child.id})

    def block(self, number: int, blocker: Card) -> None:
        """Record that ``number`` is blocked by ``blocker``."""
        self.github.rest("POST", f"{_BASE}/issues/{number}/dependencies/blocked_by",
                         {"issue_id": blocker.id})

    def unblock(self, number: int, blocker: Card) -> None:
        """Remove the record that ``number`` is blocked by ``blocker``."""
        self.github.rest(
            "DELETE", f"{_BASE}/issues/{number}/dependencies/blocked_by/{blocker.id}"
        )

    def claim(self, number: int, branch: str) -> Claim:
        """Claim a card for ``branch``; :class:`ClaimTaken` when another session holds it."""
        main = self.github.rest("GET", f"{_BASE}/git/ref/heads/main")["object"]["sha"]
        tree = self.github.rest("GET", f"{_BASE}/git/commits/{main}")["tree"]["sha"]
        commit = self.github.rest("POST", f"{_BASE}/git/commits", {
            "message": claim_message(number, branch), "tree": tree, "parents": [],
        })
        try:
            self.github.rest("POST", f"{_BASE}/git/refs",
                             {"ref": f"{CLAIM_PREFIX}{number}", "sha": commit["sha"]})
        except GitHubError as error:
            if error.status == 422 and "Reference already exists" in str(error):
                raise ClaimTaken(f"plan#{number} is already claimed") from error
            raise
        return Claim(number, branch, commit["author"]["date"], commit["sha"])

    def release(self, number: int) -> None:
        """Delete a card's claim."""
        self.github.rest("DELETE", f"{_BASE}/git/refs/claims/{number}")
