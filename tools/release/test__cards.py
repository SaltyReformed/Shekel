"""A release after the cutover, judged by the tracker's cards (rulings ``balance:R-BAL287``
- ``R-BAL289``): over throwaway repositories under tmp_path and quill's own in-memory
tracker.  No network, no docker, never a real checkout."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill.door import FakeTracker, GitHubError
from tools.quill.quill import CONNECT
from tools.release import release
from tools.release._carried import Declaration
from tools.release._model import STEPS_PATH, ReleaseError
from tools.release._plan import CLEAN, REFUSED, Inputs, plan
from tools.release.conftest import (
    MONEY,
    after_cutover,
    judge,
    pull_request,
    released_group,
    replan,
)


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


# -- the era and the money flag -------------------------------------------------------------

def test_a_cut_after_the_cutover_is_judged_by_its_cards_labels(repo):
    """The placeholder this replaces refused such a cut as unbuilt (L9a-1b); it is now
    judged: each card's flag is its moves-money label as the tracker holds it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40", "Ships: plan#41")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41)
    verdict = judge(repo, base, cut, tracker).verdict
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
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")
    with pytest.raises(ReleaseError, match="no way to reach the tracker was given"):
        plan(repo.root, base, cut)


def test_two_money_cards_refuse_and_the_cut_with_one_is_offered(repo):
    """Two money movers on dev's line: refused, and the cut after the first offered.  The
    tracker is reached ONCE: the offered cut is judged with the cut's read."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, "Ships: plan#40")
    cut = pull_request(repo, first, "Ships: plan#41")
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
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    assert judge(repo, base, cut, tracker, ["plan#40"]).verdict.refusals == (
        "plan#40 moves money and has no rehearsal kit",)


# -- an undo (R-BAL288) ----------------------------------------------------------------------

def test_an_undone_money_card_counts_as_a_money_change(repo):
    """R-BAL288's example: #40 (money) released, then reverted; the release carrying the
    revert and #41 (money) carries two money changes."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, "Ships: plan#40")
    revert = pull_request(repo, base, "Reopens: plan#40")
    cut = pull_request(repo, revert, "Ships: plan#41")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True), ("plan#41", False)]
    assert verdict.status == REFUSED
    assert verdict.refusals[0].startswith("it carries 2 steps that move money (plan#40, ")


def test_a_card_shipped_again_after_its_undo_is_carried_and_not_undone(repo):
    """A ship, its revert and a ship again inside one window: production holds it."""
    base, cutover = after_cutover(repo)
    ship = pull_request(repo, cutover, "Ships: plan#40")
    revert = pull_request(repo, ship, "Reopens: plan#40")
    cut = pull_request(repo, revert, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    (step,) = judge(repo, base, cut, tracker).verdict.carried.steps
    assert (step.money, step.undone, len(step.claims)) == (True, False, 3)


# -- what the card reader cannot read --------------------------------------------------------

def test_an_old_form_claim_after_the_cutover_names_the_cut_to_release_first(repo):
    """R-BAL287: a step ticked before the cutover and not released by its freeze has no
    card; only a cut holding steps.md can judge it, and the refusal names the NEWEST such
    cut holding the claim (two hold steps.md after it here)."""
    base = repo.plan((("balance:A-1", "Do it.", "#1", "--"),), parents=())
    tick = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),), parents=(base,),
                     ships=("balance:A-1",))
    later = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),
                       ("balance:A-2", "Do more.", "#1", "--")), parents=(tick,))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(later,))
    cut = pull_request(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"{tick[:12]} claims `balance:A-1` in the old form, which only a cut holding steps.md "
        "can judge (R-BAL287: the cutover's freeze ends with a release of every one): "
        f"release {later[:12]} first, with the release tool as that cut holds it",)


def test_an_old_form_claim_merged_after_the_cutover_names_no_cut(repo):
    """A claim on a branch merged after the cutover: the cut before the cutover holds
    steps.md but not the claim, so releasing it could not carry it; none is named."""
    base = repo.plan((("balance:A-1", "Do it.", "#1", "--"),), parents=())
    before = repo.plan((("balance:A-1", "Do it.", "#1", "--"),
                        ("balance:A-2", "Do more.", "#2", "--")), parents=(base,))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(before,))
    late = repo.commit("late", {"app.py": "1"}, ships=("balance:A-1",), parents=(base,))
    # The merge keeps the cutover's deletion, as git's does (the branch never touched it).
    cut = repo.commit("Merge pull request", {"app.py": "1"}, parents=(cutover, late))
    (refusal,) = judge(repo, base, cut, FakeTracker()).verdict.refusals
    assert refusal.startswith(f"{late[:12]} claims `balance:A-1` in the old form")
    assert refusal.endswith("no cut on dev's line in this window holds steps.md and this "
                            "commit, so no release can judge it: it was merged after the "
                            "cutover")


def test_an_old_form_reopens_after_the_cutover_is_refused(repo):
    """An undo of a step with no card: no reader judges it, so it never passes unseen."""
    base, cutover = after_cutover(repo)
    undo = repo.commit("revert", {"app.py": "1"}, trailers=("Reopens: balance:A-1",
                                                            "Ships: plan#41"),
                       parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(41, labels=MONEY)
    assert judge(repo, base, undo, tracker, ["plan#41"]).verdict.refusals[0] == (
        f"{undo[:12]} carries `Reopens: balance:A-1` in the old form: it undoes a step that "
        "has no card, so no reader judges what it takes out of production (R-BAL287, "
        "R-BAL288)")


@pytest.mark.parametrize(("kind", "what"), [("ruling", "a ruling"), ("question", "a question")])
def test_a_trailer_naming_a_card_that_is_no_work_is_refused(repo, kind, what):
    """A ruling or a question is never shipped; read as work it would pass as moving no
    money."""
    base, cutover = after_cutover(repo)
    typo = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#12",),
                       parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=kind)
    verdict = judge(repo, base, typo, tracker).verdict
    assert not verdict.carried.steps
    assert verdict.refusals == (
        f"plan#12 (named at {typo[:12]}) is {what}, which no commit ships, so it names no "
        "work whose flag can be read",)


def test_a_step_reverted_and_split_since_is_judged_by_its_own_label(repo):
    """The delta review's case: #60 (money) released, reverted, then split into #61 and
    #62.  Its trailer was valid when it landed; the code it names was #60's own, so #60's
    own label judges it: an undone money change, never a block on every later release."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, "Ships: plan#60")
    cut = pull_request(repo, base, "Reopens: plan#60")
    tracker = FakeTracker()
    tracker.add(60, labels=MONEY)
    for leaf in (61, 62):
        tracker.add(leaf)
        tracker.add_child(60, tracker.cards_by_number[leaf])
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#60", True, True)]
    assert verdict.status == CLEAN


# -- a card's own money label (the coordinator, under R-BAL207, 2026-10-14) ------------------

def test_a_step_undone_and_replanned_into_a_money_leaf_is_one_money_change(repo):
    """The full-leaf review's E2: #51 (no money label) undone, then re-planned as #511
    (moves money) and #512, both shipped.  #51 is judged by its own label, so the release
    carries one money change, #511's; reading #511's label onto #51 too counted two and
    refused every release from then on."""
    tracker, base = released_group(repo)
    undo = pull_request(repo, base, "Reopens: plan#51")
    cut = pull_request(repo, undo, "Ships: plan#511", "Ships: plan#512")
    replan(tracker, 51, (511, 512))
    tracker.cards_by_number[511] = replace(tracker.cards_by_number[511], labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#51", False, True), ("plan#511", True, False), ("plan#512", False, False)]
    assert verdict.status == CLEAN


def test_a_container_of_no_type_is_refused(repo):
    """A step split into leaves whose type a person cleared: its kind unread, it is
    refused like any card of no type, never read as moving no money."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#12",),
                      parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=None)
    replan(tracker, 12, (121, 122))
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"plan#12 (named at {cut[:12]}) is a card of no type, which no commit ships, so it "
        "names no work whose flag can be read",)


def test_a_card_of_no_type_is_refused(repo):
    """quill never clears a card's type (its one retype makes a question a ruling), so a
    card of no type is a person's doing; its kind unread, it is refused, never read as
    moving no money."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#12",),
                      parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=None)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"plan#12 (named at {cut[:12]}) is a card of no type, which no commit ships, so it "
        "names no work whose flag can be read",)


def test_a_malformed_value_after_the_cutover_is_refused(repo):
    """A value of neither form claims nothing readable."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#4a",),
                      parents=(cutover,))
    assert judge(repo, base, cut, FakeTracker()).verdict.refusals == (
        f"{cut[:12]} Ships: 'plan#4a' is not plan#<number>",)


def test_a_trailer_naming_a_card_the_tracker_does_not_hold_is_refused(repo):
    """R-BAL289: no declaration fills it after the cutover."""
    base, cutover = after_cutover(repo)
    typo = repo.commit("work", {"app.py": "1"}, trailers=("Ships: plan#999",),
                       parents=(cutover,))
    verdict = judge(repo, base, typo, FakeTracker()).verdict
    assert not verdict.carried.steps
    assert verdict.refusals == (
        f"plan#999 (named at {typo[:12]}) is a card the tracker does not hold, so whether "
        "it moves money cannot be read (R-BAL289)",)


def test_a_declaration_on_a_cut_after_the_cutover_judges_nothing(repo):
    """Every card's flag is its label, so a declaration fills nothing (R-BAL287)."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    with pytest.raises(ReleaseError, match="declare plan#40: names no step"):
        plan(repo.root, base, cut,
             Inputs((), [Declaration("plan#40", False, "x")], lambda: tracker))


# -- the command line ------------------------------------------------------------------------

def test_plan_prints_an_undone_card(repo, capsys):
    """The report names an undo beside its flag."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, "Ships: plan#40")
    cut = pull_request(repo, base, "Reopens: plan#40")
    tracker = FakeTracker()
    tracker.add(40)
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == CLEAN
    out = capsys.readouterr().out.splitlines()
    assert out[3].startswith("  plan#40  moves money: no  UNDONE: a Reopens: leaves it out of "
                             "production  (claimed at ")


def test_plan_given_no_connection_exits_2_on_a_cut_after_the_cutover(repo, capsys):
    """The command line's caller reaches the live tracker only by passing it (``python -m``
    does); a caller passing none judges nothing."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root) == 2
    assert "no way to reach the tracker was given" in capsys.readouterr().err


def test_plan_exits_2_when_the_tracker_cannot_be_read(repo, capsys):
    """A failed read of the tracker judges nothing; it never reads as no money."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")

    def refused():
        raise GitHubError(401, "GET /app -> 401")

    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=refused) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: ")


def test_plan_exits_2_when_the_apps_key_cannot_be_used(repo, capsys, monkeypatch):
    """L9d: a corrupt app.pem, read by the command line's own connection, escaped ``main``
    as a traceback with exit 1, which reads as refused; it judges nothing."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, "Ships: plan#40")
    monkeypatch.setattr("tools.quill._tracker.app_credentials",
                        lambda: ("Iv23client", b"not a pem"))
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=CONNECT) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: the App's private key "
                                   "cannot be loaded")
