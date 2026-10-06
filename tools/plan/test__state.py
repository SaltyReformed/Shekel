"""The plan's decisions -- next, resolved, stale claims, sync -- over hand-built cards."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from _state import (
    Placement,
    Unsplit,
    drop_unlinks,
    dropped,
    dropped_above,
    filing_unfinished,
    is_container,
    is_live,
    is_work,
    leaf_placement,
    leaves,
    leaves_below,
    left_bare,
    missing,
    never_offered,
    never_split,
    next_step,
    outside_reports,
    resolved,
    stale_claims,
    sync_plan,
    sync_unsplits,
    unfinished_reports,
    unsplit,
    withdrawn,
    workable,
)
from _tracker import Card, Child, Claim, OutsideLink
from setup_tracker import FILING

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _card(number, kind="step", **changes):
    """An open, unclaimed, unblocked card on the board, with ``changes`` applied."""
    fields = {
        "number": number, "id": 1000 + number, "node_id": f"I_{number}", "title": f"card {number}",
        "kind": kind, "labels": ("balance",), "is_open": True, "state_reason": None,
        "parent": None, "children": (), "blocked_by": (), "board_item": f"PVTI_{number}",
        "closed_by_tool": False, "touched_by_hand": False, "outside": (),
    }
    return Card(**{**fields, **changes})


def _closed(number, kind="step", by_tool=True, reason="COMPLETED", **changes):
    """A closed card: by the tool (sync or drop) or by a person."""
    return _card(number, kind, is_open=False, state_reason=reason, closed_by_tool=by_tool,
                 touched_by_hand=not by_tool, **changes)


def _cards(*cards):
    """Cards by number."""
    return {card.number: card for card in cards}


def _linked(split):
    """``split`` and each step linked under it, as an open unmarked card: the cards
    ``leaf_placement`` reads to know ``split``'s leaves."""
    return _cards(split, *(_card(number, parent=split.number) for number in split.step_children))


def _claim(number, branch="feat/x", made="2026-10-04T11:00:00Z"):
    """A claim made an hour before NOW."""
    return Claim(number, branch, made, f"sha{number}")


def test_shipped_in_git_is_resolved_whatever_the_card_shows():
    """Git is the answer: an open card git says shipped is done with."""
    assert resolved(5, _cards(_card(5)), shipped={5})
    assert not resolved(5, _cards(_card(5)), shipped=set())


def test_a_card_a_person_closed_or_plan_dropped_is_resolved():
    """Dropped is a tracker fact: closed by hand (any reason) or closed not-planned by the tool."""
    assert resolved(5, _cards(_closed(5, by_tool=False, reason="COMPLETED")), shipped=set())
    assert resolved(5, _cards(_closed(5, by_tool=True, reason="NOT_PLANNED")), shipped=set())


def test_a_card_the_tool_closed_as_shipped_follows_git_back_open():
    """A Reopens commit makes it unresolved at once, before sync reopens the card."""
    cards = _cards(_closed(5, by_tool=True, reason="COMPLETED"))
    assert resolved(5, cards, shipped={5})
    assert not resolved(5, cards, shipped=set())


def test_a_container_is_resolved_when_every_step_child_is():
    """R-BAL177: findings and rulings under it neither make it a container nor hold it open."""
    parent = _card(1, children=(Child(2, "step", True), Child(3, "step", True),
                                Child(4, "finding", True)))
    cards = _cards(parent, _card(2), _card(3), _card(4, "finding"))
    assert is_container(parent, cards, set())
    assert not resolved(1, cards, shipped={2})
    assert resolved(1, cards, shipped={2, 3})
    owner = _card(6, children=(Child(4, "finding", True), Child(7, "ruling", False)))
    assert not is_container(owner, _cards(owner), set())


def test_next_is_the_first_workable_step_in_board_order():
    """Board order, not number order; containers, claimed, shipped, blocked cards skipped."""
    cards = _cards(
        _card(9),
        _card(1, children=(Child(9, "step", True),)),
        _card(2), _card(3), _card(4, blocked_by=(2,)), _card(5, "question"), _card(6),
    )
    answer = next_step([5, 1, 3, 2, 4, 6], cards, shipped={3}, claims={2: _claim(2)})
    assert answer.card.number == 6
    assert answer.unplaced == (cards[9],)


def test_a_blocker_that_shipped_or_was_dropped_releases_the_step():
    """Blocked until the blocker is resolved; git's answer counts before the card's state."""
    cards = _cards(_card(2), _card(4, blocked_by=(2,)))
    assert not workable(cards[4], cards, set(), {})
    assert workable(cards[4], cards, {2}, {})
    cards[2] = _closed(2, by_tool=False, reason="NOT_PLANNED")
    assert workable(cards[4], cards, set(), {})


def test_next_filters_by_arc_and_never_skips_an_unplaced_card_silently():
    """A hand-filed step (not on the board) cannot be ordered, so it is named instead."""
    cards = _cards(_card(1, labels=("salary",)), _card(2, labels=("balance",), board_item=None),
                   _card(3, labels=("balance",)))
    answer = next_step([1, 3], cards, shipped=set(), claims={}, arc="balance")
    assert answer.card.number == 3
    assert [card.number for card in answer.unplaced] == [2]
    nothing = next_step([1], cards, shipped={1}, claims={}, arc="salary")
    assert nothing.card is None and not nothing.unplaced


def test_missing_names_every_card_a_decision_would_read():
    """Blockers, parents and children not yet read; nothing already held."""
    cards = _cards(_card(1, blocked_by=(7,), parent=8, children=(Child(2, "step", True),)),
                   _card(2))
    assert missing(cards) == {7, 8}


def test_a_stale_claim_is_old_and_its_branch_unpushed():
    """Three days and no pushed branch; either alone is not stale."""
    claims = {
        1: _claim(1, "feat/old", "2026-09-30T11:00:00Z"),
        2: _claim(2, "feat/pushed", "2026-09-30T11:00:00Z"),
        3: _claim(3, "feat/new"),
        4: Claim(4, None, "", "sha4"),
    }
    stale = stale_claims(claims, NOW, pushed=lambda branch: branch == "feat/pushed")
    assert [claim.card for claim in stale] == [1, 4]


def test_sync_closes_a_shipped_card_only_from_the_branch_its_claim_names():
    """A mistyped number must not close someone else's card (BAL-544's class)."""
    cards = _cards(_card(1), _card(2), _card(3, "finding", parent=9), _card(9))
    plan = sync_plan(cards, shipped={1, 2, 3},
                     claims={1: _claim(1, "feat/a"), 2: _claim(2, "feat/other")},
                     ship_branches={1: {"feat/a"}, 2: {"feat/a"}, 3: {"feat/a"}})
    assert plan.close == [1] and plan.release == [1]
    assert len(plan.reports) == 2
    assert "its claim names 'feat/other'" in plan.reports[0]
    assert "plan#3" in plan.reports[1] and "it has no claim" in plan.reports[1]


def test_sync_reopens_what_it_closed_when_git_takes_it_back_and_never_a_persons():
    """Its own COMPLETED close follows git; a person's close or reopen is only reported."""
    cards = _cards(_closed(1, by_tool=True), _closed(2, by_tool=False),
                   _closed(3, reason="NOT_PLANNED"), _card(4, touched_by_hand=True))
    plan = sync_plan(cards, shipped={4}, claims={4: _claim(4)}, ship_branches={4: {"feat/x"}})
    assert plan.reopen == [1]
    assert not plan.close
    assert len(plan.reports) == 1 and "a person reopened it" in plan.reports[0]


def test_sync_leaves_rulings_and_questions_alone():
    """A ruling is a record and a question the developer's: git never opens or closes them."""
    cards = _cards(_closed(1, "ruling"), _card(2, "question"))
    plan = sync_plan(cards, shipped={2}, claims={}, ship_branches={})
    assert not (plan.close or plan.reopen or plan.reports)


def test_sync_closes_a_container_whose_leaves_are_done_and_reopens_one_that_gained_work():
    """A container's state is derived from its leaves."""
    done = _card(1, children=(Child(2, "step", False),))
    regained = _closed(5, children=(Child(6, "step", True),))
    cards = _cards(done, _closed(2), regained, _card(6, parent=5))
    plan = sync_plan(cards, shipped={2}, claims={}, ship_branches={})
    assert plan.close == [1]
    assert plan.reopen == [5]


def test_sync_reports_an_open_finding_whose_owner_is_done():
    """Every open finding names a live owner; its owner shipping orphans it."""
    cards = _cards(_card(1, children=(Child(3, "finding", True),)), _card(3, "finding", parent=1),
                   _card(4, "finding"))
    plan = sync_plan(cards, shipped={1}, claims={}, ship_branches={1: set()})
    assert [report.split("'s owner")[0] for report in plan.reports[-2:]] == [
        "finding plan#3", "finding plan#4"
    ]


def test_a_ruling_or_question_is_resolved_once_closed_whoever_closed_it():
    """Review H1: git never ships either, so the tool's own close of a ruling counts, and a
    Ships naming an open question resolves nothing."""
    assert resolved(5, _cards(_closed(5, "ruling", by_tool=True)), shipped=set())
    assert resolved(5, _cards(_closed(5, "question", by_tool=False)), shipped=set())
    assert not resolved(5, _cards(_card(5, "question")), shipped={5})


def test_a_container_is_resolved_by_its_leaves_never_by_a_ships_naming_it():
    """Review H2: no commit ships a container; only its leaves, or its drop, resolve it."""
    container = _card(1, children=(Child(2, "step", True),))
    cards = _cards(container, _card(2, parent=1))
    assert not resolved(1, cards, shipped={1})
    assert resolved(1, cards, shipped={2})
    cards[1] = _closed(1, by_tool=False, children=container.children)
    assert resolved(1, cards, shipped=set())


def test_a_leaf_inherits_the_waits_and_the_drop_of_every_step_above_it():
    """R-BAL182 and R-BAL185: each is recorded once, on the step it was set on.  The drop
    is a PERSON's close of the split step: the tool's own close of one only shows its
    leaves (R-BAL190, which changed this case from the tool's close)."""
    cards = _cards(_card(2), _card(7, blocked_by=(2,), children=(Child(8, "step", True),)),
                   _card(8, parent=7, children=(Child(9, "step", True),)), _card(9, parent=8))
    assert not workable(cards[9], cards, set(), {})
    assert workable(cards[9], cards, {2}, {}) and is_live(9, cards, {2})
    cards[8] = _closed(8, by_tool=False, reason="NOT_PLANNED", parent=7,
                       children=cards[8].children)
    assert not workable(cards[9], cards, {2}, {}) and not is_live(9, cards, {2})
    assert dropped_above(cards[9], cards, {2}).number == 8
    assert dropped_above(cards[7], cards, {2}) is None


def test_leaf_placement_holds_the_split_steps_place_then_follows_its_last_leaf():
    """R-BAL179, decided apart from the board writes (review L9); GitHub adds at the bottom."""
    order = [(1, "I1"), (2, "I2"), (3, "I3")]
    on_board = _card(2, board_item="I2")
    assert leaf_placement(order, on_board, 4, _linked(on_board), set()) == Placement(
        True, "I2", "I2", "into plan#2's place")
    split = _card(2, board_item=None, children=(Child(5, "step", True), Child(4, "step", True),
                                                 Child(7, "finding", True)))
    order = [(1, "I1"), (4, "I4"), (5, "I5"), (3, "I3")]
    assert leaf_placement(order, split, 6, _linked(split), set()) == Placement(
        True, "I5", None, "to just after the leaves of plan#2 filed before it")
    off_board = _card(2, board_item=None)
    assert leaf_placement(order, off_board, 6, _linked(off_board), set()) == Placement(
        False, None, None, "no other leaf of plan#2 is on the board to place it by")


def test_missing_reads_a_containers_leaves_not_the_findings_and_rulings_it_owns():
    """Review L9: a step may own many closed rulings, and none of them decides anything."""
    cards = _cards(_card(1, children=(Child(2, "step", True), Child(3, "ruling", False),
                                      Child(4, "finding", True))))
    assert missing(cards) == {2}


def test_sync_prints_a_ships_naming_a_container_or_a_ruling_as_history_acting_on_neither():
    """R-BAL184 and review H2: a Ships naming a card no commit ships is a mistyped or stale
    number in dev's history; nothing is closed and nothing fails."""
    cards = _cards(_card(1, children=(Child(2, "step", True),)), _card(2, parent=1),
                   _closed(3, "ruling"))
    plan = sync_plan(cards, shipped={1, 3}, claims={}, ship_branches={})
    assert not (plan.close or plan.reopen or plan.release or plan.reports)
    assert [line.split(",")[0] for line in plan.history] == [
        "a Ships trailer names plan#1", "a Ships trailer names plan#3"]
    assert "a container" in plan.history[0] and "a ruling" in plan.history[1]


def test_sync_reports_steps_left_open_under_a_dropped_one_and_the_findings_they_own():
    """R-BAL185: the leaves are never offered, so a person closes or re-homes them; a finding
    owned by one has lost its live owner."""
    cards = _cards(_closed(1, by_tool=False, children=(Child(2, "step", True),)),
                   _card(2, parent=1, children=(Child(3, "finding", True),)),
                   _card(3, "finding", parent=2))
    plan = sync_plan(cards, shipped=set(), claims={}, ship_branches={})
    assert not (plan.close or plan.reopen)
    assert len(plan.reports) == 2
    assert plan.reports[0].startswith("plan#2 is open under plan#1, which was dropped")
    assert plan.reports[1].startswith("finding plan#3's owner plan#2 is no longer live")


# -- the review of checkpoint 3 -------------------------------------------------------------

def test_a_finding_off_the_board_is_never_named_as_a_step_the_order_cannot_place():
    """Review cp3 M-5: with ``kind == "step"`` deleted from ``workable``, every open finding
    (findings never sit on the board, R-BAL177) was listed NOT ON THE BOARD."""
    cards = _cards(_card(1, children=(Child(2, "finding", True),)),
                   _card(2, "finding", parent=1, board_item=None))
    answer = next_step([1], cards, shipped=set(), claims={})
    assert answer.card.number == 1 and not answer.unplaced


def test_a_container_a_person_reopened_is_reported_only_while_its_leaves_are_all_done():
    """Review cp3 L-d and M-5: a person's close of a container is a drop, so the one state of
    a person's hand its leaves contradict is a container reopened with every leaf done."""
    leaf = Child(2, "step", True)
    for leaf_card, said in ((_closed(2, parent=1), True), (_card(2, parent=1), False)):
        cards = _cards(_card(1, children=(leaf,), touched_by_hand=True), leaf_card)
        plan = sync_plan(cards, shipped={2} if said else set(), claims={}, ship_branches={})
        assert bool([r for r in plan.reports if r.startswith("container plan#1")]) is said
        assert not (plan.close or plan.drop or plan.reopen)
    for reason in ("COMPLETED", "NOT_PLANNED"):
        cards = _cards(_closed(1, by_tool=False, reason=reason, children=(leaf,)),
                       _card(2, parent=1))
        plan = sync_plan(cards, shipped=set(), claims={}, ship_branches={})
        assert not [r for r in plan.reports if r.startswith("container plan#1")]
        assert not (plan.close or plan.drop or plan.reopen)


def test_a_shipped_finding_is_not_reported_as_orphaned_by_its_owner():
    """Review cp3 M-5: with ``not in shipped`` deleted, a finding git says shipped was also
    reported for an owner that is done -- which a shipped finding no longer needs."""
    cards = _cards(_card(1, children=(Child(3, "finding", True),)), _card(3, "finding", parent=1))
    plan = sync_plan(cards, shipped={1, 3},
                     claims={1: _claim(1, "feat/a"), 3: _claim(3, "feat/a")},
                     ship_branches={1: {"feat/a"}, 3: {"feat/a"}})
    assert plan.close == [1, 3] and not plan.reports


def test_a_card_that_is_not_a_step_is_no_container_whatever_hangs_under_it():
    """Review cp3 M-4: an untyped card (or a finding) with step children was a container,
    resolved by its leaves; it is resolved by its own state."""
    for kind in (None, "finding"):
        card = _card(1, kind, children=(Child(2, "step", True),))
        cards = _cards(card, _card(2, parent=1))
        assert not is_container(card, cards, set())
        assert resolved(1, cards, shipped={1}) is (kind == "finding")
        assert not resolved(1, cards, shipped={2})


def test_a_container_whose_leaves_were_all_dropped_counts_as_dropped():
    """R-BAL187: what waits on it is released and it is shown not planned; one leaf shipped
    among dropped ones is a container done, shown completed."""
    container = _card(7, children=(Child(8, "step", False), Child(10, "step", False)),
                      board_item=None)
    cards = _cards(container, _closed(8, reason="NOT_PLANNED", parent=7),
                   _closed(10, by_tool=False, parent=7), _card(9, blocked_by=(7,)))
    assert dropped(7, cards, set()) and resolved(7, cards, set()) and not is_live(7, cards, set())
    assert next_step([9], cards, shipped=set(), claims={}).card.number == 9
    plan = sync_plan(cards, shipped=set(), claims={}, ship_branches={})
    assert (plan.drop, plan.close, plan.reopen) == ([7], [], [])
    cards[10] = _closed(10, parent=7)
    assert not dropped(7, cards, {10})
    plan = sync_plan(cards, shipped={10}, claims={}, ship_branches={})
    assert (plan.drop, plan.close) == ([], [7])


def test_a_container_shown_completed_whose_leaves_are_now_all_dropped_is_shown_not_planned():
    """R-BAL187: a leaf reopened and dropped between two syncs leaves the container closed as
    completed though nothing shipped; sync re-closes it as not planned, then leaves it."""
    linked = (Child(8, "step", False),)
    cards = _cards(_closed(7, children=linked), _closed(8, reason="NOT_PLANNED", parent=7))
    assert sync_plan(cards, shipped=set(), claims={}, ship_branches={}).drop == [7]
    cards[7] = _closed(7, reason="NOT_PLANNED", children=linked)
    plan = sync_plan(cards, shipped=set(), claims={}, ship_branches={})
    assert not (plan.drop or plan.close or plan.reopen or plan.reports)


def test_a_card_linked_outside_the_tracker_is_not_workable_and_its_blocker_holds_its_leaves():
    """R-BAL188: the card carrying the link is never offered; a step blocked by an outside
    issue waits and so does every leaf under it; an outside sub-issue holds no leaf."""
    blocker = OutsideLink("blocker", "o/code#1")
    cards = _cards(_card(1, outside=(blocker,), children=(Child(2, "step", True),)),
                   _card(2, parent=1), _card(3, outside=(OutsideLink("parent", "o/code#2"),)),
                   _card(4, outside=(OutsideLink("sub-issue", "o/code#3"),),
                         children=(Child(5, "step", True),)), _card(5, parent=4))
    assert [n for n in cards if workable(cards[n], cards, set(), {})] == [5]
    assert [line.split(",")[0] for line in outside_reports(cards)] == [
        "plan#1's blocker is o/code#1", "plan#3's parent is o/code#2",
        "plan#4's sub-issue is o/code#3"]
    cards[3] = _closed(3, outside=cards[3].outside)
    assert len(outside_reports(cards)) == 2
    assert sync_plan(cards, set(), {}, {}).reports == outside_reports(cards)


def test_sync_reports_an_open_ruling_and_tells_a_person_how_to_close_a_reopened_card():
    """Review cp3: an open ruling (a filing whose close failed) blocked what waits on it,
    unreported; "leave it" kept sync failing forever."""
    cards = _cards(_card(1, "ruling"), _card(4, touched_by_hand=True))
    plan = sync_plan(cards, shipped={4}, claims={4: _claim(4)}, ship_branches={4: {"feat/x"}})
    assert plan.reports[0].startswith("ruling plan#1 is open")
    assert "close it by hand" in plan.reports[1] and "leave it" not in plan.reports[1]


def test_a_leaf_git_says_shipped_was_not_dropped_whoever_closed_it():
    """R-BAL187 counts only DROPPED leaves: a leaf a person closed by hand that git says
    shipped is shipped work, so the container it completes is shown completed."""
    cards = _cards(_card(7, children=(Child(8, "step", False),)),
                   _closed(8, by_tool=False, parent=7))
    assert dropped(7, cards, set()) and not dropped(7, cards, {8})
    plan = sync_plan(cards, shipped={8}, claims={}, ship_branches={})
    assert (plan.close, plan.drop) == ([7], [])


def test_a_finding_whose_owner_is_outside_the_tracker_is_reported_once():
    """Review cp4 L-9: it got the outside link's report AND "owner is missing"; it has an
    owner, outside the tracker."""
    finding = _card(3, "finding", outside=(OutsideLink("parent", "o/code#9"),))
    reports = sync_plan(_cards(finding), set(), {}, {}).reports
    assert len(reports) == 1 and "outside the tracker" in reports[0]


def test_a_reopened_container_report_advises_the_remedies_the_tool_takes():
    """Review cp4 L-6, cp4b L8 and cp4c L3: it advised filing a new leaf, which the tool
    refuses under a split step whose leaves are all done, then reopening a leaf, which never
    offers one git says shipped, then a ``Reopens:`` alone, which never revives a leaf a
    person closed.  A leaf comes back by reopening it by hand when it was dropped or a person
    closed it, and by a ``Reopens:`` commit when it shipped; closing the split step by hand
    records it dropped (R-BAL190)."""
    cards = _cards(_card(1, children=(Child(2, "step", False),), touched_by_hand=True),
                   _closed(2, parent=1))
    (report,) = sync_plan(cards, {2}, {}, {}).reports
    assert "reopen by hand a leaf that was dropped or that a person closed" in report
    assert "ship 'Reopens: plan#N' for one that shipped" in report
    assert "records it dropped (R-BAL190)" in report
    assert "file a new leaf" not in report


def test_the_findings_a_split_step_owns_decide_nothing_about_its_drop():
    """Review cp4 M7: an open finding under a split step whose leaves were all dropped does
    not keep it from counting as dropped (R-BAL177: only step children are leaves)."""
    cards = _cards(_card(7, children=(Child(8, "step", False), Child(9, "finding", True))),
                   _closed(8, reason="NOT_PLANNED", parent=7), _card(9, "finding", parent=7))
    assert dropped(7, cards, set())


def test_a_ships_naming_a_dropped_container_leaves_it_dropped():
    """Review cp4 M62: git's answer counts only for work; a mistyped ``Ships:`` naming a
    dropped split step must not revive it, nor the leaves under it."""
    cards = _cards(_closed(7, by_tool=False, children=(Child(8, "step", True),)),
                   _card(8, parent=7))
    assert dropped(7, cards, {7}) and not workable(cards[8], cards, {7}, {})


def test_a_split_steps_close_by_the_tool_only_shows_its_leaves():
    """R-BAL190 (review cp4 MEDIUM-1): sync's close of a split step whose leaves were
    all dropped latched it dropped, so a leaf a person revived was never offered and a leaf
    that shipped left it "not planned".  The tool's close shows the leaves, both ways."""
    linked = (Child(8, "step", False), Child(10, "step", True))
    revived = _card(10, parent=7, touched_by_hand=True)
    cards = _cards(_closed(7, reason="NOT_PLANNED", children=linked),
                   _closed(8, reason="NOT_PLANNED", parent=7), revived, _card(11, blocked_by=(7,)))
    assert workable(cards[10], cards, set(), {}) and not workable(cards[11], cards, set(), {})
    plan = sync_plan(cards, set(), {}, {})
    assert (plan.reopen, plan.close, plan.drop, plan.reports) == ([7], [], [], [])
    cards[10] = _closed(10, parent=7)
    plan = sync_plan(cards, {10}, {}, {})
    assert (plan.reopen, plan.close, plan.drop) == ([], [7], [])
    assert workable(cards[11], cards, {10}, {})
    cards[7] = _closed(7, children=linked)
    plan = sync_plan(cards, {10}, {}, {})
    assert not (plan.reopen or plan.close or plan.drop or plan.reports)


def test_a_ships_naming_a_dropped_card_that_is_not_work_leaves_it_dropped():
    """Review cp4 M62: git's answer counts only for work, so a mistyped ``Ships:`` naming an
    untyped card a person closed (hand-linked as a parent) does not revive what is under it."""
    cards = _cards(_closed(1, None, by_tool=False, children=(Child(2, "step", True),)),
                   _card(2, parent=1))
    assert dropped_above(cards[2], cards, {1}).number == 1


def test_a_finding_with_no_parent_but_an_outside_blocker_has_lost_its_owner():
    """Review cp4b S9: only an outside PARENT is an owner outside the tracker."""
    finding = _card(3, "finding", outside=(OutsideLink("blocker", "o/code#9"),))
    reports = sync_plan(_cards(finding), set(), {}, {}).reports
    assert any("owner is missing" in line for line in reports)


def test_a_leaf_is_placed_after_its_other_leaves_never_after_itself():
    """``leaf_placement`` reads the split step's OTHER leaves on the board; a leaf already
    there is never its own anchor."""
    split = _card(2, board_item=None, children=(Child(4, "step", True), Child(5, "step", True)))
    order = [(1, "I1"), (4, "I4"), (5, "I5"), (3, "I3")]
    assert leaf_placement(order, split, 5, _linked(split), set()) == Placement(
        True, "I4", None, "to just after the leaves of plan#2 filed before it")


def test_a_leaf_with_no_earlier_leaf_on_the_board_goes_just_above_the_later_ones():
    """R-BAL202, R-BAL204: a first leaf whose link failed was no leaf yet, so a later leaf
    took the split step's place; its re-run puts it just above that leaf -- to the top when
    that leaf is first, never after itself when it sits just above it already."""
    split = _card(1, board_item=None, children=(Child(5, "step", True), Child(6, "step", True)))
    assert leaf_placement([(6, "I6"), (4, "I4"), (5, "I5")], split, 5, _linked(split),
                          set()) == Placement(
        True, None, None, "to just above the leaves of plan#1 filed after it")
    assert leaf_placement([(4, "I4"), (5, "I5"), (6, "I6")], split, 5, _linked(split),
                          set()) == Placement(
        True, "I4", None, "to just above the leaves of plan#1 filed after it")


def test_filing_order_is_the_cards_numbers_not_the_sub_issue_lists():
    """Review cp4e M1 (reordered sub-issues): a person may drag the split step's sub-issue
    list; the leaves filed before a leaf are the lower numbers, wherever the list puts them."""
    split = _card(1, board_item=None, children=(Child(2, "step", True), Child(5, "step", True),
                                                 Child(3, "step", True)))
    order = [(2, "I2"), (3, "I3"), (4, "I4"), (5, "I5")]
    assert leaf_placement(order, split, 5, _linked(split), set()).after == "I3"


def test_a_marked_card_is_an_unfinished_filing_while_open_or_a_ruling_closed_as_completed():
    """R-BAL202, R-BAL206: a ruling's filing closes it as completed before the mark comes
    off, and a person's close as completed withdraws nothing either; a ruling closed as not
    planned, by ``plan drop`` or a person, was withdrawn (review rbal202a M2), and any other
    card closed while marked was dropped: what either's filing left undone is moot."""
    marked = ("balance", FILING)
    assert filing_unfinished(_card(1, labels=marked))
    assert filing_unfinished(_closed(2, "ruling", labels=marked))
    assert not filing_unfinished(_closed(3, labels=marked, reason="NOT_PLANNED"))
    assert not filing_unfinished(_closed(4, "question", by_tool=False, labels=marked))
    assert not filing_unfinished(_card(5))
    assert not filing_unfinished(_closed(6, "ruling", labels=marked, reason="NOT_PLANNED"))
    assert filing_unfinished(_closed(7, "ruling", by_tool=False, labels=marked))
    assert not filing_unfinished(_closed(8, "ruling", by_tool=False, labels=marked,
                                         reason="NOT_PLANNED"))
    assert not filing_unfinished(_closed(9, "ruling", by_tool=False, labels=marked,
                                         reason="DUPLICATE"))


def test_a_card_whose_filing_is_unfinished_is_never_offered_and_is_reported():
    """R-BAL202: never handed out, never listed as merely off the board, and named by next
    and sync -- with ``plan drop`` offered while it is open, and ``plan show`` named for a
    filing whose command is lost."""
    cards = _cards(_card(1, labels=("balance", FILING)), _card(2, labels=("balance", FILING),
                                                               board_item=None),
                   _closed(3, "ruling", labels=("balance", FILING)))
    assert "its filing has not finished (R-BAL202)" in never_offered(cards[1], cards, set())
    answer = next_step([1], cards, set(), {})
    assert answer.card is None and not answer.unplaced
    lines = unfinished_reports(cards, set())
    assert [line.split("'")[0] for line in lines] == ["plan#1", "plan#2", "plan#3"]
    assert ", or `plan drop` it; with that command lost, `plan show plan#1` says how" in lines[0]
    assert "plan drop" not in lines[2] and "`plan show plan#3`" in lines[2]
    assert ", or, to withdraw it, reopen it and close it as not planned on the web;" in lines[2]
    assert sync_plan(cards, set(), {}, {}).reports == lines


def test_only_a_marked_leaf_is_told_its_drop_unlinks_it_and_an_open_ruling_may_be_dropped():
    """R-BAL205, R-BAL206: a marked leaf's drop unlinks it from its split step first, and
    its report says so; a marked finding under a step, and a marked step split into leaves
    of its own (plan#5, a container), are no leaf the drop unlinks; an open marked ruling
    may be dropped (withdrawn) like any open card; and a marked leaf git says shipped is
    offered no drop at all, which refuses shipped work."""
    marked = ("balance", FILING)
    cards = _cards(_card(1, children=(Child(2, "step", True), Child(3, "finding", True),
                                      Child(5, "step", True))),
                   _card(2, parent=1, labels=marked), _card(3, "finding", parent=1, labels=marked),
                   _card(4, "ruling", parent=1, labels=marked),
                   _card(5, parent=1, labels=marked, children=(Child(6, "step", True),)),
                   _card(6, parent=5))
    lines = unfinished_reports(cards, set())
    assert "(which unlinks it from plan#1 first: it was never part of that split, R-BAL205)" in (
        lines[0])
    assert not [line for line in lines[1:] if "unlinks" in line]
    assert ", or `plan drop` it;" in lines[2]
    shipped = unfinished_reports(cards, {2})
    assert "plan drop" not in shipped[0] and "unlinks" not in shipped[0]


def test_a_leaf_goes_just_above_the_topmost_of_several_leaves_filed_after_it():
    """R-BAL204's kept rule, with two later leaves: above the one highest on the board,
    wherever the other sits."""
    split = _card(1, board_item=None, children=(Child(5, "step", True), Child(6, "step", True),
                                                 Child(7, "step", True)))
    order = [(4, "I4"), (7, "I7"), (9, "I9"), (6, "I6"), (5, "I5")]
    assert leaf_placement(order, split, 5, _linked(split), set()).after == "I4"


def test_a_wait_or_a_drop_two_steps_up_holds_a_leaf_back():
    """Review cp4c S3, S4: ``never_offered`` reads every step above, not the nearest only."""
    cards = _cards(_card(1, outside=(OutsideLink("blocker", "o/code#1"),),
                         children=(Child(2, "step", True),)),
                   _card(2, parent=1, children=(Child(3, "step", True),)), _card(3, parent=2))
    assert "plan#1 above it waits on o/code#1" in never_offered(cards[3], cards, set())
    cards[1] = _closed(1, by_tool=False, children=(Child(2, "step", True),))
    assert never_offered(cards[3], cards, set()) == "plan#1 above it was dropped (R-BAL185)"


def test_an_open_ruling_is_reported_once_as_unfinished_or_as_open_by_hand():
    """R-BAL202: an open ruling still marked is an unfinished filing, its one report naming the
    command that finishes it; one with no mark (a person reopened it) is reported for a
    person to close by hand -- its filing command would write nothing."""
    cards = _cards(_card(1, "ruling", labels=("balance", FILING)), _card(2, "ruling"))
    reports = sync_plan(cards, set(), {}, {}).reports
    assert [line.split("'")[0].split(" is ")[0] for line in reports] == ["plan#1",
                                                                       "ruling plan#2"]
    assert reports[1].endswith(": close it by hand")


# -- leaf C: a leaf closed while still marked (balance:R-BAL207 applications) ------------

MARKED = ("balance", FILING)


def _split(*leaf_cards, **changes):
    """plan#1, off the board, with ``leaf_cards`` linked under it, and them."""
    split = _card(1, board_item=None, children=tuple(
        Child(leaf.number, "step", leaf.is_open) for leaf in leaf_cards), **changes)
    return _cards(split, *leaf_cards)


def test_a_leaf_closed_while_still_marked_and_unshipped_is_no_leaf():
    """It was never part of the split: plan#1 is a plain step again -- work, not dropped,
    offered when on the board -- whoever closed the leaf and however (a person, as
    completed or not planned; `plan drop`, whose unlink a read may lag)."""
    for by_tool, reason in ((False, "COMPLETED"), (False, "NOT_PLANNED"), (True, "NOT_PLANNED")):
        cards = _split(_closed(2, by_tool=by_tool, reason=reason, parent=1, labels=MARKED))
        assert not leaves(cards[1], cards, set())
        assert not is_container(cards[1], cards, set()) and is_work(cards[1], cards, set())
        assert not dropped(1, cards, set()) and not resolved(1, cards, set())
        on_board = {**cards, 1: replace(cards[1], board_item="PVTI_1")}
        assert workable(on_board[1], on_board, set(), {})
        plan = sync_plan(cards, set(), {}, {})
        assert not (plan.drop or plan.close or plan.reopen)


def test_a_step_left_with_no_leaf_is_shipped_as_work_only_by_its_own_claim():
    """No longer split, plan#1 is work: git saying it shipped closes it only through a claim
    on the branch that shipped it, as for any step -- never as a container's display."""
    cards = _split(_closed(2, by_tool=False, parent=1, labels=MARKED))
    plan = sync_plan(cards, {1}, {}, {1: {"feat/a"}})
    assert not plan.close and any("but it has no claim: not closed" in line
                                  for line in plan.reports)
    assert sync_plan(cards, {1}, {1: _claim(1, "feat/a")}, {1: {"feat/a"}}).close == [1]


def test_a_marked_leaf_counts_while_open_or_once_git_says_it_shipped():
    """Review rbal202b3 M-C: an open marked leaf is a filing under way, so its split step is
    no work meanwhile; review rbal202b2 M-B(1): one that shipped counts however it was
    closed, so its split step is done and closed as completed."""
    cards = _split(_card(2, parent=1, labels=MARKED))
    assert leaves(cards[1], cards, set()) == (2,) and not workable(cards[1], cards, set(), {})
    assert not next_step([], cards, set(), {}).unplaced
    cards = _split(_closed(2, by_tool=False, parent=1, labels=MARKED))
    assert leaves(cards[1], cards, {2}) == (2,) and resolved(1, cards, {2})
    assert sync_plan(cards, {2}, {}, {}).close == [1]


def test_a_leaf_closed_while_marked_neither_drops_nor_keeps_its_split_step():
    """R-BAL187 counts only the leaves: one dropped after its filing finished drops plan#1
    though another was closed while marked; one reopened counts again."""
    cards = _split(_closed(2, reason="NOT_PLANNED", parent=1),
                   _closed(3, by_tool=False, parent=1, labels=MARKED))
    assert leaves(cards[1], cards, set()) == (2,) and dropped(1, cards, set())
    cards[3] = _card(3, parent=1, labels=MARKED)
    assert leaves(cards[1], cards, set()) == (2, 3) and not dropped(1, cards, set())


def test_no_leaf_is_placed_by_one_closed_while_still_marked():
    """A leaf closed while marked may sit where a failed move left it; it is no leaf, so no
    anchor: the new leaf is placed by the leaves of plan#1 alone."""
    cards = _split(_closed(4, by_tool=False, parent=1, labels=MARKED), _card(5, parent=1))
    order = [(4, "I4"), (9, "I9"), (5, "I5")]
    assert leaf_placement(order, cards[1], 6, cards, set()).after == "I5"
    cards[5] = _closed(5, by_tool=False, parent=1, labels=MARKED)
    assert not leaf_placement(order, cards[1], 6, cards, set()).move


def test_a_ruling_is_withdrawn_by_any_close_but_one_as_completed():
    """R-BAL206: a ruling closed as not planned or as a duplicate was withdrawn; open, or
    closed as completed, it was not; no other kind of card is ever withdrawn."""
    assert withdrawn(_closed(1, "ruling", reason="NOT_PLANNED"))
    assert withdrawn(_closed(1, "ruling", by_tool=False, reason="DUPLICATE"))
    assert not withdrawn(_closed(1, "ruling", by_tool=False, reason="COMPLETED"))
    assert not withdrawn(_card(1, "ruling"))
    assert not withdrawn(_closed(1, "step", reason="NOT_PLANNED"))


def test_a_step_split_only_by_leaves_being_dropped_now_is_left_bare():
    """``left_bare``: a step every leaf of which is an unfinished filing dropped now carries
    its own drop; a step that keeps another leaf is left to its leaves."""
    cards = _split(_card(2, parent=1, labels=MARKED))
    assert [step.number for step in left_bare(cards[1], cards, set(), {2})] == [1]
    assert not left_bare(cards[1], cards, set(), set())
    cards = _split(_card(2, parent=1, labels=MARKED), _card(3, parent=1))
    assert not left_bare(cards[1], cards, set(), {2})
    cards = _split(_card(2, parent=1, children=(Child(3, "step", True),)))
    cards[3] = _card(3, parent=2, labels=MARKED)
    assert [step.number for step in left_bare(cards[1], cards, set(), {3})] == [2]
    assert [leaf.number for leaf in leaves_below(cards[1], cards, set())] == [3]


def test_an_open_marked_leaf_counts_however_its_own_leaves_ended():
    """Review M2 (P3): ``dropped`` holds for an OPEN container whose leaves were all
    dropped, so the rule excluded an open marked leaf split below by hand; only a CLOSED
    marked leaf is excluded."""
    cards = _split(_card(2, parent=1, labels=MARKED, children=(Child(3, "step", False),)))
    cards[3] = _closed(3, by_tool=False, reason="NOT_PLANNED", parent=2)
    assert leaves(cards[1], cards, set()) == (2,) and is_container(cards[1], cards, set())
    assert not next_step([], cards, set(), {}).unplaced


# -- leaf C2: sync undoes a split a leaf closed while still marked began (R-BAL205) -------

def test_never_split_is_every_linked_step_the_leaf_rule_leaves_out():
    """``never_split`` is ``leaves``' complement among the linked steps, one rule: a step
    closed while still marked and unshipped is in it; one open and marked (a filing under
    way), one git says shipped, and one closed after its filing finished (a dropped leaf,
    R-BAL187's) are not; a finding linked under the step is neither."""
    cards = _split(_closed(2, by_tool=False, parent=1, labels=MARKED),
                   _card(3, parent=1, labels=MARKED),
                   _closed(4, by_tool=False, parent=1, labels=MARKED),
                   _closed(5, reason="NOT_PLANNED", parent=1))
    cards[1] = replace(cards[1], children=(*cards[1].children, Child(6, "finding", True)))
    cards[6] = _card(6, "finding", parent=1, labels=MARKED)
    assert never_split(cards[1], cards, {4}) == (2,)
    assert leaves(cards[1], cards, {4}) == (3, 4, 5)


def test_a_split_step_left_with_no_leaf_goes_back_into_the_place_of_its_highest_leaf():
    """``unsplit``: an open step with no leaf left once the leaves taken out are goes back on
    the board just after the one of them highest on it (review C2 LOW 1: not the lowest
    number), else at the bottom; any other leaf keeps it split and off the board."""
    cards = _split(_closed(4, by_tool=False, parent=1, labels=MARKED, board_item=None),
                   _closed(5, by_tool=False, parent=1, labels=MARKED),
                   _closed(6, by_tool=False, parent=1, labels=MARKED))
    order = [(9, "I9"), (6, "I6"), (5, "I5")]
    assert unsplit(cards[1], (6, 5, 4), cards, set(), order) == Unsplit(
        1, (4, 5, 6), True, (6, "I6"))
    assert unsplit(cards[1], (4, 5, 6), cards, set(), [(9, "I9")]) == Unsplit(
        1, (4, 5, 6), True, None)
    cards = _split(_card(2, parent=1, labels=MARKED), _card(3, parent=1))
    assert unsplit(cards[1], (2,), cards, set(), [(2, "I2")]) == Unsplit(
        1, (2,), False, None)


def test_a_split_step_on_the_board_is_moved_only_from_below_its_leaf():
    """Review C2 M2: a run whose move failed, or whose add lost its answer, left the step on
    the board at the bottom; the next run moves it just after the leaf.  A step above the
    leaf never left its place (a leaf's own failed move leaves the leaf below it) or was
    put there by a person, and one right after it is placed: neither moves."""
    cards = _split(_closed(2, by_tool=False, parent=1, labels=MARKED))
    cards[1] = replace(cards[1], board_item="I1")
    for order, moved in (([(9, "I9"), (2, "I2"), (8, "I8"), (1, "I1")], True),
                         ([(1, "I1"), (9, "I9"), (2, "I2")], False),
                         ([(9, "I9"), (2, "I2"), (1, "I1")], False)):
        assert unsplit(cards[1], (2,), cards, set(), order) == Unsplit(
            1, (2,), False, (2, "I2") if moved else None)


def test_a_board_order_that_lags_the_steps_own_item_moves_nothing():
    """C2 delta LOW 4: the step's card says it is on the board, but the board's order, which
    lags a write, does not list it yet: nothing to compare, so no move (and no crash); a
    later run, its order caught up, moves it if it is below its leaf."""
    cards = _split(_closed(2, by_tool=False, parent=1, labels=MARKED))
    cards[1] = replace(cards[1], board_item="I1")
    assert unsplit(cards[1], (2,), cards, set(), [(2, "I2"), (9, "I9")]) == Unsplit(
        1, (2,), False, None)


def test_only_an_open_step_goes_back_on_the_board():
    """``unsplit``: a step that will not be open (no board passed: a person's close, or the
    caller's own close) and a card that is not a step are only unlinked from."""
    leaf = _closed(2, by_tool=False, parent=1, labels=MARKED)
    cards = _split(leaf)
    assert unsplit(cards[1], (2,), cards, set(), None) == Unsplit(
        1, (2,), False, None)
    finding = _cards(_card(1, "finding", board_item=None, children=(Child(2, "step", False),)),
                     leaf)
    assert unsplit(finding[1], (2,), finding, set(), [(2, "I2")]) == Unsplit(
        1, (2,), False, None)


def test_sync_unsplits_by_the_state_its_own_writes_leave_the_split_step_in():
    """Review M4, leaf C2: ``sync`` undoes every split a leaf closed while marked is still
    linked under, and boards the split step only if it will be open after the state writes
    ``sync_plan`` decided: an open one it leaves open, and a closed one it reopens (the tool
    showed it completed as a plain step git does not ship); never one it closes as shipped
    now, nor one a person closed.  Only a step's links are undone (review C2 LOW 3)."""
    leaf = _closed(2, by_tool=False, parent=1, labels=MARKED)
    order = [(2, "I2")]

    def undos(cards, shipped=frozenset(), claims=None, branches=None):
        plan = sync_plan(cards, shipped, claims or {}, branches or {})
        return plan, sync_unsplits(cards, shipped, plan, order)

    plan, found = undos(_split(leaf))
    assert found == [Unsplit(1, (2,), True, (2, "I2"))]
    assert not (plan.close or plan.drop or plan.reopen or plan.reports)
    plan, found = undos(_split(leaf, is_open=False, state_reason="COMPLETED",
                               closed_by_tool=True))
    assert plan.reopen == [1] and found == [Unsplit(1, (2,), True, (2, "I2"))]
    plan, found = undos(_split(leaf), {1}, {1: _claim(1, "feat/a")}, {1: {"feat/a"}})
    assert plan.close == [1] and found == [Unsplit(1, (2,), False, None)]
    plan, found = undos(_split(leaf, is_open=False, state_reason="NOT_PLANNED",
                               touched_by_hand=True))
    assert not plan.reopen and found == [Unsplit(1, (2,), False, None)]
    assert not undos(_split(_card(2, parent=1, labels=MARKED)))[1]
    finding = _cards(_card(1, "finding", children=(Child(2, "step", False),)), leaf)
    assert not undos(finding)[1]


def test_a_drop_unlinks_every_unfinished_leaf_it_closes_and_every_stale_link_below():
    """``drop_unlinks`` (review C2 M1): under the card dropped and every step below it, the
    leaves still being filed that the drop closes and the steps already closed while being
    filed -- below a leaf it unlinks too (C2 delta LOW 1: a hand-made link under it) --
    never a leaf that stays one, never from a card that is not a step, and an unlink only."""
    cards = _cards(_card(1, children=(Child(2, "step", True), Child(3, "step", True))),
                   _card(2, parent=1, labels=MARKED),
                   _card(3, parent=1, children=(Child(4, "step", False), Child(5, "step", True))),
                   _closed(4, by_tool=False, parent=3, labels=MARKED), _card(5, parent=3))
    assert drop_unlinks(cards[1], cards, set(), {2}) == [Unsplit(1, (2,), False, None),
                                                         Unsplit(3, (4,), False, None)]
    assert drop_unlinks(cards[3], cards, set(), set()) == [Unsplit(3, (4,), False, None)]
    cards[2] = replace(cards[2], children=(Child(6, "step", False),))
    cards[6] = _closed(6, by_tool=False, parent=2, labels=MARKED)
    assert drop_unlinks(cards[1], cards, set(), {2})[:2] == [Unsplit(1, (2,), False, None),
                                                             Unsplit(2, (6,), False, None)]
    finding = _cards(_card(1, "finding", children=(Child(4, "step", False),)),
                     _closed(4, by_tool=False, parent=1, labels=MARKED))
    assert not drop_unlinks(finding[1], finding, set(), set())
