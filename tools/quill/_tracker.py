"""The tracker's cards, board order and claims, read and written as quill's App.

Every card write goes through quill's GitHub App (ruling
``balance:R-BAL170``), a separate identity from the developer's login, so a
reader can tell the tool's write from his own edit and never act on his.  This
module is the only place quill speaks to the tracker; it decides
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
- The listings of open cards and of marked cards (the :data:`setup_tracker.FILING`
  label), and a card's sub-issues read by its number or in a listing, each
  showed a create, a mark's removal or an unlink at the first read of it after
  the write (2026-10-04 22:13, one sample each, on a quiet tracker; the delay
  itself was not timed).  Nothing here waits for them, as it does for the board;
  one sample does not show they never lag.  Removing a mark a card does not
  carry is answered 404 "Label does not exist".

**Every read and write names its tracker's :class:`setup_tracker.Place`**, the one
the :class:`Board` was resolved for: :data:`setup_tracker.PLAN` for every quill
command, and the rehearsal tracker for X-cx's migration (L7).  A write cannot
reach another tracker's cards or board:

- the App's token is minted NARROWED to the place's one repository
  (:meth:`Tracker.connect`).  That narrows repository access only: the board is an
  ORGANIZATION project, which the token may still reach by node id;
- so the board is resolved by its title in the place's organization and refused
  unless it is linked to the place's repository (:func:`board_of`), and every board
  write is sent through :meth:`Board._write`, which names that board and no other.

**The numbering** (:func:`numbering`): GitHub numbers issues and pull requests in one
sequence and answers a DELETED issue's number ``410 Gone`` (#11-#25 and #28-#30 on
the real tracker), so a listing of the cards that exist shows gaps.  Each gap is
read by number, which is how X-cx's migration (L7, Code B) is to find a card whose
create answer was lost rather than file it twice.

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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

from tools.quill._github import (
    GitHub,
    GitHubError,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
)
from tools.quill.setup_tracker import FILING, LINKED_REPOSITORIES, Place, find_board, linked

CLAIM_PREFIX = "refs/claims/"
#: How long a write waits for the board to show what it placed: three times
#: the ~20 seconds L1 measured, so a slow read is waited out and a stuck one
#: is reported rather than waited on forever.
BOARD_WAIT_SECONDS = 60
_POLL_SECONDS = 5
#: A connection GitHub caps per card (a parent holds at most 100 sub-issues).
_PAGE = 100
#: GitHub's issue search serves at most this many results of one query, whatever its
#: ``total_count`` says.
_SEARCH_CAP = 1000

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

def _open_cards(place: Place) -> str:
    """The listing of every open card of ``place``."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, _CARD_FIELDS)
    )


def _marked_cards(place: Place) -> str:
    """The listing of every card of ``place`` carrying the :data:`setup_tracker.FILING`
    mark, open or closed: a ruling's filing closes it before its last write removes the
    mark, and a ruling closed as completed by anyone is still unfinished (R-BAL206)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, labels: ["%s"], states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, FILING, _CARD_FIELDS)
    )


#: What quill reads of a comment (:func:`_comment`).
_COMMENT_FIELDS = "author { login } createdAt body"


def _all_cards(place: Place) -> str:
    """The listing of every card of ``place``, open and closed, each with its body, its
    milestone's number and its first hundred comments (and how many it has).

    Only X-cx's migration reads it, and only it reads a milestone (L7 draft 4 s.12): the
    milestone is selected HERE, not in :data:`_CARD_FIELDS`, which every other query
    selects and whose text the recordings replay word for word."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s body
      milestone { number }
      comments(first: 100) { totalCount nodes { %s } } } } } }"""
        % (place.owner, place.name, _CARD_FIELDS, _COMMENT_FIELDS)
    )

_BOARD = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { id content { ... on Issue { number repository { nameWithOwner } } } } } } } }"""

def _edits(place: Place) -> str:
    """The read of one card's saved versions, a page at a time."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number body createdAt author { login }
    userContentEdits(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id editedAt editor { login } diff } } } } }"""
        % (place.owner, place.name)
    )


def _comments(place: Place) -> str:
    """The read of one card's comments, a page at a time."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number
    comments(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { %s } } } } }"""
        % (place.owner, place.name, _COMMENT_FIELDS)
    )


#: The repositories a board is linked to, read when quill connects (:func:`board_of`).
_BOARD_REPOSITORIES = """query($id: ID!) { node(id: $id) { ... on ProjectV2 {
  %s } } }""" % LINKED_REPOSITORIES


#: The board's writes, the only GraphQL mutations quill sends (a recording
#: sends no other: ``_recorded.refusal``), each sent by :meth:`Board._write` alone.
#: ``$p`` is the board, ``$c`` a card's node id, ``$i`` a board item, ``$a`` the item
#: it goes after.
BOARD_ADD = ("mutation($p: ID!, $c: ID!) { addProjectV2ItemById(input: {projectId: $p, "
             "contentId: $c}) { item { id } } }")
BOARD_REMOVE = ("mutation($p: ID!, $i: ID!) { deleteProjectV2Item(input: {projectId: $p, "
                "itemId: $i}) { deletedItemId } }")
BOARD_TOP = ("mutation($p: ID!, $i: ID!) { updateProjectV2ItemPosition(input: "
             "{projectId: $p, itemId: $i}) { clientMutationId } }")
BOARD_AFTER = ("mutation($p: ID!, $i: ID!, $a: ID!) { updateProjectV2ItemPosition(input: "
               "{projectId: $p, itemId: $i, afterId: $a}) { clientMutationId } }")


class TrackerError(RuntimeError):
    """The tracker refused a write or answered something quill cannot read."""


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
    reopened it, it was quill -- so its state is the tool's display of
    git, not a person's decision.  ``touched_by_hand``: the last close or reopen
    was a person's.

    A card holds only what GitHub says of it.  Which of its sub-issues are LEAVES
    of the plan, and so whether it is a container, reads its children's own marks
    and git too, so the plan decides it (``_state.leaves``), never the card.
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
    def step_children(self) -> tuple[int, ...]:
        """The steps linked under this card, as GitHub lists them: each a leaf of it unless
        the plan says otherwise (``_state.leaves``, the one spelling of a leaf)."""
        return tuple(child.number for child in self.children if child.kind == "step")


@dataclass(frozen=True)
class Claim:
    """Who is building a card: the branch that will ship it, and since when."""

    card: int
    branch: str | None
    made: str
    sha: str


@dataclass(frozen=True)
class WholeCard:
    """A card with its text: its :class:`Card`, its body, every comment on it, oldest
    first, and the number of its milestone, None for none (X-cx's migration reads every
    card this way, L7)."""

    card: Card
    body: str
    comments: tuple[Comment, ...]
    milestone: int | None


@dataclass(frozen=True)
class Milestone:
    """One of a tracker's milestones: its number, title and description (X-cx's migration
    gives each outcome one, ruling ``balance:R-BAL243``)."""

    number: int
    title: str
    description: str


class NumberState(Enum):
    """What GitHub answers a REST read of an issue number with (:meth:`Tracker.number_state`)."""

    ISSUE = "an issue"
    PULL_REQUEST = "a pull request"
    DELETED = "a deleted issue (410)"
    NOT_FOUND = "nothing this token can read (404)"


@dataclass(frozen=True)
class Numbering:
    """The numbers a tracker's listing of cards lacks, read one by one (:func:`numbering`):
    the deleted ones, the pull requests, and ``stopped_at``, the first number above the
    highest listed that answers 404, where the read stopped.  It is the number the next
    create takes only when nothing the token cannot read holds it: GitHub answers 404 for
    that too, whatever it is."""

    deleted: tuple[int, ...]
    pull_requests: tuple[int, ...]
    stopped_at: int


@dataclass(frozen=True)
class Edit:
    """One saved version of a card's body, in full."""

    edit_id: str | None
    edited_at: str
    editor: str | None
    body: str


@dataclass(frozen=True)
class Comment:
    """One comment on a card: who wrote it (None for an account since deleted), when, and
    its text."""

    author: str | None
    created_at: str
    body: str


def _counted(connection: dict, what: str, number: int) -> list:
    """A connection's nodes, refusing one GitHub truncated (a silent partial read)."""
    if connection["totalCount"] > len(connection["nodes"]):
        raise TrackerError(
            f"plan#{number} has {connection['totalCount']} {what}; the read holds "
            f"{len(connection['nodes'])}"
        )
    return connection["nodes"]


def _is_tracker_issue(node: dict, place: Place) -> bool:
    """Whether a link or a board item's content names an issue of ``place``'s tracker: the
    one test, for a parent, a sub-issue, a blocker and the board alike, on the whole
    ``owner/name`` token."""
    return (node.get("repository") or {}).get("nameWithOwner") == place.full_name


def _comment(node: dict) -> Comment:
    """A :class:`Comment` from one GraphQL comment node (:data:`_COMMENT_FIELDS`)."""
    return Comment((node["author"] or {}).get("login"), node["createdAt"], node["body"])


def _outside(node: dict, what: str) -> OutsideLink:
    """A link to an issue outside the tracker."""
    return OutsideLink(what, f"{node['repository']['nameWithOwner']}#{node['number']}")


def card_from(node: dict, place: Place, board_id: str, app_login: str) -> Card:
    """A :class:`Card` of ``place``'s tracker from one GraphQL issue node
    (:data:`_CARD_FIELDS`)."""
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
    inside = [(link, what) for link, what in links if _is_tracker_issue(link, place)]
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
                      if not _is_tracker_issue(link, place)),
    )


_CLAIM_BRANCH = re.compile(r"^branch: (.+)$", re.MULTILINE)


def claim_message(card: int, branch: str) -> str:
    """The message of a claim's commit: the card, and the branch that will ship it."""
    return f"claim plan#{card}\n\nbranch: {branch}\n"


def numbering(listed: Iterable[int], state_of: Callable[[int], NumberState]) -> Numbering:
    """Read by number every number from #1 that a listing of every card lacks, up to the
    first one above the highest listed that answers 404.

    A deleted issue (410) and a pull request are stepped over.  What it REFUSES, naming the
    number: an issue the listing lacked (GitHub's listing lags a create: read again later);
    a 404 BELOW the highest listed, which GitHub's one sequence leaves only where something
    this token cannot read holds the number; and, raised by ``state_of``, an issue moved to
    another repository (301).

    Args:
        listed: The numbers of every card the listing holds, open and closed.
        state_of: How GitHub answers a REST read of one number
            (:meth:`Tracker.number_state`).

    Returns:
        The deleted numbers and pull requests among the gaps, and the number the read
        stopped at (:class:`Numbering`).

    Raises:
        TrackerError: For each refusal above.
    """
    listed = set(listed)
    highest = max(listed, default=0)
    deleted, pull_requests, number = [], [], 0
    while True:
        number += 1
        if number in listed:
            continue
        state = state_of(number)
        if state is NumberState.ISSUE:
            raise TrackerError(f"#{number} is an issue the listing of every card lacks "
                               "(GitHub's listing lags a create): read it again")
        if state is NumberState.NOT_FOUND:
            if number < highest:
                raise TrackerError(f"#{number} answers 404 below #{highest}, which the "
                                   "listing holds: GitHub gave it out, to something this "
                                   "token cannot read")
            return Numbering(tuple(deleted), tuple(pull_requests), number)
        (deleted if state is NumberState.DELETED else pull_requests).append(number)


def board_of(github: GitHub, place: Place) -> str:
    """The node id of ``place``'s board: found by its title in the place's organization,
    and refused unless the read of its links proves it linked to the place's repository
    (:func:`setup_tracker.linked`), so a board titled like another tracker's is never
    written as this one's.

    Raises:
        TrackerError: When no board has the title, or the one that has it is not linked
            to the place's repository, or a read GitHub cut short leaves that unread.
    """
    board_id = find_board(github, place)
    if board_id is None:
        raise TrackerError(f"{place.full_name} has no board titled {place.board_title!r}; "
                           "run python -m tools.quill.setup_tracker")
    repositories = github.graphql(_BOARD_REPOSITORIES, id=board_id)["node"]["repositories"]
    link = linked(place, repositories)
    if link is not True:
        names = [node["nameWithOwner"] for node in repositories["nodes"]]
        verdict = (f"not to {place.full_name}" if link is False else
                   f"a read GitHub cut short of its {repositories['totalCount']}, none of "
                   f"them {place.full_name}: whether it is linked is unread")
        raise TrackerError(f"the board titled {place.board_title!r} is linked to {names}, "
                           f"{verdict}")
    return board_id


class Board:
    """``place``'s board: the steps and questions in the developer's drag order (R-BAL177).

    ``sleep`` waits out the board's lag; tests pass one that does not wait.
    """

    def __init__(self, github: GitHub, place: Place, board_id: str,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Read and write the board ``board_id`` (``place``'s, :func:`board_of`) through
        ``github``.  The place is :attr:`location` (:meth:`place` moves a card)."""
        self.github = github
        self.location = place
        self.board_id = board_id
        self._sleep = sleep

    def _write(self, mutation: str, **variables: str) -> dict:
        """Send one of the board's writes, naming this board as ``$p`` and no other: the
        fence every board write passes through."""
        return self.github.graphql(mutation, p=self.board_id, **variables)

    def order(self) -> list[tuple[int, str]]:
        """The board's cards in their drag order: ``(card number, board item id)``."""
        order, after = [], None
        while True:
            items = self.github.graphql(_BOARD, id=self.board_id, after=after)["node"]["items"]
            order += [
                (item["content"]["number"], item["id"]) for item in items["nodes"]
                if _is_tracker_issue(item["content"] or {}, self.location)
            ]
            if not items["pageInfo"]["hasNextPage"]:
                return order
            after = items["pageInfo"]["endCursor"]

    def add(self, card: Card) -> str:
        """Put a card on the board (GitHub adds it at the bottom); its board item id."""
        return self._write(BOARD_ADD, c=card.node_id)["addProjectV2ItemById"]["item"]["id"]

    def remove(self, item: str) -> None:
        """Take a card off the board (the card itself is untouched)."""
        self._write(BOARD_REMOVE, i=item)

    def place(self, item: str, after: str | None) -> bool:
        """Move a board item to just after ``after`` (the top when None); whether the
        board shows it there within :data:`BOARD_WAIT_SECONDS`."""
        if after is None:
            self._write(BOARD_TOP, i=item)
        else:
            self._write(BOARD_AFTER, i=item, a=after)
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
    """A tracker as the App sees it: its cards and claims, and its :class:`Board`.

    Pylint: ``too-many-public-methods`` (29/20) -- ``connect``, then **one
    method per read or write quill makes of the tracker** (most one
    request, ``claim`` four; this module is the one place it speaks to
    GitHub), the board's own writes already apart in :class:`Board`.  Three
    of them, the filing mark's read and its removal (R-BAL202) and a leaf's
    unlink (R-BAL205), took it past 20; ``show``'s read of a card's comments
    is the fourth; X-cx's migration (L7) reads every card whole and asks GitHub
    what a number is, the fifth and sixth, and reads and makes milestones and
    closes a ruling with its mark's removal in one write, the seventh to ninth.
    The claims' three (``claims``, ``claim``, ``release``: git references, not
    cards) could stand apart the same way; that split is not this change's.

    ``github`` is any object with :class:`_github.GitHub`'s ``rest``,
    ``graphql`` and ``graphql_lookup``.  The tracker's :class:`setup_tracker.Place`
    is its board's (:attr:`Board.location`), its one home.
    """

    def __init__(self, github: GitHub, board: Board, app_login: str) -> None:
        """Read and write through ``github`` as ``app_login``, on ``board``'s place."""
        self.github = github
        self.board = board
        self.board_id = board.board_id
        self.place = board.location
        self.app_login = app_login

    @classmethod
    def connect(cls, place: Place) -> Tracker:
        """``place``'s tracker, through a fresh installation token of quill's App narrowed to
        the place's repository, and its board (:func:`board_of`)."""
        client_id, key = app_credentials()
        jwt = app_jwt(client_id, key, int(time.time()))
        login = GitHub(jwt).rest("GET", "/app")["slug"]
        installation = app_installation(place.owner, jwt)["id"]
        github = GitHub(installation_token(installation, jwt, repository=place.name))
        return cls(github, Board(github, place, board_of(github, place)), login)

    # -- reads ---------------------------------------------------------------

    def _nodes(self, query: str) -> Iterator[dict]:
        """Every issue node a listing ``query`` (:func:`_open_cards`, :func:`_marked_cards`,
        :func:`_all_cards`) holds, read page by page: the one walk of a listing."""
        after = None
        while True:
            page = self.github.graphql(query, after=after)["repository"]["issues"]
            yield from page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            after = page["pageInfo"]["endCursor"]

    def _card(self, node: dict) -> Card:
        """A :class:`Card` of this tracker from one GraphQL issue node."""
        return card_from(node, self.place, self.board_id, self.app_login)

    def _listing(self, query: str) -> dict[int, Card]:
        """Every card a listing ``query`` holds, by number."""
        return {node["number"]: self._card(node) for node in self._nodes(query)}

    def open_cards(self) -> dict[int, Card]:
        """Every open card, by number."""
        return self._listing(_open_cards(self.place))

    def marked(self) -> dict[int, Card]:
        """Every card carrying the :data:`setup_tracker.FILING` mark, open or closed, by
        number: each a filing quill began; whether it is still unfinished is
        ``_state.filing_unfinished``'s answer (one dropped while marked is not)."""
        return self._listing(_marked_cards(self.place))

    def all_cards(self) -> dict[int, WholeCard]:
        """Every card, open and closed, by number, each WHOLE: its body and every comment.

        The listing carries a card's first hundred comments; a card with more has them
        all read by :meth:`comments`, so no card's comments are cut short.
        """
        cards = {}
        for node in self._nodes(_all_cards(self.place)):
            number, held = node["number"], node["comments"]
            comments = ([_comment(each) for each in held["nodes"]]
                        if held["totalCount"] <= len(held["nodes"]) else self.comments(number))
            cards[number] = WholeCard(self._card(node), node["body"] or "", tuple(comments),
                                      (node["milestone"] or {}).get("number"))
        return cards

    def milestones(self) -> dict[str, Milestone]:
        """Every milestone, open and closed, by title, read page by page.

        X-cx's migration finds each outcome's milestone by its title (L7 draft 4 s.11
        V3-5), so a lost create's answer is found rather than made twice.

        Raises:
            TrackerError: When two milestones share a title, which would make the one
                found by it a guess.
        """
        found, page = {}, 1
        while True:
            more = "" if page == 1 else f"&page={page}"
            listed = self.github.rest(
                "GET", f"{self.place.path}/milestones?state=all&per_page=100{more}") or []
            for node in listed:
                if node["title"] in found:
                    raise TrackerError(f"two milestones of {self.place.full_name} are titled "
                                       f"{node['title']!r}: #{found[node['title']].number} and "
                                       f"#{node['number']}")
                found[node["title"]] = Milestone(node["number"], node["title"],
                                                 node["description"] or "")
            if len(listed) < 100:
                return found
            page += 1

    def number_state(self, number: int) -> NumberState:
        """What GitHub answers a REST read of issue ``number`` with (:func:`numbering`).

        Raises:
            TrackerError: For an issue moved to another repository (GitHub's 301, which
                :class:`_github.GitHub` never follows).
            GitHubError: For any other refusal.
        """
        try:
            issue = self.github.rest("GET", f"{self.place.path}/issues/{number}")
        except GitHubError as error:
            if error.status == 410:
                return NumberState.DELETED
            if error.status == 404:
                return NumberState.NOT_FOUND
            if error.status == 301:
                raise TrackerError(f"#{number} of {self.place.full_name} was moved to another "
                                   "repository (301)") from error
            raise
        return NumberState.PULL_REQUEST if "pull_request" in issue else NumberState.ISSUE

    def cards(self, numbers: Iterable[int]) -> dict[int, Card]:
        """The cards numbered ``numbers``, open or closed; one that does not exist is
        absent from the answer (a trailer can name a number nobody filed)."""
        wanted = sorted(set(numbers))
        found = {}
        for start in range(0, len(wanted), _PAGE // 2):
            batch = wanted[start:start + _PAGE // 2]
            fields = " ".join(f"c{n}: issue(number: {n}) {{ {_CARD_FIELDS} }}" for n in batch)
            answer = self.github.graphql_lookup(
                f'query {{ repository(owner: "{self.place.owner}", name: "{self.place.name}") '
                f'{{ {fields} }} }}'
            )["repository"]
            for number in batch:
                if answer[f"c{number}"] is not None:
                    found[number] = self._card(answer[f"c{number}"])
        return found

    def body(self, number: int) -> str:
        """A card's body as it stands."""
        return self.github.rest("GET", f"{self.place.path}/issues/{number}")["body"] or ""

    def claims(self) -> dict[int, Claim]:
        """Every claim, by card: one REST listing, then one read of their commits."""
        refs = self.github.rest("GET", f"{self.place.path}/git/matching-refs/claims/") or []
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
            f'query {{ repository(owner: "{self.place.owner}", name: "{self.place.name}") '
            f'{{ {fields} }} }}'
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
            issue = self.github.graphql(_edits(self.place), number=number,
                                        after=after)["repository"]["issue"]
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

    def comments(self, number: int) -> list[Comment]:
        """Every comment on a card, oldest first, read page by page.  A number nobody
        filed is GitHub's NOT_FOUND error, which :meth:`_github.GitHub.graphql` raises."""
        comments, after = [], None
        while True:
            answer = self.github.graphql(_comments(self.place), number=number, after=after)
            page = answer["repository"]["issue"]["comments"]
            comments += [_comment(node) for node in page["nodes"]]
            if not page["pageInfo"]["hasNextPage"]:
                return comments
            after = page["pageInfo"]["endCursor"]

    def merged_into_dev(self, repository: str, sha: str) -> set[str]:
        """The head branches of the merged pull requests into ``dev`` that carried
        ``sha`` -- the branch that SHIPPED it, which git alone does not record.

        ``repository`` is the code repository (``owner/name``); it is public, so
        the App reads its pull requests (measured 2026-10-04; and through a token
        narrowed to the tracker, 2026-10-09 08:00 EDT, X-cx L7 A2).
        """
        pulls = self.github.rest("GET", f"/repos/{repository}/commits/{sha}/pulls")
        return {pull["head"]["ref"] for pull in pulls
                if pull.get("merged_at") and pull["base"]["ref"] == "dev"}

    def find_titles(self, text: str) -> list[tuple[int, str]]:
        """Cards whose title holds ``text`` (GitHub's search: words, not characters), read
        page by page until every hit is read.

        Raises:
            TrackerError: When GitHub says its search timed out (``incomplete_results``),
                finds more hits than it serves (:data:`_SEARCH_CAP`), or answers a page
                empty before every hit is read, so no answer would be every card.
        """
        query = quote(f'repo:{self.place.full_name} in:title "{text}"', safe="")
        items, page = [], 1
        while True:
            more = "" if page == 1 else f"&page={page}"
            found = self.github.rest("GET", f"/search/issues?q={query}&per_page=100{more}")
            if found["incomplete_results"] or found["total_count"] > _SEARCH_CAP:
                raise TrackerError(
                    f"GitHub's title search for {text!r} finds {found['total_count']} "
                    f"(incomplete: {found['incomplete_results']}); it serves {_SEARCH_CAP} "
                    "at most and every one: name the card as plan#N")
            if not found["items"] and len(items) < found["total_count"]:
                raise TrackerError(
                    f"GitHub's title search for {text!r} answered page {page} empty after "
                    f"{len(items)} of {found['total_count']} hits: name the card as plan#N")
            items += found["items"]
            if len(items) >= found["total_count"]:
                break
            page += 1
        return [(item["number"], item["title"]) for item in items
                if "pull_request" not in item]

    # -- writes --------------------------------------------------------------

    def create(self, kind: str, title: str, body: str, labels: Iterable[str],
               milestone: int | None = None) -> int:
        """File a card, marked :data:`setup_tracker.FILING` beside ``labels`` in this, its
        first write (R-BAL202): every card quill files is born marked, and its
        filing's last write, :meth:`unmark`, removes the mark.  Its number, once GitHub's
        answer shows the type, labels and milestone sent.

        ``milestone``: the number of the milestone the card is filed in, which only X-cx's
        migration sets (L7 draft 4 s.3: a scope step's create carries its outcome's).
        None sends no milestone at all, so every other create's request is what it was.

        A create naming a label the tracker lacks makes that label (grey, no description;
        measured 2026-10-04 on the mark itself, before it existed; ``setup_tracker.py
        --apply`` then corrected it), so a card filed before the mark exists is still
        born marked.

        **A retry may file a second card.**  A create refused for a rate limit is re-sent
        on GitHub's word (:mod:`_github`), whose pages do not say whether the refused one
        was performed.  The extra is born marked like the first, so ``quill file`` run again
        finds both by kind, title and text and refuses while two stand
        (``_filing._same_filing``); this answer is the re-sent create's card.
        """
        labels = sorted({*labels, FILING})
        sent = {"title": title, "body": body, "type": kind, "labels": labels}
        if milestone is not None:
            sent["milestone"] = milestone
        issue = self.github.rest("POST", f"{self.place.path}/issues", sent)
        got = ((issue.get("type") or {}).get("name"), sorted(l["name"] for l in issue["labels"]))
        if got != (kind, labels):
            raise TrackerError(
                f"plan#{issue['number']} was filed as {got}, not {(kind, labels)}: fix it by hand"
            )
        held = (issue.get("milestone") or {}).get("number")
        if held != milestone:
            raise TrackerError(f"plan#{issue['number']} was filed in milestone {held}, not "
                               f"{milestone}: fix it by hand")
        return issue["number"]

    def create_milestone(self, title: str, description: str) -> int:
        """Make a milestone (X-cx's migration, one per outcome, R-BAL243); its number."""
        return self.github.rest("POST", f"{self.place.path}/milestones",
                                {"title": title, "description": description})["number"]

    def retype(self, number: int, kind: str) -> None:
        """Change a card's kind, reading it back."""
        issue = self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"type": kind})
        if (issue.get("type") or {}).get("name") != kind:
            raise TrackerError(f"GitHub did not make plan#{number} a {kind}")

    def retitle(self, number: int, title: str) -> None:
        """Rename a card."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"title": title})

    def set_body(self, number: int, body: str) -> None:
        """Replace a card's body (its spec, for a step)."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"body": body})

    def comment(self, number: int, text: str) -> None:
        """Add a comment to a card.

        A retry may add it twice, as a create may file twice (:meth:`create`); no command
        reads how many comments a card has, so a doubled note is shown on the card and
        decides nothing."""
        self.github.rest("POST", f"{self.place.path}/issues/{number}/comments", {"body": text})

    def close(self, number: int, reason: str) -> None:
        """Close a card: ``completed`` (it shipped) or ``not_planned`` (it was dropped)."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}",
                         {"state": "closed", "state_reason": reason})

    def reopen(self, number: int) -> None:
        """Reopen a card."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"state": "open"})

    def unmark(self, number: int) -> None:
        """Remove a card's :data:`setup_tracker.FILING` mark: its filing's last write."""
        self.github.rest("DELETE", f"{self.place.path}/issues/{number}/labels/{FILING}")

    def close_unmarked(self, number: int, labels: Iterable[str]) -> None:
        """Close a card as completed AND remove its :data:`setup_tracker.FILING` mark, in ONE
        write: a migrated ruling's last (L7 draft 4 s.11 V3-8, one write where
        :meth:`close` and :meth:`unmark` are two).  ``labels`` are the card's as read; the
        write sets them without the mark, so it is sent only during the cutover's freeze,
        when nothing else labels a card.  GitHub's answer is read back.

        Raises:
            TrackerError: When the answer shows the card open, closed for another reason,
                or labelled otherwise.
        """
        kept = sorted(set(labels) - {FILING})
        issue = self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {
            "state": "closed", "state_reason": "completed", "labels": kept})
        got = (issue["state"], issue.get("state_reason"),
               sorted(label["name"] for label in issue["labels"]))
        if got != ("closed", "completed", kept):
            raise TrackerError(f"plan#{number} reads {got} after its close and unmark, not "
                               f"{('closed', 'completed', kept)}: fix it by hand")

    def add_child(self, parent: int, child: Card) -> None:
        """Make ``child`` a sub-issue of ``parent``."""
        self.github.rest("POST", f"{self.place.path}/issues/{parent}/sub_issues",
                         {"sub_issue_id": child.id})

    def remove_child(self, parent: int, child: Card) -> None:
        """Unlink ``child`` from ``parent``, whose sub-issue it is."""
        self.github.rest("DELETE", f"{self.place.path}/issues/{parent}/sub_issue",
                         {"sub_issue_id": child.id})

    def block(self, number: int, blocker: Card) -> None:
        """Record that ``number`` is blocked by ``blocker``."""
        self.github.rest("POST", f"{self.place.path}/issues/{number}/dependencies/blocked_by",
                         {"issue_id": blocker.id})

    def unblock(self, number: int, blocker: Card) -> None:
        """Remove the record that ``number`` is blocked by ``blocker``."""
        self.github.rest(
            "DELETE", f"{self.place.path}/issues/{number}/dependencies/blocked_by/{blocker.id}"
        )

    def claim(self, number: int, branch: str) -> Claim:
        """Claim a card for ``branch``; :class:`ClaimTaken` when another session holds it."""
        main = self.github.rest("GET", f"{self.place.path}/git/ref/heads/main")["object"]["sha"]
        tree = self.github.rest("GET", f"{self.place.path}/git/commits/{main}")["tree"]["sha"]
        commit = self.github.rest("POST", f"{self.place.path}/git/commits", {
            "message": claim_message(number, branch), "tree": tree, "parents": [],
        })
        try:
            self.github.rest("POST", f"{self.place.path}/git/refs",
                             {"ref": f"{CLAIM_PREFIX}{number}", "sha": commit["sha"]})
        except GitHubError as error:
            if error.status == 422 and "Reference already exists" in str(error):
                raise ClaimTaken(f"plan#{number} is already claimed") from error
            raise
        return Claim(number, branch, commit["author"]["date"], commit["sha"])

    def release(self, number: int) -> None:
        """Delete a card's claim."""
        self.github.rest("DELETE", f"{self.place.path}/git/refs/claims/{number}")
