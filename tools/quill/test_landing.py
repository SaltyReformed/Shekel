"""CI's landing check (X-cx L9d a2): a change set's card trailers, judged by what the tracker
says each card is, before they land; nothing calls GitHub.

Every test reads the tracker through the REAL card-only reader (:class:`_tracker.CardReader`,
review O3 of a1b), so each card it judges is a :class:`_tracker.BareCard` built by
``card_from`` from a node in the shape GitHub answers a read without the board, and a
decision that reached for a board item would raise.  The nodes are built by :func:`_card`,
whose shape is held key for key to the recorded lookup's
(:func:`test_the_nodes_are_shaped_as_github_answers_the_reader`).  The history is a
throwaway repository's (:mod:`tools.ci.scratch`).
"""
from __future__ import annotations

import copy
import re

import pytest
import requests

from tools.ci.scratch import commit, point_dev, repository
from tools.quill import landing
from tools.quill._github import GitHubError
from tools.quill._recorded import recording
from tools.quill._tracker import CardReader, Tracker
from tools.quill.landing import TOKEN_VARIABLE, main, reader_from_environment
from tools.quill.setup_tracker import APP_LOGIN, DEPLOY_TOGETHER_LABEL, FILING, PLAN

#: A person's login, whose close is a decision of theirs.
PERSON = "SaltyReformed"


def _card(number, kind="step", parent=None, labels=("balance",)):
    """Open card ``number``'s node as GitHub answers the card-only reader (no board items),
    never closed or reopened, its sub-issues left for :func:`_tracker` to fill from the
    cards naming it their parent."""
    return {
        "id": f"I_{number}", "number": number, "fullDatabaseId": str(1000 + number),
        "title": f"card {number}", "state": "OPEN", "stateReason": None,
        "issueType": {"name": kind} if kind else None,
        "labels": {"totalCount": len(labels), "nodes": [{"name": name} for name in labels]},
        "parent": ({"number": parent, "repository": {"nameWithOwner": PLAN.full_name}}
                   if parent else None),
        "subIssues": {"totalCount": 0, "nodes": []},
        "blockedBy": {"totalCount": 0, "nodes": []},
        "timelineItems": {"nodes": []},
    }


def _closed(node, reason, closer):
    """``node`` closed as ``reason`` (``COMPLETED``, ``NOT_PLANNED``) by the login ``closer``."""
    return {**node, "state": "CLOSED", "stateReason": reason,
            "timelineItems": {"nodes": [{"__typename": "ClosedEvent",
                                         "actor": {"login": closer}}]}}


def _tracker(*cards):
    """The tracker holding ``cards``, by number, each card's sub-issues the cards naming it
    their parent, as GitHub lists them."""
    nodes = {node["number"]: copy.deepcopy(node) for node in cards}
    for node in nodes.values():
        if node["parent"]:
            listed = nodes[node["parent"]["number"]]["subIssues"]
            listed["nodes"].append({"number": node["number"], "state": node["state"],
                                    "issueType": node["issueType"],
                                    "repository": node["parent"]["repository"]})
            listed["totalCount"] += 1
    return nodes


class _Lookup:
    """A GitHub answering each lookup of cards by number from ``nodes``, a number it lacks
    answered None (as GitHub answers a number nobody filed); it keeps the numbers each
    lookup asked for.  ``fails``: what every lookup raises instead, if anything."""

    def __init__(self, nodes, fails=None):
        """Hold the tracker's nodes."""
        self.nodes = nodes
        self.fails = fails
        self.asked = []

    def graphql_lookup(self, query):
        """Answer the cards ``query`` asks for by number."""
        if self.fails is not None:
            raise self.fails
        numbers = [int(number) for number in re.findall(r"c(\d+): issue\(number: \1\)", query)]
        self.asked.append(sorted(numbers))
        return {"repository": {f"c{number}": copy.deepcopy(self.nodes.get(number))
                               for number in numbers}}


def _reader(github):
    """A connection making the card-only reader of the plan's tracker through ``github``."""
    return lambda: CardReader(github, PLAN)


def _never():
    """A connection no test may make: the tracker is read only when a trailer names a card."""
    raise AssertionError("the tracker was read")


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository with one commit, the base every change set below starts from, which
    ``origin/dev`` holds."""
    root = repository(tmp_path)
    base = commit(root, "base")
    point_dev(root, base)
    return root, base


def _on(root, parent, *messages):
    """Commits on ``parent``, one per message (``(subject, *paragraphs)``), oldest first;
    each one's sha."""
    shas = []
    for message in messages:
        parent = commit(root, *message, parents=[parent])
        shas.append(parent)
    return shas


def _land(capsys, code, github, *messages, base=None):
    """Run the check over a pull request into ``dev``: the change set ``messages`` makes on
    ``code``'s base (or on ``base``), which ``origin/dev`` is pointed at.  Its exit status,
    its output, and the change set's shas."""
    root, start = code
    point_dev(root, base or start)
    shas = _on(root, base or start, *messages)
    status = main([base or start, shas[-1]], _reader(github) if github else _never, root)
    out = capsys.readouterr()
    return status, out.out, out.err, shas


def _paths(value, prefix=""):
    """Every key path in ``value``: a dict's keys, joined by dots, a list's items as ``[]``."""
    if isinstance(value, dict):
        return set().union(*({prefix + key} | _paths(item, f"{prefix}{key}.")
                             for key, item in value.items()))
    if isinstance(value, list):
        return set().union(set(), *(_paths(item, prefix.rstrip(".") + "[].") for item in value))
    return set()


def test_the_nodes_are_shaped_as_github_answers_the_reader():
    """Two nodes :func:`_card` builds, between them with a type, a label, a parent, a
    sub-issue and a close, hold exactly the key paths the recorded lookup's nodes hold
    between them, less their board items: the fields the card-only reader asks for
    (``_queries.BARE_CARD_FIELDS``).  No recorded node has a blocker, and :func:`_card`
    builds none (no decision here reads one), so a blocker's own shape is not held here."""
    recorded = next(exchange["answer"]["data"]["repository"]
                    for exchange in recording("tracker")["exchanges"]
                    if "c99999: issue(number: 99999)" in ((exchange["body"] or {}).get("query")
                                                         or ""))
    want = {path for node in recorded.values() if node for path in _paths(node)
            if not path.startswith("projectItems")}
    built = _tracker(_card(1), _closed(_card(2, parent=1), "COMPLETED", PERSON))
    assert _paths(built[1]) | _paths(built[2]) == want


def test_a_change_set_naming_no_card_never_reads_the_tracker(capsys, code):
    """No trailer, an old-form claim and a value of neither form name no card: nothing is
    read, nothing refused (CI's trailer check refuses the malformed one)."""
    status, out, _, _ = _land(capsys, code, None, ("plain",), ("old", "Ships: balance:X-1"),
                              ("bad", "Ships: plan#seven"))
    assert status == 0
    assert out.startswith("landing check: 3 commit(s) in ") and out.endswith(", 0 refused\n")


def test_a_ships_naming_a_step_or_a_finding_lands(capsys, code):
    """Work lands: a plain step, and a finding under a live step."""
    github = _Lookup(_tracker(_card(7), _card(8, "finding", parent=7)))
    status, _, _, _ = _land(capsys, code, github, ("step", "Ships: plan#7"),
                            ("fix", "Ships: plan#8"))
    assert status == 0
    assert github.asked[0] == [7, 8]


def test_a_trailer_naming_a_card_the_tracker_lacks_is_refused(capsys, code):
    """L8's list (ii): a made-up ``Ships: plan#999`` is refused; so is a ``Reopens:`` of a
    number nobody filed, whose fix is only to correct it."""
    status, out, _, (ships, reopens) = _land(
        capsys, code, _Lookup(_tracker()), ("made up", "Ships: plan#999"),
        ("undo", "Reopens: plan#998"))
    assert status == 1
    assert out.splitlines()[:2] == [
        f"REFUSED -- {reopens[:12]} (undo) carries `Reopens: plan#998`, but the tracker holds "
        "no plan#998 (a mistyped number?): correct the number",
        f"REFUSED -- {ships[:12]} (made up) carries `Ships: plan#999`, but the tracker holds "
        "no plan#999 (a mistyped number?): correct the number, or file the card with quill "
        "file first and name the number it is given"]
    assert out.splitlines()[2].endswith(", 2 refused")


@pytest.mark.parametrize(("cards", "what"), [
    ((_card(5), _card(6, parent=5)), "a container"),
    ((_closed(_card(5, "ruling"), "COMPLETED", APP_LOGIN),), "a ruling"),
    ((_card(5, "question"),), "a question"),
    ((_card(5, None),), "a card with no type"),
])
def test_a_ships_naming_no_work_is_refused_saying_what_it_is(capsys, code, cards, what):
    """A split step's work is its smaller steps (R-BAL177); a ruling is a record, a question
    the developer's, and a card with no type no work: each named as such."""
    status, out, _, (sha,) = _land(capsys, code, _Lookup(_tracker(*cards)),
                                   ("ship", "Ships: plan#5"))
    assert status == 1
    assert out.splitlines()[0] == (
        f"REFUSED -- {sha[:12]} (ship) carries `Ships: plan#5`, but plan#5 is {what}, which "
        "no commit ships: name the step or finding the commit builds (a step split into "
        "smaller steps is built as them)")


def test_a_ships_naming_a_card_still_being_filed_is_refused(capsys, code):
    """R-BAL202: a card still carrying the filing mark, open, is still being filed."""
    status, out, _, (sha,) = _land(
        capsys, code, _Lookup(_tracker(_card(6, labels=("balance", FILING)))),
        ("ship", "Ships: plan#6"))
    assert status == 1
    assert out.splitlines()[0] == (
        f"REFUSED -- {sha[:12]} (ship) carries `Ships: plan#6`, but plan#6 is still being "
        "filed: finish it with quill file, then land")


@pytest.mark.parametrize("closed", [False, True], ids=["still being filed", "dropped while filed"])
def test_no_work_is_named_before_the_filing_mark(capsys, code, closed):
    """A question carrying the filing mark, open or closed, is refused as a question:
    finishing or reviving its filing would not make it work."""
    question = _card(6, "question", labels=("balance", FILING))
    status, out, _, _ = _land(
        capsys, code, _Lookup(_tracker(_closed(question, "NOT_PLANNED", PERSON) if closed
                                       else question)),
        ("ship", "Ships: plan#6"))
    assert status == 1
    assert "plan#6 is a question" in out and "being filed" not in out


@pytest.mark.parametrize("parent", [50, None], ids=["still linked", "unlinked by sync"])
def test_a_ships_naming_a_step_dropped_while_still_being_filed_is_refused(capsys, code, parent):
    """The release tool's card reader rests on this refusal (``tools/release/_cards.py``,
    whose docstring says "X-cx's piece L9d refuses a Ships: naming a card that still
    carries the mark when it lands"): ``sync`` unlinks a step closed while still being filed
    from the group it was filed under, so one that then shipped would be in production
    under no group, and a release carrying it without its siblings would pass.  Group
    #50 splits into live #51 and #52, which a person closed while still marked: a Ships: of
    #52 is refused, linked or not, saying how to finish it or to name the work instead
    (the coordinator's re-ruling (B), 2026-10-17)."""
    group = _card(50, labels=("balance", DEPLOY_TOGETHER_LABEL))
    dropped_leaf = _closed(_card(52, parent=parent, labels=("balance", FILING)), "NOT_PLANNED",
                           PERSON)
    github = _Lookup(_tracker(group, _card(51, parent=50), dropped_leaf))
    status, out, _, (sha,) = _land(capsys, code, github, ("ship", "Ships: plan#52"))
    assert status == 1
    assert out.splitlines()[0] == (
        f"REFUSED -- {sha[:12]} (ship) carries `Ships: plan#52`, but plan#52 was dropped while "
        "still being filed: if it is this commit's work, reopen it and finish it with quill "
        "file, then land; otherwise name the step or finding the commit builds")


def test_a_later_part_of_a_step_that_shipped_and_was_closed_lands(capsys, code):
    """A step may ship in parts (``recurrence:R16-c-2`` shipped in three): one the base's
    history ships, closed by sync as completed and carrying no mark, takes another Ships:
    -- closed is not dropped while still being filed."""
    root, start = code
    first = commit(root, "part one", "Ships: plan#7", parents=[start])
    github = _Lookup(_tracker(_closed(_card(7), "COMPLETED", APP_LOGIN)))
    assert _land(capsys, code, github, ("part two", "Ships: plan#7"), base=first)[0] == 0


def test_a_reopens_may_name_any_card_the_tracker_holds(capsys, code):
    """A release takes a group out by a Reopens: of each member it keeps, a split step
    among them: a container, a ruling and a card still being filed are each let land."""
    github = _Lookup(_tracker(_card(5), _card(6, parent=5), _card(7, "ruling"),
                              _card(8, labels=("balance", FILING))))
    status, _, _, _ = _land(capsys, code, github, ("undo", "Reopens: plan#5"),
                            ("undo", "Reopens: plan#7"), ("undo", "Reopens: plan#8"))
    assert status == 0
    assert github.asked == [[5, 7, 8]]


def _dropped_leaf():
    """Step #5 with one step linked under it, #6, closed by a person while still being
    filed: #6 is a leaf of #5 only once git says it shipped (``_state.leaves``)."""
    return _tracker(_card(5), _closed(_card(6, parent=5, labels=("balance", FILING)),
                                      "NOT_PLANNED", PERSON))


def test_a_step_is_split_by_the_leaves_git_says_shipped_once_the_change_set_lands(capsys, code):
    """Alone, #5 is a plain step and lands; shipped beside #6, #5 is split by it and is
    refused, as quill's sync reads it once both are on dev (and #6's own Ships: is refused,
    since it still carries the filing mark)."""
    assert _land(capsys, code, _Lookup(_dropped_leaf()), ("ship", "Ships: plan#5"))[0] == 0
    status, out, _, _ = _land(capsys, code, _Lookup(_dropped_leaf()),
                              ("leaf", "Ships: plan#6"), ("step", "Ships: plan#5"))
    assert status == 1
    refused = out.splitlines()[:2]
    assert "plan#5 is a container" in refused[0]
    assert "plan#6 was dropped while still being filed" in refused[1]


def test_the_base_history_ships_too_and_a_reopens_in_the_change_set_undoes_it(capsys, code):
    """#6 shipped in the base's history splits #5, so a Ships: of #5 is refused; a change
    set that also reopens #6 leaves #5 plain, and it lands."""
    root, start = code
    shipped_base = commit(root, "leaf", "Ships: plan#6", parents=[start])
    status, out, _, _ = _land(capsys, code, _Lookup(_dropped_leaf()), ("step", "Ships: plan#5"),
                              base=shipped_base)
    assert status == 1 and "plan#5 is a container" in out
    assert _land(capsys, code, _Lookup(_dropped_leaf()), ("undo", "Reopens: plan#6"),
                 ("step", "Ships: plan#5"), base=shipped_base)[0] == 0


def test_a_reopens_on_a_parallel_line_cancels_no_ship_of_the_base(capsys, code):
    """R-BAL181: a Reopens: cancels only a Ships: in its own history.  The base merges a
    line shipping #6 with a parallel one reopening it, so git still says #6 shipped, it
    splits #5, and a Ships: of #5 is refused (counting card numbers shipped less those
    reopened would land it)."""
    root, start = code
    shipping = commit(root, "leaf", "Ships: plan#6", parents=[start])
    reopening = commit(root, "elsewhere", "Reopens: plan#6", parents=[start])
    merged = commit(root, "merge", parents=[shipping, reopening])
    status, out, _, _ = _land(capsys, code, _Lookup(_dropped_leaf()), ("step", "Ships: plan#5"),
                              base=merged)
    assert status == 1 and "plan#5 is a container" in out


def test_refusals_come_by_card_whatever_the_commits_order(capsys, code):
    """The newer commit names the larger number, so git's newest-first log reads #999 first;
    the refusals still come by card, #998 first."""
    status, out, _, _ = _land(capsys, code, _Lookup(_tracker()), ("older", "Ships: plan#998"),
                              ("newer", "Ships: plan#999"))
    assert status == 1
    assert ["plan#998" in out.splitlines()[0], "plan#999" in out.splitlines()[1]] == [True, True]


def test_a_commit_carrying_one_trailer_twice_is_named_once(capsys, code):
    """Two `Ships: plan#999` lines in one commit's trailers: one commit, named once."""
    status, out, _, (sha,) = _land(capsys, code, _Lookup(_tracker()),
                                   ("twice", "Ships: plan#999\nShips: plan#999"))
    assert status == 1
    assert out.splitlines()[0].startswith(f"REFUSED -- {sha[:12]} (twice) carries `Ships: ")


def test_only_the_change_sets_own_trailers_are_judged(capsys, code):
    """R-BAL184: a made-up number already in the base's history fails no later change set,
    and is never read."""
    root, start = code
    old = commit(root, "made up long ago", "Ships: plan#999", parents=[start])
    github = _Lookup(_tracker(_card(7)))
    assert _land(capsys, code, github, ("step", "Ships: plan#7"), base=old)[0] == 0
    assert github.asked == [[7]]
    assert _land(capsys, code, None, ("plain",), base=old)[0] == 0


def test_a_release_of_commits_dev_holds_judges_none_of_them_again(capsys, code):
    """The review's M-1 (the coordinator's (A)): dev shipped #5, then undid it, and #5 was
    re-planned into smaller steps (or deleted) -- a container now, which a release judges
    as the work it was.  The release's change set, main..dev, holds only commits dev holds,
    each judged when it landed there: none is judged again and the tracker is never read,
    so permanent history cannot refuse a release for good."""
    root, start = code
    shipped = commit(root, "ship", "Ships: plan#5", parents=[start])
    undone = commit(root, "undo", "Reopens: plan#5", parents=[shipped])
    point_dev(root, undone)
    assert main([start, undone], _never, root) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "landing check: 2 of the 2 commit(s) are on origin/dev already, each judged when it "
        "landed there",
        f"landing check: 2 commit(s) in {start[:12]}..{undone[:12]}, 0 refused"]


def test_a_commit_only_on_a_release_branch_is_judged(capsys, code):
    """A release branch's commit, made on dev's tip and not on dev, is judged; dev's own
    commit beside it is not read."""
    root, start = code
    on_dev = commit(root, "ship", "Ships: plan#7", parents=[start])
    point_dev(root, on_dev)
    branch = commit(root, "release fix", "Ships: plan#999", parents=[on_dev])
    github = _Lookup(_tracker(_card(7)))
    assert main([start, branch], _reader(github), root) == 1
    out = capsys.readouterr().out
    assert github.asked == [[999]]
    assert "1 of the 2 commit(s) are on origin/dev already" in out
    assert "carries `Ships: plan#999`, but the tracker holds no plan#999" in out


def test_a_checkout_that_cannot_read_dev_refuses_to_judge(capsys, tmp_path):
    """Without origin/dev, which commits dev holds is unknown: exit 2, named, never
    'nothing to judge'."""
    root = repository(tmp_path)
    base = commit(root, "base")
    head = commit(root, "ship", "Ships: plan#7", parents=[base])
    assert main([base, head], _never, root) == 2
    assert "origin/dev cannot be read in this checkout" in capsys.readouterr().err


def test_a_number_past_graphqls_int_is_never_asked_and_is_not_held(capsys, code):
    """The review's L-3: GraphQL's Int is 32 bits, so plan#2147483648 is never asked (asked,
    it would cost the whole read) and is refused as a card the tracker lacks; the largest
    number the read can ask, plan#2147483647, is asked as any other."""
    status, out, _, _ = _land(capsys, code, (github := _Lookup(_tracker())),
                              ("largest", "Ships: plan#2147483647"),
                              ("past it", "Ships: plan#2147483648"))
    assert status == 1
    assert github.asked == [[2147483647]]
    assert [line.split(", but ")[1] for line in out.splitlines()[:2]] == [
        "the tracker holds no plan#2147483647 (a mistyped number?): correct the number, or "
        "file the card with quill file first and name the number it is given",
        "the tracker holds no plan#2147483648 (a mistyped number?): correct the number, or "
        "file the card with quill file first and name the number it is given"]


def test_one_refusal_names_every_commit_carrying_the_trailer(capsys, code):
    """Two commits shipping one made-up number: one sentence, naming both."""
    status, out, _, (first, second) = _land(capsys, code, _Lookup(_tracker()),
                                            ("one", "Ships: plan#999"), ("two", "Ships: plan#999"))
    assert status == 1
    assert out.splitlines()[0].startswith(
        f"REFUSED -- {second[:12]} (two), {first[:12]} (one) carry `Ships: plan#999`, but")


@pytest.mark.parametrize("fails", [
    GitHubError(200, "graphql: [{\"type\": \"FORBIDDEN\"}]"),
    requests.ConnectionError("the tracker cannot be reached"),
    requests.ReadTimeout("the tracker did not answer in time"),
])
def test_a_tracker_that_cannot_be_read_refuses_to_judge(capsys, code, fails):
    """An error GitHub answers, no answer at all, or none in time: exit 2, nothing
    checked."""
    status, out, err, _ = _land(capsys, code, _Lookup({}, fails), ("ship", "Ships: plan#7"))
    assert (status, out) == (2, "")
    assert err.startswith("landing check: the tracker could not be read, so nothing was "
                          f"checked: {fails}")


def test_a_card_linked_to_one_the_tracker_lacks_refuses_to_judge(capsys, code):
    """#5 lists #6 under it and the tracker has no #6: whether #5 is split cannot be read."""
    nodes = _tracker(_card(5), _card(6, parent=5))
    del nodes[6]
    status, _, err, _ = _land(capsys, code, _Lookup(nodes), ("ship", "Ships: plan#5"))
    assert status == 2 and "cards [6] are named by others but do not exist" in err


def test_without_a_token_a_change_set_naming_a_card_is_not_judged(capsys, code, monkeypatch):
    """CI's default connection reads the token from its variable: unset or empty, a change
    set naming a card exits 2 saying so, and one naming none is judged without it."""
    root, start = code
    sha = _on(root, start, ("ship", "Ships: plan#7"))[0]
    for value in (None, ""):
        if value is None:
            monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
        else:
            monkeypatch.setenv(TOKEN_VARIABLE, value)
        assert main([start, sha], root=root) == 2
        assert f"{TOKEN_VARIABLE} is not set" in capsys.readouterr().err
    plain = _on(root, start, ("plain",))[0]
    assert main([start, plain], root=root) == 0


def test_the_token_reaches_a_reader_of_cards_without_the_board(monkeypatch):
    """The token in the variable makes a card-only reader of the plan's tracker, reading
    closes as the declared App's: never quill's App and never the board (R-BAL289)."""
    monkeypatch.setenv(TOKEN_VARIABLE, "a-read-only-token")
    monkeypatch.setattr(landing, "GitHub", lambda token: ("github", token))
    reader = reader_from_environment()
    assert isinstance(reader, CardReader) and not isinstance(reader, Tracker)
    assert (reader.github, reader.place, reader.app_login) == (
        ("github", "a-read-only-token"), PLAN, APP_LOGIN)


def test_git_that_cannot_read_the_change_set_refuses_to_judge(capsys, code):
    """A base this repository does not hold: exit 2, naming the range."""
    root, start = code
    assert main(["f" * 40, start], _never, root) == 2
    assert capsys.readouterr().err.startswith(f"landing check: git could not read {'f' * 12}..")


def test_an_empty_change_set_refuses_to_judge(capsys, code):
    """The same commit at both ends holds no commit: exit 2, as CI's trailer check says."""
    root, start = code
    assert main([start, start], _never, root) == 2
    assert "holds no commit, so nothing was checked" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["a" * 40], ["dev", "a" * 40], ["0" * 40, "a" * 40]])
def test_a_command_line_not_naming_two_full_commit_ids_is_refused(capsys, argv):
    """The two ends are read by commit_trailers' one rule: two full ids, neither all
    zeros."""
    assert main(argv, _never) == 2
    assert capsys.readouterr().err.startswith("usage: python -m tools.quill.landing ")
