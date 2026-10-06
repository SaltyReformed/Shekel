"""
Shekel Budget App -- submitted digit strings

The ONE answer to "is this submitted string made of digits, and what row
does it name" (plan step X-ae, finding N-136).  Four doors each asked it
their own way through ``str.isdigit()``, and every one of them raised on
input a form can carry, into an application that registers no ``ValueError``
arm (``app/error_handlers.py``).  ``isdigit()`` is true for **888**
characters, and the two failure modes differ by door:

* at the three id parses, **128** of those characters make ``int()`` raise
  (measured, ``unicodedata`` 16.0.0 -- ``'\N{SUPERSCRIPT TWO}'`` is one);
* at the TOTP shape check there is no ``int()`` at all -- an all-``isdigit()``
  code of six non-ASCII characters reaches ``hmac.compare_digest``, which
  raises ``TypeError`` on any non-ASCII ``str``.

**This module owns the FORM, PATH and SCHEMA spelling, and the RANGE every
submitted integer is held to -- the query string's included.  It does NOT own
the QUERY STRING's SPELLING, and saying otherwise is how this step kept getting
caught.**  Three surfaces consume the spelling rule rather than restating one:
the four form doors above, the URL ``<int:...>`` converter
(:mod:`app.url_converters`), and the schemas' row-id field
(:class:`app.schemas.validation._helpers.RowId`).  **The query string's
integer args remain lax in SPELLING** -- each is parsed by :func:`integer_arg`,
which is a bare ``int()`` held to a PostgreSQL ``integer``'s range, so they
read every spelling this module refuses.  That is deliberate (finding N-142,
plan step X-ah): unlike the 123 path parameters and the 75 schema fields, which
are all row ids, those sites are MIXED, and ``offset=0`` / ``show_all=0`` are
meaningful -- so they need a per-site ruling and a second rule that admits
zero, not this one.  The RANGE is not a per-site question, which is why it is
here: see :data:`MAX_INTEGER_COLUMN` for what an integer outside it does to a
query.

A first build claimed "the ONE answer" while the path and schema surfaces were
still lax; a second claimed the FORM AND QUERY answer while the query surface
still was.  Both were refuted by adversarial review.  This paragraph is
deliberately specific about what is and is not covered.

**``isdigit()`` is the wrong predicate and no other stdlib predicate is the
right one.**  ``isdecimal()`` narrows the character set but not the
conversion: ``('1' * 4301).isdecimal()`` is ``True`` and ``int()`` still
raises on CPython's configurable 4,300-digit conversion limit
(``sys.get_int_max_str_digits()``), which a submitted field reaches
trivially.  A sound form must attempt the parse or bound the length before
it, and this module attempts it (:func:`parse_row_id` says why), which is
why this is a module and not a one-token edit.

Three rules live here, and they are deliberately together because each
later one is built on an earlier one:

* :func:`is_ascii_digits` -- what the standard library has no predicate for.
  ``isdigit`` / ``isdecimal`` / ``isnumeric`` are all Unicode-wide, so each
  admits spellings of a number that this application never emits and cannot
  round-trip.
* :data:`MIN_INTEGER_COLUMN` / :data:`MAX_INTEGER_COLUMN` and
  :func:`integer_arg` -- the range a submitted integer must lie in before any
  query binds it.
* :func:`parse_row_id` / :func:`parse_row_ids` -- what a submitted string
  means as a database row id: the spelling rule AND the range.

**Pure, and no Flask import**, so :mod:`app.services.mfa_service` can consume
:func:`is_ascii_digits` without breaching the services-are-isolated-from-Flask
boundary.  The caller names its own source (``request.form``,
``request.args``, ``getlist``); this module only decides what the string
means.
"""

from collections.abc import Iterable

#: The lowest id any row in this database can carry.  Every ``id`` column is
#: a PostgreSQL ``serial`` (``system.audit_log``'s a ``bigserial``), whose
#: sequence starts at 1, and this was MEASURED
#: rather than assumed on both databases: the seeded test template and
#: PRODUCTION each carry 60 tables with an ``id`` column across ``ref`` /
#: ``auth`` / ``budget`` / ``salary`` / ``system``, and neither holds a single
#: row with ``id < 1``.  So ``"0"`` is a well-formed digit string that names no
#: row, and :func:`parse_row_id` refuses it rather than handing a caller an id
#: it would have to re-check.
MIN_ROW_ID = 1

#: The range of a PostgreSQL ``integer`` (``int4``) column -- and so of every
#: row id a request can name, since every such ``id`` is a ``serial``: an
#: ``integer`` fed by a sequence.  (The one ``bigserial``,
#: ``system.audit_log.id``, is named by no route.)  ONE home for both ends
#: (plan step balance:X-dj): the row-id ceiling in :func:`parse_row_id`, the
#: schemas' :class:`~app.schemas.validation._helpers.RowId`, the recurrence
#: form's count fields (:mod:`app.schemas.validation._recurrence`) and every
#: query-string integer (:func:`integer_arg`) read it.
#:
#: **Why a submitted integer is held to it before it reaches a query, not only
#: before a write.**  SQLAlchemy's psycopg 3 dialect sends an ``Integer`` bind
#: with a server-side cast (``%(id_1)s::INTEGER``), so a value outside this
#: range is refused by the CAST -- SQLSTATE 22003, a ``DataError`` -- before
#: any comparison runs.  psycopg2 inlined the literal, which PostgreSQL types
#: as ``bigint`` up to 2^63-1 and ``numeric`` past it, so the comparison ran in
#: the wider type and simply matched no row; on psycopg 3 the same forged id is
#: an unhandled 500.  Measured 2026-10-05 on psycopg 3.3.6 and SQLAlchemy 2.1.3:
#: ``TestTheOversizedPathSegment``'s forty-nines path segment, which answered
#: 404 under psycopg2, raised that ``DataError`` until :func:`parse_row_id`
#: held its ceiling.
MIN_INTEGER_COLUMN = -2_147_483_648
MAX_INTEGER_COLUMN = 2_147_483_647


def is_ascii_digits(value: str) -> bool:
    """Report whether *value* is one or more ASCII decimal digits.

    The predicate the standard library does not offer.  ``str.isdigit`` is
    true for superscripts and every non-Latin digit script; ``str.isdecimal``
    drops the superscripts but keeps the scripts; ``str.isnumeric`` is wider
    still.  This application emits ids and codes as ``str(int)`` -- ASCII
    ``0``-``9``, no sign, no separator, no surrounding space -- so anything
    else is a value no form of ours produced.

    Admitting the wider sets is not merely untidy.  ``int('١٠٦')``
    is ``106``, so a Unicode-wide predicate gives one row id many spellings;
    and ``hmac.compare_digest`` raises ``TypeError`` on any non-ASCII string,
    which is why :mod:`app.services.mfa_service` consumes this same rule for
    its TOTP shape check rather than restating one of its own.

    Note that a true answer does NOT license ``int()``: an arbitrarily long
    run of ASCII digits still exceeds CPython's integer-conversion limit.
    :func:`parse_row_id` attempts the parse for exactly that reason.

    Args:
        value: The submitted string to test.  The empty string is false --
            it is not "zero digits worth of number", it is no answer.

    Returns:
        True when *value* is non-empty and every character is an ASCII
        decimal digit.
    """
    return value.isascii() and value.isdigit()


def parse_row_id(raw: str | None) -> int | None:
    """Return the row id *raw* names, or ``None`` when it names none.

    The single implementation of "turn a submitted string into a row id",
    replacing the ``int(raw) if raw.isdigit() else ...`` restatements that
    three route files each carried.  It does not raise for any ``str`` or
    ``None`` -- the whole domain its signature declares -- so no caller needs
    an exception arm and no door can 500 on forged input.  (It is not
    defensive beyond that domain: an ``int`` argument is a caller bug and
    raises ``AttributeError``, which is what a wrong type should do.)

    **One row id has exactly one accepted spelling**, and the round-trip below
    is what makes that true rather than nearly true.  Refusing the non-ASCII
    scripts is not sufficient on its own: ``"007"``, ``"0000007"`` and 100
    leading zeros are all ASCII digits that ``int()`` reads as ``7``, so
    without the round-trip a row would have unboundedly many spellings on the
    very rule that exists to give it one.  The test is stated as "the string is
    what ``str`` would have produced from the id", which is exactly how every
    template emits one.

    ``None`` means only "this string does not name a row".  It does not mean
    "the row is missing" or "the row is not yours" -- those are the caller's
    to answer, and every consumer here already re-scopes the id it gets
    (owner-and-account filters at the reconcile writer, ownership checks at
    the collateral validator and the companion list), so a parsed id is a
    lookup key and never an authorization decision.

    **And it names a value an ``id`` column can hold.**  A canonical spelling
    above :data:`MAX_INTEGER_COLUMN` names no row for the same reason ``"0"``
    does, and refusing it here is what keeps it from reaching a query whose
    bind cast would raise (see :data:`MAX_INTEGER_COLUMN`).

    Args:
        raw: The submitted value, or ``None`` when the field was absent.

    Returns:
        The id as an ``int`` when *raw* is the canonical decimal spelling of a
        value from :data:`MIN_ROW_ID` to :data:`MAX_INTEGER_COLUMN`; otherwise
        ``None``.
    """
    if raw is None or not is_ascii_digits(raw):
        return None
    try:
        row_id = int(raw)
    except ValueError:
        # NOT dead code, and the reason this function exists rather than a
        # predicate swap: ASCII digits alone do not license the conversion.
        # CPython refuses to build an int from more than
        # ``sys.get_int_max_str_digits()`` digits (4,300 by default).  Since
        # the ceiling below, a length test could stand in for this arm -- a
        # canonical id has at most ten digits, and the limit cannot be set
        # under 640 (zero lifts it) -- but it would be a second statement of
        # :data:`MAX_INTEGER_COLUMN`, so the parse is attempted and the range
        # is stated once, below.
        return None
    if not MIN_ROW_ID <= row_id <= MAX_INTEGER_COLUMN or str(row_id) != raw:
        return None
    return row_id


def integer_arg(raw: str) -> int:
    """Return *raw* as an integer a PostgreSQL ``integer`` can hold.

    The ``type=`` of every integer ``request.args.get`` (plan step
    balance:X-dj).  Werkzeug's ``MultiDict.get`` catches the ``ValueError``
    and answers the site's default, so an out-of-range value is treated
    exactly as an unparseable one already was, and no query string can hand a
    query an integer its bind cast refuses (see :data:`MAX_INTEGER_COLUMN`).

    **It is ``int()`` and the range, and nothing more, on purpose.**  Which
    spellings a query arg may use (a non-ASCII digit, surrounding space, a
    sign, a leading zero), whether zero is meaningful, and which args are ids
    are finding N-142's open per-site questions (plan step X-ah); this answers
    none of them.  The one behaviour it moves is where the default takes over:
    ``int()`` already raised beyond ``sys.get_int_max_str_digits()`` digits,
    so a site's default already applied past ten to the 4,300th, and it now
    applies past the column's range.

    Args:
        raw: The submitted value, as Werkzeug passes it.

    Returns:
        The integer *raw* spells.

    Raises:
        ValueError: ``int()`` does not read *raw*, or the integer lies outside
            :data:`MIN_INTEGER_COLUMN` to :data:`MAX_INTEGER_COLUMN`.
    """
    value = int(raw)
    if not MIN_INTEGER_COLUMN <= value <= MAX_INTEGER_COLUMN:
        raise ValueError("outside a PostgreSQL integer's range")
    return value


def parse_row_ids(raws: Iterable[str]) -> set[int]:
    """Return the set of row ids *raws* names, dropping every value that names none.

    The multi-valued form of :func:`parse_row_id`, for a form field submitted
    once per ticked checkbox (``request.form.getlist``).  Dropping the
    unparseable rather than refusing the whole submission is the same posture
    the consumers take toward an id that is real but not the user's: it
    simply matches nothing.  A caller that must distinguish "you ticked
    something impossible" from "you ticked nothing" wants
    :func:`parse_row_id` per value instead.

    Args:
        raws: The submitted values, in any order and with any duplicates.

    Returns:
        The distinct ids named, as a set.  Empty when *raws* is empty or
        names nothing.
    """
    return {
        row_id
        for raw in raws
        if (row_id := parse_row_id(raw)) is not None
    }
