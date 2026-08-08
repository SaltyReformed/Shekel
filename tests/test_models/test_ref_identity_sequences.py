"""Every ``ref`` identity sequence can hand out an id no row already holds.

Plan step **R-F1** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- finding **F-1** -- and the migration that closes it,
``c7f3a9d1e864_resync_lagging_ref_identity_sequences.py``.

**The defect this file guards.**  Seeding a ref table with literal ids
(``INSERT INTO ref.goal_modes (id, name) VALUES (1, 'Fixed'), ...``) does not
advance the table's identity sequence, so the sequence still offers id 1 while
the table already holds it.  Nothing exercises that until a value is ADDED to
the enum: ``ref_seeds.seed_reference_data`` then emits an id-less INSERT, asks
the sequence for an id, gets one that exists, and fails on the primary key --
at entrypoint step 4, mid-deploy, with the migrations of step 3 already
applied.

**Why the gate is over EVERY ref sequence, discovered by query.**  Five tables
were in that state before ``c7f3a9d1e864``, but naming them here would make
this a record of a measurement rather than a rule.  The scan below asks
PostgreSQL which ``ref`` columns draw from a sequence and checks all of them,
so a ref table written next year is covered on the day it is created -- a
claim :class:`TestTheGateCoversATableThatDoesNotExistYet` demonstrates by
building such a table rather than asserting it in prose.

**Two tests exist because adversarial review proved their absence.**  The
first draft of this file was mutated so the shipped statement read
``GREATEST(max(id) - 1, last_value)`` -- a repair that does NOT repair -- and
every test stayed green, because nothing here ever ran the statement against a
BROKEN sequence.  :class:`TestTheRepairActuallyRepairs` is that missing test.
The same review weakened the gate's own predicate from ``<=`` to ``<`` and the
suite stayed green, because the only negative control pushed a sequence
strictly behind its data rather than to the ``next_id == max_id`` boundary,
which is the exact-collision case; that boundary is now its own control, and
it attempts the colliding INSERT rather than assuming it.

**Assertions are cross-checked, not self-checked.**  The coverage test does
not assert a threshold ("at least 20 tables") -- a threshold with slack is what
let a mutation drop three tables from the scan unnoticed.  It enumerates the
``ref`` sequences a SECOND, independent way (``pg_sequences``) and requires the
two sets to be equal, and it names the three tables whose by-name coverage was
retired from ``test_recurrence_ref_tables_migration.py`` when this file
generalised it.

**On perturbing sequences in a test.**  ``setval`` is NOT transactional: it
survives a rollback.  The real isolation guarantee is stronger than that,
though, and worth stating correctly -- ``tests/conftest.py``'s autouse ``db``
fixture DROPs and re-clones the whole worker database from
``shekel_test_template`` before EVERY test, so nothing done here can reach the
next test at all.  The controls below still capture and restore each sequence
through :func:`_sequence_restored`, because a test should not depend on the
fixture's cleanup strategy to be correct -- and that helper rolls the session
back BEFORE restoring, so a failed statement inside the block cannot turn the
restore into a ``PendingRollbackError`` that replaces the real error.
"""
from __future__ import annotations

import re
from contextlib import ExitStack, contextmanager
from typing import NamedTuple

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests._test_helpers import load_migration_module


_MIGRATION_FILENAME = "c7f3a9d1e864_resync_lagging_ref_identity_sequences.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)
_REVISION = "c7f3a9d1e864"

# Pylint: ``protected-access`` -- the migration's private constants are read
# off the module deliberately.  Retyping them here would let the test drift
# from the statements that actually ship, which is the one thing these
# assertions exist to prevent.
# pylint: disable=protected-access
_LAGGING_REF_TABLES: tuple[str, ...] = _MIGRATION._LAGGING_REF_TABLES
_RESYNC_SQL: tuple[str, ...] = _MIGRATION._RESYNC_SQL
_RESYNC_SQL_FOR = _MIGRATION._resync_sql
# pylint: enable=protected-access


#: The five tables measured behind their data on ``shekel-prod-db``, the dev
#: clone and ``shekel_test_template`` on 2026-08-08.  Written out here rather
#: than imported so the migration's own tuple is checked against a second
#: statement of the measurement rather than against itself.
_MEASURED_LAGGING_TABLES: frozenset[str] = frozenset({
    "goal_modes",
    "income_units",
    "user_roles",
    "compounding_frequencies",
    "employer_contribution_types",
})

#: The three tables ``TestIdentitySequenceInStep`` asserted BY NAME in
#: ``test_recurrence_ref_tables_migration.py`` before this file generalised the
#: property.  Named here so that generalisation is a strict widening: a scan
#: that silently stopped reaching them would otherwise be invisible, which
#: adversarial review demonstrated with a mutation dropping exactly these
#: three that left the suite green.
_TABLES_FORMERLY_COVERED_BY_NAME: frozenset[str] = frozenset({
    "recurrence_units",
    "period_placements",
    "business_day_shifts",
})

#: Pulls the sequence out of a ``SERIAL`` column default, which PostgreSQL
#: stores as ``nextval('ref.goal_modes_id_seq'::regclass)``.  Parsed rather
#: than resolved through ``pg_get_serial_sequence`` so that a sequence the
#: column draws from but does not OWN -- a shape that function reports as NULL
#: -- is still discovered.
_NEXTVAL_DEFAULT = re.compile(r"nextval\('([^']+)'::regclass\)")


class _SequenceColumn(NamedTuple):
    """One ``ref`` column whose values come from a sequence."""

    table: str
    column: str
    sequence: str


def _ref_sequence_columns(session) -> list[_SequenceColumn]:
    """Return every ``ref`` column that draws its value from a sequence.

    Discovered through ``information_schema``, which covers both spellings --
    a ``SERIAL`` column (a ``nextval`` default) and a ``GENERATED AS IDENTITY``
    column -- and does not assume the column is called ``id`` or that the
    table is unpartitioned.  Those three assumptions were blind spots in this
    file's first draft, each demonstrated reachable by adversarial review.

    Args:
        session: SQLAlchemy session bound to the test database.

    Returns:
        One entry per sequence-backed column, sorted by table then column.
    """
    rows = session.execute(text(
        "SELECT c.table_name, c.column_name, c.column_default, c.is_identity "
        "FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema "
        " AND t.table_name = c.table_name "
        "WHERE c.table_schema = 'ref' "
        "  AND t.table_type = 'BASE TABLE' "
        "  AND (c.is_identity = 'YES' OR c.column_default LIKE 'nextval(%')"
    )).fetchall()

    found: list[_SequenceColumn] = []
    for table, column, column_default, is_identity in rows:
        sequence: str | None = None
        if column_default is not None:
            match = _NEXTVAL_DEFAULT.search(column_default)
            if match is not None:
                sequence = match.group(1)
        if sequence is None and is_identity == "YES":
            # An identity column carries no default; the catalogue knows its
            # sequence.  Safe as a standalone scalar query -- the relation
            # demonstrably exists, having just come out of the catalogue.
            sequence = session.execute(text(
                "SELECT pg_get_serial_sequence(:t, :c)"
            ), {"t": f"ref.{table}", "c": column}).scalar()
        assert sequence is not None, (
            f"ref.{table}.{column} is sequence-backed but no sequence could "
            f"be resolved for it (default={column_default!r}, "
            f"is_identity={is_identity!r}) -- the gate would skip it silently."
        )
        if "." not in sequence:
            sequence = f"ref.{sequence}"
        found.append(_SequenceColumn(table, column, sequence))
    return sorted(found)


def _sequence_for(session, table: str) -> str:
    """Return the sequence backing one ``ref`` table's ``id`` column.

    Args:
        session: SQLAlchemy session bound to the test database.
        table: Bare ``ref`` table name.

    Returns:
        The schema-qualified sequence name, resolved from the catalogue.
    """
    for entry in _ref_sequence_columns(session):
        if entry.table == table and entry.column == "id":
            return entry.sequence
    raise AssertionError(f"ref.{table}.id has no sequence in the catalogue")


def _ref_sequences_from_catalogue(session) -> set[str]:
    """Return every sequence in the ``ref`` schema, enumerated independently.

    The cross-check for :func:`_ref_sequence_columns`.  Asking a different
    catalogue a different question is what makes the coverage assertion mean
    something: a scan that narrows shows up as an unmatched sequence here
    rather than as a smaller number nobody notices.

    Args:
        session: SQLAlchemy session bound to the test database.

    Returns:
        Schema-qualified sequence names.
    """
    return {
        f"ref.{name}"
        for name in session.execute(text(
            "SELECT sequencename FROM pg_sequences WHERE schemaname = 'ref'"
        )).scalars().all()
    }


def _sequence_position(session, sequence: str) -> tuple[int, bool]:
    """Return ``(last_value, is_called)`` for one sequence.

    Read from the sequence relation itself rather than from ``pg_sequences``:
    the view reports ``last_value`` as NULL for a sequence that has never been
    read, while the relation always carries a concrete value.

    Args:
        session: SQLAlchemy session bound to the test database.
        sequence: Schema-qualified sequence name.

    Returns:
        The sequence's stored value and whether it has been consumed.
    """
    row = session.execute(text(
        f"SELECT last_value, is_called FROM {sequence}"
    )).fetchone()
    return int(row[0]), bool(row[1])


def _set_sequence(session, sequence: str, last_value: int, is_called: bool):
    """Place a sequence at an exact ``(last_value, is_called)`` position.

    Args:
        session: SQLAlchemy session bound to the test database.
        sequence: Schema-qualified sequence name.
        last_value: The value to store.
        is_called: False means ``last_value`` is handed out next; True means
            ``last_value + 1`` is.
    """
    session.execute(text(
        f"SELECT setval('{sequence}', {last_value}, "
        f"{'true' if is_called else 'false'})"
    ))


def _next_id(session, sequence: str) -> int:
    """Return the id a sequence would hand out next.

    Args:
        session: SQLAlchemy session bound to the test database.
        sequence: Schema-qualified sequence name.

    Returns:
        ``last_value + 1`` for a consumed sequence, ``last_value`` for one
        that has never been read.
    """
    last_value, is_called = _sequence_position(session, sequence)
    return last_value + 1 if is_called else last_value


def _max_value(session, table: str, column: str = "id") -> int:
    """Return the largest value in a ``ref`` column, or 0 when it is empty.

    Args:
        session: SQLAlchemy session bound to the test database.
        table: Bare ``ref`` table name.
        column: The sequence-backed column.

    Returns:
        ``max(<column>)``, or 0 for an empty table.
    """
    return int(session.execute(text(
        f"SELECT COALESCE(max({column}), 0) FROM ref.{table}"
    )).scalar())


def _lagging_columns(session) -> list[tuple[str, int, int]]:
    """Return every ``ref`` column whose sequence cannot hand out a fresh id.

    The gate's whole judgement, factored out so every control drives the same
    code.  Table, column and sequence names are interpolated into the SQL
    because each names a RELATION or an attribute, which no bind parameter can
    carry; every one comes from the catalogue, never from input.

    Args:
        session: SQLAlchemy session bound to the test database.

    Returns:
        One ``(table, next_id, max_value)`` triple per offending column,
        sorted; empty when every sequence is in step.
    """
    offenders: list[tuple[str, int, int]] = []
    for entry in _ref_sequence_columns(session):
        next_id = _next_id(session, entry.sequence)
        max_value = _max_value(session, entry.table, entry.column)
        if next_id <= max_value:
            offenders.append((entry.table, next_id, max_value))
    return sorted(offenders)


@contextmanager
def _sequence_restored(session, sequence: str):
    """Capture a sequence's position and put it back on the way out.

    ``setval`` is not transactional, so a perturbation has to be undone
    explicitly.  The rollback before the restore is what keeps a failed
    statement inside the block from turning the restore into a
    ``PendingRollbackError`` that would replace the real assertion error.

    Args:
        session: SQLAlchemy session bound to the test database.
        sequence: Schema-qualified sequence name.

    Yields:
        The captured ``(last_value, is_called)``.
    """
    original = _sequence_position(session, sequence)
    try:
        yield original
    finally:
        session.rollback()
        _set_sequence(session, sequence, *original)


class TestMigrationRevisionPair:
    """The migration chains off the R4b-1 repair head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == _REVISION
        assert _MIGRATION.down_revision == "a3f8b1c40d92"


class TestTheDatabaseUnderTestIsMigrationBuilt:
    """The gate is only meaningful on a database the migrations built.

    ``scripts/build_test_template.py`` runs ``alembic upgrade head`` and then
    seeds, exactly as a deploy does, which is what makes a literal-id seed in
    some future migration show up here.  The other in-repo path,
    ``scripts/init_database.py``'s ``db.create_all()`` branch, populates ref
    tables only through ``ref_seeds`` -- which inserts WITHOUT ids -- so every
    sequence would be in step by construction and this whole file would pass
    while proving nothing.  Asserting the revision is applied also catches a
    STALE template: edit the migration, forget to rebuild, and the DB-driven
    tests below would otherwise be checking a database built from the old
    source.
    """

    def test_this_revision_is_applied(self, app, db):
        """``alembic_version`` carries this migration's revision."""
        with app.app_context():
            applied = db.session.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalars().all()
            assert _REVISION in applied, (
                f"the test database is at {applied}, not {_REVISION} -- "
                f"rebuild it with scripts/build_test_template.py.  Until then "
                f"every assertion in this file is about the wrong database."
            )


class TestEveryRefSequenceCanHandOutAFreshId:
    """No ``ref`` sequence sits behind the column it feeds.

    The gate.  Deliberately not restricted to the five tables the migration
    repaired: a ref table added by a future migration that seeds literal ids
    fails here, on a migration-built database, instead of on the deploy that
    first adds a value to its enum.
    """

    def test_no_ref_sequence_is_behind_its_data(self, app, db):
        """Every ``ref`` sequence would hand out a value larger than the max."""
        with app.app_context():
            offenders = _lagging_columns(db.session)
            assert offenders == [], (
                "these ref sequences would hand out an id the table already "
                "holds, so the next id-less INSERT (ref_seeds adding a "
                "missing row, at entrypoint step 4) fails on the primary key "
                "mid-deploy: "
                + "; ".join(
                    f"ref.{t} offers id {n} but holds id {m}"
                    for t, n, m in offenders
                )
                + " -- the fix is to seed the table WITHOUT explicit ids; "
                  "c7f3a9d1e864 repairs a sequence already left behind."
            )

    def test_the_scan_finds_every_sequence_the_catalogue_knows(self, app, db):
        """The scan's sequences equal ``pg_sequences``' -- no silent narrowing.

        Set equality against an independently enumerated source, rather than a
        count with slack: a mutation that dropped three tables from the scan
        passed a ``>= 20`` threshold against the 23 that exist.
        """
        with app.app_context():
            scanned = {e.sequence for e in _ref_sequence_columns(db.session)}
            catalogued = _ref_sequences_from_catalogue(db.session)
            assert scanned == catalogued, (
                f"the scan and pg_sequences disagree.  Missed by the scan: "
                f"{sorted(catalogued - scanned)}; claimed by the scan but not "
                f"a ref sequence: {sorted(scanned - catalogued)}."
            )

    def test_the_scan_reaches_every_table_covered_by_name(self, app, db):
        """The five repaired tables and the three formerly named are all in.

        Guards the set-equality test above against being vacuously true, and
        makes this file's generalisation a strict widening of the
        ``TestIdentitySequenceInStep`` class it replaced.
        """
        with app.app_context():
            found = {e.table for e in _ref_sequence_columns(db.session)}
            required = (
                _MEASURED_LAGGING_TABLES | _TABLES_FORMERLY_COVERED_BY_NAME
            )
            assert required <= found, (
                f"the scan misses {sorted(required - found)} -- it cannot "
                f"regress-test the fix it exists for, nor the three tables "
                f"whose by-name coverage this file took over."
            )

    def test_the_scan_reports_a_sequence_pushed_behind_its_data(self, app, db):
        """Negative control: break one sequence, the gate names it.

        Without this, a scan that could never fail would read exactly like a
        scan that passes.
        """
        table = "goal_modes"
        with app.app_context():
            sequence = _sequence_for(db.session, table)
            max_id = _max_value(db.session, table)
            assert max_id > 0, f"ref.{table} is empty; it cannot host this"
            with _sequence_restored(db.session, sequence) as original:
                _set_sequence(db.session, sequence, 1, False)
                offenders = {
                    t: (n, m) for t, n, m in _lagging_columns(db.session)
                }
                assert offenders.get(table) == (1, max_id), (
                    f"{sequence} was pushed back to offer id 1 while "
                    f"ref.{table} holds id {max_id}, and the scan reported "
                    f"{offenders.get(table)} -- the gate cannot fail, so it "
                    f"proves nothing."
                )
            assert _sequence_position(db.session, sequence) == original

    def test_the_scan_reports_the_exact_collision_boundary(self, app, db):
        """Negative control: ``next_id == max_id`` is a collision, and fires.

        The boundary the gate's ``<=`` exists for.  A predicate weakened to
        ``<`` still passes the control above (next 1 vs max 2) but misses this
        one, where the very next id-less INSERT collides -- so the INSERT is
        attempted here rather than assumed.
        """
        table = "goal_modes"
        with app.app_context():
            sequence = _sequence_for(db.session, table)
            max_id = _max_value(db.session, table)
            with _sequence_restored(db.session, sequence) as original:
                # is_called=True at max_id - 1 means the next id IS max_id,
                # which ref.goal_modes already holds.
                _set_sequence(db.session, sequence, max_id - 1, True)
                assert _next_id(db.session, sequence) == max_id

                offenders = {
                    t: (n, m) for t, n, m in _lagging_columns(db.session)
                }
                assert offenders.get(table) == (max_id, max_id), (
                    f"a sequence offering id {max_id} into a table that holds "
                    f"id {max_id} was not reported: {offenders.get(table)}.  "
                    f"The gate's predicate has lost its equality boundary."
                )

                with pytest.raises(IntegrityError) as excinfo:
                    db.session.execute(text(
                        "INSERT INTO ref.goal_modes (name) VALUES "
                        "('boundary-probe')"
                    ))
                assert "duplicate key" in str(excinfo.value), (
                    "the boundary state did not actually collide, so the gate "
                    "would be refusing a state that is fine"
                )
            assert _sequence_position(db.session, sequence) == original


class TestTheRepairActuallyRepairs:
    """The shipped statement, driven from the state it exists to fix.

    **The tests whose absence adversarial review proved.**  Every other
    DB-driven test here runs against a database where the migration has
    already converged, so a statement that lands on the WRONG value -- the
    review's ``GREATEST(max(id) - 1, last_value)`` mutant -- is a no-op at the
    fixpoint and invisible.  These reconstruct the measured pre-migration
    state and assert where the repair puts the sequence.
    """

    def test_it_moves_each_broken_sequence_to_max_plus_one(self, app, db):
        """From ``(1, false)``, every sequence lands on ``max(id) + 1``."""
        with app.app_context():
            sequences = {
                t: _sequence_for(db.session, t) for t in _LAGGING_REF_TABLES
            }
            with ExitStack() as stack:
                for sequence in sequences.values():
                    stack.enter_context(
                        _sequence_restored(db.session, sequence)
                    )
                # The exact measured pre-migration state: seeded by literal
                # ids, so the sequence was never read and still offers id 1.
                for sequence in sequences.values():
                    _set_sequence(db.session, sequence, 1, False)
                broken = {t for t, _, _ in _lagging_columns(db.session)}
                assert set(_LAGGING_REF_TABLES) <= broken, (
                    "the pre-migration state was not reconstructed; this test "
                    "would prove nothing about the repair"
                )

                for statement in _RESYNC_SQL:
                    db.session.execute(text(statement))

                for table, sequence in sequences.items():
                    expected = _max_value(db.session, table) + 1
                    actual = _next_id(db.session, sequence)
                    assert actual == expected, (
                        f"after the repair, {sequence} offers {actual} but "
                        f"ref.{table} holds up to {expected - 1}"
                    )
                # Spelled out for the case the whole finding was measured on:
                # ref.goal_modes holds ids 1 and 2 (Fixed, Income-Relative),
                # so the next id handed out must be 3 -- not 1, which is what
                # collided on the primary key mid-deploy.
                assert _max_value(db.session, "goal_modes") == 2
                assert _next_id(db.session, sequences["goal_modes"]) == 3
                assert _lagging_columns(db.session) == []

    def test_it_is_idempotent_from_the_broken_state(self, app, db):
        """``f(f(x)) == f(x)``: a retry after a partial run converges.

        The property the migration's docstring claims, and the one a
        mid-migration failure depends on -- ``setval`` is not transactional,
        so a retry re-runs statements that already landed.  Asserting it only
        at the fixpoint (where the statement is a no-op for a broad family of
        WRONG statements) proves much less.
        """
        table = "goal_modes"
        statement = _RESYNC_SQL[_LAGGING_REF_TABLES.index(table)]
        with app.app_context():
            sequence = _sequence_for(db.session, table)
            with _sequence_restored(db.session, sequence) as original:
                _set_sequence(db.session, sequence, 1, False)
                db.session.execute(text(statement))
                after_once = _sequence_position(db.session, sequence)
                db.session.execute(text(statement))
                after_twice = _sequence_position(db.session, sequence)
                assert after_twice == after_once, (
                    f"the second run moved the sequence {after_once} -> "
                    f"{after_twice}; a retry would not converge"
                )
            assert _sequence_position(db.session, sequence) == original

    def test_it_never_moves_a_sequence_backwards(self, app, db):
        """Monotone: a sequence AHEAD of its data is left where it is.

        The property the plan's first-draft statement (``GREATEST(max(id), 1)``
        against a literal floor) did not have -- it would have LOWERED such a
        sequence and started re-issuing ids that had been handed out.
        """
        table = "goal_modes"
        statement = _RESYNC_SQL[_LAGGING_REF_TABLES.index(table)]
        with app.app_context():
            sequence = _sequence_for(db.session, table)
            with _sequence_restored(db.session, sequence) as original:
                ahead = _max_value(db.session, table) + 500
                _set_sequence(db.session, sequence, ahead, True)
                db.session.execute(text(statement))
                assert _next_id(db.session, sequence) == ahead + 1, (
                    f"the re-sync pulled {sequence} back from {ahead + 1} to "
                    f"{_next_id(db.session, sequence)}; it would re-issue ids "
                    f"that were already handed out."
                )
            assert _sequence_position(db.session, sequence) == original


class TestTheGateCoversATableThatDoesNotExistYet:
    """The "a ref table written next year is covered" claim, demonstrated.

    Builds the shapes rather than asserting them in prose.  Every test gets a
    freshly cloned database (``conftest``'s autouse ``db`` fixture), so the
    probe tables cannot outlive the test.
    """

    def test_a_new_table_seeded_with_literal_ids_is_caught_and_repairable(
        self, app, db
    ):
        """A future literal-id seed fails the gate, and the statement fixes it."""
        with app.app_context():
            db.session.execute(text(
                "CREATE TABLE ref.zz_probe_lagging ("
                "  id SERIAL PRIMARY KEY, name VARCHAR(20) NOT NULL)"
            ))
            db.session.execute(text(
                "INSERT INTO ref.zz_probe_lagging (id, name) VALUES "
                "(1, 'a'), (2, 'b'), (3, 'c')"
            ))

            offenders = {t: (n, m) for t, n, m in _lagging_columns(db.session)}
            assert offenders.get("zz_probe_lagging") == (1, 3), (
                f"a brand-new ref table seeded with literal ids 1-3 offers id "
                f"1 next, and the gate reported "
                f"{offenders.get('zz_probe_lagging')} -- the 'covered on the "
                f"day it is created' claim is false."
            )

            db.session.execute(text(_RESYNC_SQL_FOR("zz_probe_lagging")))
            assert _next_id(db.session, "ref.zz_probe_lagging_id_seq") == 4
            assert "zz_probe_lagging" not in {
                t for t, _, _ in _lagging_columns(db.session)
            }
            db.session.rollback()

    def test_the_statement_does_not_burn_the_first_id_of_a_virgin_sequence(
        self, app, db
    ):
        """An empty, never-read table keeps id 1; the first draft consumed it.

        This is why the shipped statement is ``is_called``-aware.  The simpler
        ``setval(seq, GREATEST(max(id), last_value))`` reads ``last_value`` as
        "the last id issued", which on a virgin sequence is the NEXT id -- so
        it consumed it and the table would have started at id 2.
        """
        with app.app_context():
            db.session.execute(text(
                "CREATE TABLE ref.zz_probe_virgin ("
                "  id SERIAL PRIMARY KEY, name VARCHAR(20) NOT NULL)"
            ))
            assert _sequence_position(
                db.session, "ref.zz_probe_virgin_id_seq"
            ) == (1, False)

            db.session.execute(text(_RESYNC_SQL_FOR("zz_probe_virgin")))

            assert _next_id(db.session, "ref.zz_probe_virgin_id_seq") == 1, (
                "the re-sync consumed the first id of an empty table's virgin "
                "sequence; a newly created ref table would start at id 2."
            )
            db.session.execute(text(
                "INSERT INTO ref.zz_probe_virgin (name) VALUES ('first')"
            ))
            assert db.session.execute(text(
                "SELECT id FROM ref.zz_probe_virgin"
            )).scalar() == 1
            db.session.rollback()


class TestUpgradeExecutesEveryResyncStatement:
    """``upgrade`` executes one shipped statement per lagging table.

    Driven rather than scanned: ``op.execute`` is replaced with a recorder and
    ``upgrade()`` is called, so the assertion is about what the function DOES.
    A statement defined but never executed -- the silent form of this failure,
    where the migration reads as though it repairs and the deploy is the first
    thing to find out -- cannot survive it.
    """

    def test_upgrade_executes_exactly_the_shipped_statements(self, monkeypatch):
        """Every ``_RESYNC_SQL`` entry is executed, in order, and nothing else."""
        executed: list[str] = []

        class _Recorder:
            """Stand-in for ``alembic.op`` that records executed SQL."""

            @staticmethod
            def execute(statement):
                """Record one statement instead of running it."""
                executed.append(statement)

        monkeypatch.setattr(_MIGRATION, "op", _Recorder)
        _MIGRATION.upgrade()

        assert executed == list(_RESYNC_SQL), (
            f"upgrade() executed {executed!r}, expected the module's "
            f"_RESYNC_SQL tuple {list(_RESYNC_SQL)!r}"
        )

    def test_one_statement_per_lagging_table_naming_that_table(self):
        """Each statement targets its own table's sequence and column."""
        assert len(_RESYNC_SQL) == len(_LAGGING_REF_TABLES)
        for table, statement in zip(_LAGGING_REF_TABLES, _RESYNC_SQL):
            assert f"setval('ref.{table}_id_seq'" in statement
            assert f"SELECT max(id) FROM ref.{table}" in statement
            assert f"FROM ref.{table}_id_seq" in statement


class TestTheRepairedSetIsTheMeasuredSet:
    """The migration repairs the five tables that were measured behind."""

    def test_lagging_tuple_equals_the_measurement(self):
        """``_LAGGING_REF_TABLES`` is exactly the measured five."""
        assert set(_LAGGING_REF_TABLES) == set(_MEASURED_LAGGING_TABLES)
        assert len(_LAGGING_REF_TABLES) == len(_MEASURED_LAGGING_TABLES), (
            "a table is named twice in _LAGGING_REF_TABLES"
        )

    def test_every_named_table_exists(self, app, db):
        """Each named table is a real ``ref`` table with a sequence-backed id.

        A typo in the tuple would otherwise surface as a failed migration
        mid-deploy rather than a failed test.
        """
        with app.app_context():
            existing = {e.table for e in _ref_sequence_columns(db.session)}
            missing = set(_LAGGING_REF_TABLES) - existing
            assert missing == set(), (
                f"c7f3a9d1e864 names ref tables that do not exist with a "
                f"sequence-backed id: {sorted(missing)}"
            )

    def test_the_hardcoded_sequence_name_is_the_columns_real_sequence(
        self, app, db
    ):
        """``ref.<t>_id_seq`` really is the sequence that column draws from.

        The migration hardcodes the conventional name.  Renaming a table does
        NOT rename its sequence, so a conventionally-named decoy could exist
        and take the ``setval`` while the real sequence stayed behind -- the
        repair would report success and change nothing.  This pins the
        measurement the hardcoding rests on.
        """
        with app.app_context():
            by_table = {
                e.table: e.sequence
                for e in _ref_sequence_columns(db.session)
                if e.column == "id"
            }
            for table in _LAGGING_REF_TABLES:
                assert by_table[table] == f"ref.{table}_id_seq", (
                    f"the migration will setval ref.{table}_id_seq but "
                    f"ref.{table}.id actually draws from {by_table[table]}"
                )


class TestDowngradeRefuses:
    """``downgrade`` refuses, and its message can be acted on.

    The migration rules allow a refusal in place of a working downgrade only
    when the message carries (a) why reverting is unsafe and (b) the literal
    SQL to do it by hand.  Both halves are asserted, and the SQL is compared
    as a SET so a table dropped from -- or left stale in -- the refusal is
    caught in either direction.
    """

    def test_downgrade_raises_naming_why_and_how(self):
        """The refusal states the revision, the danger, and the exact SQL."""
        with pytest.raises(NotImplementedError) as excinfo:
            _MIGRATION.downgrade()
        message = str(excinfo.value)

        assert _REVISION in message
        assert "collide on the primary key" in message, (
            "the refusal does not say WHY reverting is unsafe"
        )
        assert "capture the real position first" in message, (
            "the refusal presents (1, false) as a general inverse; it is the "
            "measured pre-state and is unsafe on any other database"
        )

        named = set(re.findall(
            r"setval\('ref\.(\w+)_id_seq', 1, false\)", message
        ))
        assert named == set(_LAGGING_REF_TABLES), (
            f"the refusal's SQL and the migration's table tuple disagree.  "
            f"Missing from the message: "
            f"{sorted(set(_LAGGING_REF_TABLES) - named)}; stale in the "
            f"message: {sorted(named - set(_LAGGING_REF_TABLES))}."
        )
