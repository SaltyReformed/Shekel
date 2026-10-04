"""The tracker's reads and writes, against GitHub answers RECORDED from the live tracker.

``recorded/tracker.json`` was kept 2026-10-04 (re-recorded 10:18 EDT for
checkpoint 3) by a session that ran every :class:`_tracker.Tracker` and
:class:`_tracker.Board` call once against scratch cards #11-#15
(``_recorded.Recorder``, which redacts every other card's text); these tests
replay it (``_recorded.Replay``), which answers only a request it recorded, in
the order recorded.  Nothing here calls GitHub.  A query whose text changes no
longer matches its recording: re-record it, never edit an answer by hand.  A
test that needs an answer no scratch card can give (a link to another
repository, an open pull request) starts from a recorded answer and changes the
one field it is about, and says so.
"""
from __future__ import annotations

import copy
import json

import pytest

from _github import GitHub, GitHubError
from _recorded import REDACTED, RECORDINGS, SCRATCH, Recorder, Replay, redacted
from _tracker import (
    BOARD_WAIT_SECONDS,
    TRACKER,
    Board,
    ClaimTaken,
    Tracker,
    TrackerError,
    card_from,
    claim_message,
)
from setup_tracker import find_board

APP = "shekel-plan-tool"
#: The code repository's commits the recording asks about: PR #506's last commit (merged
#: into dev, not yet on main) and PR #447's head (closed, never merged).
ON_DEV_ONLY = "8540e8359a2649a3532379d68b3b9d7794913f52"
NEVER_MERGED = "d3e1979040edc74086fcad98ddf432ef078c84cf"
#: Card #15's REST id, the scratch finding the recording filed.
FILED_ID = 5700129994


@pytest.fixture(name="recorded")
def _recorded():
    """The tracker over the recording, its board found the way ``connect`` finds it."""
    replay = Replay("tracker")
    github = GitHub("token", replay)
    board = Board(github, find_board(github), sleep=lambda _seconds: None)
    return Tracker(github, board, APP), replay


def test_open_cards_reads_each_cards_facts(recorded):
    """The container off the board, its leaves on it, their blockers, a card with no type."""
    tracker, _ = recorded
    cards = tracker.open_cards()
    assert sorted(cards) == [*range(1, 12), 13, 14]
    container, leaf = cards[1], cards[2]
    assert container.is_container and container.board_item is None
    # A parent lists its sub-issues in the order they were added: L1 added them in plan order.
    assert [child.number for child in container.children] == [2, 3, 4, 5, 6, 9, 7, 8, 10]
    assert (leaf.kind, leaf.labels, leaf.parent, leaf.is_container) == ("step", ("balance",), 1,
                                                                        False)
    assert leaf.board_item and leaf.id == 5696958955
    assert cards[7].blocked_by == (9, 6, 5, 4, 3, 2)
    assert cards[11].kind is None and not cards[11].labels
    assert cards[2].title == REDACTED and cards[13].title.startswith(SCRATCH)


def test_cards_leaves_out_a_number_nobody_filed_and_reads_a_persons_close(recorded):
    """GitHub answers a missing number NOT_FOUND beside the rest; #12 was closed by the
    developer's own login, so the tool must not read it as its own display."""
    tracker, _ = recorded
    cards = tracker.cards([1, 2, 11, 12, 13, 99999])
    assert sorted(cards) == [1, 2, 11, 12, 13]
    closed = cards[12]
    assert (closed.is_open, closed.state_reason) == (False, "NOT_PLANNED")
    assert closed.touched_by_hand and not closed.closed_by_tool
    assert [(c.number, c.is_open) for c in cards[11].children] == [(12, False), (13, True),
                                                                  (14, True)]


def test_the_board_reads_in_drag_order(recorded):
    """Position order, which is not number order (L9 sits before L7)."""
    tracker, _ = recorded
    assert [number for number, _ in tracker.board.order()] == [2, 3, 4, 5, 6, 9, 7, 8, 10]


def test_a_claim_is_won_once_read_back_and_released(recorded):
    """The second claim is GitHub's 422 "Reference already exists"; GraphQL reads the commit."""
    tracker, _ = recorded
    assert tracker.claims() == {}
    won = tracker.claim(11, "feat/plan-recording")
    assert (won.card, won.branch) == (11, "feat/plan-recording")
    with pytest.raises(ClaimTaken):
        tracker.claim(11, "feat/other")
    held = tracker.claims()
    assert list(held) == [11]
    assert (held[11].branch, held[11].made, held[11].sha) == (won.branch, won.made, won.sha)
    tracker.release(11)
    assert tracker.claims() == {}


def test_a_claims_commit_names_its_card_and_branch():
    """The message ``claims()`` reads the branch back from."""
    assert claim_message(12, "feat/x") == "claim plan#12\n\nbranch: feat/x\n"


def test_edits_are_every_saved_version_oldest_first_each_in_full(recorded):
    """Measured: GitHub keeps the FULL body per edit, with its editor; a card never edited
    has none, so its body as filed is its one version."""
    tracker, _ = recorded
    body, versions = tracker.edits(11)
    assert [v.editor for v in versions] == [APP, APP, "SaltyReformed", APP]
    assert versions[0].body.startswith("L2 probe body v0") and body == versions[-1].body
    assert [v.edited_at for v in versions] == sorted(v.edited_at for v in versions)
    assert versions[0].body != versions[-1].body
    body, versions = tracker.edits(12)
    assert len(versions) == 1 and versions[0].edit_id is None and versions[0].body == body


def test_find_titles_and_the_branch_that_merged_a_commit(recorded):
    """A title search as the App; the code repository's pull requests as the App.

    Measured 2026-10-04: GitHub names the merged pull request into dev for a commit
    not yet on main (the default branch), and NO pull request for the head of one
    closed unmerged."""
    tracker, _ = recorded
    assert [number for number, _ in tracker.find_titles("L2 measurement")] == [14, 13, 11, 12]
    assert tracker.merged_into_dev("SaltyReformed/Shekel", ON_DEV_ONLY) == {
        "tick/balance-x-bi-6-4d-1"}
    assert tracker.merged_into_dev("SaltyReformed/Shekel", NEVER_MERGED) == set()


def _replay_claims(tracker):
    """The claim reads and writes, a ref under claims/ that names no card, and a 422 that
    is not "Reference already exists"."""
    github, base = tracker.github, f"/repos/{TRACKER}"
    tracker.claims()
    tracker.claim(11, "feat/plan-recording")
    with pytest.raises(ClaimTaken):
        tracker.claim(11, "feat/other")
    tracker.claims()
    tracker.release(11)
    tracker.claims()
    main = github.rest("GET", f"{base}/git/ref/heads/main")["object"]["sha"]
    tree = github.rest("GET", f"{base}/git/commits/{main}")["tree"]["sha"]
    stray = github.rest("POST", f"{base}/git/commits", {
        "message": "recording: a ref under claims/ that names no card\n", "tree": tree,
        "parents": []})
    github.rest("POST", f"{base}/git/refs",
                {"ref": "refs/claims/recording-scratch", "sha": stray["sha"]})
    assert tracker.claims() == {}, "a ref under claims/ naming no card is no claim"
    github.rest("DELETE", f"{base}/git/refs/claims/recording-scratch")
    with pytest.raises(GitHubError, match="Object does not exist"):
        github.rest("POST", f"{base}/git/refs", {"ref": "refs/claims/99998", "sha": "1" * 40})


def _replay_reads(tracker):
    """Edit histories, a title search, and the code repository's pull requests."""
    for number in (11, 12):
        tracker.edits(number)
    tracker.find_titles("L2 measurement")
    tracker.merged_into_dev("SaltyReformed/Shekel", ON_DEV_ONLY)
    tracker.merged_into_dev("SaltyReformed/Shekel", NEVER_MERGED)


def _replay_filed_card(tracker):
    """A card filed, linked, refused a second parent, changed every way, closed, reopened."""
    number = tracker.create("finding", f"{SCRATCH} finding, filed by the recorder (delete me)",
                            "The recorder files this card to record GitHub's answers.",
                            ["balance"])
    assert number == 15
    card = tracker.cards([number])[number]
    assert (card.kind, card.labels, card.id) == ("finding", ("balance",), FILED_ID)
    both = tracker.cards([11, 12])
    tracker.add_child(11, card)
    with pytest.raises(GitHubError, match="Sub issue may only have one parent"):
        tracker.github.rest("POST", f"/repos/{TRACKER}/issues/13/sub_issues",
                            {"sub_issue_id": card.id})
    assert tracker.cards([number])[number].parent == 11
    tracker.retype(number, "question")
    tracker.retitle(number, f"{SCRATCH} question, retitled by the recorder (delete me)")
    tracker.set_body(number, "Is this recorded?")
    tracker.comment(number, "A comment the recorder posts.")
    tracker.block(number, both[12])
    assert tracker.cards([number])[number].blocked_by == (12,)
    tracker.unblock(number, both[12])
    tracker.close(number, "not_planned")
    closed = tracker.cards([number])[number]
    assert (closed.is_open, closed.state_reason, closed.closed_by_tool) == (False, "NOT_PLANNED",
                                                                            True)
    assert (closed.kind, closed.parent) == ("question", 11)
    tracker.reopen(number)
    reopened = tracker.cards([number])[number]
    assert reopened.is_open and not (reopened.closed_by_tool or reopened.touched_by_hand)
    return both[11]


def _replay_board(tracker, order, parent):
    """A card added, placed, read back while in the wrong place, placed again, removed."""
    assert tracker.board.order() == order
    item = tracker.board.add(parent)
    assert tracker.board.place(item, order[0][1])
    quick = Board(tracker.github, tracker.board_id, sleep=lambda _seconds: None)
    assert quick.shows(item, None) is False, "second on the board is not first"
    assert tracker.board.place(item, None)
    tracker.board.remove(item)
    tracker.board.order()


def test_the_recorded_session_replays_end_to_end(recorded):
    """Every write the tool makes, in the order recorded, each answered as GitHub answered;
    with what checkpoint 2's review found unrecorded (review M2, M3): a ref under claims/
    that names no card, GitHub refusing a second parent, and a board read that keeps
    showing an item in the wrong place."""
    tracker, replay = recorded
    tracker.open_cards()
    tracker.cards([1, 2, 11, 12, 13, 99999])
    order = tracker.board.order()
    _replay_claims(tracker)
    _replay_reads(tracker)
    parent = _replay_filed_card(tracker)
    _replay_board(tracker, order, parent)
    assert replay.unused() == 0


def _recorded_answer(method, url_end):
    """GitHub's recorded answer to the first ``method`` request whose URL ends ``url_end``."""
    for exchange in json.loads((RECORDINGS / "tracker.json").read_text(encoding="utf-8")):
        if exchange["method"] == method and exchange["url"].endswith(url_end):
            return copy.deepcopy(exchange["answer"])
    raise LookupError(url_end)


class _Answers:
    """A GitHub that answers every REST call with one answer."""

    def __init__(self, answer):
        """Hold the answer."""
        self.answer = answer

    def rest(self, _method, _path, _body=None):
        """The answer, whatever was asked."""
        return self.answer


def test_a_type_github_dropped_is_refused_not_trusted():
    """GitHub silently drops a type its writer may not set; ``create`` reads it back."""
    filed = _recorded_answer("POST", "/shekel-plan/issues")
    assert filed["type"]["name"] == "finding"
    tracker = Tracker(_Answers({**filed, "type": None}), Board(None, "B"), APP)
    with pytest.raises(TrackerError, match="not .*'finding'"):
        tracker.create("finding", "t", "b", ["balance"])


def _recorded_node(number):
    """One card's GraphQL node as the recording holds it."""
    for exchange in json.loads((RECORDINGS / "tracker.json").read_text(encoding="utf-8")):
        found = ((exchange["answer"] or {}).get("data") or {}).get("repository") or {}
        if f"c{number}" in found and found[f"c{number}"]:
            return copy.deepcopy(found[f"c{number}"])
    raise LookupError(number)


def test_a_connection_github_cut_short_is_an_error_not_a_partial_card():
    """A card with more sub-issues than the read holds must not look like fewer."""
    node = _recorded_node(1)
    assert card_from(node, "board", APP).is_container
    node["subIssues"]["totalCount"] += 1
    with pytest.raises(TrackerError, match="10 sub-issues; the read holds 9"):
        card_from(node, "board", APP)


class _StuckBoard:
    """A board read that never shows the item placed (GitHub's lag, at its worst)."""

    def graphql(self, query, **_variables):
        """Answer a placement, and every read with one other item."""
        if query.lstrip().startswith("mutation"):
            return {}
        return {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                   "nodes": [{"id": "OTHER", "content": {
                                       "number": 2, "repository": {"name": "shekel-plan"}}}]}}}


def test_a_placement_the_board_never_shows_is_reported_after_the_wait():
    """The writer waits out the lag, then says so instead of waiting forever."""
    slept = []
    board = Board(_StuckBoard(), "B", sleep=slept.append)
    assert board.place("MINE", "OTHER") is False
    assert sum(slept) == BOARD_WAIT_SECONDS


def test_a_recording_never_keeps_a_token():
    """An installation token is minted through a client the recorder never wraps; if one
    ever reached it, saving refuses."""
    recorder = Recorder()
    recorder.exchanges.append({"method": "POST", "url": "u", "body": None, "status": 201,
                               "answer": {"token": "secret"}})
    with pytest.raises(ValueError, match="token"):
        recorder.save("never-written")
    assert not (RECORDINGS / "never-written.json").exists()


def test_a_card_nobody_ever_closed_is_neither_the_tools_display_nor_a_persons():
    """Review M2: with no close or reopen on record the card is untouched; read as a
    person's, sync would only report every card it should close."""
    node = _recorded_node(2)
    assert not node["timelineItems"]["nodes"]
    card = card_from(node, "board", APP)
    assert not (card.touched_by_hand or card.closed_by_tool)


def _recorded_query(fragment):
    """GitHub's recorded ``data`` for the first GraphQL query whose text holds ``fragment``."""
    for exchange in json.loads((RECORDINGS / "tracker.json").read_text(encoding="utf-8")):
        if fragment in ((exchange["body"] or {}).get("query") or ""):
            return copy.deepcopy(exchange["answer"]["data"])
    raise LookupError(fragment)


def _open_node(number):
    """One card's node as the recorded read of every open card holds it."""
    nodes = _recorded_query("states: [OPEN]")["repository"]["issues"]["nodes"]
    return next(node for node in nodes if node["number"] == number)


class _GraphQLAnswer:
    """A GitHub answering every GraphQL read with one ``data``."""

    def __init__(self, data):
        """Hold the data."""
        self.data = data

    def graphql(self, _query, **_variables):
        """The data, whatever was asked."""
        return self.data


def test_an_item_on_another_board_is_not_this_boards():
    """Review M2: an issue may sit on several boards. From card #2's recorded node, its item
    moved to a board with another id."""
    node = _recorded_node(2)
    board = node["projectItems"]["nodes"][0]["project"]["id"]
    assert card_from(node, board, APP).board_item
    node["projectItems"]["nodes"][0]["project"]["id"] = "PVT_another_board"
    assert card_from(node, board, APP).board_item is None


def test_a_board_item_of_another_repository_is_not_in_the_order():
    """Review M2: after L5 the code repository's issues can sit on the board. From the
    recorded board read, its first item's repository renamed."""
    data = _recorded_query("orderBy: {field: POSITION")
    first = data["node"]["items"]["nodes"][0]
    first["content"]["repository"]["name"] = "Shekel"
    order = Board(_GraphQLAnswer(data), "B").order()
    assert [number for number, _ in order] == [3, 4, 5, 6, 9, 7, 8, 10]


def test_a_pull_request_a_title_search_finds_is_not_a_card():
    """Review M2: GitHub's issue search returns pull requests too, marked so. From the
    recorded search, one item copied as a pull request."""
    found = next(exchange["answer"] for exchange in json.loads(
        (RECORDINGS / "tracker.json").read_text(encoding="utf-8"))
        if "/search/issues?" in exchange["url"])
    found = copy.deepcopy(found)
    found["items"].append({**found["items"][0], "number": 99, "pull_request": {"url": "u"}})
    tracker = Tracker(_Answers(found), Board(None, "B"), APP)
    assert [number for number, _ in tracker.find_titles("L2 measurement")] == [14, 13, 11, 12]


def test_only_a_pull_request_merged_into_dev_shipped_a_commit():
    """Review M2: GitHub names an OPEN pull request holding a commit not yet on main (and,
    measured, the merged one into dev, but no closed unmerged one).  None is open today, so
    this starts from the recorded answer for #506: the same pull request still open into
    dev, open into main (a pending release), and merged into main."""
    (merged,) = _recorded_answer("GET", f"/commits/{ON_DEV_ONLY}/pulls")
    open_dev = {**merged, "number": 997, "merged_at": None, "head": {**merged["head"],
                                                                     "ref": "feat/pending"}}
    release = {**merged, "number": 998, "merged_at": None, "base": {**merged["base"],
                                                                   "ref": "main"},
               "head": {**merged["head"], "ref": "release/pending"}}
    released = {**release, "number": 999, "merged_at": merged["merged_at"],
                "head": {**merged["head"], "ref": "release/done"}}
    tracker = Tracker(_Answers([merged, open_dev, release, released]), Board(None, "B"), APP)
    assert tracker.merged_into_dev("SaltyReformed/Shekel", ON_DEV_ONLY) == {
        "tick/balance-x-bi-6-4d-1"}


@pytest.mark.parametrize("link", ["parent", "sub-issue", "blocker"])
def test_a_link_to_an_issue_of_another_repository_is_refused_not_read_as_a_card(link):
    """Review L4: read by number alone, it named the tracker's card of that number.  From
    recorded nodes (#2's parent, #1's first sub-issue, #7's first blocker), its repository
    renamed; as recorded, each names the tracker."""
    node, target = {
        "parent": lambda n: (n, n["parent"]),
        "sub-issue": lambda n: (n, n["subIssues"]["nodes"][0]),
        "blocker": lambda n: (n, n["blockedBy"]["nodes"][0]),
    }[link]({"parent": _recorded_node(2), "sub-issue": _recorded_node(1),
             "blocker": _open_node(7)}[link])
    assert target["repository"]["nameWithOwner"] == TRACKER
    card_from(node, "board", APP)
    target["repository"]["nameWithOwner"] = "saltyreformed-labs/Shekel"
    with pytest.raises(TrackerError, match=f"{link} is saltyreformed-labs/Shekel#"):
        card_from(node, "board", APP)


class _RefRefused:
    """GitHub making a claim's commit, then refusing its ref with ``error``."""

    def __init__(self, error):
        """Hold the refusal."""
        self.error = error

    def rest(self, method, path, _body=None):
        """A commit for the claim; ``error`` for its ref."""
        if path.endswith("/git/refs"):
            raise self.error
        if method == "POST":
            return {"sha": "c" * 40, "author": {"date": "2026-10-04T14:18:09Z"}}
        return {"object": {"sha": "m" * 40}, "tree": {"sha": "t" * 40}}


def test_a_ref_refused_for_another_reason_is_not_a_taken_claim(recorded):
    """Review M2: only "Reference already exists" means another session holds the claim; the
    recorded 422 for a ref to a missing object must not read as one."""
    tracker, _ = recorded
    with pytest.raises(GitHubError) as refused:
        tracker.github.rest("POST", f"/repos/{TRACKER}/git/refs",
                            {"ref": "refs/claims/99998", "sha": "1" * 40})
    assert refused.value.status == 422 and "Object does not exist" in str(refused.value)
    claimer = Tracker(_RefRefused(refused.value), Board(None, "B"), APP)
    with pytest.raises(GitHubError, match="Object does not exist"):
        claimer.claim(99998, "feat/x")


def test_a_recording_keeps_a_scratch_cards_text_and_redacts_every_other_cards():
    """L10: the recording is in the public repository and the tracker is private.  A card's
    text is kept only for a scratch card, wherever it is read; the board's own title stays
    (its configuration); a text tied to no card is redacted."""
    graphql = "https://api.github.com/graphql"
    exchanges = [
        {"method": "POST", "url": graphql, "body": {"query": "q", "variables": {}}, "status": 200,
         "answer": {"data": {"repository": {
             "c2": {"number": 2, "title": "Real figure: $1,234.56", "body": "secret"},
             "c11": {"number": 11, "title": f"{SCRATCH} card", "body": "kept text"}}}}},
        {"method": "POST", "url": graphql, "body": {"query": "edits", "variables": {"number": 2}},
         "status": 200, "answer": {"data": {"repository": {"issue": {
             "body": "secret body", "userContentEdits": {"nodes": [{"diff": "secret diff"}]}}}}}},
        {"method": "POST", "url": graphql, "body": {"query": "edits", "variables": {"number": 11}},
         "status": 200, "answer": {"data": {"repository": {"issue": {
             "body": "scratch body",
             "userContentEdits": {"nodes": [{"diff": "scratch diff"}]}}}}}},
        {"method": "GET", "url": "https://api.github.com/x", "body": None, "status": 200,
         "answer": {"body": "tied to no card"}},
        {"method": "POST", "url": graphql, "body": {"query": "boards", "variables": {}},
         "status": 200, "answer": {"data": {"organization": {"projectsV2": {"nodes": [
             {"id": "PVT_1", "title": "Shekel plan"}]}}}}},
    ]
    kept = json.dumps(redacted(exchanges))
    for private in ("Real figure", "secret", "tied to no card"):
        assert private not in kept, private
    for public in (f"{SCRATCH} card", "kept text", "scratch body", "scratch diff", "Shekel plan"):
        assert public in kept, public


def test_a_recording_that_wrote_text_to_a_card_not_scratch_is_refused():
    """A request is replayed as it was sent, so its text cannot be redacted: refused."""
    wrote = [{"method": "PATCH", "url": f"https://api.github.com/repos/{TRACKER}/issues/2",
              "body": {"body": "a new spec"}, "status": 200,
              "answer": {"number": 2, "title": "t"}}]
    with pytest.raises(ValueError, match="text written to card 2"):
        redacted(wrote)


def test_the_recording_the_tests_replay_holds_no_text_but_a_scratch_cards():
    """A census of ``recorded/tracker.json`` (L10): redacting it changes nothing, so no text
    of a card that is not a scratch card is in it."""
    exchanges = json.loads((RECORDINGS / "tracker.json").read_text(encoding="utf-8"))
    assert redacted(exchanges) == exchanges
    assert any(REDACTED in json.dumps(exchange["answer"]) for exchange in exchanges)


def test_a_saved_recording_is_redacted(tmp_path):
    """L10 where it is enforced: ``save`` writes only the redacted exchanges."""
    recorder = Recorder()
    recorder.exchanges.append({"method": "GET", "url": f"https://api.github.com/repos/{TRACKER}"
                               "/issues/2", "body": None, "status": 200,
                               "answer": {"number": 2, "title": "Real figure", "body": "secret"}})
    saved = recorder.save("redacted", tmp_path).read_text(encoding="utf-8")
    assert "Real figure" not in saved and "secret" not in saved and REDACTED in saved
