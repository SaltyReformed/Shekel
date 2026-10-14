"""A release after the cutover, judged by the tracker's cards (rulings ``balance:R-BAL287``
- ``R-BAL289``): over throwaway repositories under tmp_path and quill's own in-memory
tracker.  No network, no docker, never a real checkout."""
from __future__ import annotations

import pytest

from tools.quill.door import FakeTracker, GitHubError
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release import release
from tools.release._carried import Declaration
from tools.release._model import STEPS_PATH, ReleaseError
from tools.release._plan import CLEAN, REFUSED, Inputs, plan

MONEY = ("balance", MOVES_MONEY_LABEL)


class Reached:
    """A tracker connection that counts each time it is made."""

    def __init__(self, tracker: FakeTracker) -> None:
        """Connect to ``tracker``."""
        self.tracker = tracker
        self.times = 0

    def __call__(self) -> FakeTracker:
        """The tracker, counted."""
        self.times += 1
        return self.tracker


def _never() -> FakeTracker:
    """A connection a test says is never made."""
    raise AssertionError("the tracker was reached")


def _after_cutover(repo):
    """A base holding steps.md, then the cutover's commit deleting it: ``(base, cutover)``."""
    base = repo.plan((), parents=())
    return base, repo.commit("cutover", {STEPS_PATH: None}, parents=(base,))


def _merge(repo, onto, *lines, files=None):
    """A pull request built on ``onto`` carrying the trailer ``lines``, merged into it as
    ``dev`` takes one: the merge."""
    work = repo.commit(f"work {' '.join(lines)}", files or {"app.py": " ".join(lines)},
                       trailers=lines, parents=(onto,))
    return repo.merge(onto, work, f"Merge pull request ({' '.join(lines)})")


def _judge(repo, base, cut, tracker, unrehearsed=()):
    """``plan`` over ``<base>..<cut>``, reaching ``tracker``."""
    return plan(repo.root, base, cut, Inputs(unrehearsed, (), lambda: tracker))


def _group(tracker, container=50, leaves=(51, 52)):
    """A container labelled deploy-together and its leaves, linked under it."""
    tracker.add(container, labels=("balance", DEPLOY_TOGETHER_LABEL))
    for leaf in leaves:
        tracker.add(leaf)
        tracker.add_child(container, tracker.cards_by_number[leaf])


# -- the era and the money flag -------------------------------------------------------------

def test_a_cut_after_the_cutover_is_judged_by_its_cards_labels(repo):
    """The placeholder this replaces refused such a cut as unbuilt (L9a-1b); it is now
    judged: each card's flag is its moves-money label as the tracker holds it."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#40", "Ships: plan#41")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41)
    verdict = _judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True, False), ("plan#41", False, False)]
    assert verdict.status == CLEAN


def test_a_cut_before_the_cutover_never_reaches_the_tracker(repo):
    """The 4d release is before the cutover: its reader is the registries', offline."""
    base = repo.plan((), parents=())
    cut = repo.commit("work", {"app.py": "1"}, parents=(base,))
    assert plan(repo.root, base, cut, Inputs(connect=_never)).verdict.status == CLEAN


def test_a_cut_after_the_cutover_with_no_tracker_given_judges_nothing(repo):
    """Only the command line reaches the live tracker; a caller giving no connection is
    stopped, never judged as carrying nothing."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#40")
    with pytest.raises(ReleaseError, match="no way to reach the tracker was given"):
        plan(repo.root, base, cut)


def test_two_money_cards_refuse_and_the_cut_with_one_is_offered(repo):
    """Two money movers on dev's line: refused, and the cut after the first offered.  The
    tracker is reached ONCE: the offered cut is judged with the cut's read."""
    base, cutover = _after_cutover(repo)
    first = _merge(repo, cutover, "Ships: plan#40")
    cut = _merge(repo, first, "Ships: plan#41")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    reached = Reached(tracker)
    judged = plan(repo.root, base, cut, Inputs(connect=reached))
    assert judged.verdict.refusals == (
        "it carries 2 steps that move money (plan#40, plan#41); a release carries one at most",)
    assert judged.passing.cut == first
    assert reached.times == 1


def test_an_unrehearsed_money_card_is_refused(repo):
    """R-BAL281's interim names a card the same way it names a step."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    assert _judge(repo, base, cut, tracker, ["plan#40"]).verdict.refusals == (
        "plan#40 moves money and has no rehearsal kit",)


# -- an undo (R-BAL288) ----------------------------------------------------------------------

def test_an_undone_money_card_counts_as_a_money_change(repo):
    """R-BAL288's example: #40 (money) released, then reverted; the release carrying the
    revert and #41 (money) carries two money changes."""
    _, cutover = _after_cutover(repo)
    base = _merge(repo, cutover, "Ships: plan#40")
    revert = _merge(repo, base, "Reopens: plan#40")
    cut = _merge(repo, revert, "Ships: plan#41")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    verdict = _judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True), ("plan#41", False)]
    assert verdict.status == REFUSED
    assert verdict.refusals[0].startswith("it carries 2 steps that move money (plan#40, ")


def test_a_card_shipped_again_after_its_undo_is_carried_and_not_undone(repo):
    """A ship, its revert and a ship again inside one window: production holds it."""
    base, cutover = _after_cutover(repo)
    ship = _merge(repo, cutover, "Ships: plan#40")
    revert = _merge(repo, ship, "Reopens: plan#40")
    cut = _merge(repo, revert, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    (step,) = _judge(repo, base, cut, tracker).verdict.carried.steps
    assert (step.money, step.undone, len(step.claims)) == (True, False, 3)


# -- the deploy-together group (R-BAL288: wholly in production or wholly out) ----------------

def test_a_group_shipped_in_part_is_refused_naming_the_missing_leaf(repo):
    """#51 without #52: production would hold part of the group."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#51")
    tracker = FakeTracker()
    _group(tracker)
    verdict = _judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.key, group.carried, group.released, group.missing, group.undone) == (
        "plan#50", ("plan#51",), (), ("plan#52",), ())
    assert verdict.refusals == (
        "it carries part of the deploy-together group plan#50 (plan#51) without plan#52; "
        "the group ships whole",)
    assert verdict.carried.steps[0].groups == ("plan#50",)


def test_a_group_shipped_whole_with_a_released_leaf_is_clean(repo):
    """#51 already in production, #52 shipped now: the group is whole."""
    _, cutover = _after_cutover(repo)
    base = _merge(repo, cutover, "Ships: plan#51")
    cut = _merge(repo, base, "Ships: plan#52")
    tracker = FakeTracker()
    _group(tracker)
    verdict = _judge(repo, base, cut, tracker).verdict
    assert verdict.carried.groups[0].released == ("plan#51",)
    assert verdict.status == CLEAN


def test_undoing_one_leaf_of_a_released_group_is_refused(repo):
    """Both leaves in production; reverting #51 alone leaves #52 without it."""
    _, cutover = _after_cutover(repo)
    base = _merge(repo, cutover, "Ships: plan#51", "Ships: plan#52")
    cut = _merge(repo, base, "Reopens: plan#51")
    tracker = FakeTracker()
    _group(tracker)
    assert _judge(repo, base, cut, tracker).verdict.refusals == (
        "it takes plan#51 of the deploy-together group plan#50 out of production and keeps "
        "plan#52; the group ships whole",)


def test_undoing_a_whole_group_is_clean(repo):
    """Reverting every leaf takes the group out whole."""
    _, cutover = _after_cutover(repo)
    base = _merge(repo, cutover, "Ships: plan#51", "Ships: plan#52")
    cut = _merge(repo, base, "Reopens: plan#51", "Reopens: plan#52")
    tracker = FakeTracker()
    _group(tracker)
    verdict = _judge(repo, base, cut, tracker).verdict
    assert verdict.carried.groups[0].undone == ("plan#51", "plan#52")
    assert verdict.status == CLEAN


def test_a_dropped_leaf_is_no_part_of_the_group(repo):
    """A leaf closed as not planned never ships, so the group is whole without it."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#51", "Ships: plan#52")
    tracker = FakeTracker()
    _group(tracker, leaves=(51, 52, 53))
    tracker.close(53, "not_planned")
    assert _judge(repo, base, cut, tracker).verdict.carried.groups[0].missing == ()


def test_a_leaf_split_again_counts_as_its_own_leaves(repo):
    """#52 split into #521 and #522: a release without #522 names #522 alone."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#51", "Ships: plan#521")
    tracker = FakeTracker()
    _group(tracker)
    for leaf in (521, 522):
        tracker.add(leaf)
        tracker.add_child(52, tracker.cards_by_number[leaf])
    assert _judge(repo, base, cut, tracker).verdict.carried.groups[0].missing == ("plan#522",)


# -- what the card reader cannot read --------------------------------------------------------

def test_an_old_form_claim_after_the_cutover_names_the_cut_to_release_first(repo):
    """R-BAL287: a step ticked before the cutover and not released by its freeze has no
    card; only a cut holding steps.md can judge it, and the refusal names the newest."""
    base = repo.plan((("balance:A-1", "Do it.", "#1", "--"),), parents=())
    tick = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),), parents=(base,),
                     ships=("balance:A-1",))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(tick,))
    cut = _merge(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    assert _judge(repo, base, cut, tracker).verdict.refusals == (
        f"{tick[:12]} claims `balance:A-1` in the old form, which only a cut holding steps.md "
        "can judge (R-BAL287: the cutover's freeze ends with a release of every one): "
        f"release {tick[:12]} first, with the release tool as that cut holds it",)


def test_a_trailer_naming_a_card_the_tracker_does_not_hold_is_refused(repo):
    """R-BAL289: no declaration fills it after the cutover."""
    base, cutover = _after_cutover(repo)
    typo = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#999",),
                       parents=(cutover,))
    verdict = _judge(repo, base, typo, FakeTracker()).verdict
    assert not verdict.carried.steps
    assert verdict.refusals == (
        f"plan#999 (named at {typo[:12]}) is a card the tracker does not hold, so whether "
        "it moves money cannot be read (R-BAL289)",)


def test_a_declaration_on_a_cut_after_the_cutover_judges_nothing(repo):
    """Every card's flag is its label, so a declaration fills nothing (R-BAL287)."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    with pytest.raises(ReleaseError, match="declare plan#40: names no step"):
        plan(repo.root, base, cut,
             Inputs((), [Declaration("plan#40", False, "x")], lambda: tracker))


# -- the command line ------------------------------------------------------------------------

def test_plan_prints_an_undone_card(repo, capsys):
    """The report names an undo beside its flag."""
    _, cutover = _after_cutover(repo)
    base = _merge(repo, cutover, "Ships: plan#40")
    cut = _merge(repo, base, "Reopens: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == CLEAN
    out = capsys.readouterr().out.splitlines()
    assert out[3].startswith("  plan#40  moves money: no  UNDONE: a Reopens: takes it out of "
                             "production  (claimed at ")


def test_plan_exits_2_when_the_tracker_cannot_be_read(repo, capsys):
    """A failed read of the tracker judges nothing; it never reads as no money."""
    base, cutover = _after_cutover(repo)
    cut = _merge(repo, cutover, "Ships: plan#40")

    def refused():
        raise GitHubError(401, "GET /app -> 401")

    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=refused) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: ")
