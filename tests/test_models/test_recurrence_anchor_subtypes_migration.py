"""Tests for the c8f2b6a41d93 recurrence anchor-subtype migration.

Plan step **R2b** of ``docs/plans/implementation_plan_recurrence_redesign.md``,
as amended by plan step **R2d**.  The migration adds ``max_occurrences`` to
``budget.recurrence_rules`` with its two bound CHECKs, creates the two 0-or-1
anchor subtype tables, and audits them.  It writes no data at all.

Three groups, and the split is deliberate:

* **Schema at HEAD.**  The migration is already applied when these run (the
  template builder upgraded base->head), so the column, constraints and
  subtype tables are asserted against the live catalogue rather than by
  re-running DDL in an xdist worker.  Every CHECK is additionally shown to
  REJECT the value it names -- a constraint nobody has seen fire is a
  constraint nobody knows is wired up.
* **The audit trigger actually FIRES on both subtype tables**, and writes the
  right ``row_id``.  This is the regression guard for a defect measured while
  building the step: the plan specified ``recurrence_rule_id`` as the sole
  primary key, and ``system.audit_trigger_func`` assigns ``v_row_id :=
  NEW.id``, so every INSERT into such a table dies with ``record "new" has no
  field "id"``.  A presence check on ``pg_trigger`` would NOT have caught it
  -- the trigger exists either way and fails at INSERT time.
* **The derived columns are ABSENT, and that is asserted.**  See
  :class:`TestTheDerivedColumnsAreAbsent`.

**What this file no longer covers, and where that coverage went.**  An earlier
version of this migration added four derived columns and backfilled all 50
live rules, so this file carried ~600 lines unit-testing a frozen COPY of the
derivation that lives in ``app.services.recurrence``.  Plan step R2d removed
the columns and the backfill with them, and the copy went too.  The derivation
itself is covered -- more thoroughly than the copy ever was -- by
``tests/test_services/test_recurrence_resolution.py``, which exercises the
real function at exact dates: six first-of-month cases where this file had
three, plus month-end clamping, totality past the horizon, and the malformed
input coercions.

The executable upgrade -> downgrade -> re-upgrade round trip is a
development-time step: it was run against the prod-clone dev database while
building R2d (13 columns -> 12 -> 13, 2 subtype tables -> 0 -> 2).  Executing
a downgrade inside an xdist worker would drop tables the whole session's ORM
depends on, so :class:`TestDowngradeIsAReversal` reads the migration's AST
instead -- parsed rather than grepped, because a name in a comment satisfies a
substring search.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.audit_infrastructure import AUDITED_TABLES
from tests._test_helpers import load_migration_module

_MIGRATION_FILENAME = "c8f2b6a41d93_add_recurrence_anchor_subtypes.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_SOURCE = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

#: The one column the migration adds, nullable and unwritten until step R8.
_NEW_COLUMNS: tuple[str, ...] = ("max_occurrences",)

#: The two subtype tables, with the value column each carries.
_SUBTYPE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recurrence_weekday_anchors", ("nth_week", "weekday")),
    ("recurrence_month_anchors", ("nominal_day",)),
)

#: The two-axis values, which are DERIVED and therefore must not be columns
#: on ``budget.recurrence_rules`` until plan step R7c makes them authored.
_DERIVED_COLUMNS: tuple[str, ...] = (
    "unit_id", "anchor_date", "placement_id", "shift_id",
)


class TestMigrationRevisionPair:
    """The migration chains off the R2a vocabulary head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "c8f2b6a41d93"
        assert _MIGRATION.down_revision == "e7a4d95c2b18"


class TestNewColumnShape:
    """``max_occurrences`` exists at HEAD, nullable."""

    def test_every_new_column_exists_and_is_nullable(self, app, db):
        """Nullable because it is genuinely optional, not because it is staged.

        ``max_occurrences`` is one of the two mutually exclusive closing
        bounds, so NULL is a real value meaning "not count-bounded" -- unlike
        the columns plan step R7c will add NOT NULL.
        """
        with app.app_context():
            for column in _NEW_COLUMNS:
                row = db.session.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'budget' "
                    "  AND table_name = 'recurrence_rules' "
                    "  AND column_name = :c"
                ), {"c": column}).fetchone()
                assert row is not None, (
                    f"budget.recurrence_rules.{column} missing at HEAD"
                )
                assert row[0] == "YES", (
                    f"{column} is NOT NULL; it is an optional closing bound "
                    f"and NULL means 'not count-bounded'."
                )


class TestTheDerivedColumnsAreAbsent:
    """The two-axis values are computed, so they are not columns yet.

    Developer ruling, 2026-08-07 (plan step R2d).  ``unit_id`` /
    ``anchor_date`` / ``placement_id`` / ``shift_id`` are a DERIVATION over
    the columns beside them plus the owner's pay-period schedule, and a
    derivation stored next to its own inputs is a cache that drifts the moment
    one writer moves one side alone.  They are produced on demand by
    ``app.services.recurrence.resolve`` instead.

    **This test must be DELETED by plan step R7c**, which adds the four
    columns NOT NULL -- authored by the form rather than derived -- in the
    same transaction that drops the closed-set columns they came from.  Until
    then it is what fails if a migration re-introduces the cache.
    """

    def test_no_derived_column_exists_on_the_rule_table(self, app, db):
        """None of the four is a column at HEAD."""
        with app.app_context():
            present = db.session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'recurrence_rules' "
                "  AND column_name = ANY(:cols)"
            ), {"cols": list(_DERIVED_COLUMNS)}).scalars().all()

        assert not present, (
            f"budget.recurrence_rules carries {sorted(present)}, which plan "
            f"step R2d removed: these values are derived from the columns "
            f"beside them, and storing a derivation next to its inputs is the "
            f"cache the ruling exists to prevent.  If plan step R7c is "
            f"landing, delete this test with the ruling it enforces."
        )

    def test_the_subtype_tables_are_still_empty(self, app, db):
        """Neither anchor subtype has a writer yet.

        ``recurrence_month_anchors`` describes a clamp of ``anchor_date``,
        which is not a column, and ``recurrence_weekday_anchors`` waits for
        plan step R8.  A row in either now would describe a value nothing
        stores and nothing reads.
        """
        with app.app_context():
            for table, _values in _SUBTYPE_TABLES:
                count = db.session.execute(text(
                    # ``table`` names a RELATION, which no bind parameter can
                    # carry; it comes from the module literal above.
                    f"SELECT count(*) FROM budget.{table}"
                )).scalar()
                assert count == 0, (
                    f"budget.{table} holds {count} rows, but its first writer "
                    f"is a later plan step (R7c / R8)."
                )


class TestRuleCheckConstraintsReject:
    """Each new CHECK is shown to refuse the value it names.

    A constraint asserted only by presence in ``pg_constraint`` is a
    constraint nobody has seen fire.  Each test writes the forbidden row and
    requires the database to refuse it.
    """

    def test_both_end_bounds_at_once_is_refused(self, app, db, seed_user):
        """``end_date`` and ``max_occurrences`` together are rejected.

        Two answers to "when does this stop" is a question the engine would
        have to break a tie on; the schema refuses the question instead.
        """
        with app.app_context():
            with pytest.raises(IntegrityError, match="single_end_bound"):
                db.session.execute(text(
                    "INSERT INTO budget.recurrence_rules "
                    "  (user_id, pattern_id, interval_n, offset_periods, "
                    "   end_date, max_occurrences) "
                    "SELECT :u, id, 1, 0, DATE '2026-12-31', 12 "
                    "  FROM ref.recurrence_patterns WHERE name = 'Monthly'"
                ), {"u": seed_user["user"].id})
            db.session.rollback()

    def test_zero_max_occurrences_is_refused(self, app, db, seed_user):
        """A count bound of zero would mean "never", which NULL already means."""
        with app.app_context():
            with pytest.raises(IntegrityError, match="positive_max_occurrences"):
                db.session.execute(text(
                    "INSERT INTO budget.recurrence_rules "
                    "  (user_id, pattern_id, interval_n, offset_periods, "
                    "   max_occurrences) "
                    "SELECT :u, id, 1, 0, 0 "
                    "  FROM ref.recurrence_patterns WHERE name = 'Monthly'"
                ), {"u": seed_user["user"].id})
            db.session.rollback()


class TestSubtypeTables:
    """Both 0-or-1 anchor tables exist with the cardinality the design needs."""

    def test_both_tables_exist_with_a_surrogate_id_primary_key(self, app, db):
        """Each has an ``id`` PK -- which the audit trigger requires.

        The design specifies ``recurrence_rule_id`` as the primary key.  That
        shape is unusable here: ``system.audit_trigger_func`` assigns
        ``v_row_id := NEW.id`` and both tables are audited, so an ``id``
        column is not decoration.  ``UNIQUE (recurrence_rule_id)`` carries the
        0-or-1 cardinality instead.
        """
        with app.app_context():
            for table, _values in _SUBTYPE_TABLES:
                pk_cols = db.session.execute(text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    " AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = ('budget.' || :t)::regclass "
                    "  AND i.indisprimary"
                ), {"t": table}).scalars().all()
                assert pk_cols == ["id"], (
                    f"budget.{table} primary key is {pk_cols}, expected "
                    f"['id'] -- the audit trigger reads NEW.id"
                )

    def test_rule_id_is_unique_so_a_rule_carries_at_most_one(self, app, db):
        """``recurrence_rule_id`` is UNIQUE on both tables."""
        with app.app_context():
            for table, _values in _SUBTYPE_TABLES:
                unique_cols = db.session.execute(text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    " AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = ('budget.' || :t)::regclass "
                    "  AND i.indisunique AND NOT i.indisprimary"
                ), {"t": table}).scalars().all()
                assert unique_cols == ["recurrence_rule_id"], (
                    f"budget.{table} unique columns are {unique_cols}, "
                    f"expected ['recurrence_rule_id'] -- without it a rule "
                    f"could carry two contradictory anchors."
                )

    def test_a_second_row_for_the_same_rule_is_refused(
        self, app, db, seed_user,
    ):
        """The UNIQUE constraint actually fires on a duplicate."""
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Monthly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            db.session.execute(text(
                "INSERT INTO budget.recurrence_month_anchors "
                "  (recurrence_rule_id, nominal_day) VALUES (:r, 31)"
            ), {"r": rule_id})
            with pytest.raises(IntegrityError, match="uq_recurrence_month"):
                db.session.execute(text(
                    "INSERT INTO budget.recurrence_month_anchors "
                    "  (recurrence_rule_id, nominal_day) VALUES (:r, 30)"
                ), {"r": rule_id})
            db.session.rollback()

    def test_deleting_the_rule_cascades_to_its_anchor(self, app, db, seed_user):
        """A subtype row cannot outlive the rule it describes."""
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Monthly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            db.session.execute(text(
                "INSERT INTO budget.recurrence_month_anchors "
                "  (recurrence_rule_id, nominal_day) VALUES (:r, 31)"
            ), {"r": rule_id})
            db.session.execute(text(
                "DELETE FROM budget.recurrence_rules WHERE id = :r"
            ), {"r": rule_id})
            survivors = db.session.execute(text(
                "SELECT count(*) FROM budget.recurrence_month_anchors "
                " WHERE recurrence_rule_id = :r"
            ), {"r": rule_id}).scalar()
            assert survivors == 0, (
                "the month anchor outlived its rule -- the FK's ON DELETE "
                "CASCADE is missing or misdirected"
            )
            db.session.rollback()

    @pytest.mark.parametrize("nominal_day", [28, 32])
    def test_a_nominal_day_that_cannot_clamp_is_refused(
        self, app, db, seed_user, nominal_day,
    ):
        """Only 29-31 can be lost to a short month, so only those are stored.

        A row for day 28 would carry no information (every month holds a
        28th), and day 32 is not a day.
        """
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Monthly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            with pytest.raises(IntegrityError, match="nominal_day"):
                db.session.execute(text(
                    "INSERT INTO budget.recurrence_month_anchors "
                    "  (recurrence_rule_id, nominal_day) VALUES (:r, :d)"
                ), {"r": rule_id, "d": nominal_day})
            db.session.rollback()

    @pytest.mark.parametrize(
        "nth_week,weekday,constraint",
        [
            (0, 4, "nth_week"),     # there is no zeroth Friday
            (6, 4, "nth_week"),     # no month has a sixth Friday
            (-2, 4, "nth_week"),    # only -1 ("last") counts backward
            (1, 7, "weekday"),      # date.weekday() is 0..6
        ],
    )
    def test_out_of_domain_weekday_anchors_are_refused(
        self, app, db, seed_user, nth_week, weekday, constraint,
    ):
        """The nth-weekday domain is enforced by the database, not by hope."""
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Monthly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            with pytest.raises(IntegrityError, match=constraint):
                db.session.execute(text(
                    "INSERT INTO budget.recurrence_weekday_anchors "
                    "  (recurrence_rule_id, nth_week, weekday) "
                    "VALUES (:r, :n, :w)"
                ), {"r": rule_id, "n": nth_week, "w": weekday})
            db.session.rollback()


class TestSubtypeTablesAreAudited:
    """Both tables are audited, and the trigger is proven to FIRE.

    The presence check alone would have missed the defect this guards: with
    the design's ``recurrence_rule_id``-only primary key the trigger EXISTS
    and every INSERT fails at runtime, because ``audit_trigger_func`` assigns
    ``v_row_id := NEW.id``.  Only an actual INSERT distinguishes the two.
    """

    def test_both_tables_are_in_audited_tables(self):
        """Both carry user-controlled budget state, so both are audited."""
        for table, _values in _SUBTYPE_TABLES:
            assert ("budget", table) in AUDITED_TABLES, (
                f"budget.{table} holds user-controlled financial state and "
                f"must be audited; EXPECTED_TRIGGER_COUNT derives from this "
                f"list and the container entrypoint asserts it at start."
            )

    def test_inserting_writes_an_audit_row(self, app, db, seed_user):
        """An INSERT into each subtype table lands in ``system.audit_log``."""
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Monthly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            db.session.execute(text(
                "INSERT INTO budget.recurrence_month_anchors "
                "  (recurrence_rule_id, nominal_day) VALUES (:r, 31)"
            ), {"r": rule_id})
            db.session.execute(text(
                "INSERT INTO budget.recurrence_weekday_anchors "
                "  (recurrence_rule_id, nth_week, weekday) VALUES (:r, -1, 4)"
            ), {"r": rule_id})
            for table, _values in _SUBTYPE_TABLES:
                inserted_id = db.session.execute(text(
                    # ``table`` names a RELATION, which no bind parameter
                    # can carry; it comes from the module literal above.
                    f"SELECT id FROM budget.{table} "
                    " WHERE recurrence_rule_id = :r"
                ), {"r": rule_id}).scalar()
                logged = db.session.execute(text(
                    "SELECT row_id, new_data ->> 'recurrence_rule_id' "
                    "  FROM system.audit_log "
                    " WHERE table_schema = 'budget' AND table_name = :t "
                    "   AND operation = 'INSERT' "
                    "   AND new_data ->> 'recurrence_rule_id' = :r"
                ), {"t": table, "r": str(rule_id)}).fetchone()
                assert logged is not None, (
                    f"no audit row for the INSERT into budget.{table}"
                )
                # ``row_id`` is the whole reason these tables carry a
                # surrogate ``id``: the trigger assigns ``v_row_id := NEW.id``.
                # Asserting only that SOME row was logged would pass with a
                # NULL or wrong row_id, which is the failure the surrogate key
                # exists to prevent.
                assert logged[0] == inserted_id, (
                    f"budget.{table} audit row carries row_id={logged[0]!r}, "
                    f"expected the inserted id {inserted_id}"
                )
            db.session.rollback()


class TestDowngradeIsAReversal:
    """``downgrade`` undoes every part of ``upgrade``.

    Parsed from the AST rather than grepped for a quoted string: an earlier
    version asserted ``'"unit_id"' in downgrade_body``, which a mention in a
    COMMENT satisfies.  Executing the real downgrade is not possible in an
    xdist worker -- it would drop tables the whole session's ORM depends on --
    so the executable round trip is a development-time step, run against the
    prod-clone dev database while building R2d.

    The reversal is EXACT rather than best-effort because this revision writes
    no data: both subtype tables are created empty and stay empty until plan
    steps R7c and R8, and ``max_occurrences`` has no writer until R8.  There
    is nothing for a downgrade to reconstruct.
    """

    @staticmethod
    def _downgrade_calls(function_name):
        """Return the literal string arguments of each ``op.<fn>(...)`` call.

        Walks ``downgrade``'s AST so a name appearing in a comment, a
        docstring or an unrelated string cannot satisfy an assertion.
        """
        tree = ast.parse(_MIGRATION_SOURCE)
        body = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        calls = []
        for node in ast.walk(body):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == function_name
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "op"):
                continue
            calls.append([
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ])
        return calls

    def test_it_drops_every_column_the_upgrade_adds(self):
        """The column is the target of a real ``drop_column``."""
        dropped = {
            args[1] for args in self._downgrade_calls("drop_column")
            if len(args) >= 2 and args[0] == "recurrence_rules"
        }
        assert dropped == set(_NEW_COLUMNS), (
            f"downgrade drops {sorted(dropped)} from budget.recurrence_rules, "
            f"expected {sorted(_NEW_COLUMNS)}"
        )

    def test_it_drops_both_subtype_tables(self):
        """Dropping each table takes its audit trigger with it."""
        dropped = {
            args[0] for args in self._downgrade_calls("drop_table") if args
        }
        assert dropped == {table for table, _values in _SUBTYPE_TABLES}, (
            f"downgrade drops tables {sorted(dropped)}, expected both subtypes"
        )

    def test_it_drops_only_the_constraints_the_upgrade_added(self):
        """Both new CHECKs go, and nothing pre-existing is touched."""
        dropped = {
            args[0] for args in self._downgrade_calls("drop_constraint")
            if args
        }
        assert dropped == {
            "ck_recurrence_rules_single_end_bound",
            "ck_recurrence_rules_positive_max_occurrences",
        }, f"downgrade drops constraints {sorted(dropped)}"

    def test_the_upgrade_adds_no_derived_column(self):
        """No ``add_column`` names one of the four derived values.

        The AST sibling of :class:`TestTheDerivedColumnsAreAbsent`: that class
        asserts the live catalogue, this one asserts the migration source, so
        re-introducing the cache fails whether or not the reader has a
        database.  Delete both at plan step R7c.
        """
        tree = ast.parse(_MIGRATION_SOURCE)
        added = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in _DERIVED_COLUMNS
        }
        assert not added, (
            f"the migration names {sorted(added)} as a string literal; plan "
            f"step R2d removed those columns because they are derived."
        )
