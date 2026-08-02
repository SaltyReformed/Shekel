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

**This module owns the FORM, PATH and SCHEMA answer.  It does NOT yet own the
QUERY STRING, and saying otherwise is how this step kept getting caught.**
Three surfaces consume this same rule rather than restating one: the four form
doors above, the URL ``<int:...>`` converter (:mod:`app.url_converters`), and
the schemas' row-id field
(:class:`app.schemas.validation._helpers.RowId`).  **42
``request.args.get(..., type=int)`` call sites remain lax** -- Werkzeug catches
the ``ValueError`` so none can crash, but the coercion is a bare ``int()``, so
they read every spelling this module refuses.  They are deliberately not
converted (finding N-142, plan step X-ah): unlike the 123 path parameters and
the 75 schema fields, which are all row ids, those sites are MIXED, and
``offset=0`` / ``show_all=0`` are meaningful -- so they need a per-site ruling
and a second rule that admits zero, not this one.

A first build claimed "the ONE answer" while the path and schema surfaces were
still lax; a second claimed the FORM AND QUERY answer while the query surface
still was.  Both were refuted by adversarial review.  This paragraph is
deliberately specific about what is and is not covered.

**``isdigit()`` is the wrong predicate and no other stdlib predicate is the
right one.**  ``isdecimal()`` narrows the character set but not the
conversion: ``('1' * 4301).isdecimal()`` is ``True`` and ``int()`` still
raises on CPython's configurable 4,300-digit conversion limit
(``sys.get_int_max_str_digits()``), which a submitted field reaches
trivially.  The only sound form is to ATTEMPT the parse, which is why this
is a module and not a one-token edit.

Two rules live here, and they are deliberately together because the second
is built on the first:

* :func:`is_ascii_digits` -- what the standard library has no predicate for.
  ``isdigit`` / ``isdecimal`` / ``isnumeric`` are all Unicode-wide, so each
  admits spellings of a number that this application never emits and cannot
  round-trip.
* :func:`parse_row_id` / :func:`parse_row_ids` -- what a submitted string
  means as a database row id.

**Pure, and no Flask import**, so :mod:`app.services.mfa_service` can consume
:func:`is_ascii_digits` without breaching the services-are-isolated-from-Flask
boundary.  The caller names its own source (``request.form``,
``request.args``, ``getlist``); this module only decides what the string
means.
"""

from collections.abc import Iterable

#: The lowest id any row in this database can carry.  Every ``id`` column is
#: a PostgreSQL ``serial``, whose sequence starts at 1, and this was MEASURED
#: rather than assumed on both databases: the seeded test template and
#: PRODUCTION each carry 60 tables with an ``id`` column across ``ref`` /
#: ``auth`` / ``budget`` / ``salary`` / ``system``, and neither holds a single
#: row with ``id < 1``.  So ``"0"`` is a well-formed digit string that names no
#: row, and :func:`parse_row_id` refuses it rather than handing a caller an id
#: it would have to re-check.
MIN_ROW_ID = 1


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

    Args:
        raw: The submitted value, or ``None`` when the field was absent.

    Returns:
        The id as an ``int`` when *raw* is the canonical decimal spelling of a
        value of at least :data:`MIN_ROW_ID`; otherwise ``None``.
    """
    if raw is None or not is_ascii_digits(raw):
        return None
    try:
        row_id = int(raw)
    except ValueError:
        # NOT dead code, and the reason this function exists rather than a
        # predicate swap: ASCII digits alone do not license the conversion.
        # CPython refuses to build an int from more than
        # ``sys.get_int_max_str_digits()`` digits (4,300 by default), and the
        # ceiling is configurable at runtime, so no length constant could
        # stand in for attempting the parse.
        return None
    if row_id < MIN_ROW_ID or str(row_id) != raw:
        return None
    return row_id


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
