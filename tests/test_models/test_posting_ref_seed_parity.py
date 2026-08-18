"""Cross-migration inline-seed parity for the migration-seeded ref enums.

Introduced for the posting-ledger enums (``LedgerAccountClassEnum``,
``PostingKindEnum``, ``PostingSourceEnum``, ``LedgerAccountKindEnum``) and
since extended -- the scan is enum-driven, so every ref enum that follows the
dual-seed pattern is registered in the tuples below and needs no new harness.
The recurrence redesign's two-axis vocabulary (``RecurrenceUnitEnum``,
``PeriodPlacementEnum``, ``BusinessDayShiftEnum``; plan step R2a of
``docs/plans/implementation_plan_recurrence_redesign.md``) joined that
registration rather than duplicating this file.

Those enums follow the project's dual-seed pattern: every value is
inline-seeded by the migration that introduces it (so a bare ``flask db
upgrade`` lets ``ref_cache.init()`` resolve it BEFORE the app-layer
``seed_reference_data`` runs -- an enum member with no row is a fatal
``RuntimeError`` at app start) AND listed in ``app/ref_seeds.py`` (the
ongoing idempotent reseed).

Step 2 introduced the posting enums with a single value each (``transfer``);
Step 3 adds the ``income`` / ``expense`` kinds and a ``transaction``
source via its own migration, and later steps will add more.  This single
enum-driven scan replaces the former per-migration inline-seed check so
future additions need NO test edits: it asserts that for every member of
each enum, SOME migration inline-seeds the member's value into that enum's
OWN ``ref`` table.

Two design points make the scan precise:

  * **Statement anchoring.**  A member's value must appear INSIDE an
    ``INSERT INTO <that member's table>`` statement -- scoped from the table
    name to the end of the Python STRING LITERAL carrying it, or to the next
    SQL statement keyword within that literal, whichever comes first -- not
    merely somewhere in a file that also happens to insert into the table.
    The literal is the outer bound because it is where the statement actually
    ends: a keyword list alone left the LAST ``INSERT`` in a file running to
    end-of-file, so any quoted literal in the Python BELOW the seed satisfied
    the scan (plan step R-F2, ledger row F-2; measured honest at the time --
    none of ``'none'`` / ``'prior'`` / ``'next'`` appeared in the 1,304
    characters after ``e7a4d95c2b18``'s final seed).  Every one of the 78
    ``INSERT INTO`` occurrences in the chain sits inside a string constant and
    2 constants carry more than one statement, which is why both bounds are
    kept rather than either alone.  DOCSTRINGS are excluded: the scan asks
    what a migration EXECUTES, and prose describing a seed is not it.
    Without this scoping a
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
     ``TestRefInlineSeedParity`` scan below asserts every enum member
     is INSERTed by some migration;
  3. the ongoing idempotent reseed (``app/ref_seeds.py``'s
     ``_REF_TABLE_SEEDS``) -- the ``TestRefSeedsEnumParity`` check
     below asserts its value lists equal the enums exactly.

Leg 3 needs its own check because every test database is migration-built and
THEN idempotently reseeded, so a value missing from ``_REF_TABLE_SEEDS`` (but
present in the enum and the migration) is invisible to a migration-built
suite -- the migration already seeded it.  The omission would only fail loud
on the ``create_all`` + ``seed_reference_data`` fresh-init path (dev/test
bootstrap, the production deploy reseed), at ``ref_cache.init()`` time.  The
source-level check here catches it in the suite instead.

:class:`TestDeliberateRefSeedSurplus` is the mirror image of leg 3, and the
reason ``RecurrencePatternEnum`` is absent from the registration tuples: its
reseed list is deliberately a SUPERSET of the enum during an expand/contract
window.  Registering it above would fail on that surplus, and removing the
surplus would break the deploy's rollback image.

These are SOURCE-level guards.  The complementary RUNTIME guarantee -- that
the seeded database actually contains a row for every member -- is enforced
by the enum<->DB-row parity tests in ``tests/test_ref_cache.py`` and, at app
start, by ``ref_cache.init()`` itself (which raises if any member is
unresolved).  A migration that deletes a previously-seeded value would slip
past the source scans but be caught by those runtime tests; the layers
together are exhaustive.
"""
import ast
import pathlib
import re
from enum import Enum
from functools import lru_cache

from app.enums import (
    AmountSourceEnum,
    SettlementBasisEnum,
    BusinessDayShiftEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PeriodPlacementEnum,
    PostingKindEnum,
    PostingSourceEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.ref_seeds import _REF_TABLE_SEEDS


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


# Each migration-seeded ref enum mapped to the ``ref`` table its values are
# INSERTed into.  The scan requires each member's value to appear inside an
# ``INSERT INTO <table>`` statement for THIS table (see the module docstring
# on statement anchoring).  Registering a new dual-seeded enum here (and in
# ``_SEED_LIST_REF_ENUMS`` below) is the whole cost of covering it.
_INLINE_SEEDED_REF_ENUMS: tuple[tuple[type[Enum], str], ...] = (
    (LedgerAccountClassEnum, "ref.ledger_account_classes"),
    (LedgerAccountKindEnum, "ref.ledger_account_kinds"),
    (PostingKindEnum, "ref.posting_kinds"),
    (PostingSourceEnum, "ref.posting_sources"),
    (RecurrenceUnitEnum, "ref.recurrence_units"),
    (PeriodPlacementEnum, "ref.period_placements"),
    (BusinessDayShiftEnum, "ref.business_day_shifts"),
    (AmountSourceEnum, "ref.amount_sources"),
    (SettlementBasisEnum, "ref.settlement_bases"),
)


# Each migration-seeded ref enum mapped to its ``_REF_TABLE_SEEDS`` model-attr
# key (the first element of each ``(model_attr_name, entries)`` tuple).  Used
# by ``TestRefSeedsEnumParity`` to assert the ongoing reseed list (leg 3 of
# the dual seed) equals the enum exactly.
_SEED_LIST_REF_ENUMS: tuple[tuple[type[Enum], str], ...] = (
    (LedgerAccountClassEnum, "LedgerAccountClass"),
    (LedgerAccountKindEnum, "LedgerAccountKind"),
    (PostingKindEnum, "PostingKind"),
    (PostingSourceEnum, "PostingSource"),
    (RecurrenceUnitEnum, "RecurrenceUnit"),
    (PeriodPlacementEnum, "PeriodPlacement"),
    (BusinessDayShiftEnum, "BusinessDayShift"),
    (AmountSourceEnum, "AmountSource"),
    (SettlementBasisEnum, "SettlementBasis"),
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


# SQL statement-starting keywords used across the migration chain.  Within one
# string literal, an ``INSERT INTO <table>`` body is scoped from the table name
# to the next such keyword, so a value belonging to a following ``DELETE`` or a
# different table's ``INSERT`` in the SAME literal cannot leak into it.  All are
# upper-case with a trailing space, matching the raw-SQL keyword style, so they
# never collide with the lower-case quoted value literals the scan searches for.
#
# This is the INNER bound only.  The outer one is the literal itself
# (:func:`_executed_sql_literals`) -- see the module docstring on statement
# anchoring for why a keyword list alone could not be the whole rule.
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


@lru_cache(maxsize=None)
def _executed_sql_literals(source: str) -> tuple[str, ...]:
    """Return every non-docstring string constant in a migration module.

    The SQL a migration runs lives inside Python string literals, so a literal
    is where a statement really ends -- which is what makes it the honest outer
    bound for :func:`_insert_statement_bodies`.  Adjacent literals concatenated
    across lines (the house style for these seeds) are ONE constant to
    :mod:`ast`, so a seed written over five lines is not split.

    Docstrings are excluded because the scan asks what a migration EXECUTES:
    three migrations describe an ``INSERT INTO`` in prose, and prose about a
    seed is not a seed.

    Cached on the source text, which is what makes the parse affordable: the
    inline-seed scan asks about 129 migrations once per enum MEMBER, so an
    uncached parse ran 5,289 times for 2.04s against 129 files of real work.

    Args:
        source: Full text of a migration module.

    Returns:
        The value of every string constant that is not a docstring.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                   ast.ClassDef),
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def _insert_statement_bodies(source: str, table: str) -> list[str]:
    """Return each ``INSERT INTO <table> ...`` statement body in *source*.

    Each body runs from the ``INSERT INTO <table>`` token to the end of the
    string literal carrying it, or to the next SQL statement keyword
    (:data:`_STATEMENT_BOUNDARY`) inside that literal -- whichever comes first.
    So a value belonging to a following ``DELETE``, to a different table's
    ``INSERT``, or to any Python BELOW the seed does not bleed in.

    Args:
        source: Full text of a migration module.
        table: Schema-qualified table name (e.g. ``ref.posting_kinds``).

    Returns:
        One string per ``INSERT INTO <table>`` statement found (empty if the
        file never inserts into *table*).
    """
    insert_token = f"INSERT INTO {table}"
    bodies: list[str] = []
    for literal in _executed_sql_literals(source):
        start = literal.find(insert_token)
        while start != -1:
            after = start + len(insert_token)
            boundary = _STATEMENT_BOUNDARY.search(literal, after)
            end = boundary.start() if boundary is not None else len(literal)
            bodies.append(literal[start:end])
            start = literal.find(insert_token, after)
    return bodies


class TestTheStatementBoundary:
    """The scan's own boundary, graded on sources built to break it.

    Plan step R-F2 (ledger row **F-2**).  Every assertion below FAILS against
    the keyword-only bound this replaced, which is the standard a control has
    to meet: the first two are the defect itself, the third is the property
    that kept the keyword list, and the fourth is why a docstring is not a
    seed.  Without them the change proves nothing -- a scan that has never
    been shown to reject anything is not a scan.
    """

    #: A migration whose last seed is followed by ordinary Python holding a
    #: value literal.  Under the keyword-only bound the ``INSERT`` body ran to
    #: end-of-file and ``'beta'`` read as seeded.
    SEED_THEN_PYTHON = (
        '"""Create and seed ref.widgets."""\n'
        "_SEED_SQL = (\n"
        '    "INSERT INTO ref.widgets (name) VALUES "\n'
        "    \"('alpha') \"\n"
        '    "ON CONFLICT (name) DO NOTHING"\n'
        ")\n"
        "\n"
        "_AUDIT_NOTE = \"seeded 'beta' by hand on 2026-01-01\"\n"
        "\n"
        "\n"
        "def upgrade():\n"
        '    """Seed the table."""\n'
        "    op.execute(_SEED_SQL)\n"
    )

    def test_a_value_below_the_seed_is_not_seeded(self):
        """A literal in the Python after the last INSERT does not count."""
        bodies = _insert_statement_bodies(
            self.SEED_THEN_PYTHON, "ref.widgets",
        )

        assert bodies, "the seed statement itself must still be found"
        assert not any("'beta'" in body for body in bodies), (
            f"'beta' appears only in Python BELOW the seed, so no INSERT "
            f"seeds it; bodies were {bodies}"
        )

    def test_the_seeded_value_is_still_found(self):
        """The control above passes for the right reason, not by finding none."""
        bodies = _insert_statement_bodies(
            self.SEED_THEN_PYTHON, "ref.widgets",
        )

        assert any("'alpha'" in body for body in bodies), bodies

    def test_two_statements_in_one_literal_stay_separate(self):
        """The keyword bound still splits statements sharing a literal.

        2 of the chain's constants carry more than one ``INSERT``, so dropping
        the keyword bound in favour of the literal alone would credit one
        table's value to another's statement -- the leak the anchoring exists
        to stop, moved one level in.
        """
        source = (
            "_SEED_SQL = (\n"
            '    "INSERT INTO ref.widgets (name) VALUES (\'alpha\'); "\n'
            '    "INSERT INTO ref.gadgets (name) VALUES (\'gamma\')"\n'
            ")\n"
        )

        widgets = _insert_statement_bodies(source, "ref.widgets")

        assert any("'alpha'" in body for body in widgets), widgets
        assert not any("'gamma'" in body for body in widgets), (
            f"'gamma' belongs to ref.gadgets' statement in the same literal; "
            f"bodies were {widgets}"
        )

    def test_an_insert_described_in_a_docstring_is_not_a_seed(self):
        """Prose about a seed is not a seed."""
        source = (
            '"""Drop ref.widgets.\n'
            "\n"
            "The rows came from ``INSERT INTO ref.widgets (name) VALUES\n"
            "('delta')`` in an earlier migration.\n"
            '"""\n'
            "\n"
            "\n"
            "def upgrade():\n"
            '    """Drop it."""\n'
            '    op.execute("DROP TABLE ref.widgets")\n'
        )

        assert _insert_statement_bodies(source, "ref.widgets") == []


class TestRefInlineSeedParity:
    """Every registered ref-enum value is inline-seeded by some migration."""

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


class TestRefSeedsEnumParity:
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
        """Each registered enum's value set equals its reseed list's set."""
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


class TestDeliberateRefSeedSurplus:
    """The ``Once`` reseed entry outlives its enum member, on purpose.

    The mirror image of :class:`TestRefSeedsEnumParity`, and the reason
    ``RecurrencePatternEnum`` is NOT registered in the tuples above: plan step
    R2e-3 of ``docs/plans/implementation_plan_recurrence_redesign.md`` deleted
    the ``Once`` member while deliberately KEEPING the row and its
    ``_REF_TABLE_SEEDS`` entry, until plan step R9 drops the table (ruling
    R-R11).

    **This is an expand/contract guard, and the failure it prevents is a
    broken DEPLOY, not a wrong number.**  ``ref_cache.init`` raises
    ``RuntimeError`` for an enum member with no row and says nothing about the
    reverse, so an image that still carries the member -- which is exactly the
    image ``shekel-deploy`` auto-rolls back to when a deploy comes up
    unhealthy -- cannot boot without this row.  The container entrypoint runs
    migrations BEFORE the seed, so the seed list is what would put it back.
    Deleting the entry as "dead" turns a failed deploy into "rollback
    container also unhealthy; manual intervention required".

    Both halves are asserted together: the entry is present AND no member
    names it.  Either alone passes for the wrong reason -- the first would go
    on holding if the member came back, and the second is satisfied by
    deleting the entry, which is the failure itself.
    """

    #: The ``ref.recurrence_patterns`` seed value with no enum member.
    RETIRED_PATTERN_NAME = "Once"

    def test_the_retired_pattern_is_still_reseeded(self):
        """``_REF_TABLE_SEEDS`` still carries the row R9 will drop."""
        seeded = _seed_value_set(_REF_SEEDS_BY_MODEL["RecurrencePattern"])

        assert self.RETIRED_PATTERN_NAME in seeded, (
            f"app/ref_seeds.py must keep seeding "
            f"'{self.RETIRED_PATTERN_NAME}' until plan step R9 drops "
            f"ref.recurrence_patterns.  RecurrencePatternEnum no longer names "
            f"it (plan step R2e-3), but ref_cache.init() in the PREVIOUS "
            f"image -- the one shekel-deploy rolls back to -- still does, and "
            f"raises RuntimeError without the row."
        )

    def test_no_enum_member_names_the_retired_pattern(self):
        """The member really is gone, so the entry is a surplus not a mirror."""
        assert self.RETIRED_PATTERN_NAME not in {
            member.value for member in RecurrencePatternEnum
        }

    def test_every_other_seeded_pattern_has_a_member(self):
        """``Once`` is the ONLY surplus; a second one is a real drift.

        Without this the class would license any number of unmodelled rows.
        The picker, the write doors and ``resolve`` are all enum-driven, so a
        second surplus is a row nothing can author and nothing can read.
        """
        seeded = _seed_value_set(_REF_SEEDS_BY_MODEL["RecurrencePattern"])
        members = {member.value for member in RecurrencePatternEnum}

        assert members <= seeded, (
            f"every RecurrencePatternEnum member must be reseeded; "
            f"missing {sorted(members - seeded)}"
        )
        assert seeded - members == {self.RETIRED_PATTERN_NAME}, (
            f"unexpected unmodelled recurrence-pattern seed value(s): "
            f"{sorted(seeded - members - {self.RETIRED_PATTERN_NAME})}"
        )
