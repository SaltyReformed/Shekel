"""Cross-migration inline-seed parity for the posting-ledger ref enums.

The posting-ledger reference enums (``LedgerAccountClassEnum``,
``PostingKindEnum``, ``PostingSourceEnum``) follow the project's dual-seed
pattern: every value is inline-seeded by the migration that introduces it
(so a bare ``flask db upgrade`` lets ``ref_cache.init()`` resolve it BEFORE
the app-layer ``seed_reference_data`` runs -- an enum member with no row is
a fatal ``RuntimeError`` at app start) AND listed in ``app/ref_seeds.py``
(the ongoing idempotent reseed).

Step 2 introduced these enums with a single value each (``transfer``);
Step 3 adds the ``income`` / ``expense`` kinds and a ``transaction``
source via its own migration, and later steps will add more.  This single
enum-driven scan replaces the former per-migration inline-seed check so
future additions need NO test edits: it asserts that for every member of
each enum, SOME migration inline-seeds the member's value into that enum's
OWN ``ref`` table.

Two design points make the scan precise:

  * **Statement anchoring.**  A member's value must appear INSIDE an
    ``INSERT INTO <that member's table>`` statement -- scoped from the table
    name up to the next SQL statement keyword -- not merely somewhere in a
    file that also happens to insert into the table.  Without this scoping a
    multi-table migration (``f5037400dc5e`` seeds all three posting-ledger
    tables in one file) would let any literal in the file satisfy any of its
    tables, and a value named only in a downgrade ``DELETE`` would
    masquerade as seeded.  Statement anchoring ties the value to the exact
    INSERT that seeds it -- e.g. ``LedgerAccountClassEnum.INCOME``
    (``'Income'``) is credited only to ``ref.ledger_account_classes``,
    never to ``ref.posting_kinds`` whose own ``INCOME`` is ``'income'``.
  * **Quoted SQL-literal form.**  ``'income'`` (single-quoted) is a second
    discriminator: it appears in inline-seed SQL, never in the unquoted
    prose docstrings (which write ``income`` in backticks), so a value
    named only in documentation does not satisfy the check.

The dual seed has three legs, each guarded by a distinct layer:

  1. the enum (``app/enums.py``) is the source of truth for valid values;
  2. the migration inline-seed (the bare-``flask db upgrade`` path) -- the
     ``TestPostingRefInlineSeedParity`` scan below asserts every enum member
     is INSERTed by some migration;
  3. the ongoing idempotent reseed (``app/ref_seeds.py``'s
     ``_REF_TABLE_SEEDS``) -- the ``TestPostingRefSeedsEnumParity`` check
     below asserts its value lists equal the enums exactly.

Leg 3 needs its own check because every test database is migration-built and
THEN idempotently reseeded, so a value missing from ``_REF_TABLE_SEEDS`` (but
present in the enum and the migration) is invisible to a migration-built
suite -- the migration already seeded it.  The omission would only fail loud
on the ``create_all`` + ``seed_reference_data`` fresh-init path (dev/test
bootstrap, the production deploy reseed), at ``ref_cache.init()`` time.  The
source-level check here catches it in the suite instead.

These are SOURCE-level guards.  The complementary RUNTIME guarantee -- that
the seeded database actually contains a row for every member -- is enforced
by the enum<->DB-row parity tests in ``tests/test_ref_cache.py`` and, at app
start, by ``ref_cache.init()`` itself (which raises if any member is
unresolved).  A migration that deletes a previously-seeded value would slip
past the source scans but be caught by those runtime tests; the layers
together are exhaustive.
"""
import pathlib
import re
from enum import Enum

from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.ref_seeds import _REF_TABLE_SEEDS


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


# Each migration-seeded posting-ledger ref enum mapped to the ``ref`` table
# its values are INSERTed into.  The scan requires each member's value to
# appear inside an ``INSERT INTO <table>`` statement for THIS table (see the
# module docstring on statement anchoring).
_INLINE_SEEDED_REF_ENUMS: tuple[tuple[type[Enum], str], ...] = (
    (LedgerAccountClassEnum, "ref.ledger_account_classes"),
    (LedgerAccountKindEnum, "ref.ledger_account_kinds"),
    (PostingKindEnum, "ref.posting_kinds"),
    (PostingSourceEnum, "ref.posting_sources"),
)


# Each posting-ledger ref enum mapped to its ``_REF_TABLE_SEEDS`` model-attr
# key (the first element of each ``(model_attr_name, entries)`` tuple).  Used
# by ``TestPostingRefSeedsEnumParity`` to assert the ongoing reseed list (leg
# 3 of the dual seed) equals the enum exactly.
_SEED_LIST_REF_ENUMS: tuple[tuple[type[Enum], str], ...] = (
    (LedgerAccountClassEnum, "LedgerAccountClass"),
    (LedgerAccountKindEnum, "LedgerAccountKind"),
    (PostingKindEnum, "PostingKind"),
    (PostingSourceEnum, "PostingSource"),
)


# ``_REF_TABLE_SEEDS`` keyed by model-attr name.  Entries are either bare
# strings (``name`` only) or dicts carrying non-name columns (e.g. the
# ``LedgerAccountClass`` ``is_debit_normal`` rows); :func:`_seed_value_set`
# normalises both to the set of ``name`` values.
_REF_SEEDS_BY_MODEL: dict[str, list] = dict(_REF_TABLE_SEEDS)


def _seed_value_set(entries: list) -> set[str]:
    """Return the set of ``name`` values in a ``_REF_TABLE_SEEDS`` entry list.

    Args:
        entries: One ``_REF_TABLE_SEEDS`` value list -- bare strings, dicts
            with a ``name`` key, or a mix.

    Returns:
        The ``name`` value of every entry.
    """
    return {
        entry["name"] if isinstance(entry, dict) else entry
        for entry in entries
    }


# SQL statement-starting keywords used across the migration chain.  An
# ``INSERT INTO <table>`` statement body is scoped from the table name to
# the next such keyword (or end of file), so a value belonging to a
# following ``DELETE`` or a different table's ``INSERT`` cannot leak into
# it.  All are upper-case with a trailing space, matching the raw-SQL
# keyword style, so they never collide with the lower-case quoted value
# literals the scan searches for.
_STATEMENT_BOUNDARY = re.compile(
    r"INSERT INTO |DELETE FROM |UPDATE |DROP |CREATE |ALTER "
)


def _migration_sources() -> dict[str, str]:
    """Return ``{filename: source}`` for every migration script.

    ``migrations/versions`` has no ``__init__.py`` and holds only migration
    modules, so a non-recursive ``*.py`` glob captures exactly the chain.
    """
    return {
        path.name: path.read_text()
        for path in _MIGRATIONS_DIR.glob("*.py")
    }


def _insert_statement_bodies(source: str, table: str) -> list[str]:
    """Return each ``INSERT INTO <table> ...`` statement body in *source*.

    Each body runs from the ``INSERT INTO <table>`` token up to the next SQL
    statement keyword (:data:`_STATEMENT_BOUNDARY`) or end of *source*, so a
    value belonging to a following ``DELETE`` or a different table's
    ``INSERT`` -- common in a multi-statement migration -- does not bleed in.

    Args:
        source: Full text of a migration module.
        table: Schema-qualified table name (e.g. ``ref.posting_kinds``).

    Returns:
        One string per ``INSERT INTO <table>`` statement found (empty if the
        file never inserts into *table*).
    """
    insert_token = f"INSERT INTO {table}"
    bodies: list[str] = []
    start = source.find(insert_token)
    while start != -1:
        after = start + len(insert_token)
        boundary = _STATEMENT_BOUNDARY.search(source, after)
        end = boundary.start() if boundary is not None else len(source)
        bodies.append(source[start:end])
        start = source.find(insert_token, after)
    return bodies


class TestPostingRefInlineSeedParity:
    """Every posting-ledger enum value is inline-seeded by some migration."""

    def test_every_member_inline_seeded_by_some_migration(self):
        """Each enum value sits inside an ``INSERT INTO`` its own ref table."""
        sources = _migration_sources()
        for enum_cls, table in _INLINE_SEEDED_REF_ENUMS:
            for member in enum_cls:
                literal = f"'{member.value}'"
                covered = any(
                    any(literal in body
                        for body in _insert_statement_bodies(src, table))
                    for src in sources.values()
                )
                assert covered, (
                    f"{enum_cls.__name__}.{member.name} ('{member.value}') "
                    f"is not inline-seeded by any migration's "
                    f"'INSERT INTO {table} ...' -- a bare `flask db upgrade` "
                    f"would leave ref_cache.init() unable to resolve it. "
                    f"Add it to the introducing migration's inline seed "
                    f"(and to app/ref_seeds.py)."
                )


class TestPostingRefSeedsEnumParity:
    """The ongoing reseed list (``_REF_TABLE_SEEDS``) equals each enum exactly.

    Leg 3 of the dual seed (see the module docstring).  A migration-built test
    suite cannot otherwise catch a value present in the enum + migration but
    MISSING from ``app/ref_seeds.py``: the migration already seeded the row, so
    every test DB has it.  Such an omission would only surface on the
    ``create_all`` + ``seed_reference_data`` fresh-init path, at
    ``ref_cache.init()`` time.  This source-level set equality catches it in
    the suite, in both directions (missing OR extra reseed value).
    """

    def test_ref_seeds_lists_equal_enums(self):
        """Each posting-ledger enum's value set equals its reseed list's set."""
        for enum_cls, model_attr in _SEED_LIST_REF_ENUMS:
            seed_values = _seed_value_set(_REF_SEEDS_BY_MODEL[model_attr])
            enum_values = {member.value for member in enum_cls}
            assert seed_values == enum_values, (
                f"app/ref_seeds.py _REF_TABLE_SEEDS['{model_attr}'] = "
                f"{sorted(seed_values)} does not equal "
                f"{enum_cls.__name__} values {sorted(enum_values)}. "
                f"The ongoing idempotent reseed (the create_all + "
                f"seed_reference_data fresh-init path) would diverge from the "
                f"enum -- add the missing value to _REF_TABLE_SEEDS (or remove "
                f"the stray one) so all three dual-seed legs agree."
            )
