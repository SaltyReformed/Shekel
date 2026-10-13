"""X-cx's migration, its verify (:mod:`_migrate_verify`): the tracker graded against the
registries both ways, on the small corpus (:mod:`_migrate_fake`) and the in-memory tracker
(:mod:`_fake`).  Nothing here reaches GitHub or git: a commit resolves by a predicate the
test hands in.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill import _migrate_source
from tools.quill._migrate_bodies import row_texts
from tools.quill._migrate_fake import COMMIT
from tools.quill._migrate_plan import plan
from tools.quill._migrate_plan_fake import (
    MILESTONES,
    corpus_items,
    corpus_texts,
    filed_tracker,
    tracked,
)
from tools.quill._migrate_run import Filing
from tools.quill._migrate_verify import Unreadable, Verified, verify
from tools.quill._tracker import Comment


def _verify(corpus, tracker, resolves=lambda commit: commit == COMMIT):
    """``tracker`` verified against the corpus, the corpus's commit the one that resolves
    and the one whose rows are known (what its items' rows write)."""
    return verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
                  tracked(tracker), resolves, lambda commit: _rows_at(corpus, commit))


def _rows_at(corpus, commit):
    """What the corpus's rows write at its commit; any other is unreadable."""
    if commit != COMMIT:
        raise Unreadable(f"no {commit} here")
    return row_texts(corpus_items(corpus))


def test_a_finished_migration_is_verified_and_holds_none_of_its_own(corpus):
    """Every item's card as a finished filing leaves it: nothing refused, and no card that
    is no item's."""
    tracker, _ = filed_tracker(corpus)
    assert _verify(corpus, tracker) == Verified((), ())


def test_the_trackers_own_cards_are_listed(corpus, monkeypatch):
    """S-1 mapped onto its card (as X-cx is card #1, off the board as a container is, its
    text its own, with no As filed block, so no commit of the migration's to read), and a
    step of the tracker's own, #1, under it: verified, #1 listed as the tracker's own."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.bodies[numbers["salary:S-1"]] = "Paycheck, filed long ago, in words of its own."
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(1, "step", "Its own.", on_board=False, title="Its own leaf", labels=("salary",),
                parent=numbers["salary:S-1"])
    assert _verify(corpus, tracker) == Verified((1,), ())


@pytest.mark.parametrize("marked, refusal", [
    ("balance:A-3", "unmark balance:A-3: still to make (`migrate open` makes it), or this "
                    "read lags the run's writes"),
    ("balance:R-A", "close-unmarked balance:R-A: still to make (`migrate history` makes it), "
                    "or this read lags the run's writes"),
], ids=["open", "history"])
def test_a_write_still_to_make_is_refused_naming_its_phase(corpus, marked, refusal):
    """A card still marked: its last write is still to make, named with the phase that
    makes it; which cards are the tracker's own is not listed."""
    tracker, _ = filed_tracker(corpus, marked=[marked])
    assert _verify(corpus, tracker) == Verified(None, (refusal,))


def test_a_card_the_plan_refuses_is_refused_in_its_words_and_nothing_more_is_graded(corpus):
    """A-3's body edited by hand: the plan's own refusal, word for word, and neither the
    board's order (two cards swapped) nor any commit (none resolves) is graded besides."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    tracker.bodies[numbers["balance:A-3"]] = texts["balance:A-3"].body.replace("the", "a", 1)
    tracker.board.items[:2] = tracker.board.items[1::-1]
    expected = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals
    assert expected == (f"balance:A-3 (plan#{numbers['balance:A-3']}): it differs in its body "
                        "from what the registries make it at this commit: a stale card is "
                        "never finished; settle it by hand, giving it that text, its As filed "
                        f"block naming this commit ({COMMIT})",)
    assert _verify(corpus, tracker, lambda commit: False) == Verified(None, expected)


def test_a_board_out_of_its_desired_order_is_refused_naming_what_moves(corpus):
    """The board's first two cards swapped, which no plan grades: refused, naming the card
    a run would move -- the one now first, which the longest common subsequence leaves out
    (:func:`_migrate_plan.moves`)."""
    tracker, _ = filed_tracker(corpus)
    first, second = tracker.board.items[:2]
    tracker.board.items[:2] = [second, first]
    assert _verify(corpus, tracker) == Verified((), (
        f"the board is not in its desired order, 1 card(s) out of place (plan#{second}): "
        "`migrate open` orders it",))


@pytest.mark.parametrize("unshown", ["first", "last"])
def test_a_card_its_own_read_puts_on_the_board_and_the_boards_read_lacks_is_refused(
        corpus, unshown):
    """The board's read lags its cards' read (or a card was taken off it during the read):
    a card whose own read shows it on the board, missing from the board's read, is refused
    by name, and nothing raises (review 7, LOW-1: ``moves`` refused the two orders)."""
    tracker, _ = filed_tracker(corpus)
    missing = tracker.board.items[0 if unshown == "first" else -1]
    read = tracked(tracker)
    lagging = replace(read, board=tuple(number for number in read.board if number != missing))
    assert read.cards[missing].card.board_item is not None
    assert verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES), lagging,
                  lambda commit: commit == COMMIT,
                  lambda commit: _rows_at(corpus, commit)) == Verified((), (
        f"the board's read does not show plan#{missing}, which its card's read puts on it: a "
        "read that lags, or an edit made during the read; run `migrate verify` again",))


@pytest.mark.parametrize("doubled", ["first", "last"])
def test_a_card_the_boards_read_shows_twice_is_refused(corpus, doubled):
    """The board's read lists a card twice, as a paginated read does when the card moves
    between its pages: refused by name, and nothing raises (review 8, LOW-2: ``moves``
    refused the two orders)."""
    tracker, _ = filed_tracker(corpus)
    read = tracked(tracker)
    twice = read.board[0 if doubled == "first" else -1]
    lagging = replace(read, board=(*read.board, twice))
    assert verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES), lagging,
                  lambda commit: commit == COMMIT,
                  lambda commit: _rows_at(corpus, commit)) == Verified((), (
        f"the board's read shows plan#{twice} more than once: an edit made during the read, "
        "a card moved between its pages; run `migrate verify` again",))


def test_a_boards_read_lagging_and_doubled_at_once_is_refused_for_both(corpus):
    """The board's read lacks its first card and shows its last twice: both refused, the
    lag first, and nothing raises (review 9, LOW-3: either refusal alone passed the
    tests)."""
    tracker, _ = filed_tracker(corpus)
    read = tracked(tracker)
    missing, twice = read.board[0], read.board[-1]
    lagging = replace(read, board=(*read.board[1:], twice))
    assert verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES), lagging,
                  lambda commit: commit == COMMIT,
                  lambda commit: _rows_at(corpus, commit)) == Verified((), (
        f"the board's read does not show plan#{missing}, which its card's read puts on it: a "
        "read that lags, or an edit made during the read; run `migrate verify` again",
        f"the board's read shows plan#{twice} more than once: an edit made during the read, "
        "a card moved between its pages; run `migrate verify` again"))


def _commit_refusal(numbers, commit):
    """The refusal of the cards ``numbers`` whose As filed block names ``commit``."""
    return (", ".join(f"plan#{number}" for number in numbers)
            + f": the As filed block names {commit}, not the full id of a commit this "
            "checkout's HEAD descends from, so a reader cannot find the text it quotes")


def test_a_commit_that_does_not_resolve_is_refused_with_every_card_naming_it(corpus):
    """None resolves: every card's block names the one commit, so one refusal names them
    all, a card filed already apart; each commit is asked about once."""
    tracker, numbers = filed_tracker(corpus)
    asked = []
    found = _verify(corpus, tracker, lambda commit: asked.append(commit) and False)
    assert found == Verified((), (_commit_refusal(sorted(numbers.values()), COMMIT),))
    assert asked == [COMMIT]


@pytest.mark.parametrize("key", ["balance:A-3", "balance:F-1", "balance:R-A", "balance:Q-1"])
def test_a_cards_own_commit_is_read_where_its_block_is(corpus, key):
    """One card's block names another commit -- a step's, a finding's and a ruling's in its
    body, a question's in its As filed comment -- which the plan does not compare: that
    card alone is refused."""
    tracker, numbers = filed_tracker(corpus)
    number, other = numbers[key], "f" * 40
    if key == "balance:Q-1":
        (comment,) = tracker.notes[number]
        tracker.notes[number] = [replace(comment, body=comment.body.replace(COMMIT, other))]
    else:
        tracker.bodies[number] = corpus_texts(corpus)[key].body.replace(COMMIT, other)
    assert _verify(corpus, tracker) == Verified((), (_commit_refusal([number], other),))


@pytest.mark.parametrize("there, refusal", [
    ("no item", "it is no item"),
    ("another figure", "its row reads otherwise"),
    ("another finding", "its row reads otherwise"),
])
def test_a_card_naming_a_commit_where_its_row_writes_otherwise_or_none_is_refused(
        corpus, there, refusal):
    """Every card names the corpus's commit, which resolves, but there F-1 is no item (its
    row not there yet), or its row writes otherwise: its worst figure (made up), a cell its
    block quotes, or its finding, the lead its body opens with and no block quotes (review
    9, MEDIUM-1).  F-1's card alone is refused, saying which, and naming this checkout's
    commit as the remedy."""
    tracker, numbers = filed_tracker(corpus)
    change = {"no item": {}, "another figure": {"worst": "$9.99"},
              "another finding": {"finding": "The first half reads a stale row twice."}}[there]
    rows = row_texts(corpus_items(replace(corpus, findings=tuple(
        replace(row, **change) if row.ident.startswith("F-1") else row
        for row in corpus.findings))))
    if there == "no item":
        rows = {key: made for key, made in rows.items() if key != "balance:F-1"}
    found = verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
                   tracked(tracker), lambda commit: commit == COMMIT, lambda commit: rows)
    assert found == Verified((), (
        f"balance:F-1 (plan#{numbers['balance:F-1']}): its As filed block names {COMMIT}, at "
        f"which {refusal}; the card holds this checkout's text, so set the commit its As filed "
        f"block names to this checkout's ({COMMIT})",))


@pytest.mark.parametrize("quoted", ["first", "last"])
def test_a_card_saying_what_either_of_two_rows_under_one_key_writes_passes(corpus, quoted):
    """The commit holds F-1 twice, its row as filed and one with another figure (a doubled
    id, which the source refuses), the row the card says first or last: the card says what
    a row of that commit writes, word for word, and passes."""
    tracker, _ = filed_tracker(corpus)
    other = replace(next(row for row in corpus.findings if row.ident.startswith("F-1")),
                    worst="$9.99")
    findings = (*corpus.findings, other) if quoted == "first" else (other, *corpus.findings)
    rows = row_texts(corpus_items(replace(corpus, findings=findings)))
    assert len(rows["balance:F-1"]) == 2
    found = verify(Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
                   tracked(tracker), lambda commit: commit == COMMIT, lambda commit: rows)
    assert found == Verified((), ())


def test_a_block_naming_no_commit_is_refused_and_no_resolver_asked(corpus, monkeypatch):
    """A block whose first line names no commit is refused as naming none, and nothing is
    asked of the resolver.  The plan leaves no such card standing (its text must equal the
    built one but the commit), so the reader is made to find none here."""
    tracker, numbers = filed_tracker(corpus)
    asked = []
    monkeypatch.setattr("tools.quill._migrate_verify.filed_commit", lambda text: None)
    found = _verify(corpus, tracker, lambda commit: asked.append(commit) or True)
    assert found == Verified((), (_commit_refusal(sorted(numbers.values()), "no commit"),))
    assert not asked


def test_a_milestone_whose_description_was_edited_is_refused_beside_the_plans_refusals(corpus):
    """The milestone's description edited on the web, which the plan does not read: refused
    on a tracker the plan refuses nothing on, and beside a plan refusal (a card still
    marked) too; one differing only in line ends and outer space is not."""
    tracker, _ = filed_tracker(corpus)
    held = tracker.milestones_by_number[1]
    tracker.milestones_by_number[1] = replace(held, description=f"  {held.description}\r\n")
    assert _verify(corpus, tracker) == Verified((), ())
    tracker.milestones_by_number[1] = replace(held, description="Another outcome.")
    refusal = ("milestone #1 'The first': its description is not the input file's (edited on "
               "the web?): set one of the two to the other")
    assert _verify(corpus, tracker) == Verified((), (refusal,))
    tracker, _ = filed_tracker(corpus, marked=["balance:A-3"])
    tracker.milestones_by_number[1] = replace(held, description="Another outcome.")
    assert _verify(corpus, tracker).refusals == (
        refusal, "unmark balance:A-3: still to make (`migrate open` makes it), or this read "
        "lags the run's writes")


def test_a_card_no_item_claims_is_the_plans_refusal_not_the_trackers_own(corpus):
    """A card above every item's card that no item claims (a stray, filed inside the
    freeze): refused by the plan, never listed as the tracker's own."""
    tracker, numbers = filed_tracker(corpus)
    stray = max(numbers.values()) + 1
    tracker.add(stray, "step", "A stray.", on_board=False, title="A stray card",
                labels=("balance",))
    found = _verify(corpus, tracker)
    assert found.own is None and len(found.refusals) == 1
    assert found.refusals[0].startswith(f"plan#{stray} ('A stray card') is no item's card")


def test_a_comment_of_a_persons_on_a_card_is_no_difference(corpus):
    """A person's comment before a question's As filed one: the registries hold no comments,
    so it is none of verify's, and the As filed one is still the one whose commit is read."""
    tracker, numbers = filed_tracker(corpus)
    number = numbers["balance:Q-1"]
    tracker.notes[number] = [Comment("a-person", "t", "filed from: x at y"),
                             *tracker.notes[number]]
    assert _verify(corpus, tracker) == Verified((), ())
