"""The GraphQL text of each read quill makes of a tracker: the fields of a card, each
listing of cards, a card's histories, and the board's order and links.

What pins each text: the recordings (``_recorded.Replay``) replay most of them word for
word, so a change to one no longer matches its recording: re-record it, never edit an
answer by hand.  Three no recording holds: :func:`all_cards` (its milestone is graded by
``test__tracker_place.py``), :data:`BARE_CARD_FIELDS` (graded against the recorded lookup,
less its board items, by ``test__tracker_card_reader.py``), and :data:`BOARD_REPOSITORIES`,
which nothing pins.

Split out of :mod:`_tracker`, which sends them and reads their answers, at X-cx L9d's
card-only reader, whose change would otherwise take that module past the 1,000 lines
``too-many-lines`` allows.  The lookups of cards and claims by number compose these fields
there, and the board's writes stay there beside :meth:`_tracker.Board._write`, the one
door they are sent through.
"""
from __future__ import annotations

from tools.quill.setup_tracker import FILING, LINKED_REPOSITORIES, Place

#: What a card says of itself and its links, read first.
_ISSUE_FIELDS = """
  id number fullDatabaseId title state stateReason
  issueType { name }
  labels(first: 100) { totalCount nodes { name } }
  parent { number repository { nameWithOwner } }
  subIssues(first: 100) { totalCount nodes {
    number state issueType { name } repository { nameWithOwner } } }
  blockedBy(first: 100) { totalCount nodes { number repository { nameWithOwner } } }
"""
#: A card's items on the organization's boards, which only a reader of the board reads.
_BOARD_ITEM_FIELDS = """\
  projectItems(first: 100) { totalCount nodes { id project { id } } }
"""
#: Who last closed or reopened a card.
_LAST_CLOSE_FIELDS = """\
  timelineItems(itemTypes: [CLOSED_EVENT, REOPENED_EVENT], last: 1) { nodes {
    __typename
    ... on ClosedEvent { actor { login } }
    ... on ReopenedEvent { actor { login } } } }
"""
#: What :class:`_tracker.Tracker` reads of every card: in this order, the text every
#: recording replays word for word.
CARD_FIELDS = _ISSUE_FIELDS + _BOARD_ITEM_FIELDS + _LAST_CLOSE_FIELDS
#: What :class:`_tracker.CardReader` reads of every card: the same, but its board items.
BARE_CARD_FIELDS = _ISSUE_FIELDS + _LAST_CLOSE_FIELDS


def open_cards(place: Place) -> str:
    """The listing of every open card of ``place``."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, CARD_FIELDS)
    )


def marked_cards(place: Place) -> str:
    """The listing of every card of ``place`` carrying the :data:`setup_tracker.FILING`
    mark, open or closed: a ruling's filing closes it before its last write removes the
    mark, and a ruling closed as completed by anyone is still unfinished (R-BAL206)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, labels: ["%s"], states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, FILING, CARD_FIELDS)
    )


#: What quill reads of a comment (:func:`_tracker._comment`).
COMMENT_FIELDS = "author { login } createdAt body"


def all_cards(place: Place) -> str:
    """The listing of every card of ``place``, open and closed, each with its body, its
    milestone's number and its first hundred comments (and how many it has).

    Only X-cx's migration reads it, and only it reads a milestone (L7 draft 4 s.12): the
    milestone is selected HERE, not in :data:`CARD_FIELDS`, which every other query
    selects and whose text the recordings replay word for word."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s body
      milestone { number }
      comments(first: 100) { totalCount nodes { %s } } } } } }"""
        % (place.owner, place.name, CARD_FIELDS, COMMENT_FIELDS)
    )


#: The board's cards in their drag order, a page at a time (:meth:`_tracker.Board.order`).
BOARD_ORDER = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { id content { ... on Issue { number repository { nameWithOwner } } } } } } } }"""


def edits(place: Place) -> str:
    """The read of one card's saved versions, a page at a time."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number body createdAt author { login }
    userContentEdits(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id editedAt editor { login } diff } } } } }"""
        % (place.owner, place.name)
    )


def comments(place: Place) -> str:
    """The read of one card's comments, a page at a time."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number
    comments(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { %s } } } } }"""
        % (place.owner, place.name, COMMENT_FIELDS)
    )


#: The repositories a board is linked to, read when quill connects
#: (:func:`_tracker.board_of`).
BOARD_REPOSITORIES = """query($id: ID!) { node(id: $id) { ... on ProjectV2 {
  %s } } }""" % LINKED_REPOSITORIES
