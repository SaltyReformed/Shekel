"""X-cx's migration plan (:mod:`_migrate_plan`): which cards the tracker may hold besides
the items' cards -- the cards filed already, and the tracker's own below them, told by their
NUMBER -- and a card filed already (card #1) graded a case per check, on the small corpus
(:mod:`_migrate_fake`) and the in-memory tracker (:mod:`_migrate_plan_fake`); five more of
card #1's tests are :mod:`test__migrate_plan`'s.  Nothing here writes.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill import _migrate_source
from tools.quill._fake import FakeTracker
from tools.quill._migrate_plan import CREATE, LINK, Write, plan
from tools.quill._migrate_plan_fake import (
    MILESTONES,
    corpus_items,
    corpus_plan,
    corpus_texts,
    filed_tracker,
    tracked,
    with_change,
)
from tools.quill._tracker import Comment, OutsideLink
from tools.quill.check import card_title
from tools.quill.setup_tracker import FILING


def _rekeyed(number, title, first=2):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, numbered
    after ``first``, the migration's first card (A-2's, #2 in
    :func:`_migrate_plan_fake.filed_tracker`'s numbering)."""
    return (f"plan#{number} ({title!r}) is no item's card, and it is numbered above "
            f"plan#{first}, the lowest card an item's key names, so it reads as the migration's.  "
            f"FIRST, if plan#{first} is a card of the tracker's own given an item's key by hand "
            "(refused besides), settle that one: this refusal goes with it, and this card is not "
            "to be deleted for it.  Else it is the migration's card with its title or arc label "
            "changed by hand, or its item left the registries, or it was filed natively inside "
            "the freeze; settle it before the run")


def _outside(number, title):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, numbered
    lower than every card an item's key names and under no card filed already."""
    return (f"plan#{number} ({title!r}) is no item's card, numbered lower than every card an "
            "item's key names (a card filed already apart) and under no card filed already: a "
            "card filed outside X-cx before the migration, an X-cx leaf unlinked from a card "
            "filed already by hand, or one of the migration's first cards with its title or arc "
            "label changed by hand or its item gone from the registries; settle it before the "
            "run")


# -- a card whose key was changed by hand (review of B2a, M3; its delta review) ----

@pytest.mark.parametrize("key, change", [
    ("balance:Q-1", {"labels": ("balance", "salary")}),
    ("balance:R-A", {"title": "Here"}),
    ("balance:A-2b", {"labels": ("balance", "moves-money", "recurrence")}),
], ids=["question-two-arcs", "ruling-retitled", "step-third-arc"])
def test_a_migrated_card_whose_key_was_changed_by_hand_is_refused_never_filed_again(
        corpus, key, change):
    """Its key lost: numbered after the migration's first card, it is refused, and its item
    is not filed again beside it (review of B2a, M3)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[key], **change)
    found = corpus_plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (_rekeyed(numbers[key], title),)
    assert (found.history, found.freeze) == ((), ())


def test_a_marked_card_no_item_claims_is_refused(corpus):
    """Q-2 created, still marked, before its As filed comment, then retitled by hand: it
    carries nothing of the migration's but its mark, and it sits below no card filed
    already, so it is refused."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:Q-2",))
    tracker.notes[numbers["balance:Q-2"]] = []
    with_change(tracker, numbers["balance:Q-2"], title="Unwritten fork")
    assert any(refusal.startswith(f"plan#{numbers['balance:Q-2']} ('Unwritten fork')")
               for refusal in corpus_plan(corpus, tracker).refusals)


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
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[key], **change)
    found = corpus_plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (_rekeyed(numbers[key], title),)
    assert not found.freeze


def test_a_card_whose_item_left_the_registries_is_refused(corpus):
    """F-2's row gone, its card still there: refused, never left to stand unaccounted."""
    tracker, numbers = filed_tracker(corpus)
    items = [item for item in corpus_items(corpus) if item.key != "balance:F-2"]
    found = corpus_plan(corpus, tracker, items)
    assert found.refusals == (_rekeyed(numbers["balance:F-2"], "[F-2] Double count"),)


@pytest.mark.parametrize("key, edit", [
    ("balance:R-A", "crlf"), ("balance:Q-1", "comment-deleted"),
], ids=["crlf-body", "question-comment-deleted"])
def test_a_retitled_card_with_no_readable_block_is_refused_all_the_same(corpus, key, edit):
    """Retitled by hand, with its body saved through a web form with CRLF, or a question's
    As filed comment deleted (delta review of B2a, MEDIUM-3): numbered after the
    migration's first card, it is refused whatever its body or comments still say."""
    tracker, numbers = filed_tracker(corpus)
    number = numbers[key]
    if edit == "crlf":
        tracker.bodies[number] = tracker.bodies[number].replace("\n", "\r\n")
    else:
        tracker.notes[number] = []
    with_change(tracker, number, title=card_title("Zz", "Retitled"))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_rekeyed(number, "[Zz] Retitled"),)
    assert (found.history, found.freeze) == ((), ())


# -- the tracker's own cards, told by their number (review of PR #549, M1, L2, L3) --

def _ended(number, title):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, whose
    filing ended, numbered lower than every card an item's key names and under no card filed
    already."""
    return (f"plan#{number} ({title!r})'s filing ended (dropped while marked, or a ruling "
            "withdrawn), no item claims it, and it is numbered lower than every card an item's "
            "key names (a card filed already apart) and under no card filed already: a card "
            "filed outside X-cx before the migration, an X-cx leaf quill unlinked when it was "
            "dropped (R-BAL205), or one of the migration's first cards with its title or arc "
            "label changed by hand or its item gone from the registries; delete it on the web "
            "before the run")


def _mapped_with_leaf(corpus, monkeypatch, start=2):
    """The wholly filed corpus, numbered from ``start``, with S-1 mapped onto its card (off
    the board, as a container is) and a step of the tracker's own, #1, under it -- filed
    before the migration, as X-cx's own are (#2-#10, #26 and #27 under #1 today, the
    migration's first card #31 at the earliest)."""
    tracker, numbers = filed_tracker(corpus, start=start)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(1, "step", "Its own.", on_board=False, title="Its own leaf", labels=("salary",),
                parent=numbers["salary:S-1"])
    return tracker


@pytest.mark.parametrize("kind, where", [
    ("step", "a-persons-comment"), ("step", "body"), ("step", "tool-comment"),
    ("finding", "nowhere"), ("ruling", "nowhere"), ("question", "nowhere"),
])
def test_a_card_of_the_trackers_own_below_a_card_filed_already_is_let_be(corpus, monkeypatch,
                                                                         kind, where):
    """X-cx's own cards (#2-#10, #26 and #27 under #1 today), filed before the migration:
    no item's, and not refused, whatever their kind (``quill file finding --owner 7`` puts a
    finding there; review of PR #549, L3) and whatever they hold -- an As filed block in a
    body (a fenced example quoting one) or in a comment, the tool's or a person's.  Only its
    number tells a card of the tracker's own (review of PR #549, M1)."""
    texts = corpus_texts(corpus)
    block = texts["balance:F-1"].body
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    with_change(tracker, 1, kind=kind)
    if where == "body":
        tracker.bodies[1] = block
    tracker.notes[1] = {"a-persons-comment": [Comment("a-person", "t", block)],
                        "tool-comment": [Comment(FakeTracker.app_login, "t",
                                                 texts["balance:Q-1"].comment)]}.get(where, [])
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


@pytest.mark.parametrize("kind, where", [
    ("step", "nowhere"), ("step", "body"), ("step", "crlf-body"), ("step", "tool-comment"),
    ("finding", "nowhere"), ("ruling", "nowhere"),
])
def test_a_card_below_a_card_filed_already_filed_after_the_migrations_first_is_refused(
        corpus, monkeypatch, kind, where):
    """The same cards numbered after the migration's first card: none is the tracker's own
    (no card is filed natively inside the freeze), so each is refused, whatever its kind and
    whatever it holds (review of PR #549, M1)."""
    texts = corpus_texts(corpus)
    block = texts["balance:F-1"].body
    body = {"body": block, "crlf-body": block.replace("\n", "\r\n")}.get(where, "Its own.")
    comments = ([Comment(FakeTracker.app_login, "t", texts["balance:Q-1"].comment)]
                if where == "tool-comment" else [])
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    tracker.add(90, kind, body, on_board=False, title="A card of its own", labels=("salary",),
                parent=1)
    tracker.notes[90] = comments
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(90, "A card of its own"),)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_dropped_while_marked_outside_x_cx_is_refused_as_what_it_is(corpus):
    """An X-cx leaf dropped while marked, which quill UNLINKS (R-BAL205) and keeps marked for
    good: refused, said to be a dropped card (review of B2a's fixes, L1; none on the tracker
    2026-10-09).  The same card still below the card filed already is X-cx's own."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, title="[X-cx L13] A leaf", labels=("balance", FILING), is_open=False,
                state_reason="NOT_PLANNED", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (
        _ended(1, "[X-cx L13] A leaf"),)


def test_a_card_filed_outside_x_cx_is_refused(corpus):
    """A card of its own outside X-cx, open and unmarked, filed before the migration: the
    cutover starts on X-cx's cards and the migration's only."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, title="A card of its own", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (_outside(1, "A card of its own"),)


def test_x_cx_s_own_leaf_dropped_while_marked_below_it_is_let_be(corpus, monkeypatch):
    """Not yet unlinked: X-cx's own step, its filing quill's business, not the migration's."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    with_change(tracker, 1, labels=("salary", FILING), is_open=False, state_reason="NOT_PLANNED")
    assert not plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals


@pytest.mark.parametrize("key", ["salary:N-391", "balance:Q-1", "balance:A-3"])
def test_a_migrated_card_moved_below_a_card_filed_already_and_rekeyed_is_refused(
        corpus, monkeypatch, key):
    """A finding below it retitled and rewritten as ``quill edit`` would (one sentence, no
    block); a question moved there and retitled, its comment deleted; a step moved there and
    retitled: none is X-cx's own, each numbered after the migration's first card, so each is
    refused (third review of B2a, M-A)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    number = numbers[key]
    with_change(tracker, number, title="Renamed by hand", parent=numbers["salary:S-1"])
    if key == "salary:N-391":
        tracker.bodies[number] = "The stub disagrees with the profile."
    tracker.notes[number] = []
    if number in tracker.board.items:
        tracker.board.items.remove(number)
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, "Renamed by hand"),)
    assert not found.freeze and not found.history


def test_cards_two_levels_below_a_card_filed_already_are_its_own_or_refused(corpus,
                                                                            monkeypatch):
    """Below X-cx's own leaf #1, a step and a finding of its own filed before the migration
    are let be, and a card no item claims filed after its first card is refused (third
    review of B2a, L5: M19; review of PR #549, M1 and L3)."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, start=10)
    tracker.add(2, "step", "Deeper.", title="Its own deeper leaf", labels=("salary",),
                parent=1, on_board=False)
    tracker.add(3, "finding", "One sentence.", title="Its own finding", labels=("salary",),
                parent=2, on_board=False)
    assert not plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals
    tracker.add(92, "step", "One sentence.", title="Deeper step", labels=("salary",),
                parent=3, on_board=False)
    assert plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals == (
        _rekeyed(92, "Deeper step", first=10),)


def test_a_card_filed_already_that_is_no_item_is_not_refused_as_unclaimed(corpus, monkeypatch):
    """S-1 mapped but no longer among the items (the source refuses that itself): its card is
    not refused again as a card nothing accounts for (third review of B2a, L5: M1)."""
    tracker, _ = filed_tracker(corpus, skip=("salary:S-1", "salary:N-391"))
    tracker.add(50, "step", title="[S-1] filed", labels=("salary",), on_board=False)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 50)
    items = [item for item in corpus_items(corpus)
             if item.key not in ("salary:S-1", "salary:N-391")]
    assert not plan(items, texts, MILESTONES, tracked(tracker)).refusals


def _mapped_s1(corpus, monkeypatch):
    """The wholly filed corpus with S-1 mapped onto its card, off the board as a container
    is: card #1's place in the corpus."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    return tracker, numbers


@pytest.mark.parametrize("change", [{"title": "Promoted to a step"}, {"labels": ("balance",)}],
                         ids=["retitled", "relabelled"])
def test_a_finding_below_a_card_filed_already_turned_into_a_step_is_refused(
        corpus, monkeypatch, change):
    """N-391's finding card, where the migration itself links it below S-1, made a step, its
    title or its arc label changed, its body rewritten without its block -- never moved: it
    is the migration's card by its number, so it is refused and N-391 is not filed again
    beside it (review of PR #549, M1, case 1)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    number = numbers["salary:N-391"]
    with_change(tracker, number, kind="step", **change)
    tracker.bodies[number] = "Re-read the stub."
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, tracker.cards_by_number[number].title),)
    assert (found.history, found.freeze) == ((), ())


def test_a_step_moved_below_a_card_filed_already_and_relabelled_is_refused(corpus, monkeypatch):
    """A-2b moved below S-1, its arc label changed (its title untouched), its body rewritten
    without its block: refused, and A-2b not filed again (review of PR #549, M1, case 2)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    number = numbers["balance:A-2b"]
    with_change(tracker, number, parent=numbers["salary:S-1"], labels=("moves-money", "salary"))
    tracker.bodies[number] = "Do the second half."
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, tracker.cards_by_number[number].title),)
    assert (found.history, found.freeze) == ((), ())


def _with_own_cards(tracker):
    """``tracker`` with card #1 as the tracker holds it -- filed already, here S-1's (X-cx's
    in the registries) -- and X-cx's own steps #2-#10, #26 and #27 under it (measured
    read-only 2026-10-09)."""
    tracker.add(1, title="[S-1] Filed long ago", labels=("salary",), on_board=False)
    for number in (*range(2, 11), 26, 27):
        tracker.add(number, title=f"[X-cx L{number}] A leaf", labels=("salary",), parent=1,
                    on_board=False)
    return tracker


def test_the_live_trackers_own_cards_refuse_nothing_before_or_after_the_first_card(
        corpus, monkeypatch):
    """Card #1, filed already and an item's card as X-cx's row makes it, is numbered below
    every card of the tracker's own, which it is not the migration's first card for: with no
    card of the migration's filed, every other item is filed, and with its cards filed from
    #31, the first number free, nothing is left and nothing refused."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    tracker = _with_own_cards(filed_tracker(
        corpus, skip=tuple(item.key for item in corpus_items(corpus)))[0])
    empty = corpus_plan(corpus, tracker)
    assert not empty.refusals and {
        write.key for write in (*empty.history, *empty.freeze) if write.what == CREATE} == {
        item.key for item in corpus_items(corpus) if item.key != "salary:S-1"}
    filed, numbers = filed_tracker(corpus, skip=("salary:S-1",), start=31)
    with_change(_with_own_cards(filed), numbers["salary:N-391"], parent=1)
    found = corpus_plan(corpus, filed)
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


def test_a_card_of_the_trackers_own_on_the_board_below_an_unranked_card_filed_already_is_refused(
        corpus, monkeypatch):
    """S-1 mapped with no place, so no rank for the cards below it to stand at, and its own
    leaf #1 on the board: refused by the plan, before any write, never handed to
    :func:`moves` (review of PR #549, L2)."""
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    tracker.board.items.append(1)
    items = [replace(item, links=replace(item.links, place=None))
             if item.key == "salary:S-1" else item for item in corpus_items(corpus)]
    found = plan(items, corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == ("plan#1 is on the board, and the desired order has no place for "
                              "it: settle it before the run",)
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("change, reason", [
    ({"kind": "finding"}, "it is a finding, not a step"),
    ({"labels": ("moves-money", "salary")},
     "its labels are ['moves-money', 'salary'], not ['salary']"),
    ({"outside": (OutsideLink("parent", "o/code#5"),)},
     "it is linked outside the tracker (o/code#5): a person's call"),
    ({"parent": 3}, "it is a sub-issue of plan#3, not none: re-homing is a person's call"),
    ({"blocked_by": (3,)},
     "it is blocked by plan#3, which steps.md does not record: a person's call"),
    ({"is_open": False, "state_reason": "COMPLETED"}, "it is closed, and a step moves open"),
    ({"labels": ("salary", FILING), "is_open": False, "state_reason": "NOT_PLANNED"},
     "its filing ended (dropped while marked, or a ruling withdrawn): nothing is filed over "
     "it; settle it by hand"),
], ids=["kind", "labels", "outside", "parent", "extra-blocker", "closed", "dropped-marked"])
def test_a_card_filed_already_is_graded_on_its_kind_labels_links_and_state(
        corpus, monkeypatch, change, reason):
    """Card #1's grading (draft 4 s.5's key, kind, labels and links, and its state): each way
    it differs is refused, and nothing is written onto it (review of PR #549, M2)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    with_change(tracker, numbers["salary:S-1"], **change)
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (f"salary:S-1 (plan#{numbers['salary:S-1']}): {reason}",)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_filed_already_takes_its_missing_parent_link(corpus, monkeypatch):
    """S-1 mapped, under A-2 in the registries and under nothing on the tracker: its parent
    link is written (review of PR #549, M2; the block is
    :func:`test_a_card_filed_already_takes_its_links_alone`)."""
    tracker, _ = _mapped_s1(corpus, monkeypatch)
    items = [replace(item, links=replace(item.links, parent="balance:A-2"))
             if item.key == "salary:S-1" else item for item in corpus_items(corpus)]
    found = plan(items, corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert (found.refusals, found.freeze) == ((), (Write(LINK, "salary:S-1", "balance:A-2"),))


# -- review of the #549 fix: what a refusal names, and the rule's premises ----------

def test_the_migrations_lowest_card_rekeyed_is_refused_naming_both_causes(corpus):
    """A-2's card, #2, the lowest the migration filed, retitled by hand: the first card an
    item's key names is #3 now, so #2 reads as numbered below it -- refused, naming the
    migration's first cards rekeyed beside a card filed outside X-cx (review of the #549
    fix, LOW-1) and an X-cx leaf unlinked by hand (review of the fix itself, LOW-2)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers["balance:A-2"], title="Fixed a typo by hand")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_outside(numbers["balance:A-2"], "Fixed a typo by hand"),)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_of_the_trackers_own_carrying_an_items_key_fails_closed(corpus, monkeypatch):
    """The rule's first premise broken: X-cx's own leaf #3 of four, below S-1 mapped onto
    #1, retitled to F-1's key before any card of the migration's is filed.  It is refused as
    F-1's card, and the own leaves numbered above it as the migration's: refused, never
    filed twice (review of the #549 fix, LOW-2)."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    tracker, _ = filed_tracker(corpus, skip=tuple(item.key for item in corpus_items(corpus)),
                               milestone=False)
    tracker.add(1, title="[S-1] Filed long ago", labels=("salary",), on_board=False)
    for number in range(2, 6):
        tracker.add(number, title=f"[X-cx L{number}] A leaf", labels=("salary",), parent=1,
                    on_board=False)
    with_change(tracker, 3, title=card_title("F-1", "A leaf"), labels=("balance",))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _rekeyed(4, "[X-cx L4] A leaf", first=3), _rekeyed(5, "[X-cx L5] A leaf", first=3),
        "balance:F-1 (plan#3): it is a step, not a finding")
    assert (found.history, found.freeze) == ((), ())


def test_a_withdrawn_ruling_no_item_claims_is_refused_as_a_filing_that_ended(corpus):
    """A ruling filed outside X-cx before the migration and withdrawn (closed as not planned,
    its mark removed): its filing ended, mark or none, and it is refused as such (third review
    of the #549 fix, LOW-2 and LOW-3)."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, "ruling", title="A ruling of its own", is_open=False,
                state_reason="NOT_PLANNED", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (_ended(1, "A ruling of its own"),)
