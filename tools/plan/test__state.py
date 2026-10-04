"""The plan's decisions -- next, resolved, stale claims, sync -- over hand-built cards."""
from __future__ import annotations

from datetime import UTC, datetime

from _state import (
    Placement,
    dropped,
    dropped_above,
    is_live,
    leaf_placement,
    missing,
    never_offered,
    next_step,
    outside_reports,
    resolved,
    stale_claims,
    sync_plan,
    workable,
)
from _tracker import Card, Child, Claim, OutsideLink

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
    assert parent.is_container
    assert not resolved(1, cards, shipped={2})
    assert resolved(1, cards, shipped={2, 3})
    owner = _card(6, children=(Child(4, "finding", True), Child(7, "ruling", False)))
    assert not owner.is_container


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
    assert leaf_placement(order, _card(2, board_item="I2"), 4) == Placement(
        "I2", "I2", "into plan#2's place")
    split = _card(2, board_item=None, children=(Child(5, "step", True), Child(4, "step", True),
                                                 Child(7, "finding", True)))
    order = [(1, "I1"), (4, "I4"), (5, "I5"), (3, "I3")]
    assert leaf_placement(order, split, 6) == Placement(
        "I5", None, "to just after plan#2's other leaves")
    assert leaf_placement(order, _card(2, board_item=None), 6) == Placement(
        None, None, "at the bottom")


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
        assert not card.is_container
        cards = _cards(card, _card(2, parent=1))
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
    leaves = (Child(8, "step", False),)
    cards = _cards(_closed(7, children=leaves), _closed(8, reason="NOT_PLANNED", parent=7))
    assert sync_plan(cards, shipped=set(), claims={}, ship_branches={}).drop == [7]
    cards[7] = _closed(7, reason="NOT_PLANNED", children=leaves)
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
    leaves = (Child(8, "step", False), Child(10, "step", True))
    revived = _card(10, parent=7, touched_by_hand=True)
    cards = _cards(_closed(7, reason="NOT_PLANNED", children=leaves),
                   _closed(8, reason="NOT_PLANNED", parent=7), revived, _card(11, blocked_by=(7,)))
    assert workable(cards[10], cards, set(), {}) and not workable(cards[11], cards, set(), {})
    plan = sync_plan(cards, set(), {}, {})
    assert (plan.reopen, plan.close, plan.drop, plan.reports) == ([7], [], [], [])
    cards[10] = _closed(10, parent=7)
    plan = sync_plan(cards, {10}, {}, {})
    assert (plan.reopen, plan.close, plan.drop) == ([], [7], [])
    assert workable(cards[11], cards, {10}, {})
    cards[7] = _closed(7, children=leaves)
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
    assert leaf_placement(order, split, 5) == Placement(
        "I4", None, "to just after plan#2's other leaves")


def test_a_wait_or_a_drop_two_steps_up_holds_a_leaf_back():
    """Review cp4c S3, S4: ``never_offered`` reads every step above, not the nearest only."""
    cards = _cards(_card(1, outside=(OutsideLink("blocker", "o/code#1"),),
                         children=(Child(2, "step", True),)),
                   _card(2, parent=1, children=(Child(3, "step", True),)), _card(3, parent=2))
    assert "plan#1 above it waits on o/code#1" in never_offered(cards[3], cards, set())
    cards[1] = _closed(1, by_tool=False, children=(Child(2, "step", True),))
    assert never_offered(cards[3], cards, set()) == "plan#1 above it was dropped (R-BAL185)"
