"""X-cx's migration, its PLAN: the writes still to make, from what the tracker holds, to file
the cards the registries make (the L7 design's draft 4 s.4).

**GitHub is the only state.**  Every run reads the whole tracker -- every card with its body
and comments, its milestones, its board -- and this module says, from that and the items
(:mod:`_migrate_source`), what is still to write and what it refuses.  So a run cut short
anywhere is resumed by running it again, and a run with nothing left writes nothing.  It is
PURE: it reads only what it is handed (:class:`Tracked`), and writes nothing; X-cx's B2b
executes what it says.

**A card's KEY is its arc label and its alias** (:func:`card_key`): the old id its title
carries in brackets (:func:`check.title_alias`), under its one arc label -- except a card
filed already (:data:`_migrate_source.MAPPED`), keyed by its number.  An item's card is the
card of its key; a card whose key is no item's is the tracker's own, and moves nowhere.

**The writes, in order** (draft 4 s.4, s.11 V3-8, ruling ``balance:R-BAL242``):

- HISTORY, first, inside the cutover's freeze: each ruling filed born marked, then closed
  with its mark removed in ONE write (:data:`CLOSE_UNMARKED`);
- the FREEZE: each outcome's milestone, found by its TITLE (R-BAL243); then the steps in
  an order that files a card's parent and its blockers before it; then the findings, under
  their steps; then the questions.  A card's writes are its create (born marked, its
  labels and milestone in it), its parent link, a link per open blocker, a question's As
  filed comment, its board place (a ranked step's or a question's), and last its mark's
  removal -- a container's only after every step's writes, so after its last leaf is
  linked (draft 4 s.4, R9).  A card filed already takes its links alone.

**The board** is put in its desired order apart, once every card is on it
(:func:`desired_board`, :func:`moves`): the ranked steps by rank, the card filed already
at its rank holding every board card below it in its present order, then the questions in
the ledger's order (R-BAL238); only the cards outside the longest common subsequence of the
present and desired orders move, so a second run moves nothing.  What the board's shape
refuses is refused by :func:`plan` itself, before any write (:func:`board_refusals`).

**What it refuses, never chooses** (each a line of :attr:`Plan.refusals`): a key on two
cards (a retry's second create among them); a card filed already that the tracker does not
hold, or that still carries the filing mark (its own filing is unfinished); a card no item
claims but X-cx's own (:func:`_unclaimed`); a step the registries put below a card filed
already (X-cx's leaves are its sub-issues, never ``steps.md`` rows); a milestone the input
file does not name (one renamed on the web would be made again); a board card the desired
order has no place for (:func:`board_refusals`); an item's card of another kind, labels or
milestone, closed when it moves open, or open when it is a ruling; one whose filing ENDED
(:func:`_state.filing_ended`: dropped, or a ruling withdrawn, R-BAL205's hazard); one under
another parent, blocked by a card its item is not, or linked outside the tracker (a
person's call); one whose title, body or As filed comment is not what the registries make
it at this commit (:func:`_migrate_bodies.differences`; a stale card is never finished,
and ``quill edit`` cannot repair a ruling, R-BAL220); one whose filing finished with a write
still missing; a title whose alias does not read back as its item's; and steps waiting in
a cycle.  Each is stated once: a card or item already refused is not refused again for
what follows from it.  **A plan that refuses anything writes nothing** (:func:`plan`): one
refusal leaves other items' writes leaning on it -- a container unmarked over a leaf that
was refused, a finding linked under a step with no card -- so every refusal is settled
before any write.
The registries' and the input file's own refusals (:func:`_migrate_source.read_source`,
:func:`_migrate_input.grade`, :func:`_migrate_bodies.build` and its census) are the
executor's to gate on: it runs no plan while any of them refuses.

**A card filed already** (:data:`_migrate_source.MAPPED`, card #1) is graded on its kind,
labels and links -- parent, blockers and links outside the tracker -- and on its state
(open, its filing not ended); its title, text and milestone are its own, never the
registries'.  That is draft 4 s.5's "key, kind, labels and links", and the state besides:
no link is written onto a card in a state the migration does not expect.

**What the tracker may hold besides the items' cards** (:func:`_unclaimed`): the cards
filed already and X-cx's own cards below them, which are STEPS (#2-#10, #26 and #27 under
#1, measured read-only 2026-10-09) -- and nothing else, so the cutover starts on a tracker
holding X-cx's cards and the migration's only.  Refused: a card no item claims outside
every card filed already (one whose title or arc label a person changed, one whose item
left the registries, one filed outside X-cx, or an X-cx leaf quill unlinked when it was
dropped while marked, R-BAL205); below one, a card no item claims that is no step (the
migration links findings there and files no step there, so it is the migration's with its
key changed by hand, ``quill edit`` of title and body included), or a step carrying the
migration's As filed block.  What this cannot see: a step of the migration's that a person
moved below a card filed already AND retitled AND rewrote without its block -- three edits
inside the freeze, which forbids every one; ``migrate verify`` (X-cx's B2b) counts every
kind of card per arc against the registries, so the second card filed beside it is found.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from tools.ci.arcs import ARCS
from tools.quill._migrate_bodies import AS_FILED, Built, differences, holds_as_filed
from tools.quill._migrate_source import MAPPED, Item
from tools.quill._state import filing_ended
from tools.quill._tracker import Card, Milestone, WholeCard
from tools.quill.check import card_title, title_alias
from tools.quill.setup_tracker import FILING

#: The writes, by what :attr:`Write.what` says.  Every one but :data:`MILESTONE` names its
#: card by key.
MILESTONE = "milestone"
CREATE = "create"
CLOSE_UNMARKED = "close-unmarked"
LINK = "link"
BLOCK = "block"
COMMENT = "comment"
BOARD_ADD = "board-add"
UNMARK = "unmark"


@dataclass(frozen=True)
class Write:
    """One write, addressed by KEY (a card filed in this run has no number until its create
    answers): what it is, the card's key (for :data:`MILESTONE`, the outcome's name), and
    the other card it names -- a :data:`LINK`'s parent, a :data:`BLOCK`'s blocker."""

    what: str
    key: str
    other: str | None = None


@dataclass(frozen=True)
class Tracked:
    """The tracker as one run read it: every card whole, by number
    (:meth:`_tracker.Tracker.all_cards`); its milestones, by title; its board's cards in
    order; and the App's login, whose comments are the tool's."""

    cards: Mapping[int, WholeCard]
    milestones: Mapping[str, Milestone]
    board: tuple[int, ...]
    app_login: str


@dataclass(frozen=True)
class Plan:
    """What a run writes and refuses: each item's card number where one exists, the
    HISTORY phase's writes, the FREEZE phase's, and the refusals."""

    numbers: Mapping[str, int]
    history: tuple[Write, ...]
    freeze: tuple[Write, ...]
    refusals: tuple[str, ...]


def card_key(number: int, whole: WholeCard) -> str | None:
    """The key a card is an item's card by: :data:`_migrate_source.MAPPED`'s for a card filed
    already, else its one arc label and its title's alias (:func:`check.title_alias`); None
    for a card with no alias or not exactly one arc label."""
    for key, mapped in MAPPED.items():
        if mapped == number:
            return key
    alias = title_alias(whole.card.title)
    arcs = [label for label in whole.card.labels if label in ARCS]
    if alias is None or len(arcs) != 1:
        return None
    return f"{arcs[0]}:{alias}"


def plan(items: Sequence[Item], built: Mapping[str, Built],
         milestones: Mapping[str, tuple[str, str]], tracked: Tracked) -> Plan:
    """The writes still to make, and what is refused; none at all when anything is (the
    registries' and the input file's own refusals are the caller's to gate on).

    Args:
        items: Every item, as :func:`_migrate_source.read_source` orders them.
        built: Each item's card text, by key (:func:`_migrate_bodies.build`; a card filed
            already has none).
        milestones: Each outcome's milestone, ``(title, description)``, as the input file
            gives it (:attr:`_migrate_input.Decisions.milestones`).
        tracked: The tracker as this run read it.

    Returns:
        The :class:`Plan`.
    """
    known = _numbers(items, tracked)
    refusals = [*known.refusals, *_unknown_milestones(milestones, tracked)]
    graded = _Grading(known.numbers, tracked, milestones, built, frozenset(known.unaccounted))
    writes, refused = {}, set(known.unwritable)
    for item in items:
        count = len(refusals)
        writes[item.key] = [] if item.key in known.unwritable else graded.writes(item, refusals)
        if len(refusals) > count:
            refused.add(item.key)
    refusals += board_refusals(items, known.numbers, tracked, refused, known.unaccounted)
    freeze, cycle = _freeze(items, writes, milestones, tracked)
    refusals += cycle
    numbers = known.numbers
    if refusals:
        return Plan(numbers, (), (), tuple(refusals))
    history = [write for item in items if item.kind == "ruling" for write in writes[item.key]]
    return Plan(numbers, tuple(history), tuple(freeze), ())


def _unknown_milestones(milestones: Mapping[str, tuple[str, str]],
                        tracked: Tracked) -> list[str]:
    """A refusal for each milestone of the tracker the input file does not name: renamed on
    the web, the one the input names would be made again beside it."""
    titles = {title for title, _ in milestones.values()}
    return [f"milestone #{held.number} {title!r} is none the input file names (renamed on the "
            "web?): settle it before the run" for title, held in sorted(
                tracked.milestones.items()) if title not in titles]


@dataclass(frozen=True)
class _Known:
    """What a run knows of each item's card before grading it: its number, where exactly one
    card holds its key; the items no write may touch; the refusals that say why; and the
    cards those refusals name, which nothing else is refused for."""

    numbers: dict[str, int]
    unwritable: set[str]
    refusals: list[str]
    unaccounted: set[int]


def _numbers(items: Sequence[Item], tracked: Tracked) -> _Known:
    """Each item's card number, and the items and cards refused before any is graded: a key
    on two cards, a card filed already that the tracker does not hold, an alias no title
    gives back, a step the registries put below a card filed already, and each card no item
    claims but X-cx's own (:func:`_unclaimed`)."""
    by_key = defaultdict(list)
    for number, whole in sorted(tracked.cards.items()):
        if (key := card_key(number, whole)) is not None:
            by_key[key].append(number)
    keys = {item.key for item in items}
    parents = {item.key: item.links.parent for item in items}
    shared = {key for key, found in by_key.items() if key in keys and len(found) > 1}
    unheld = {key for key, number in MAPPED.items() if key in keys and number not in tracked.cards}
    misread = {item.key for item in items
               if title_alias(card_title(item.alias, item.alias)) != item.alias}
    under = {item.key for item in items
             if item.kind == "step" and _under_mapped(item.key, parents)}
    refusals = [f"{key} is the key of {len(by_key[key])} cards "
                f"({', '.join(f'plan#{n}' for n in by_key[key])}): delete the extras on the "
                "web (an extra is born of a create whose answer was lost and re-sent), then "
                "run again" for key in sorted(shared)]
    refusals += [f"{key} is plan#{MAPPED[key]}, which the tracker does not hold: nothing is "
                 "filed in its place" for key in sorted(unheld)]
    refusals += [f"{key}: its alias does not read back from the title it would carry, so no "
                 "run could find its card" for key in sorted(misread)]
    refusals += [f"{key} is a step below a card filed already in the registries: that card's "
                 "leaves are its own sub-issues, never steps.md rows, so no step is filed below "
                 "it; re-point the row before the migration" for key in sorted(under)]
    unclaimed = _unclaimed(keys, tracked)
    refusals += [refusal for _, refusal in unclaimed]
    numbers = {key: found[0] for key, found in by_key.items() if key in keys and len(found) == 1}
    return _Known(numbers, shared | unheld | misread | under, refusals,
                  {number for key in shared for number in by_key[key]}
                  | {number for number, _ in unclaimed})


def _as_filed_comments(whole: WholeCard, app_login: str) -> list[str]:
    """The card's As filed comments: the tool's that open :data:`_migrate_bodies.AS_FILED`,
    found by reading its comments, never by remembering (draft 4 s.4)."""
    return [comment.body for comment in whole.comments
            if comment.author == app_login and comment.body.startswith(AS_FILED)]


def _unclaimed(keys: set[str], tracked: Tracked) -> list[tuple[int, str]]:
    """Each card no item claims that is not X-cx's own, with its refusal: one below no card
    filed already (dropped while marked, or not), and below one, a card that is no step or a
    step carrying the migration's As filed block, in its body or its As filed comment (the
    module docstring says why, and what this cannot see)."""
    mapped = set(MAPPED.values())
    found = []
    for number, whole in sorted(tracked.cards.items()):
        if number in mapped or card_key(number, whole) in keys:
            continue
        card, label = whole.card, f"plan#{number} ({whole.card.title[:60]!r})"
        above = _ancestor(number, mapped, tracked.cards)
        if above is None and filing_ended(card):
            found.append((number, f"{label} was dropped while still marked, and no item claims "
                          "it: an X-cx leaf quill unlinked when it was dropped (R-BAL205), or a "
                          "card of the migration's dropped and retitled; delete it on the web "
                          "before the run"))
        elif above is None:
            found.append((number, f"{label} is no item's card and sits below no card filed "
                          "already: its title or arc label was changed by hand, its item left "
                          "the registries, or it was filed outside X-cx; settle it before the "
                          "run"))
        elif card.kind != "step" or any(holds_as_filed(text) for text in (
                whole.body, *_as_filed_comments(whole, tracked.app_login))):
            found.append((number, f"{label} sits below plan#{above}, where X-cx's own cards are "
                          "steps carrying no As filed block, and no item claims it: the "
                          "migration's card with its title or arc label changed by hand, or "
                          "one whose item left the registries; settle it before the run"))
    return found


def _freeze(items: Sequence[Item], writes: Mapping[str, list[Write]],
            milestones: Mapping[str, tuple[str, str]],
            tracked: Tracked) -> tuple[list[Write], list[str]]:
    """The FREEZE phase's writes in order: the milestones not found by title; the steps,
    each after its parent and blockers, a container's mark removed only after every step's
    writes; the findings; the questions -- and a refusal when steps wait on each other."""
    order, cycle = _filing_order([item for item in items if item.kind == "step"])
    containers = {item.links.parent for item in items
                  if item.kind == "step" and item.links.parent is not None}
    freeze = [Write(MILESTONE, name) for name, (title, _) in milestones.items()
              if title not in tracked.milestones]
    held = []
    for item in order:
        held += [write for write in writes[item.key]
                 if item.key in containers and write.what == UNMARK]
        freeze += [write for write in writes[item.key]
                   if not (item.key in containers and write.what == UNMARK)]
    freeze += held
    for kind in ("finding", "question"):
        freeze += [write for item in items if item.kind == kind for write in writes[item.key]]
    return freeze, cycle


@dataclass(frozen=True)
class _Grading:
    """What one run grades an item's card against: each item's card number, the tracker as
    read, each outcome's milestone, each item's card text, and the cards refused already,
    a link to which is not refused again."""

    numbers: Mapping[str, int]
    tracked: Tracked
    milestones: Mapping[str, tuple[str, str]]
    built: Mapping[str, Built]
    unaccounted: frozenset[int]

    def writes(self, item: Item, refusals: list[str]) -> list[Write]:
        """``item``'s writes still to make: all of them when it has no card; else what its
        card lacks, each refusal added instead where its card differs from it."""
        number = self.numbers.get(item.key)
        if number is None:
            return self._new(item)
        found = self._refusal(item, self.tracked.cards[number])
        if found:
            refusals.append(f"{item.key} (plan#{number}): {found}")
            return []
        missing = self._missing(item, self.tracked.cards[number])
        whole = self.tracked.cards[number]
        unfinished = FILING in whole.card.labels
        if item.key not in MAPPED and not unfinished and [
                write for write in missing if not self._follows(write, whole.card)]:
            refusals.append(f"{item.key} (plan#{number}): its filing finished, yet it lacks "
                            f"{', '.join(sorted({write.what for write in missing}))}: a "
                            "person changed it; settle it by hand")
            return []
        if item.key in MAPPED:
            return [write for write in missing if write.what in (LINK, BLOCK)]
        return missing + ([Write(UNMARK, item.key)] if unfinished and item.kind != "ruling"
                          else [])

    def _follows(self, write: Write, card: Card) -> bool:
        """Whether a missing ``write`` follows from a card refused already: a link to an item
        with no card of its key, where the card is linked so to a card refused already (the
        item's card with its key changed), so it is no refusal of its own."""
        if write.what not in (LINK, BLOCK) or write.other in self.numbers:
            return False
        held = [card.parent] if write.what == LINK else list(card.blocked_by)
        return any(number in self.unaccounted for number in held)

    def _new(self, item: Item) -> list[Write]:
        """Every write that files ``item`` as a new card."""
        if item.kind == "ruling":
            return [Write(CREATE, item.key), Write(CLOSE_UNMARKED, item.key)]
        writes = [Write(CREATE, item.key)]
        if item.links.parent is not None:
            writes.append(Write(LINK, item.key, item.links.parent))
        writes += [Write(BLOCK, item.key, blocker) for blocker in item.links.blockers]
        if item.kind == "question":
            writes.append(Write(COMMENT, item.key))
        if item.links.place is not None:
            writes.append(Write(BOARD_ADD, item.key))
        return [*writes, Write(UNMARK, item.key)]

    def _milestone(self, item: Item) -> int | None:
        """The number of the milestone ``item``'s card is filed in, None for none (an item in
        no outcome's scope, or one whose milestone does not exist yet)."""
        if item.outcome is None or item.outcome not in self.milestones:
            return None
        found = self.tracked.milestones.get(self.milestones[item.outcome][0])
        return None if found is None else found.number

    def _refusal(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card is not one the migration may write on, or None: it is not
        the item's card (:meth:`_unlike`), it is placed otherwise (:meth:`_misplaced`), or
        it says otherwise (:meth:`_stale`)."""
        return (self._unlike(item, whole) or self._misplaced(item, whole)
                or self._stale(item, whole))

    def _stale(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card does not say what the registries make it, or None: its title,
        body or As filed comment differs (:func:`_migrate_bodies.differences`), or it holds
        that comment more than once.  A card filed already says what it says."""
        if item.key in MAPPED:
            return None
        comments = self._as_filed(whole)
        if len(comments) > 1:
            return f"it holds {len(comments)} As filed comments; the tool comments one"
        parts = differences(self.built[item.key], whole.card.title, whole.body,
                            comments[0] if comments else None)
        if parts:
            return (f"it differs in its {', '.join(parts)} from what the registries make it at "
                    "this commit: a stale card is never finished; settle it by hand")
        return None

    def _unlike(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card is not what the migration files for it: its kind, labels or
        milestone; a filing that ended; a link outside the tracker."""
        card = whole.card
        labels = sorted(label for label in card.labels if label != FILING)
        if card.kind != item.kind:
            return f"it is a {card.kind}, not a {item.kind}"
        if labels != sorted(item.labels):
            return f"its labels are {labels}, not {sorted(item.labels)}"
        if filing_ended(card):
            return ("its filing ended (dropped while marked, or a ruling withdrawn): nothing is "
                    "filed over it; settle it by hand")
        if card.outside:
            outside = ", ".join(link.issue for link in card.outside)
            return f"it is linked outside the tracker ({outside}): a person's call"
        if item.key not in MAPPED and whole.milestone != self._milestone(item):
            return (f"it is in milestone {whole.milestone}, not {self._milestone(item)}; a "
                    "card's milestone is set only by its create")
        return None

    def _misplaced(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card stands where the migration would not put it: a card filed
        already still marked, an open ruling whose filing finished, a closed card of work,
        another parent, a blocker steps.md does not record."""
        card = whole.card
        if item.key in MAPPED and FILING in card.labels:
            return ("a card filed already still carrying the filing mark: its own filing is "
                    "unfinished; finish it with `quill file` before the run")
        if item.kind == "ruling" and card.is_open and FILING not in card.labels:
            return "an open ruling whose filing finished: a ruling is a closed record"
        if item.kind != "ruling" and not card.is_open:
            return f"it is closed, and a {item.kind} moves open"
        parent = None if item.links.parent is None else self.numbers.get(item.links.parent)
        if card.parent is not None and card.parent != parent \
                and card.parent not in self.unaccounted:
            return (f"it is a sub-issue of plan#{card.parent}, not "
                    f"{'plan#' + str(parent) if parent else 'none'}: re-homing is a person's call")
        expected = {self.numbers.get(key) for key in item.links.blockers}
        extra = sorted(set(card.blocked_by) - expected - self.unaccounted)
        if extra:
            return (f"it is blocked by {', '.join(f'plan#{n}' for n in extra)}, which "
                    "steps.md does not record: a person's call")
        return None

    def _missing(self, item: Item, whole: WholeCard) -> list[Write]:
        """The writes ``item``'s card does not show done, but its mark's removal."""
        card = whole.card
        if item.kind == "ruling":
            return [Write(CLOSE_UNMARKED, item.key)] if FILING in card.labels else []
        missing = []
        if item.links.parent is not None and card.parent is None:
            missing.append(Write(LINK, item.key, item.links.parent))
        missing += [Write(BLOCK, item.key, blocker) for blocker in item.links.blockers
                    if self.numbers.get(blocker) not in card.blocked_by]
        if item.kind == "question" and not self._as_filed(whole):
            missing.append(Write(COMMENT, item.key))
        if item.links.place is not None and card.board_item is None:
            missing.append(Write(BOARD_ADD, item.key))
        return missing

    def _as_filed(self, whole: WholeCard) -> list[str]:
        """The card's As filed comments (:func:`_as_filed_comments`)."""
        return _as_filed_comments(whole, self.tracked.app_login)


def _filing_order(steps: Sequence[Item]) -> tuple[list[Item], list[str]]:
    """``steps`` in an order that files each one after its parent and its open blockers, in
    any arc -- the order given wherever that allows, which it often does not: ``steps.md``
    lists its containers after their leaves, and a blocker may be a container listed later
    -- and a refusal naming the steps left unordered when some wait in a cycle."""
    position = {item.key: index for index, item in enumerate(steps)}
    waits = {item.key: {key for key in (item.links.parent, *item.links.blockers)
                        if key in position} for item in steps}
    order, done = [], set()
    while len(order) < len(steps):
        ready = [item for item in steps if item.key not in done and waits[item.key] <= done]
        if not ready:
            left = sorted(key for key in position if key not in done)
            return order, ["steps wait in a cycle, so these can be filed in no order (the cycle "
                           f"and every step waiting on it): {left}"]
        order.append(ready[0])
        done.add(ready[0].key)
    return order, []


def board_refusals(items: Sequence[Item], numbers: Mapping[str, int], tracked: Tracked,
                   refused: Iterable[str] = (), unaccounted: Iterable[int] = ()) -> list[str]:
    """Each board card the desired order has no place for: neither the card of an item with a
    place (a card filed already is a container, never on the board) nor X-cx's own below a
    card filed already, which stands at that card's rank (draft 4 s.4: the coordinator
    settles it before the run).  An item's card ``refused`` already, and a card
    ``unaccounted`` for already, is not refused again here."""
    skip = set(unaccounted) | {numbers[key] for key in refused if key in numbers}
    placed = {numbers[item.key] for item in items
              if item.links.place is not None and item.key in numbers and item.key not in MAPPED}
    claimed = set(numbers.values())
    mapped = set(MAPPED.values())
    return [f"plan#{number} is on the board, and the desired order has no place for it: settle "
            "it before the run" for number in tracked.board
            if number not in skip and number not in placed
            and (number in claimed or _ancestor(number, mapped, tracked.cards) is None)]


def _under_mapped(key: str, parents: Mapping[str, str | None]) -> bool:
    """Whether the item ``key`` sits below a card filed already in the registries' own links
    (its parent, its parent's, ...)."""
    seen = set()
    parent = parents.get(key)
    while parent is not None and parent not in seen:
        if parent in MAPPED:
            return True
        seen.add(parent)
        parent = parents.get(parent)
    return False


def desired_board(items: Sequence[Item], numbers: Mapping[str, int],
                  tracked: Tracked) -> tuple[list[int], list[str]]:
    """The board's desired order, card by card, and what refuses it: a board card with no
    place (:func:`board_refusals`, which :func:`plan` refuses already) and each item with a
    place that has no card yet, which a run orders the board only after filing (R-BAL238,
    draft 4 s.4).  Called on a tracker :func:`plan` refused nothing on; a card the order would
    hold twice there is refused by :func:`moves`.

    The items with a place, by tier and position: the ranked steps by rank, then the
    questions in the ledger's order.  A card filed already (:data:`_migrate_source.MAPPED`)
    is a container on the tracker, never on the board itself: at its rank stands every board
    card below it, in the order the board holds them now.
    """
    mapped = set(MAPPED.values())
    below = {number: _ancestor(number, mapped, tracked.cards) for number in tracked.board}
    placed = sorted((item for item in items if item.links.place is not None),
                    key=lambda item: item.links.place)
    desired, refusals = [], board_refusals(items, numbers, tracked)
    for item in placed:
        if item.key in MAPPED:
            desired += [number for number in tracked.board if below[number] == MAPPED[item.key]]
        elif item.key in numbers:
            desired.append(numbers[item.key])
        else:
            refusals.append(f"{item.key} has a place on the board and no card yet: file it "
                            "before the board is ordered")
    return desired, refusals


def _ancestor(number: int, among: set[int], cards: Mapping[int, WholeCard]) -> int | None:
    """The first card of ``among`` that card ``number`` sits below (a sub-issue, at any
    depth), None for none."""
    seen = set()
    parent = cards[number].card.parent if number in cards else None
    while parent is not None and parent not in seen:
        if parent in among:
            return parent
        seen.add(parent)
        parent = cards[parent].card.parent if parent in cards else None
    return None


def moves(current: Sequence[int], desired: Sequence[int]) -> list[tuple[int, int | None]]:
    """The moves that put the board's cards ``current`` in the order ``desired`` (the same
    cards): each desired card outside a longest common subsequence of the two, top-down,
    moved to just after its desired predecessor (None: the top).  A card in the subsequence
    is never moved, a move never undoes one made before it, and equal orders take none
    (draft 4 s.4, V3-6).

    Raises:
        ValueError: When the two do not hold the same cards, each once.
    """
    if sorted(current) != sorted(desired) or len(set(desired)) != len(desired):
        raise ValueError("the board's present and desired orders hold different cards")
    common = set(_longest_common(current, desired))
    return [(number, desired[index - 1] if index else None)
            for index, number in enumerate(desired) if number not in common]


def _longest_common(first: Sequence[int], second: Sequence[int]) -> list[int]:
    """A longest common subsequence of two sequences of distinct cards."""
    lengths = [[0] * (len(second) + 1) for _ in range(len(first) + 1)]
    for i in range(len(first) - 1, -1, -1):
        for j in range(len(second) - 1, -1, -1):
            lengths[i][j] = (lengths[i + 1][j + 1] + 1 if first[i] == second[j]
                             else max(lengths[i + 1][j], lengths[i][j + 1]))
    common, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        if first[i] == second[j]:
            common.append(first[i])
            i, j = i + 1, j + 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1
    return common
