"""Tests for ``app.utils.digit_strings`` -- the one submitted-digit-string rule.

Plan step X-ae / finding N-136.  Four doors each asked "is this string a
number I can use" through ``str.isdigit()``, and every one of them was a
reachable unhandled 500.  These grade the replacement rule directly; the
four doors themselves are graded in their own route tests.

The character-set assertions below are EXHAUSTIVE over the Unicode
codepoint space rather than sampled, and each asserts a non-zero
population first, so none of them can pass by finding nothing to check.
"""

import sys

from app.utils.digit_strings import (
    MIN_ROW_ID,
    is_ascii_digits,
    parse_row_id,
    parse_row_ids,
)


def _chars_where(predicate):
    """Return every Unicode character satisfying *predicate*.

    Args:
        predicate: A one-argument callable taking a single-character str.

    Returns:
        list[str] of every matching character in the codepoint space.
    """
    return [c for c in map(chr, range(0x110000)) if predicate(c)]


def _int_raises(char):
    """Report whether ``int(char)`` raises despite ``char.isdigit()``."""
    if not char.isdigit():
        return False
    try:
        int(char)
    except ValueError:
        return True
    return False


class TestIsAsciiDigits:
    """The predicate the standard library does not offer."""

    def test_plain_ascii_digits_pass(self):
        """The spelling this application actually emits (``str(int)``)."""
        assert is_ascii_digits("0") is True
        assert is_ascii_digits("7") is True
        assert is_ascii_digits("106") is True
        assert is_ascii_digits("0123456789") is True

    def test_the_empty_string_is_not_a_number(self):
        """"" is no answer, not zero digits worth of one.

        Load-bearing at the collateral picker, which reads "" as the user's
        explicit "nothing secures this loan" rather than as a malformed id.
        """
        assert is_ascii_digits("") is False

    def test_every_non_ascii_isdigit_character_is_refused(self):
        """The whole gap between ``str.isdigit`` and this predicate.

        ``isdigit()`` is true for 888 characters; only the ten ASCII ones
        are a spelling this application emits.  Asserted over every one of
        the other 878 rather than a sample, because the population is what
        makes the old predicate wrong.
        """
        offenders = _chars_where(lambda c: c.isdigit() and not c.isascii())
        assert len(offenders) > 800, (
            f"Expected the ~878 non-ASCII isdigit characters, got "
            f"{len(offenders)} -- has the Unicode data version changed?"
        )
        assert all(is_ascii_digits(c) is False for c in offenders)

    def test_the_forms_int_would_have_accepted_are_refused(self):
        """``int()`` is laxer than the wire format, and each gap is closed.

        Every string here converts cleanly under a bare ``int()``, so a
        parse-only fix would have kept accepting all of them -- giving one
        row id many spellings.
        """
        assert int(" 12 ") == 12
        assert int("+12") == 12
        assert int("1_0") == 10
        assert int("\N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT TWO}") == 12

        assert is_ascii_digits(" 12 ") is False
        assert is_ascii_digits("+12") is False
        assert is_ascii_digits("1_0") is False
        assert is_ascii_digits(
            "\N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT TWO}",
        ) is False

    def test_mixed_and_signed_strings_are_refused(self):
        """A digit run is the whole string or it is not a digit run."""
        assert is_ascii_digits("12a") is False
        assert is_ascii_digits("-5") is False
        assert is_ascii_digits("1.0") is False
        assert is_ascii_digits("١2") is False


class TestParseRowId:
    """"Turn a submitted string into a row id" -- the one implementation."""

    def test_a_plain_digit_string_is_its_id(self):
        """The happy path every door depends on."""
        assert parse_row_id("106") == 106
        assert parse_row_id("1") == MIN_ROW_ID

    def test_leading_zeros_name_no_row(self):
        """One id, ONE spelling -- and ASCII alone does not deliver that.

        An adversarial review of the first build caught this: the module
        argues that the app emits ids as ``str(int)`` "so anything else is a
        value no form of ours produced", and then accepted ``"007"`` as row
        7 under that same argument.  ``str(7)`` is never ``"007"``.  Without
        the round-trip a row has unboundedly many spellings on the very rule
        that exists to give it one.
        """
        assert parse_row_id("007") is None
        assert parse_row_id("0000007") is None
        assert parse_row_id("0" * 100 + "7") is None
        # The canonical spelling of the same row still resolves.
        assert parse_row_id("7") == 7

    def test_a_bytes_value_names_no_row(self):
        """``bytes`` has BOTH ``.isascii()`` and ``.isdigit()``.

        So it slips through a predicate that only asks those two, and
        ``int(b"12")`` is ``12`` -- a non-``str`` silently satisfying a
        ``str``-hinted parameter.  The round-trip closes it for free:
        ``str(12)`` is ``"12"``, which is not equal to ``b"12"``.
        """
        assert b"12".isascii() and b"12".isdigit()
        assert int(b"12") == 12
        assert parse_row_id(b"12") is None

    def test_every_isdigit_character_int_refuses_returns_none(self):
        """The crash itself: 128 characters that pass ``isdigit()`` and raise.

        Exhaustive rather than sampled, because a spot check on
        ``'\\N{SUPERSCRIPT TWO}'`` is what the four doors already had -- a
        rule believed to hold for a set nobody enumerated.
        """
        offenders = _chars_where(_int_raises)
        assert len(offenders) > 100, (
            f"Expected the ~128 int()-raising isdigit characters, got "
            f"{len(offenders)} -- has the Unicode data version changed?"
        )
        assert all(parse_row_id(c) is None for c in offenders)

    def test_a_digit_run_past_the_conversion_limit_returns_none(self):
        """The reason this is a parse and not a predicate swap.

        These digits are ASCII, so no character-set predicate can refuse
        them; CPython refuses the CONVERSION instead, and a submitted field
        reaches the limit trivially.  This is the test that keeps
        ``parse_row_id``'s ``except ValueError`` arm from being dead code.
        """
        oversized = "1" * (sys.get_int_max_str_digits() + 1)
        assert oversized.isascii() and oversized.isdigit()
        assert is_ascii_digits(oversized) is True
        assert parse_row_id(oversized) is None

    def test_a_digit_run_within_the_conversion_limit_parses(self):
        """The far side of that boundary still converts.

        Paired with the test above so the pair pins the limit rather than
        just asserting that something long fails.  A 40-digit id names no
        row, but naming no row is the caller's answer to give, not the
        parser's -- and it is measured: the reconcile POST, the collateral
        validator and the companion scan all answer it without raising.
        """
        large = "9" * 40
        assert parse_row_id(large) == int(large)

    def test_zero_names_no_row(self):
        """Every id column is a ``serial``, whose sequence starts at 1."""
        assert parse_row_id("0") is None
        assert parse_row_id("00") is None

    def test_an_absent_field_returns_none(self):
        """``request.args.get`` yields ``None`` for a field nobody sent."""
        assert parse_row_id(None) is None

    def test_an_empty_field_returns_none(self):
        """A submitted-but-blank field names no row either."""
        assert parse_row_id("") is None


class TestParseRowIds:
    """The multi-valued form, for a checkbox submitted once per tick."""

    def test_the_named_rows_survive_and_the_rest_are_dropped(self):
        """A junk value costs its own id, not the whole submission.

        The posture the reconcile writer already takes toward an id that is
        real but not the user's: it simply matches nothing.
        """
        assert parse_row_ids(
            ["12", "\N{SUPERSCRIPT TWO}", "34", "", "0", "-5", "1_0"],
        ) == {12, 34}

    def test_duplicates_collapse(self):
        """The same row ticked twice is one row.

        ``"012"`` is NOT a second spelling of it -- it names no row at all
        (see :meth:`TestParseRowId.test_leading_zeros_name_no_row`), so it is
        dropped rather than merged.
        """
        assert parse_row_ids(["12", "12"]) == {12}
        assert parse_row_ids(["12", "012"]) == {12}

    def test_no_values_is_an_empty_set(self):
        """Submitting the form with nothing ticked names nothing."""
        assert parse_row_ids([]) == set()

    def test_only_junk_is_an_empty_set(self):
        """Distinguishable from a partial parse: nothing survives."""
        assert parse_row_ids(["\N{SUPERSCRIPT TWO}", "abc", ""]) == set()
