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

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError
from werkzeug.datastructures import MultiDict

from app.utils.db_errors import sqlstate_of
from app.utils.digit_strings import (
    MAX_INTEGER_COLUMN,
    MIN_INTEGER_COLUMN,
    MIN_ROW_ID,
    integer_arg,
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

    def test_a_digit_run_within_the_conversion_limit_names_no_row(self):
        """The far side of that boundary converts, and is still no row.

        Until plan step balance:X-dj this asserted ``'9' * 40`` parsed to
        its integer, on the ground that naming no row was the CALLER's
        answer -- the reconcile POST, the collateral validator and the
        companion scan each met it with an ordinary no-match.  psycopg 3
        binds an id with a server-side ``::INTEGER`` cast that refuses it,
        so those callers would raise instead; an id above
        :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN` now names no row
        HERE, before any query (ruling R-BAL211).  It still converts -- the
        ceiling refuses it, not the
        conversion limit above -- which ``int(large)`` pins.
        """
        large = "9" * 40
        assert int(large) > MAX_INTEGER_COLUMN
        assert parse_row_id(large) is None

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


class TestTheIntegerColumnRange:
    """The range every submitted integer is held to is the DATABASE's, measured.

    Plan step balance:X-dj.  The constants claim to be a PostgreSQL
    ``integer``'s range, so they are graded against the server rather than
    against a second spelling of the same two numbers: each end casts, and
    one past each end is refused with ``numeric_value_out_of_range`` -- the
    very refusal psycopg 3's bind casts turn a forged id into.
    """

    @pytest.mark.parametrize("bound", [MIN_INTEGER_COLUMN, MAX_INTEGER_COLUMN])
    def test_each_end_is_an_integer_the_server_holds(self, db, bound):
        """The server casts both ends to ``integer`` unchanged."""
        held = db.session.execute(text(f"SELECT ({bound})::integer")).scalar()
        assert held == bound

    @pytest.mark.parametrize(
        "beyond", [MIN_INTEGER_COLUMN - 1, MAX_INTEGER_COLUMN + 1],
    )
    def test_one_past_each_end_is_refused_by_the_server(self, db, beyond):
        """One past either end is SQLSTATE 22003, so the range is exact."""
        with pytest.raises(DataError) as excinfo:
            db.session.execute(text(f"SELECT ({beyond})::integer"))
        db.session.rollback()
        assert sqlstate_of(excinfo.value) == "22003", excinfo.value


class TestParseRowIdHoldsTheColumnCeiling:
    """A canonical spelling above the ``id`` column's range names no row."""

    def test_the_largest_id_a_serial_can_hold_parses(self):
        """The ceiling itself is an id."""
        assert parse_row_id(str(MAX_INTEGER_COLUMN)) == MAX_INTEGER_COLUMN

    def test_one_past_the_ceiling_names_no_row(self):
        """Canonical, round-trips, and still names no row: no column holds it."""
        beyond = str(MAX_INTEGER_COLUMN + 1)
        assert is_ascii_digits(beyond)
        assert parse_row_id(beyond) is None
        assert parse_row_ids([beyond, "12"]) == {12}


class TestIntegerArg:
    """The ``type=`` every integer query arg is read through.

    ``int()`` held to the column range, and NOTHING more: the spellings it
    admits are finding N-142's open questions (plan step X-ah), so these pin
    that it still admits every one ``int()`` does, alongside the range.
    """

    @pytest.mark.parametrize("raw, value", [
        ("12", 12),
        ("-4", -4),
        ("0", 0),
        ("007", 7),
        (" 12 ", 12),
        ("+5", 5),
        ("1_0", 10),
        ("\u0661\u0662", 12),
    ])
    def test_it_reads_exactly_what_int_reads(self, raw, value):
        """Every spelling ``int()`` accepts, unchanged -- X-ah's to rule on."""
        assert integer_arg(raw) == int(raw) == value

    @pytest.mark.parametrize("bound", [MIN_INTEGER_COLUMN, MAX_INTEGER_COLUMN])
    def test_each_end_of_the_range_is_read(self, bound):
        """Both ends are integers a query can bind."""
        assert integer_arg(str(bound)) == bound

    @pytest.mark.parametrize("raw", [
        str(MIN_INTEGER_COLUMN - 1),
        str(MAX_INTEGER_COLUMN + 1),
        "9" * 40,
        "1" * (sys.get_int_max_str_digits() + 1),
        "abc",
        "",
    ])
    def test_anything_else_raises_value_error(self, raw):
        """Out of range, too long to convert, or not a number: ``ValueError``."""
        with pytest.raises(ValueError):
            integer_arg(raw)

    def test_werkzeug_answers_the_default_for_an_out_of_range_value(self):
        """The contract every site relies on: no raise, the site's default.

        ``MultiDict.get`` catches the ``ValueError``, so an integer no column
        can hold is treated exactly as an unparseable one already was.
        """
        args = MultiDict({
            "beyond": str(MAX_INTEGER_COLUMN + 1),
            "within": str(MAX_INTEGER_COLUMN),
            "junk": "abc",
        })
        assert args.get("beyond", default=7, type=integer_arg) == 7
        assert args.get("junk", default=7, type=integer_arg) == 7
        assert args.get("within", default=7, type=integer_arg) == MAX_INTEGER_COLUMN
        assert args.get("absent", type=integer_arg) is None
