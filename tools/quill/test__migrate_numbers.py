"""X-cx's migration, its bare ``#N`` (R-BAL277): which numbers GitHub would link, each one
written as the input file decides, and what is refused rather than written.

The forms GitHub links and does not were measured 2026-10-21 through its Markdown API,
rendering in the plan tracker's context (the X-cx L8 lane's ``plan-tracker-2026-10-21/``
records); the cards here are made up.
"""
from __future__ import annotations

import pytest

from tools.quill._migrate_bodies import Built
from tools.quill._migrate_input import Numbered
from tools.quill._migrate_numbers import (
    APP_PULLS,
    CARD,
    PLAIN,
    PR,
    UNDECIDED,
    decided_from,
    link,
    numbers,
)

#: Each form GitHub linked, and how many links it made: after a quote, a bracket, a dash,
#: a slash, another ``#``, a backslash and emphasis among them.
LINKED = [
    ("see #2 here", 1), ("'#2' quoted", 1), ("(#2) paren", 1), ("#1-#2 range", 2),
    ("a/#2 slash", 1), ("#2's possessive", 1), ("**#2** bold", 1), ("_#2_ italic", 1),
    ("[ #2 (03/26 bracket", 1), ('"#2 dquote', 1), ("-#2 dash", 1), ("##2 double", 1),
    ("#2.5 decimal", 1), ("*#2* star", 1), ("~#2 tilde", 1), (":#2 colon", 1),
    ("=#2 equals", 1), ("#02 leading-zero", 1), ("\\#2 backslash", 1), ("@#2 at", 1),
    (".#2 dot", 1), ("#2/3 slash-after", 1),
]
#: Each form GitHub did not link that no candidate is taken from.
UNLINKED = ["x#2 word", "`#2` code", "PR#2 glued", "#2a suffix", "1#2 digit",
            "#<!-- -->2 marker", "```\n#2\n```"]


def _card(body: str, comment: str | None = None, title: str = "[R-X] A ruling") -> Built:
    """A card's text as built."""
    return Built(title, body, comment)


def _decided(card: Built, *treatments: str) -> Numbered:
    """``card``'s numbers decided, in reading order, as ``treatments``, for its text now."""
    return Numbered(tuple((number.text, treatment)
                          for number, treatment in zip(numbers(card), treatments)),
                    decided_from(card))


def _linked(card: Built, decision: Numbered | None):
    """:func:`link` of the one card ``card``, keyed ``balance:R-X``, decided ``decision``."""
    return link({"balance:R-X": card}, {} if decision is None else {"balance:R-X": decision})


@pytest.mark.parametrize("text, links", LINKED)
def test_every_number_github_links_is_a_candidate(text, links):
    """What GitHub linked is taken, every one: a number taken by no decision would link the
    tracker's card N unasked (the scanner before this took none after ``/``, ``#``, ``_``)."""
    assert len(numbers(_card(text))) == links


@pytest.mark.parametrize("text", UNLINKED)
def test_a_number_github_does_not_link_after_a_letter_or_in_code_is_no_candidate(text):
    """After a letter or a digit, before a letter, a digit or ``_``, in a code span, in a
    fence, or behind the marker: GitHub links none, and none is taken."""
    assert not numbers(_card(text))


@pytest.mark.parametrize("text", ["a_#2 b", "#2_x c"])
def test_more_than_github_links_may_be_taken_where_a_marker_reads_the_same(text):
    """``a_#2`` and ``#2_x``, which GitHub did not link, are taken: an ``_`` may be
    emphasis, which GitHub links inside (``_#2_``), and a marker where GitHub would not link
    reads the same."""
    assert [number.text for number in numbers(_card(text))] == ["#2"]


def test_a_code_span_starts_at_the_start_of_its_backtick_run():
    """#5 after a doubled backtick that no doubled run closes, and #6 after an unmatched
    pair, are links GitHub makes; neither run is opened in its middle (review of B1's
    fixes, LOW-6).  On one line, the doubled runs would pair and hide #5 as code."""
    assert [len(numbers(_card(text))) for text in ("``#5`", "x `` #6 `")] == [1, 1]
    assert [number.text for number in numbers(_card("``#5` and x `` #6 `"))] == ["#6"]


def test_each_treatment_writes_what_was_measured_to_read_as_written():
    """A tracker card stays bare, a pull request becomes a link to the app's, a rank takes
    the invisible marker; nothing else on the card moves."""
    card = _card("Card #8 cites PR #47 at rank #149.\n\nAgain #1-#34.")
    found = _linked(card, _decided(card, CARD, PR, PLAIN, PLAIN, PLAIN))
    assert not found.refusals
    assert found.built["balance:R-X"] == _card(
        f"Card #8 cites PR [#47]({APP_PULLS}47) at rank #<!-- -->149.\n\n"
        "Again #<!-- -->1-#<!-- -->34.")
    assert APP_PULLS == "https://github.com/saltyreformed-labs/Shekel/pull/"
    assert found.counts == {CARD: 1, PR: 1, PLAIN: 3}


def test_a_pull_request_links_its_number_with_no_leading_zero():
    """``PR #02`` reads as written and links pull request 2, whose URL GitHub writes with no
    leading zero (review of S2, L2)."""
    card = _card("PR #02 again.")
    found = _linked(card, _decided(card, PR))
    assert found.built["balance:R-X"].body == f"PR [#02]({APP_PULLS}2) again."


def test_a_number_in_the_as_filed_block_is_none_and_moves_no_decision():
    """The block is fenced, so GitHub links nothing in it: none is taken, and a cell it
    quotes changing leaves the decisions standing."""
    block = "```text\nfiled from: rulings.md at c0ffee\nid: #9\n```"
    card = _card(f"Rank #3.\n\n{block}")
    assert [number.text for number in numbers(card)] == ["#3"]
    moved = _card(f"Rank #3.\n\n{block.replace('#9', '#10')}")
    found = _linked(moved, _decided(card, PLAIN))
    assert not found.refusals
    assert found.built["balance:R-X"].body == f"Rank #<!-- -->3.\n\n{block.replace('#9', '#10')}"


def test_a_comment_is_read_and_written_as_a_body_is():
    """A number in a card's comment outside its fence is a candidate, written there."""
    card = _card("Which?", "As filed in ledger.md, see #4\n\n```text\nid: #5\n```")
    found = _linked(card, _decided(card, PLAIN))
    assert found.built["balance:R-X"].comment == (
        "As filed in ledger.md, see #<!-- -->4\n\n```text\nid: #5\n```")


def test_a_line_is_split_at_newlines_alone_so_the_text_comes_back_whole():
    """A line separator that is not ``\\n`` stays where it was."""
    card = _card("one two #2\r\nthree")
    assert _linked(card, _decided(card, PLAIN)).built["balance:R-X"].body == (
        "one two #<!-- -->2\r\nthree")


def test_a_card_holding_numbers_nobody_decided_is_refused_naming_each_and_its_hash():
    """R-BAL277: the migration refuses to file any ``#N`` nobody decided; the refusal names
    each with its words around it and the hash an entry records, and the card is left as
    built, its numbers counted undecided."""
    card = _card("It lands at #149 of about 180 (card #8).")
    found = _linked(card, None)
    assert found.refusals == (
        "numbers: balance:R-X: its card holds 2 bare #N, none decided: "
        "#149 at 'It lands at #149 of about 180 (card '; "
        "#8 at 'It lands at #149 of about 180 (card #8).'; "
        f"its text's hash is {decided_from(card)}",)
    assert found.built["balance:R-X"] is card
    assert found.counts == {UNDECIDED: 2}


def test_decisions_for_text_that_changed_since_are_refused_until_decided_again():
    """An entry records the hash of the text it decided; the text moved, so the numbers
    may have, and nothing is written."""
    card = _card("Rank #5.")
    found = _linked(card, Numbered((("#5", PLAIN),), "0" * 16))
    assert found.refusals == (
        f"numbers: balance:R-X: its text changed since its numbers were decided (hash "
        f"{'0' * 16}, now {decided_from(card)}): decide and review them again",)
    assert found.built["balance:R-X"] is card and found.counts == {UNDECIDED: 1}


def test_decisions_naming_other_numbers_or_another_order_are_refused():
    """Each decision names its number, so a list out of step with the card is seen."""
    card = _card("PR #47, card #8.")
    swapped = Numbered((("#8", CARD), ("#47", PR)), decided_from(card))
    assert _linked(card, swapped).refusals == (
        "numbers: balance:R-X: decides #8, #47, and its card holds #47, #8, in that order",)


def test_an_entry_for_a_card_holding_no_number_is_refused():
    """A decision that matches nothing is stale, never ignored (R-BAL277's record, the
    coordinator's 2026-10-13 ruling)."""
    found = _linked(_card("No numbers."), Numbered((("#1", PLAIN),), "0" * 16))
    assert found.refusals == (
        "numbers: balance:R-X: decides 1 number(s), and its card holds no bare #N: drop its "
        "entry",)


def test_a_number_in_a_title_is_refused():
    """A title is not Markdown and takes no marker."""
    found = _linked(_card("Nothing.", title="[R-X] About #5"), None)
    assert found.refusals == (
        "numbers: balance:R-X: its title holds #5, which no marker can stop in a title: "
        "reword its name",)


@pytest.mark.parametrize("text, references", [
    ("&#35;2", [("#", "&#35;")]), ("&#x23;2", [("#", "&#x23;")]), ("&num;2", [("#", "&num;")]),
    ("&#035;2", [("#", "&#035;")]), ("&#X23;2", [("#", "&#X23;")]),
    ("#&#50;", [("2", "&#50;")]), ("&#35;&#50;", [("#", "&#35;"), ("2", "&#50;")]),
])
def test_a_hash_or_a_digit_written_as_a_character_reference_is_refused(text, references):
    """GitHub reads each as its character and linked card 2 through every one (measured),
    where no ``#N`` stands for a candidate; a reference's own digits are none."""
    card = _card(f"see {text} here")
    assert not numbers(card)
    assert _linked(card, None).refusals == tuple(
        f"numbers: balance:R-X: its text writes {read!r} as the character reference "
        f"{reference!r}, which GitHub reads as that character, so it can link where no #N "
        "stands: write the character itself in its source" for read, reference in references)


@pytest.mark.parametrize("text", ["`&#35;2`", "&NUM;2", "&num2", "&amp; and &#65;"])
def test_a_reference_in_code_or_reading_as_no_hash_or_digit_is_let_be(text):
    """In code GitHub reads a reference as written, ``&NUM;`` and ``&num`` are none
    (measured: neither linked), and ``&amp;`` or ``&#65;`` reads as no ``#`` or digit, so
    its own digits are no candidate either."""
    assert not _linked(_card(text), None).refusals


def test_a_decimal_run_too_long_for_a_reference_is_read_as_written():
    """Eight digits make no reference (CommonMark), so its ``#`` and digits are read as
    written: a candidate, after ``&``, where no rewrite was measured."""
    assert [number.text for number in numbers(_card("&#12345678;"))] == ["#12345678"]


@pytest.mark.parametrize("text, problem", [
    ("see a/#2", "#2 follows '/', after which no rewrite of it was measured "
                 "(_migrate_numbers.MEASURED_BEFORE): reword its source"),
    ("_#2_", "#2 follows '_', after which no rewrite of it was measured "
             "(_migrate_numbers.MEASURED_BEFORE): reword its source"),
    ("    code #2", "#2 sits on a line indented 4 spaces, which Markdown may read as code, "
                    "where a marker or a link prints as written: reword or re-indent its "
                    "source"),
])
def test_a_number_where_no_rewrite_was_measured_is_refused(text, problem):
    """After a character no rewrite was rendered behind, or where Markdown may read code,
    a marker or a link could change what the card reads: refused, whatever is decided."""
    card = _card(text)
    assert _linked(card, _decided(card, PLAIN)).refusals == (f"numbers: balance:R-X: {problem}",)


def test_a_number_two_spaces_in_is_written():
    """A spec's continuation line, two spaces in, is prose."""
    card = _card("- a step\n  its rank #4")
    assert _linked(card, _decided(card, PLAIN)).built["balance:R-X"].body == (
        "- a step\n  its rank #<!-- -->4")


def test_a_pull_request_inside_an_open_bracket_is_refused_and_a_marker_there_is_written():
    """A link inside a link's text would nest, so a ``pr`` there is refused; R-PC111's
    ``[ #2 (03/26 - 04/08) v ]``, a paycheck's number, takes the marker."""
    linked = _card("[see PR #47](https://example.com)")
    assert _linked(linked, _decided(linked, PR)).refusals == (
        "numbers: balance:R-X: #47 at '[see PR #47](https://example.co' is decided pr "
        "inside an open '[', where its link would nest in a link's text: decide it otherwise "
        "or reword its source",)
    menu = _card("[ #2 (03/26 - 04/08) v ]")
    assert _linked(menu, _decided(menu, PLAIN)).built["balance:R-X"].body == (
        "[ #<!-- -->2 (03/26 - 04/08) v ]")


def test_only_the_card_refused_is_left_as_built():
    """One card's refusal writes nothing on it and leaves another's decisions written."""
    good, bad = _card("Rank #5."), _card("Rank #6.")
    found = link({"balance:R-X": good, "balance:R-Y": bad},
                 {"balance:R-X": _decided(good, PLAIN)})
    assert found.built == {"balance:R-X": _card("Rank #<!-- -->5."), "balance:R-Y": bad}
    assert found.counts == {PLAIN: 1, UNDECIDED: 1}
    assert len(found.refusals) == 1 and found.refusals[0].startswith("numbers: balance:R-Y: ")
