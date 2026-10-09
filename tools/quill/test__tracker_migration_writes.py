"""The tracker's reads and writes only X-cx's migration makes (L7, Code B2a): every card's
milestone in the whole-card listing, the milestones listing and create, a create carrying a
milestone, and a ruling's close with its mark's removal in ONE write -- against scripted
GitHub answers, and on :class:`_fake.FakeTracker`, which the migration's plan is graded on.

No recording holds them: the migration's writes are graded on the fake and the scripted
answers here, and measured on the rehearsal tracker (draft 4 s.11 V3-12).  So each answer
below is the shape GitHub's REST documentation gives, and says so where it matters.
"""
from __future__ import annotations

import pytest

from tools.quill._fake import FakeTracker
from tools.quill._tracker import Board, Milestone, Tracker, TrackerError
from tools.quill.setup_tracker import FILING, PLAN, REHEARSAL

APP = "shekel-quill"


class _Rest:
    """A GitHub answering each REST call from ``answers`` by ``(method, path)``, or with
    ``echo(body)``; it keeps every call."""

    def __init__(self, answers=None, echo=None):
        """Hold the answers."""
        self.answers, self.echo, self.sent = answers or {}, echo, []

    def rest(self, method, path, body=None):
        """Keep the call; answer it."""
        self.sent.append((method, path, body))
        if (method, path) in self.answers:
            return self.answers[method, path]
        return self.echo(body)


def _tracker(github, place=PLAN):
    """A tracker on ``place`` speaking through ``github``."""
    return Tracker(github, Board(github, place, "B"), APP)


def _filed(body, milestone="as sent"):
    """GitHub's answer to a create: the type, labels and milestone sent (``milestone``
    otherwise, a number or None)."""
    held = body.get("milestone") if milestone == "as sent" else milestone
    return {"number": 31, "type": {"name": body["type"]},
            "labels": [{"name": name} for name in body["labels"]],
            "milestone": None if held is None else {"number": held, "title": "t"}}


# -- a create carrying a milestone --------------------------------------------

def test_a_create_without_a_milestone_sends_what_it_always_sent():
    """Every quill command's create, and the recordings' replays of it, send exactly
    ``title``, ``body``, ``type`` and ``labels``: no ``milestone`` key at all."""
    github = _Rest(echo=_filed)
    assert _tracker(github).create("step", "t", "b", ["balance"]) == 31
    assert set(github.sent[0][2]) == {"title", "body", "type", "labels"}


def test_a_create_carrying_a_milestone_sends_it_and_reads_it_back():
    """The migration's scope steps are filed in their outcome's milestone (R-BAL243) in
    the create itself, and the answer must show it there."""
    github = _Rest(echo=_filed)
    assert _tracker(github).create("step", "t", "b", ["balance"], milestone=3) == 31
    assert github.sent[0][2]["milestone"] == 3


@pytest.mark.parametrize("held, sent", [(None, 3), (4, 3), (3, None)],
                         ids=["dropped", "another", "unasked"])
def test_a_milestone_the_answer_does_not_show_as_sent_is_refused(held, sent):
    """GitHub drops a field its writer may not set without saying so (as it drops a type),
    so a milestone missing, another one, or one nobody asked for is refused."""
    github = _Rest(echo=lambda body: _filed(body, held))
    with pytest.raises(TrackerError, match=f"filed in milestone {held}, not {sent}"):
        _tracker(github).create("step", "t", "b", ["balance"], milestone=sent)


# -- milestones ----------------------------------------------------------------

def _milestone(number, title, description="d"):
    """One milestone as GitHub's REST listing answers it."""
    return {"number": number, "title": title, "description": description, "state": "open"}


def test_every_milestone_is_read_by_title_page_by_page_open_and_closed():
    """A full first page asks for a second; a short one ends the read; a milestone with no
    description reads as an empty one."""
    path = f"{PLAN.path}/milestones?state=all&per_page=100"
    first = [_milestone(n, f"M{n}") for n in range(1, 101)]
    github = _Rest({("GET", path): first,
                    ("GET", path + "&page=2"): [_milestone(101, "The flip", None)]})
    found = _tracker(github).milestones()
    assert len(found) == 101 and found["The flip"] == Milestone(101, "The flip", "")
    assert [sent[1] for sent in github.sent] == [path, path + "&page=2"]


def test_two_milestones_of_one_title_are_refused():
    """The migration finds an outcome's milestone by its title, so two of one title would
    make the one it finds a guess."""
    github = _Rest({("GET", f"{PLAN.path}/milestones?state=all&per_page=100"): [
        _milestone(1, "The flip"), _milestone(2, "The flip")]})
    with pytest.raises(TrackerError, match="two milestones .* titled 'The flip': #1 and #2"):
        _tracker(github).milestones()


def test_a_milestone_is_made_with_its_title_and_description():
    """One POST, its number from the answer."""
    github = _Rest(echo=lambda body: {"number": 7, **body})
    assert _tracker(github).create_milestone("The flip", "Scope.") == 7
    assert github.sent == [("POST", f"{PLAN.path}/milestones",
                            {"title": "The flip", "description": "Scope."})]


# -- a ruling's close and unmark, one write -------------------------------------

def _patched(body, state="closed", reason="completed"):
    """GitHub's answer to an issue PATCH: the state and reason given, the labels sent."""
    return {"number": 5, "state": state, "state_reason": reason,
            "labels": [{"name": name} for name in body["labels"]]}


def test_a_close_and_unmark_is_one_patch_setting_the_labels_without_the_mark():
    """The ruling's labels as read, the mark among them, are sent back without it, with the
    close, in one PATCH (draft 4 s.11 V3-8)."""
    github = _Rest(echo=_patched)
    _tracker(github).close_unmarked(5, ["balance", FILING])
    assert github.sent == [("PATCH", f"{PLAN.path}/issues/5", {
        "state": "closed", "state_reason": "completed", "labels": ["balance"]})]


@pytest.mark.parametrize("answer", [
    lambda body: _patched(body, state="open"),
    lambda body: _patched(body, reason="not_planned"),
    lambda body: {**_patched(body), "labels": [{"name": "balance"}, {"name": FILING}]},
], ids=["still-open", "another-reason", "mark-kept"])
def test_a_close_and_unmark_the_answer_does_not_show_is_refused(answer):
    """The answer is read back: open, closed for another reason, or still marked."""
    with pytest.raises(TrackerError, match="after its close and unmark"):
        _tracker(_Rest(echo=answer)).close_unmarked(5, ["balance", FILING])


def test_the_migrations_writes_to_a_rehearsal_tracker_go_to_the_rehearsal_repository():
    """As every other write (``test__tracker_place.py``): the milestones' read and create,
    a create in a milestone and the close with its unmark, on the rehearsal's path only."""
    github = _Rest({("GET", f"{REHEARSAL.path}/milestones?state=all&per_page=100"): []},
                   echo=lambda body: {**_filed(body), **_patched(body)}
                   if "type" in body else {"number": 1} if "title" in body
                   else _patched(body))
    tracker = _tracker(github, REHEARSAL)
    tracker.milestones()
    tracker.create_milestone("t", "d")
    tracker.create("step", "t", "b", ["balance"], milestone=1)
    tracker.close_unmarked(5, ["balance"])
    assert [path for _, path, _ in github.sent] == [
        f"{REHEARSAL.path}/milestones?state=all&per_page=100", f"{REHEARSAL.path}/milestones",
        f"{REHEARSAL.path}/issues", f"{REHEARSAL.path}/issues/5"]


# -- the fake ------------------------------------------------------------------

def test_the_fake_files_a_card_in_a_milestone_and_lists_it_whole():
    """What the plan reads back: the milestone by title, the card's milestone by number."""
    tracker = FakeTracker()
    number = tracker.create_milestone("The flip", "Scope.")
    card = tracker.create("step", "[A-1] t", "b", ["balance"], milestone=number)
    assert tracker.milestones() == {"The flip": Milestone(1, "The flip", "Scope.")}
    assert tracker.all_cards()[card].milestone == number
    assert tracker.writes[-1] == ("create", card, "step", "[A-1] t", ("balance", FILING), number)


def test_the_fake_refuses_a_create_in_a_milestone_it_does_not_hold():
    """A write the tool must never send fails loudly rather than guess GitHub's answer."""
    with pytest.raises(AssertionError, match="no milestone #4"):
        FakeTracker().create("step", "t", "b", ["balance"], milestone=4)


def test_the_fake_closes_and_unmarks_in_one_write():
    """Closed as completed by the tool, the mark gone, one write logged."""
    tracker = FakeTracker()
    number = tracker.create("ruling", "[R-A] t", "b", ["balance"])
    tracker.close_unmarked(number, tracker.cards([number])[number].labels)
    card = tracker.cards([number])[number]
    assert (card.is_open, card.state_reason, card.labels) == (False, "COMPLETED", ("balance",))
    assert tracker.writes[-1] == ("close_unmarked", number, ("balance",))


def test_the_fake_refuses_two_milestones_of_one_title_as_the_tracker_does():
    """Its milestones read as :meth:`_tracker.Tracker.milestones` reads them (review of B2a,
    L5)."""
    tracker = FakeTracker()
    tracker.create_milestone("The flip", "a")
    tracker.create_milestone("The flip", "b")
    with pytest.raises(TrackerError, match="two milestones share a title"):
        tracker.milestones()
