"""X-cx's migration plan (:mod:`_migrate_plan`): the writes still to make, and what it refuses,
on the small corpus (:mod:`_migrate_fake`) and the in-memory tracker (:mod:`_fake`).

Each test builds a tracker state by hand -- empty, wholly filed, or a run cut short at some
write -- and reads what the plan says of it.  Executing the writes is X-cx's B2b; nothing
here writes.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from tools.ci.arcs import REPO
from tools.ci.gitcmd import git
from tools.quill import _migrate_source
from tools.quill._fake import FakeTracker
from tools.quill._migrate_bodies import AS_FILED, Inputs, build
from tools.quill._migrate_fake import COMMIT, complete_input
from tools.quill._migrate_input import grade
from tools.quill._migrate_plan import (
    BLOCK,
    BOARD_ADD,
    CLOSE_UNMARKED,
    COMMENT,
    CREATE,
    LINK,
    MILESTONE,
    UNMARK,
    Tracked,
    Write,
    card_key,
    desired_board,
    moves,
    plan,
)
from tools.quill._migrate_source import read_registries, read_source
from tools.quill._tracker import Comment, OutsideLink
from tools.quill.check import card_title
from tools.quill.setup_tracker import FILING

OUTCOME = "O1 the first"
#: The corpus's one milestone, as :func:`_migrate_fake.complete_input` names it.
MILESTONES = {OUTCOME: ("The first", "The first outcome.")}
#: How the plan refuses a card no item claims that sits below no card filed already, after
#: its number and title.
OUTSIDE = ("is no item's card and sits below no card filed already: its title or arc label was "
           "changed by hand, its item left the registries, or it was filed outside X-cx; settle "
           "it before the run")


def _items(corpus):
    """The corpus's items, in the source's order."""
    return read_source(corpus).items


def _texts(corpus):
    """Each item's card text, as the input file :func:`_migrate_fake.complete_input` writes
    makes it (:func:`_migrate_bodies.build`)."""
    source = read_source(corpus)
    decisions, problems = grade(complete_input(corpus, source), source, corpus)
    assert not problems and decisions.milestones == MILESTONES
    return build(Inputs(source, corpus, decisions))[0]


def _plan(corpus, tracker, items=None):
    """The plan for the corpus's items (or ``items``) on ``tracker`` as a run reads it."""
    return plan(_items(corpus) if items is None else items, _texts(corpus), MILESTONES,
                _tracked(tracker))


def _tracked(tracker):
    """What a run reads of ``tracker``."""
    return Tracked(tracker.all_cards(), tracker.milestones(),
                   tuple(number for number, _ in tracker.board.order()), tracker.app_login)


def _filed(corpus, *, marked=(), skip=(), milestone=True):
    """A tracker holding every item's card as a finished filing would leave it: numbered
    from #2 in the source's order, titled, written and labelled as built, linked, on the
    board in the desired order, the scope step in its milestone, each question carrying its
    As filed comment, each ruling closed; the keys ``marked`` still carry the mark and
    ``skip`` have no card."""
    tracker = FakeTracker()
    if milestone:
        tracker.create_milestone(*MILESTONES[OUTCOME])
    texts = _texts(corpus)
    items = [item for item in _items(corpus) if item.key not in skip]
    numbers = {item.key: index + 2 for index, item in enumerate(items)}
    for item in items:
        number = numbers[item.key]
        tracker.add(number, item.kind, texts[item.key].body, on_board=False,
                    title=texts[item.key].title,
                    labels=tuple(sorted({*item.labels, *([FILING] if item.key in marked
                                                         else [])})),
                    is_open=item.kind != "ruling",
                    state_reason="COMPLETED" if item.kind == "ruling" else None,
                    parent=numbers.get(item.links.parent),
                    blocked_by=tuple(numbers[key] for key in item.links.blockers
                                     if key in numbers))
        if item.outcome is not None and milestone:
            tracker.milestone_of[number] = 1
        if item.kind == "question":
            tracker.notes[number] = [Comment(tracker.app_login, "t", texts[item.key].comment)]
    for item in sorted((item for item in items if item.links.place is not None),
                       key=lambda item: item.links.place):
        tracker.board.items.append(numbers[item.key])
    return tracker, numbers


def _change(tracker, number, **fields):
    """Card ``number`` of ``tracker`` with ``fields`` changed: the state a test reads."""
    tracker.cards_by_number[number] = replace(tracker.cards_by_number[number], **fields)


# -- what a run writes -----------------------------------------------------------

def test_an_empty_tracker_takes_every_write_in_the_order_draft_4_gives(corpus):
    """HISTORY: each ruling's create, then its close with its unmark.  The FREEZE: the
    milestone; the steps, each after its parent and blockers, the container A-2's unmark
    held to the end of the steps; the findings under their steps; the questions, each with
    its As filed comment and its board place."""
    found = _plan(corpus, FakeTracker())
    assert not found.refusals and found.numbers == {}
    assert found.history == (
        Write(CREATE, "balance:R-A"), Write(CLOSE_UNMARKED, "balance:R-A"),
        Write(CREATE, "salary:R-SAL1"), Write(CLOSE_UNMARKED, "salary:R-SAL1"))
    assert found.freeze == (
        Write(MILESTONE, OUTCOME),
        Write(CREATE, "balance:A-2"),
        Write(CREATE, "balance:A-2a"), Write(LINK, "balance:A-2a", "balance:A-2"),
        Write(BOARD_ADD, "balance:A-2a"), Write(UNMARK, "balance:A-2a"),
        Write(CREATE, "balance:A-2b"), Write(LINK, "balance:A-2b", "balance:A-2"),
        Write(BLOCK, "balance:A-2b", "balance:A-2a"), Write(BOARD_ADD, "balance:A-2b"),
        Write(UNMARK, "balance:A-2b"),
        Write(CREATE, "balance:A-3"), Write(BLOCK, "balance:A-3", "balance:A-2a"),
        Write(BOARD_ADD, "balance:A-3"), Write(UNMARK, "balance:A-3"),
        Write(CREATE, "salary:S-1"), Write(BOARD_ADD, "salary:S-1"), Write(UNMARK, "salary:S-1"),
        Write(UNMARK, "balance:A-2"),
        Write(CREATE, "balance:F-1"), Write(LINK, "balance:F-1", "balance:A-2a"),
        Write(UNMARK, "balance:F-1"),
        Write(CREATE, "balance:F-2"), Write(LINK, "balance:F-2", "balance:A-3"),
        Write(UNMARK, "balance:F-2"),
        Write(CREATE, "salary:N-391"), Write(LINK, "salary:N-391", "salary:S-1"),
        Write(UNMARK, "salary:N-391"),
        *(write for key in ("balance:Q-1", "balance:Q-2", "salary:N-391 question")
          for write in (Write(CREATE, key), Write(COMMENT, key), Write(BOARD_ADD, key),
                        Write(UNMARK, key))))


def test_a_wholly_filed_tracker_takes_no_write(corpus):
    """Every card filed, finished, linked and placed: nothing left, nothing refused."""
    tracker, numbers = _filed(corpus)
    found = _plan(corpus, tracker)
    assert (found.history, found.freeze, found.refusals) == ((), (), ())
    assert found.numbers == numbers


def test_a_steps_filing_cut_short_after_its_create_resumes_at_its_links(corpus):
    """A-2b created, still marked, with no link yet (a run stopped after the create's
    answer): its parent link, its blocker link, its board place and its unmark remain."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2b",))
    _change(tracker, numbers["balance:A-2b"], parent=None, blocked_by=())
    tracker.board.items.remove(numbers["balance:A-2b"])
    assert _plan(corpus, tracker).freeze == (
        Write(LINK, "balance:A-2b", "balance:A-2"), Write(BLOCK, "balance:A-2b", "balance:A-2a"),
        Write(BOARD_ADD, "balance:A-2b"), Write(UNMARK, "balance:A-2b"))


def test_a_ruling_created_and_not_yet_closed_takes_its_one_last_write(corpus):
    """Open and marked, or closed and still marked: the one PATCH closing and unmarking it."""
    tracker, numbers = _filed(corpus, marked=("balance:R-A", "salary:R-SAL1"))
    _change(tracker, numbers["balance:R-A"], is_open=True, state_reason=None)
    found = _plan(corpus, tracker)
    assert found.history == (Write(CLOSE_UNMARKED, "balance:R-A"),
                             Write(CLOSE_UNMARKED, "salary:R-SAL1"))


def test_a_questions_as_filed_comment_is_found_by_reading_never_made_twice(corpus):
    """Q-1 marked: with the tool's As filed comment on it, only its unmark remains; with the
    same text from a person, or the tool's other comment, the comment is still to make."""
    tracker, numbers = _filed(corpus, marked=("balance:Q-1",))
    assert _plan(corpus, tracker).freeze == (
        Write(UNMARK, "balance:Q-1"),)
    for comment in (Comment("a-person", "t", f"{AS_FILED}\n\nblock"),
                    Comment(tracker.app_login, "t", "another note")):
        tracker.notes[numbers["balance:Q-1"]] = [comment]
        assert _plan(corpus, tracker).freeze == (
            Write(COMMENT, "balance:Q-1"), Write(UNMARK, "balance:Q-1"))


def test_a_container_is_unmarked_after_every_steps_writes(corpus):
    """A-2 still marked while its leaf A-2b is still to link: A-2's unmark comes after
    A-2b's writes and S-1's, never before its last leaf is linked (draft 4 s.4, R9)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2", "balance:A-2b", "salary:S-1"))
    _change(tracker, numbers["balance:A-2b"], parent=None)
    assert _plan(corpus, tracker).freeze == (
        Write(LINK, "balance:A-2b", "balance:A-2"), Write(UNMARK, "balance:A-2b"),
        Write(UNMARK, "salary:S-1"), Write(UNMARK, "balance:A-2"))


def test_a_milestone_is_found_by_its_title_and_made_only_when_missing(corpus):
    """With no milestone the freeze opens by making it; the scope step A-3 then has no card
    either, so it is filed in it by its create."""
    tracker, _ = _filed(corpus, milestone=False, skip=("balance:A-3", "balance:F-2"))
    freeze = _plan(corpus, tracker).freeze
    assert freeze[0] == Write(MILESTONE, OUTCOME) and Write(CREATE, "balance:A-3") in freeze


def test_a_card_filed_already_takes_its_links_alone(corpus, monkeypatch):
    """S-1 mapped onto its card (as X-cx is card #1), off the board as a container is, not yet
    blocked by A-2a: its block, and no board place, comment or unmark -- its title and text
    are not graded."""
    tracker, numbers = _filed(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    items = [item if item.key != "salary:S-1" else _blocked(item, "balance:A-2a")
             for item in _items(corpus)]
    _change(tracker, numbers["salary:S-1"], title="Paycheck, filed long ago")
    tracker.board.items.remove(numbers["salary:S-1"])
    found = _plan(corpus, tracker, items)
    assert (found.freeze, found.refusals) == ((Write(BLOCK, "salary:S-1", "balance:A-2a"),), ())


def _blocked(item, key):
    """``item`` blocked by ``key`` too."""
    return _migrate_source.Item(item.arc, item.alias, item.kind, item.row, item.labels,
                                _migrate_source.Links(item.links.parent,
                                                      (*item.links.blockers, key),
                                                      item.links.place), item.outcome)


# -- what a run refuses ----------------------------------------------------------

def test_a_key_on_two_cards_is_refused_and_nothing_written_for_it(corpus):
    """A retry's second create of F-1: refused, and no third card planned for it."""
    tracker, numbers = _filed(corpus)
    tracker.add(40, "finding", "dup", on_board=False,
                title=card_title("F-1", "Stale row"), labels=("balance", FILING))
    found = _plan(corpus, tracker)
    assert found.refusals == (
        f"balance:F-1 is the key of 2 cards (plan#{numbers['balance:F-1']}, plan#40): delete "
        "the extras on the web (an extra is born of a create whose answer was lost and "
        "re-sent), then run again",)
    assert not found.freeze and "balance:F-1" not in found.numbers


def test_a_card_filed_already_that_the_tracker_lacks_is_refused_never_filed_again(
        corpus, monkeypatch):
    """S-1 mapped onto #99, which the tracker does not hold: nothing is filed for it."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 99)
    found = _plan(corpus, FakeTracker())
    assert ("salary:S-1 is plan#99, which the tracker does not hold: nothing is filed in its "
            "place") in found.refusals
    assert not [write for write in found.freeze if write.key == "salary:S-1"]


@pytest.mark.parametrize("key, change, reason", [
    ("balance:F-1", {"kind": "step"}, "it is a step, not a finding"),
    ("balance:F-1", {"labels": ("balance", "moves-money")},
     "its labels are ['balance', 'moves-money'], not ['balance']"),
    ("balance:F-1", {"is_open": False, "labels": ("balance", FILING)},
     "its filing ended (dropped while marked, or a ruling withdrawn)"),
    ("balance:R-A", {"state_reason": "NOT_PLANNED"}, "its filing ended"),
    ("balance:F-1", {"outside": (OutsideLink("parent", "o/code#5"),)},
     "it is linked outside the tracker (o/code#5)"),
    ("balance:R-A", {"is_open": True}, "an open ruling whose filing finished"),
    ("balance:F-2", {"is_open": False}, "it is closed, and a finding moves open"),
    ("balance:F-1", {"parent": 5}, "it is a sub-issue of plan#5, not plan#3"),
    ("balance:A-3", {"blocked_by": (3, 6)},
     "it is blocked by plan#6, which steps.md does not record"),
], ids=["kind", "labels", "dropped-marked", "ruling-withdrawn", "outside", "ruling-open",
        "closed", "rehomed", "extra-blocker"])
def test_an_items_card_unlike_it_is_refused_and_left_alone(corpus, key, change, reason):
    """Each way an item's card differs from the item that no write repairs: refused, naming
    the card, and nothing written to it."""
    tracker, numbers = _filed(corpus)
    _change(tracker, numbers[key], **change)
    found = _plan(corpus, tracker)
    assert len(found.refusals) == 1 and found.refusals[0].startswith(
        f"{key} (plan#{numbers[key]}): {reason}"), found.refusals
    assert not [write for write in (*found.history, *found.freeze) if write.key == key]


def test_a_card_in_another_milestone_or_none_is_refused(corpus):
    """A-3's card out of its outcome's milestone, or in another: a card's milestone is set
    only by its create, so no write repairs it."""
    tracker, numbers = _filed(corpus)
    for held in (None, 9):
        tracker.milestone_of.pop(numbers["balance:A-3"], None)
        if held is not None:
            tracker.milestone_of[numbers["balance:A-3"]] = held
        assert _plan(corpus, tracker).refusals == (
            f"balance:A-3 (plan#{numbers['balance:A-3']}): it is in milestone {held}, not 1; a "
            "card's milestone is set only by its create",)


def test_a_finished_filing_lacking_a_write_is_refused(corpus):
    """A-3 unmarked, but off the board and unblocked: a person changed it after its filing
    finished, so the migration does not redo it over them."""
    tracker, numbers = _filed(corpus)
    _change(tracker, numbers["balance:A-3"], blocked_by=())
    tracker.board.items.remove(numbers["balance:A-3"])
    assert _plan(corpus, tracker).refusals == (
        f"balance:A-3 (plan#{numbers['balance:A-3']}): its filing finished, yet it lacks "
        "block, board-add: a person changed it; settle it by hand",)


def test_steps_waiting_in_a_cycle_are_refused(corpus):
    """A-2a blocked by A-3, which is blocked by A-2a: neither can be filed first, nor A-2b,
    which waits on A-2a (review of B2a, L7: the refusal names the cycle and what waits on
    it)."""
    items = [_blocked(item, "balance:A-3") if item.key == "balance:A-2a" else item
             for item in _items(corpus)]
    found = _plan(corpus, FakeTracker(), items)
    assert found.refusals == (
        "steps wait in a cycle, so these can be filed in no order (the cycle and every step "
        "waiting on it): ['balance:A-2a', 'balance:A-2b', 'balance:A-3']",)


# -- a card's key -----------------------------------------------------------------

def test_a_cards_key_is_its_arc_label_and_title_alias_or_its_mapped_number(monkeypatch):
    """A migrated card by its one arc label and alias; a card filed already by its number,
    whatever its title; none for a title with no alias or a card with two arc labels."""
    tracker = FakeTracker()
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:X-cx", 1)
    tracker.add(1, title="Move the plan", labels=("balance",))
    tracker.add(2, title="[N-391 question] Re-read the stub", labels=("salary", FILING))
    tracker.add(3, title="Move the plan", labels=("balance",))
    tracker.add(4, title="[A-1] t", labels=("balance", "salary"))
    cards = tracker.all_cards()
    assert [card_key(number, cards[number]) for number in (1, 2, 3, 4)] == [
        "balance:X-cx", "salary:N-391 question", None, None]


# -- the board ----------------------------------------------------------------------

def test_the_desired_board_is_the_steps_by_rank_then_the_questions_in_ledger_order(corpus):
    """A-2a, A-2b, A-3, S-1, then Q-1, Q-2 and N-391's question; nothing refused."""
    tracker, numbers = _filed(corpus)
    desired, refusals = desired_board(_items(corpus), numbers, _tracked(tracker))
    assert desired == [numbers[key] for key in (
        "balance:A-2a", "balance:A-2b", "balance:A-3", "salary:S-1", "balance:Q-1",
        "balance:Q-2", "salary:N-391 question")] and refusals == []


def test_a_card_filed_already_holds_its_rank_with_the_cards_below_it_in_their_order(
        corpus, monkeypatch):
    """S-1 (rank 4) mapped onto #50, a container: at its rank stand the board cards below it
    (#52 under #51 under #50, and #53), in the order the board holds them, never #50."""
    tracker, numbers = _filed(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 50)
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(50, on_board=False)
    tracker.add(51, parent=50, on_board=False)
    tracker.add(52, parent=51)
    tracker.add(53, parent=50)
    tracker.board.items[:0] = [tracker.board.items.pop()]  # #53 first, #52 after the others
    numbers["salary:S-1"] = 50
    desired, refusals = desired_board(_items(corpus), numbers, _tracked(tracker))
    assert desired[3:5] == [53, 52] and 50 not in desired and refusals == []


def test_a_board_card_with_no_place_and_an_item_with_no_card_are_refused(corpus):
    """A finding put on the board by hand has no place in the desired order; a question with
    no card yet cannot be placed."""
    tracker, numbers = _filed(corpus, skip=("balance:Q-2",))
    tracker.board.items.append(numbers["balance:F-1"])
    refusals = desired_board(_items(corpus), numbers, _tracked(tracker))[1]
    assert refusals == [
        f"plan#{numbers['balance:F-1']} is on the board, and the desired order has no place "
        "for it: settle it before the run",
        "balance:Q-2 has a place on the board and no card yet: file it before the board is "
        "ordered"]


def _apply(order, planned):
    """``order`` after each move of ``planned``, as the board makes it."""
    order = list(order)
    for number, after in planned:
        order.remove(number)
        order.insert(0 if after is None else order.index(after) + 1, number)
    return order


def test_moves_take_the_board_to_its_desired_order_and_a_second_run_moves_nothing():
    """Two cards swapped and one dragged to the bottom: only the cards outside the longest
    common subsequence move; once there, nothing moves."""
    current, desired = [1, 3, 2, 4, 5, 6, 0], [0, 1, 2, 3, 4, 5, 6]
    planned = moves(current, desired)
    assert len(planned) == 2 and _apply(current, planned) == desired
    assert moves(desired, desired) == []


def test_moves_reach_any_desired_order_with_the_fewest_cards_moved():
    """Over 500 random orders of 30 cards (seed 20261009): each reached, each moving exactly
    the cards outside a longest common subsequence (counted independently)."""
    shuffle = random.Random(20261009)
    for _ in range(500):
        desired = list(range(30))
        current = desired[:]
        shuffle.shuffle(current)
        planned = moves(current, desired)
        assert _apply(current, planned) == desired
        assert len(planned) == 30 - _longest_increasing(current)


def _longest_increasing(order):
    """The length of the longest increasing subsequence of ``order``: the longest common
    subsequence with the sorted order, counted another way (patience sorting)."""
    tops = []
    for value in order:
        index = next((i for i, top in enumerate(tops) if top >= value), len(tops))
        tops[index:index + 1] = [value]
    return len(tops)


def test_moves_refuse_orders_of_different_cards():
    """The board read must show every card the desired order holds, and no other."""
    with pytest.raises(ValueError):
        moves([1, 2], [1, 2, 3])


# -- review of B2a: the order on the registries' real shapes (M1) -----------------

def _early(writes):
    """Each LINK and BLOCK that names a card whose CREATE has not come before it (a card
    filed already needs none)."""
    created, early = set(), []
    for write in writes:
        if write.what == CREATE:
            created.add(write.key)
        elif write.what in (LINK, BLOCK) and not all(
                key in created or key in _migrate_source.MAPPED
                for key in (write.key, write.other)):
            early.append(write)
    return early


def test_a_container_listed_after_its_leaves_and_a_blocker_of_another_arc_come_first(corpus):
    """``steps.md`` lists its containers after their leaves, and a step may wait on a container
    of another arc listed later (review of B2a, M1: 63 and 17 such edges live).  S-1 blocked
    by A-2, with A-2 listed last: A-2 is filed first, and every LINK and BLOCK follows the
    CREATEs it names; the board still stands by rank."""
    items = {item.key: item for item in _items(corpus)}
    items["salary:S-1"] = _blocked(items["salary:S-1"], "balance:A-2")
    steps = [items[key] for key in ("salary:S-1", "balance:A-2a", "balance:A-2b", "balance:A-3",
                                    "balance:A-2")]
    reordered = steps + [item for key, item in items.items() if item.kind != "step"]
    freeze = _plan(corpus, FakeTracker(), reordered).freeze
    assert freeze[1] == Write(CREATE, "balance:A-2") and not _early(freeze)
    tracker, numbers = _filed(corpus)
    assert desired_board(reordered, numbers, _tracked(tracker))[0] == [numbers[key] for key in (
        "balance:A-2a", "balance:A-2b", "balance:A-3", "salary:S-1", "balance:Q-1",
        "balance:Q-2", "salary:N-391 question")]


def test_on_the_live_registries_every_link_follows_the_creates_it_names():
    """The registries as this checkout's HEAD holds them, on a tracker holding only card #1
    (X-cx): nothing refused, and every LINK and BLOCK follows the CREATE of each card it
    names (review of B2a, M1)."""
    source = read_source(read_registries(REPO, git(REPO, "rev-parse", "HEAD").strip()))
    tracker = FakeTracker()
    tracker.add(1, title="[X-cx] Move the plan", labels=("balance",), on_board=False)
    found = plan(source.items, {}, {}, _tracked(tracker))
    assert not found.refusals and found.freeze and not _early(found.freeze)


# -- review of B2a: a plan that refuses anything writes nothing (M2) ---------------

def test_a_finished_leaf_left_unlinked_keeps_its_marked_container_from_being_unmarked(corpus):
    """A-2b's filing finished yet unlinked (refused), A-2 still marked: no unmark of A-2,
    nor any other write (review of B2a, M2, probe C)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2",))
    _change(tracker, numbers["balance:A-2b"], parent=None)
    found = _plan(corpus, tracker)
    assert found.refusals and (found.history, found.freeze) == ((), ())


def test_a_cycle_files_no_container_and_links_no_finding(corpus):
    """A-2a and A-2b blocked by each other: no create of A-2 left unmarked over no leaf, and
    no F-1 linked under a card nobody files (probe D)."""
    items = [_blocked(item, "balance:A-2b") if item.key == "balance:A-2a" else item
             for item in _items(corpus)]
    found = _plan(corpus, FakeTracker(), items)
    assert found.refusals and (found.history, found.freeze) == ((), ())


def test_a_refused_step_takes_no_finding_under_it(corpus):
    """A-2a's card of another kind (refused), F-1 not yet filed: F-1 is not linked under it
    (probe G)."""
    tracker, numbers = _filed(corpus, skip=("balance:F-1",))
    _change(tracker, numbers["balance:A-2a"], kind="question")
    found = _plan(corpus, tracker)
    assert found.refusals and (found.history, found.freeze) == ((), ())


# -- review of B2a: a card whose key was damaged by hand (M3) ----------------------

@pytest.mark.parametrize("key, change", [
    ("balance:Q-1", {"labels": ("balance", "salary")}),
    ("balance:R-A", {"title": "Here"}),
    ("balance:A-2b", {"labels": ("balance", "moves-money", "recurrence")}),
], ids=["question-two-arcs", "ruling-retitled", "step-third-arc"])
def test_a_migrated_card_whose_key_was_changed_by_hand_is_refused_never_filed_again(
        corpus, key, change):
    """Its key lost, and it sits below no card filed already: refused, and its item is not
    filed again beside it (review of B2a, M3)."""
    tracker, numbers = _filed(corpus)
    _change(tracker, numbers[key], **change)
    found = _plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (f"plan#{numbers[key]} ({title!r}) " + OUTSIDE,)
    assert (found.history, found.freeze) == ((), ())


def test_a_marked_card_no_item_claims_is_refused(corpus):
    """Q-2 created, still marked, before its As filed comment, then retitled by hand: it
    carries nothing of the migration's but its mark, and it sits below no card filed
    already, so it is refused."""
    tracker, numbers = _filed(corpus, marked=("balance:Q-2",))
    tracker.notes[numbers["balance:Q-2"]] = []
    _change(tracker, numbers["balance:Q-2"], title="Unwritten fork")
    assert any(refusal.startswith(f"plan#{numbers['balance:Q-2']} ('Unwritten fork')")
               for refusal in _plan(corpus, tracker).refusals)


# -- review of B2a: a card that says something else (M4) ---------------------------

@pytest.mark.parametrize("key, part", [
    ("balance:A-3", "title"), ("balance:A-3", "body"), ("balance:R-A", "body"),
    ("balance:Q-1", "comment"),
], ids=["step-title", "step-body", "ruling-body", "question-comment"])
def test_a_stale_card_is_refused_never_finished(corpus, key, part):
    """Each card still to finish whose title, body or As filed comment is not what the
    registries make it now: refused, so no unmark (or a ruling's close) finishes it (review
    of B2a, M4)."""
    tracker, numbers = _filed(corpus, marked=(key,))
    number = numbers[key]
    if part == "title":
        _change(tracker, number, title=card_title("A-3", "An old name"))
    elif part == "body":
        tracker.bodies[number] = tracker.bodies[number].replace(".", ";", 1)
    else:
        held = tracker.notes[number][0]
        tracker.notes[number] = [replace(held, body=held.body + "\nan old cell")]
    found = _plan(corpus, tracker)
    assert found.refusals == (f"{key} (plan#{number}): it differs in its {part} from what the "
                              "registries make it at this commit: a stale card is never "
                              "finished; settle it by hand",)


def test_a_card_written_at_another_commit_is_not_stale(corpus):
    """The As filed block's commit is the one the card was written at; a run reading the
    same text at a later commit finishes it (draft 4 s.5: the commit line is not compared)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-3",))
    number = numbers["balance:A-3"]
    tracker.bodies[number] = tracker.bodies[number].replace(COMMIT, "f" * 40)
    assert _plan(corpus, tracker).freeze == (Write(UNMARK, "balance:A-3"),)


def test_a_doubled_as_filed_comment_is_refused(corpus):
    """Two of the tool's As filed comments on Q-1: the tool comments one."""
    tracker, numbers = _filed(corpus, marked=("balance:Q-1",))
    tracker.notes[numbers["balance:Q-1"]] *= 2
    assert _plan(corpus, tracker).refusals == (
        f"balance:Q-1 (plan#{numbers['balance:Q-1']}): it holds 2 As filed comments; the tool "
        "comments one",)


# -- review of B2a: the lows ------------------------------------------------------

def test_a_milestone_renamed_on_the_web_is_refused_not_made_again(corpus):
    """The input's milestone renamed: refused, and the input's title not made beside it
    (review of B2a, L2)."""
    tracker, _ = _filed(corpus, skip=("balance:A-3", "balance:F-2"))
    tracker.milestones_by_number[1] = replace(tracker.milestones_by_number[1], title="Renamed")
    found = _plan(corpus, tracker)
    assert found.refusals == ("milestone #1 'Renamed' is none the input file names (renamed on "
                              "the web?): settle it before the run",) and not found.freeze


def test_a_card_moved_below_a_card_filed_already_is_refused_once(corpus, monkeypatch):
    """A-3's card moved below the card S-1 is mapped onto, where the board would hold it
    twice (review of B2a, L3): refused once, as re-homed, and never ordered."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    _change(tracker, numbers["balance:A-3"], parent=numbers["salary:S-1"])
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert found.refusals == (
        f"balance:A-3 (plan#{numbers['balance:A-3']}): it is a sub-issue of "
        f"plan#{numbers['salary:S-1']}, not none: re-homing is a person's call",)
    with pytest.raises(ValueError):
        moves([1, 2], [1, 2, 2])


def test_an_alias_no_title_gives_back_is_refused(corpus):
    """An alias holding ``] ``, which a title would cut short (review of B2a, L4)."""
    items = [replace(item, alias="Z] z") if item.key == "salary:S-1" else item
             for item in _items(corpus)]
    found = plan(items, _texts(corpus), MILESTONES, _tracked(FakeTracker()))
    assert ("salary:Z] z: its alias does not read back from the title it would carry, so no "
            "run could find its card") in found.refusals


def test_a_card_filed_already_is_not_graded_on_its_milestone(corpus, monkeypatch):
    """A-3, in the outcome's scope, mapped onto its card, which holds no milestone: not
    refused, and nothing to write (review of B2a, L4)."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-3", numbers["balance:A-3"])
    tracker.milestone_of.pop(numbers["balance:A-3"])
    tracker.board.items.remove(numbers["balance:A-3"])
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert (found.refusals, found.freeze) == ((), ())


# -- delta review of B2a: no card of the migration's is filed again beside itself --

@pytest.mark.parametrize("key, change", [
    ("balance:A-3", {"labels": ("recurrence",)}),
    ("balance:A-3", {"title": "[A-3x] Last part"}),
    ("balance:A-2a", {"title": "[A-2z] First half"}),
], ids=["another-arc", "another-alias", "a-blocker-and-owner"])
def test_a_card_whose_key_now_names_no_item_is_refused(corpus, key, change):
    """Its key read whole but no item's -- its one arc label changed, or its alias (delta
    review of B2a, MEDIUM-2: every earlier case read no key at all) -- refused ONCE: what
    follows from it is not refused again (A-3's finding under it; for A-2a, the finding F-1
    under it, the steps A-2b and A-3 it blocks, and its board place)."""
    tracker, numbers = _filed(corpus)
    _change(tracker, numbers[key], **change)
    found = _plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (f"plan#{numbers[key]} ({title!r}) " + OUTSIDE,)
    assert not found.freeze


def test_a_card_whose_item_left_the_registries_is_refused(corpus):
    """F-2's row gone, its card still there: refused, never left to stand unaccounted."""
    tracker, numbers = _filed(corpus)
    items = [item for item in _items(corpus) if item.key != "balance:F-2"]
    found = _plan(corpus, tracker, items)
    assert found.refusals == (
        f"plan#{numbers['balance:F-2']} ('[F-2] Double count') is no item's card and sits below "
        "no card filed already: its title or arc label was changed by hand, its item left the "
        "registries, or it was filed outside X-cx; settle it before the run",)


@pytest.mark.parametrize("key, edit", [
    ("balance:R-A", "crlf"), ("balance:Q-1", "comment-deleted"),
], ids=["crlf-body", "question-comment-deleted"])
def test_a_retitled_card_with_no_readable_block_is_refused_all_the_same(corpus, key, edit):
    """Retitled by hand, with its body saved through a web form with CRLF, or a question's
    As filed comment deleted (delta review of B2a, MEDIUM-3): it sits below no card filed
    already, so it is refused whatever its body or comments still say."""
    tracker, numbers = _filed(corpus)
    number = numbers[key]
    if edit == "crlf":
        tracker.bodies[number] = tracker.bodies[number].replace("\n", "\r\n")
    else:
        tracker.notes[number] = []
    _change(tracker, number, title=card_title("Zz", "Retitled"))
    found = _plan(corpus, tracker)
    assert found.refusals == (f"plan#{number} ('[Zz] Retitled') " + OUTSIDE,)
    assert (found.history, found.freeze) == ((), ())


def _mapped_with_leaf(corpus, monkeypatch, body, comments=()):
    """The wholly filed corpus with S-1 mapped onto its card (off the board, as a container
    is) and a card of the tracker's own, #90, under it, holding ``body`` and ``comments``."""
    tracker, numbers = _filed(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(90, "step", body, on_board=False, title="Its own leaf", labels=("salary",),
                parent=numbers["salary:S-1"])
    tracker.notes[90] = list(comments)
    return tracker


def test_a_card_of_the_trackers_own_below_a_card_filed_already_is_let_be(corpus, monkeypatch):
    """X-cx's own leaves (#2-#10, #26, #27 under #1 today): no item's, and not refused --
    even where a PERSON's comment quotes an As filed block (the tool's comments are what
    count)."""
    texts = _texts(corpus)
    block = texts["balance:F-1"].body
    tracker = _mapped_with_leaf(corpus, monkeypatch, "A leaf of its own.",
                                [Comment("a-person", "t", block)])
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert not found.refusals


@pytest.mark.parametrize("where", ["body", "crlf-body", "tool-comment"])
def test_a_card_below_a_card_filed_already_carrying_a_block_is_refused(corpus, monkeypatch,
                                                                       where):
    """Below a card filed already the migration links findings, so one carrying an As filed
    block that no item claims is its card with its key changed by hand."""
    texts = _texts(corpus)
    block = texts["balance:F-1"].body
    body = {"body": block, "crlf-body": block.replace("\n", "\r\n")}.get(where, "Its own.")
    comments = ([Comment(FakeTracker.app_login, "t", texts["balance:Q-1"].comment)]
                if where == "tool-comment" else [])
    tracker = _mapped_with_leaf(corpus, monkeypatch, body, comments)
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert len(found.refusals) == 1 and found.refusals[0].startswith(
        "plan#90 ('Its own leaf') sits below plan#") and not found.freeze


def test_a_tool_comment_quoting_a_block_is_not_an_as_filed_comment(corpus, monkeypatch):
    """``quill comment`` posts as the App: a comment of the tool's that QUOTES a card's text
    but does not open as an As filed comment marks nothing (review of B2a's fixes, L2)."""
    texts = _texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, "Its own.", [
        Comment(FakeTracker.app_login, "t", "A note quoting:\n\n" + texts["balance:F-1"].body)])
    assert not plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals


def test_a_card_dropped_while_marked_outside_x_cx_is_refused_as_what_it_is(corpus):
    """An X-cx leaf dropped while marked, which quill UNLINKS (R-BAL205) and keeps marked for
    good: refused, said to be a dropped card (review of B2a's fixes, L1; none on the tracker
    2026-10-09).  The same card still below the card filed already is X-cx's own."""
    tracker, _ = _filed(corpus)
    tracker.add(91, title="[X-cx L13] A leaf", labels=("balance", FILING), is_open=False,
                state_reason="NOT_PLANNED", on_board=False)
    assert _plan(corpus, tracker).refusals == (
        "plan#91 ('[X-cx L13] A leaf') was dropped while still marked, and no item claims it: "
        "an X-cx leaf quill unlinked when it was dropped (R-BAL205), or a card of the "
        "migration's dropped and retitled; delete it on the web before the run",)


def test_a_card_filed_outside_x_cx_is_refused(corpus):
    """A card of its own outside X-cx, open and unmarked: the cutover starts on X-cx's cards
    and the migration's only."""
    tracker, _ = _filed(corpus)
    tracker.add(91, title="A card of its own", on_board=False)
    assert _plan(corpus, tracker).refusals == (
        "plan#91 ('A card of its own') is no item's card and sits below no card filed already: "
        "its title or arc label was changed by hand, its item left the registries, or it was "
        "filed outside X-cx; settle it before the run",)


def test_x_cx_s_own_leaf_dropped_while_marked_below_it_is_let_be(corpus, monkeypatch):
    """Not yet unlinked: X-cx's own step, its filing quill's business, not the migration's."""
    texts = _texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, "Its own.")
    _change(tracker, 90, labels=("salary", FILING), is_open=False, state_reason="NOT_PLANNED")
    assert not plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals


def test_a_card_filed_already_still_marked_is_refused(corpus, monkeypatch):
    """Its own filing unfinished: the migration does not write links onto it (delta review
    of B2a, LOW-4)."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    _change(tracker, numbers["salary:S-1"], labels=("salary", FILING))
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert found.refusals == (
        f"salary:S-1 (plan#{numbers['salary:S-1']}): a card filed already still carrying the "
        "filing mark: its own filing is unfinished; finish it with `quill file` before the run",)


# -- delta review of B2a: the board's shape is refused before any write ------------

def test_a_board_card_with_no_place_refuses_the_plan_before_any_write(corpus):
    """The container A-2's card put on the board by hand, the corpus half filed: the plan
    refuses, and writes nothing (delta review of B2a, MEDIUM-1)."""
    tracker, numbers = _filed(corpus, skip=("balance:A-3", "balance:F-2"))
    tracker.board.items.append(numbers["balance:A-2"])
    found = _plan(corpus, tracker)
    assert found.refusals == (
        f"plan#{numbers['balance:A-2']} is on the board, and the desired order has no place "
        "for it: settle it before the run",) and not found.freeze


def test_a_step_the_registries_put_below_a_card_filed_already_is_refused(corpus, monkeypatch):
    """A-3 under S-1 in the registries, S-1 filed already: X-cx's leaves are its sub-issues,
    never steps.md rows (its row says so), so no step is filed below it -- which also keeps a
    ranked one from standing twice on the board (delta review of B2a, MEDIUM-1)."""
    tracker, numbers = _filed(corpus, skip=("balance:A-3", "balance:F-2"))
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    items = [replace(item, links=replace(item.links, parent="salary:S-1"))
             if item.key == "balance:A-3" else item for item in _items(corpus)]
    found = plan(items, texts, MILESTONES, _tracked(tracker))
    assert found.refusals == (
        "balance:A-3 is a step below a card filed already in the registries: that card's "
        "leaves are its own sub-issues, never steps.md rows, so no step is filed below it; "
        "re-point the row before the migration",) and not found.freeze


# -- delta review of B2a: the stale card, every kind and state (LOW-2) ---------------

@pytest.mark.parametrize("key", ["balance:A-3", "balance:F-1", "balance:R-A", "balance:Q-1"])
def test_a_finished_card_gone_stale_is_refused_whatever_its_kind(corpus, key):
    """Its filing finished, its body changed since (for a question, the developer's own
    question, which ``quill file ruling --from-question`` takes word for word)."""
    tracker, numbers = _filed(corpus)
    number = numbers[key]
    tracker.bodies[number] = tracker.bodies[number] + " (amended)"
    assert _plan(corpus, tracker).refusals == (
        f"{key} (plan#{number}): it differs in its body from what the registries make it at "
        "this commit: a stale card is never finished; settle it by hand",)


def test_a_body_github_stored_with_crlf_and_a_trailing_newline_is_not_stale(corpus):
    """What a web form or GitHub's storage may add -- CRLF, a trailing newline -- is no
    difference (:func:`check.normalized`)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-3",))
    number = numbers["balance:A-3"]
    tracker.bodies[number] = tracker.bodies[number].replace("\n", "\r\n") + "\r\n"
    assert _plan(corpus, tracker).freeze == (Write(UNMARK, "balance:A-3"),)



# -- third review of B2a ---------------------------------------------------------

@pytest.mark.parametrize("key", ["salary:N-391", "balance:Q-1", "balance:A-3"])
def test_a_migrated_card_moved_below_a_card_filed_already_and_rekeyed_is_refused(
        corpus, monkeypatch, key):
    """A finding below it retitled and rewritten as ``quill edit`` would (one sentence, no
    block); a question moved there and retitled, its comment deleted; a step moved there and
    retitled: none is X-cx's own, so each is refused (third review of B2a, M-A)."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    number = numbers[key]
    _change(tracker, number, title="Renamed by hand", parent=numbers["salary:S-1"])
    if key == "salary:N-391":
        tracker.bodies[number] = "The stub disagrees with the profile."
    tracker.notes[number] = []
    if number in tracker.board.items:
        tracker.board.items.remove(number)
    found = plan(_items(corpus), texts, MILESTONES, _tracked(tracker))
    assert found.refusals[0].startswith(f"plan#{number} ('Renamed by hand') sits below plan#"), \
        found.refusals
    assert not found.freeze and not found.history


def test_a_card_refused_on_the_board_is_refused_once(corpus):
    """Q-1 retitled while on the board, and F-1 on two cards both on the board: one refusal
    each, never also "no place" for what follows from it (third review of B2a, L3)."""
    tracker, numbers = _filed(corpus)
    _change(tracker, numbers["balance:Q-1"], title="Retitled")
    tracker.add(40, "finding", "dup", title=card_title("F-1", "Stale row"),
                labels=("balance",), on_board=False)
    tracker.board.items += [numbers["balance:F-1"], 40]
    found = _plan(corpus, tracker)
    assert len(found.refusals) == 2 and not [refusal for refusal in found.refusals
                                             if "no place" in refusal], found.refusals


def test_a_finding_below_a_card_filed_already_put_on_the_board_is_refused(corpus,
                                                                           monkeypatch):
    """N-391's finding card, below S-1 mapped, put on the board by hand: it is an item's card
    with no place, never one of X-cx's own to stand at S-1's rank (third review of B2a,
    L4)."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.board.items.append(numbers["salary:N-391"])
    assert plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals == (
        f"plan#{numbers['salary:N-391']} is on the board, and the desired order has no place "
        "for it: settle it before the run",)


def test_a_card_filed_already_on_the_board_is_refused(corpus, monkeypatch):
    """A container is never on the board (R-BAL179); S-1 mapped and left on it is refused
    rather than handed to :func:`moves` (third review of B2a, L5: M5)."""
    tracker, numbers = _filed(corpus)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    assert plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals == (
        f"plan#{numbers['salary:S-1']} is on the board, and the desired order has no place "
        "for it: settle it before the run",)


def test_a_step_two_levels_below_a_card_filed_already_in_the_registries_is_refused(
        corpus, monkeypatch):
    """A-2 under S-1 mapped: A-2a and A-2b sit two levels below it, each refused too (third
    review of B2a, L5: M11)."""
    tracker = FakeTracker()
    tracker.create_milestone(*MILESTONES[OUTCOME])
    tracker.add(1, "step", title="[S-1] filed", labels=("salary",), on_board=False)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    items = [replace(item, links=replace(item.links, parent="salary:S-1"))
             if item.key == "balance:A-2" else item for item in _items(corpus)]
    refused = [refusal.split(" ")[0] for refusal in
               plan(items, texts, MILESTONES, _tracked(tracker)).refusals]
    assert refused == ["balance:A-2", "balance:A-2a", "balance:A-2b"]


def test_cards_two_levels_below_a_card_filed_already_are_its_own_or_refused(corpus,
                                                                            monkeypatch):
    """Below X-cx's own leaf #90, a step of its own is let be and a finding no item claims is
    refused (third review of B2a, L5: M19)."""
    texts = _texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, "Its own.")
    tracker.add(91, "step", "Deeper.", title="Its own deeper leaf", labels=("salary",),
                parent=90, on_board=False)
    assert not plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals
    tracker.add(92, "finding", "One sentence.", title="Deeper finding", labels=("salary",),
                parent=91, on_board=False)
    assert [refusal.split(" ")[0] for refusal in
            plan(_items(corpus), texts, MILESTONES, _tracked(tracker)).refusals] == ["plan#92"]


def test_a_card_filed_already_that_is_no_item_is_not_refused_as_unclaimed(corpus, monkeypatch):
    """S-1 mapped but no longer among the items (the source refuses that itself): its card is
    not refused again as a card nothing accounts for (third review of B2a, L5: M1)."""
    tracker, _ = _filed(corpus, skip=("salary:S-1", "salary:N-391"))
    tracker.add(50, "step", title="[S-1] filed", labels=("salary",), on_board=False)
    texts = _texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 50)
    items = [item for item in _items(corpus) if item.key not in ("salary:S-1", "salary:N-391")]
    assert not plan(items, texts, MILESTONES, _tracked(tracker)).refusals
