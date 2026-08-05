"""Tests for the c8f2b6a41d93 two-axis recurrence column migration.

Plan step **R2b** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- the column half of R2.  The migration adds the two-axis columns to
``budget.recurrence_rules`` NULLABLE, creates the two 0-or-1 anchor subtype
tables, and BACKFILLS every existing rule.  Nothing reads any of it until step
R4, so this file proves the SHAPE and the DERIVATIONS rather than any
behaviour change; the R1 baseline
(``tests/test_services/test_recurrence_baseline.py``) is what proves nothing
moved.

Four groups, and the split is deliberate:

* **Schema at HEAD.**  The migration is already applied when these run (the
  template builder upgraded base->head), so the columns, constraints, foreign
  keys and subtype tables are asserted against the live catalogue rather than
  by re-running DDL in an xdist worker.  Every CHECK is additionally shown to
  REJECT the value it names -- a constraint nobody has seen fire is a
  constraint nobody knows is wired up -- and the one constraint the redesign's
  END state carries but this step deliberately omits
  (``end_date >= anchor_date``) has a test asserting the write still SUCCEEDS,
  so re-adding it without the form validator that must accompany it fails
  here rather than in production.
* **The audit trigger actually FIRES on both subtype tables**, and writes the
  right ``row_id``.  This is the regression guard for a defect measured while
  building the step: the plan specified ``recurrence_rule_id`` as the sole
  primary key, and ``system.audit_trigger_func`` assigns ``v_row_id :=
  NEW.id``, so every INSERT into such a table dies with ``record "new" has no
  field "id"``.  A presence check on ``pg_trigger`` would NOT have caught it
  -- the trigger exists either way and fails at INSERT time.
* **The backfill derivations, unit-tested on the migration's own helpers.**
  These carry the financial logic: which pay period a rule first fires in, and
  which day of the month a month-end rule keeps forever.  Each expected value
  is hand-computed in the docstring or an inline comment.  Two branches are
  NOT reachable from live data and exist only here: the month-end clamp
  (ruling R-R3; zero live rules qualify) and the mid-schedule Monthly First
  start (developer ruling 2026-08-05; the one live Monthly First rule starts
  at period index 0).
* **The backfill END TO END**, over rules the test constructs, because three
  earlier tests in this file queried the rule table unfiltered and the suite's
  database carries ZERO recurrence rules -- they asserted ``[] == []`` and
  would have passed with the whole backfill deleted.  An adversarial review
  measured that; ``TestBackfillEndToEnd`` is the replacement.

The executable upgrade -> downgrade -> re-upgrade round trip is a
development-time step: it was run against a throwaway ``pg_restore`` clone of
the prod-clone dev database and independently reproduced during review (17
columns -> 11 -> 17, 2 subtype tables -> 0 -> 2, 42 audit triggers -> 40 ->
42, every rewritten ``interval_n`` restored to 1 and then re-derived), and
``flask db migrate`` reported no diff for anything this migration touches.
Executing a downgrade inside an xdist worker would drop tables the whole
session's ORM depends on, so ``TestDowngradeIsAReversal`` reads the
migration's AST instead -- the same split
``test_recurrence_ref_tables_migration.py`` uses, but parsed rather than
grepped, because a name in a comment satisfies a substring search.
"""
from __future__ import annotations

import ast
import datetime
import pathlib
from collections import namedtuple

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.audit_infrastructure import AUDITED_TABLES
from tests._test_helpers import load_migration_module


_MIGRATION_FILENAME = "c8f2b6a41d93_add_two_axis_recurrence_columns.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_SOURCE = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

#: The five columns the migration adds, all nullable until step R2c.
_NEW_COLUMNS: tuple[str, ...] = (
    "unit_id", "anchor_date", "placement_id", "shift_id", "max_occurrences",
)

#: The two subtype tables, with the value column each carries.
_SUBTYPE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recurrence_weekday_anchors", ("nth_week", "weekday")),
    ("recurrence_month_anchors", ("nominal_day",)),
)

#: Stand-in for a ``budget.pay_periods`` row as the migration's helpers see it
#: (``SELECT user_id, period_index, start_date, end_date``).  A namedtuple
#: rather than a Mock so a helper reading a field the query does not select
#: raises here instead of silently receiving a new Mock.
_Period = namedtuple("_Period", "user_id period_index start_date end_date")

#: Stand-in for the backfill's joined rule row.  Same reasoning.
_Rule = namedtuple(
    "_Rule",
    "id user_id pattern_name interval_n offset_periods day_of_month "
    "month_of_year start_date start_period_start",
)


def _schedule(start: datetime.date, count: int, cadence_days: int = 14):
    """Return ``count`` contiguous periods from *start*, index 0 upward.

    Mirrors ``pay_period_service.generate_pay_periods``: each period ends
    ``cadence_days - 1`` after it starts and the next begins the following
    day, so the schedule is CONTIGUOUS and every date falls in exactly one
    period.
    """
    return [
        _Period(
            user_id=1,
            period_index=index,
            start_date=start + datetime.timedelta(days=cadence_days * index),
            end_date=start + datetime.timedelta(
                days=cadence_days * index + cadence_days - 1,
            ),
        )
        for index in range(count)
    ]


def _rule(pattern_name: str, **overrides) -> _Rule:
    """Return a joined-rule stand-in with the engine's own column defaults."""
    fields = {
        "id": 1, "user_id": 1, "pattern_name": pattern_name,
        "interval_n": 1, "offset_periods": 0, "day_of_month": None,
        "month_of_year": None, "start_date": None, "start_period_start": None,
    }
    fields.update(overrides)
    return _Rule(**fields)


def _derive(pattern_name: str, periods, **overrides):
    """Run the migration's per-rule derivation with stub ref-id tables.

    The unit / placement ids are stubbed to their NAMES so an assertion can
    read ``derived["unit_id"] == "month"`` without a database round trip; the
    real migration passes the integer maps it loaded from ``ref``.
    """
    units = {"period": "period", "week": "week", "month": "month", "year": "year"}
    placements = {
        "containing_date": "containing_date",
        "period_starting_on_or_after": "period_starting_on_or_after",
    }
    return _MIGRATION._derive_rule(          # pylint: disable=protected-access
        _rule(pattern_name, **overrides), periods, units, placements,
    )


class TestMigrationRevisionPair:
    """The migration chains off the R2a vocabulary head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "c8f2b6a41d93"
        assert _MIGRATION.down_revision == "e7a4d95c2b18"


class TestNewColumnShape:
    """The five columns exist at HEAD, nullable, with the declared FKs."""

    def test_every_new_column_exists_and_is_nullable(self, app, db):
        """All five are present and NULLABLE until step R2c tightens them."""
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
                    f"{column} is NOT NULL already; R2b adds it nullable and "
                    f"R2c tightens it once every writer supplies a value."
                )

    def test_the_three_ref_foreign_keys_are_restrict(self, app, db):
        """Each new FK points at its ``ref`` table with ON DELETE RESTRICT.

        RESTRICT because the seeded rows are application invariants: a
        successful DELETE would orphan every rule naming that unit,
        placement or shift.
        """
        expected = {
            "fk_recurrence_rules_unit_id": "recurrence_units",
            "fk_recurrence_rules_placement_id": "period_placements",
            "fk_recurrence_rules_shift_id": "business_day_shifts",
        }
        with app.app_context():
            for name, target in expected.items():
                row = db.session.execute(text(
                    "SELECT confdeltype, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE conname = :n"
                ), {"n": name}).fetchone()
                assert row is not None, f"{name} missing at HEAD"
                assert row[0] == "r", (
                    f"{name} has ondelete {row[0]!r}, expected 'r' (RESTRICT)"
                )
                assert f"ref.{target}" in row[1], (
                    f"{name} targets {row[1]!r}, expected ref.{target}"
                )


class TestRuleCheckConstraintsReject:
    """Each new CHECK is shown to refuse the value it names.

    A constraint asserted only by presence in ``pg_constraint`` is a
    constraint nobody has seen fire.  Each test writes the forbidden row and
    requires the database to refuse it.
    """

    def test_an_end_date_before_the_anchor_is_still_ACCEPTED(
        self, app, db, seed_user,
    ):
        """R2b deliberately does NOT constrain ``end_date`` against the anchor.

        The redesign's END state carries ``CHECK (end_date IS NULL OR end_date
        >= anchor_date)``, and adding it in R2b would have been a regression:
        ``anchor_date`` is derived and inert while ``end_date`` is
        user-authored and live, and 14 live rules carry a derived anchor in
        the future.  Setting an earlier end date -- what the field's own help
        text invites -- would raise a CheckViolation out of
        ``update_template``'s autoflush, which nothing catches, so the user
        could not stop an annual bill and the projection would keep charging
        it.  Step R7 adds the constraint once the form collects the anchor and
        Marshmallow can refuse the pair at the door.

        Asserted as ACCEPTED, not skipped: an inverted window is a legal state
        today, and this is the test that fails if a future step re-adds the
        constraint without the validator that has to come with it.
        """
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods, "
                "   anchor_date, end_date) "
                "SELECT :u, id, 1, 0, DATE '2026-06-01', DATE '2026-05-31' "
                "  FROM ref.recurrence_patterns WHERE name = 'Monthly' "
                "RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            assert rule_id is not None, (
                "an end date before the anchor must remain writable until "
                "step R7 pairs the constraint with a form validator"
            )
            db.session.rollback()

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
                    "   anchor_date, end_date, max_occurrences) "
                    "SELECT :u, id, 1, 0, DATE '2026-06-01', "
                    "       DATE '2026-12-31', 12 "
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


class TestEffectiveStartDerivation:
    """The bound every anchor is measured from.

    ``match_periods`` applies BOTH ``effective_from`` (seeded from the start
    period) and ``rule.start_date``, so the effective start is their MAXIMUM
    -- and that is what keeps the loan bound: with the anchor derived from it,
    ``anchor_date >= start_date`` holds by construction and no installment can
    precede origination.
    """

    def test_the_greater_of_the_two_bounds_wins(self):
        """Two bounds present -> the later one, in both orders."""
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        later_start_date = _MIGRATION._effective_start(   # pylint: disable=protected-access
            datetime.date(2026, 5, 1), datetime.date(2026, 4, 9), periods,
        )
        assert later_start_date == datetime.date(2026, 5, 1)
        later_period = _MIGRATION._effective_start(       # pylint: disable=protected-access
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 9), periods,
        )
        assert later_period == datetime.date(2026, 4, 9)

    def test_with_no_bound_it_falls_back_to_the_earliest_period(self):
        """The engine's own default: ``periods[0].start_date``."""
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        assert _MIGRATION._effective_start(None, None, periods) == (  # pylint: disable=protected-access
            datetime.date(2026, 3, 26)
        )

    def test_with_no_bound_and_no_schedule_there_is_no_anchor(self):
        """A user with no pay periods leaves the columns NULL, not guessed."""
        assert _MIGRATION._effective_start(None, None, []) is None  # pylint: disable=protected-access


class TestPeriodSpaceAnchors:
    """Every Period / Every N Periods / Once anchor on a period START."""

    def test_every_period_anchors_on_the_first_qualifying_period(self):
        """A period CONTAINING the bound still qualifies (period END >= bound).

        Schedule opens 2026-03-26 with 14-day periods, so index 0 spans
        2026-03-26..2026-04-08.  A bound of 2026-04-01 falls INSIDE index 0,
        whose end (04-08) is on or after it, so index 0 is the first match and
        its START is the anchor -- exactly what ``match_periods`` does.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive(
            "Every Period", periods, start_date=datetime.date(2026, 4, 1),
        )
        assert derived["anchor_date"] == datetime.date(2026, 3, 26)
        assert derived["unit_id"] == "period"
        assert derived["interval_n"] == 1
        assert derived["placement_id"] == "containing_date"

    def test_every_n_periods_anchors_on_the_first_in_phase_period(self):
        """The phase filter is ``(period_index - offset) % n == 0``.

        With n=3 and offset=2 the rule fires on indexes 2, 5, 8...  Starting
        from index 0 (2026-03-26), the first in-phase period is index 2, which
        opens 2026-03-26 + 28 days = 2026-04-23.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive(
            "Every N Periods", periods, interval_n=3, offset_periods=2,
        )
        assert derived["anchor_date"] == datetime.date(2026, 4, 23)
        assert derived["interval_n"] == 3, (
            "Every N Periods keeps the rule's OWN interval, unlike the "
            "calendar patterns whose interval is implied by the pattern name"
        )

    def test_once_gets_inert_period_values_rather_than_a_deletion(self):
        """Ruling R-R4: a ``Once`` rule is backfilled, never deleted.

        ``Once`` means "does not recur", so no honest cadence exists -- but 2
        of the 4 live Once rules hang off transfer templates whose form has no
        null option, so deleting them would pull step R7's form work forward.
        ``pattern_id = Once`` stays the thing that suppresses generation.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive(
            "Once", periods, start_period_start=datetime.date(2026, 4, 23),
        )
        assert derived["anchor_date"] == datetime.date(2026, 4, 23)
        assert derived["unit_id"] == "period"
        assert derived["interval_n"] == 1
        assert derived["nominal_day"] is None

    def test_a_schedule_ending_before_the_bound_yields_no_anchor(self):
        """No qualifying period -> NULL, which step R2c resolves loudly."""
        periods = _schedule(datetime.date(2026, 3, 26), 2)   # ends 2026-04-22
        derived = _derive(
            "Every Period", periods, start_date=datetime.date(2027, 1, 1),
        )
        assert derived is None


class TestCalendarAnchors:
    """Monthly / Quarterly / Semi-Annual / Annual anchor on a calendar date."""

    def test_monthly_takes_the_first_matching_day_on_or_after_the_bound(self):
        """Day 22 with a 2026-03-26 bound: March 22 has passed, so April 22."""
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive("Monthly", periods, day_of_month=22)
        assert derived["anchor_date"] == datetime.date(2026, 4, 22)
        assert (derived["unit_id"], derived["interval_n"]) == ("month", 1)

    def test_monthly_uses_the_bound_month_when_the_day_has_not_passed(self):
        """Day 26 with a 2026-03-26 bound: March 26 IS the bound, so March."""
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive("Monthly", periods, day_of_month=26)
        assert derived["anchor_date"] == datetime.date(2026, 3, 26)

    def test_quarterly_lands_in_its_own_residue_class(self):
        """Quarterly from month 3 fires in Mar/Jun/Sep/Dec, never April.

        Bound 2026-03-26, cycle month 3, day 2: March 2 has passed, and the
        next month in the class is June -- so 2026-06-02, not 2026-04-02.
        This is the live ``rule 36`` case.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 30)
        derived = _derive(
            "Quarterly", periods, month_of_year=3, day_of_month=2,
        )
        assert derived["anchor_date"] == datetime.date(2026, 6, 2)
        assert (derived["unit_id"], derived["interval_n"]) == ("month", 3)

    def test_semi_annual_lands_in_its_own_residue_class(self):
        """Semi-annual from month 3 fires in Mar/Sep only.

        Bound 2026-03-26, cycle month 3, day 15: March 15 has passed, so the
        next in class is September -- 2026-09-15 (the live ``rule 19`` case).
        """
        periods = _schedule(datetime.date(2026, 3, 26), 30)
        derived = _derive(
            "Semi-Annual", periods, month_of_year=3, day_of_month=15,
        )
        assert derived["anchor_date"] == datetime.date(2026, 9, 15)
        assert (derived["unit_id"], derived["interval_n"]) == ("month", 6)

    def test_annual_rolls_to_the_next_year_when_the_month_has_passed(self):
        """Cycle month 2, day 22, bound 2026-03-26 -> 2027-02-22."""
        periods = _schedule(datetime.date(2026, 3, 26), 60)
        derived = _derive(
            "Annual", periods, month_of_year=2, day_of_month=22,
        )
        assert derived["anchor_date"] == datetime.date(2027, 2, 22)
        assert (derived["unit_id"], derived["interval_n"]) == ("year", 1)

    def test_a_dayless_calendar_rule_falls_back_the_way_the_engine_does(self):
        """``day_of_month or 1`` mirrors ``recurrence_engine.py:504-518``.

        A malformed rule is reproduced, not re-invented: the engine already
        substitutes 1 for a missing day and month, so the backfill must derive
        the anchor the engine would actually have fired on.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive("Monthly", periods, day_of_month=None)
        assert derived["anchor_date"] == datetime.date(2026, 4, 1)


class TestMonthEndClampSubtype:
    """Ruling R-R3 -- the branch no live rule reaches.

    ``anchor_date`` is a real DATE, so it cannot hold "the 31st" when the
    anchor month is April; it holds 2026-04-30 and an engine reading the day
    back off the anchor would fire on the 30th forever.  The subtype row
    carries the nominal day, and its presence is the discriminator.
    """

    def test_a_clamped_anchor_gets_a_month_anchor_row(self):
        """Day 31 anchored in April: anchor 2026-04-30, nominal day 31.

        April has 30 days, so ``min(31, 30)`` clamps and the information that
        the user meant "the 31st" would otherwise be lost.
        """
        periods = _schedule(datetime.date(2026, 4, 1), 10)
        derived = _derive("Monthly", periods, day_of_month=31)
        assert derived["anchor_date"] == datetime.date(2026, 4, 30)
        assert derived["nominal_day"] == 31
        assert _MIGRATION._needs_month_anchor(derived) is True  # pylint: disable=protected-access

    def test_an_unclamped_month_end_anchor_gets_no_row(self):
        """Day 31 anchored in MARCH: 2026-03-31 is a real 31st, so no row.

        The anchor is its month's last day here too, which is why the
        predicate needs both halves -- last-day alone would write a row that
        says nothing, and this is the live ``rule 42`` case (annual, March 31).
        """
        periods = _schedule(datetime.date(2026, 3, 1), 10)
        derived = _derive("Monthly", periods, day_of_month=31)
        assert derived["anchor_date"] == datetime.date(2026, 3, 31)
        assert _MIGRATION._needs_month_anchor(derived) is False  # pylint: disable=protected-access

    def test_an_annual_february_29_rule_anchored_in_a_common_year(self):
        """2027 has no Feb 29: anchor 2027-02-28, nominal day 29.

        Without the subtype row this rule would never fire on the 29th again
        -- not in 2028, not ever.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 60)
        derived = _derive(
            "Annual", periods, month_of_year=2, day_of_month=29,
        )
        assert derived["anchor_date"] == datetime.date(2027, 2, 28)
        assert derived["nominal_day"] == 29
        assert _MIGRATION._needs_month_anchor(derived) is True  # pylint: disable=protected-access

    def test_a_day_28_rule_never_needs_a_row(self):
        """Every month holds a 28th, so the clamp can never lose anything."""
        periods = _schedule(datetime.date(2027, 2, 1), 10)
        derived = _derive("Monthly", periods, day_of_month=28)
        assert derived["anchor_date"] == datetime.date(2027, 2, 28)
        assert _MIGRATION._needs_month_anchor(derived) is False  # pylint: disable=protected-access

    def test_a_period_space_rule_never_needs_a_row(self):
        """No day-of-month concept exists under the PERIOD unit."""
        periods = _schedule(datetime.date(2026, 4, 1), 10)
        derived = _derive("Every Period", periods)
        assert _MIGRATION._needs_month_anchor(derived) is False  # pylint: disable=protected-access


class TestMonthlyFirstAnchor:
    """The developer's 2026-08-05 ruling, and the branch live data misses.

    "Monthly First" means the FIRST paycheck of each month.  Its anchor is the
    1st of the first month whose own first paycheck falls on or after the
    effective start -- not the 1st of the effective start's month, which would
    place the first row in a paycheck EARLIER than the one the user chose,
    because the placement rule is "the first period starting on or after the
    occurrence".
    """

    def test_a_rule_starting_at_index_zero_anchors_on_that_month(self):
        """The live case: start period index 0 (2026-03-26) -> 2026-03-01.

        March's first paycheck IS 2026-03-26 (nothing earlier exists), so
        March qualifies and the anchor is its 1st.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 10)
        derived = _derive("Monthly First", periods)
        assert derived["anchor_date"] == datetime.date(2026, 3, 1)
        assert derived["placement_id"] == "period_starting_on_or_after"
        assert (derived["unit_id"], derived["interval_n"]) == ("month", 1)

    def test_a_mid_month_start_skips_to_the_first_month_it_can_honour(self):
        """The ruling's worked case, on the developer's own schedule.

        Periods run 2026-03-26 + 14n, so July's paychecks are 07-02, 07-16 and
        07-30.  A rule whose start period is 2026-07-30 cannot honour July:
        July's FIRST paycheck (07-02) precedes the chosen start.  August's
        first paycheck is 08-13, which qualifies, so the anchor is 2026-08-01
        and the first row lands on the 08-13 paycheck.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 20)
        assert periods[9].start_date == datetime.date(2026, 7, 30)
        assert periods[7].start_date == datetime.date(2026, 7, 2)
        derived = _derive(
            "Monthly First", periods,
            start_period_start=datetime.date(2026, 7, 30),
        )
        assert derived["anchor_date"] == datetime.date(2026, 8, 1)

    def test_a_start_on_a_month_s_first_paycheck_keeps_that_month(self):
        """Boundary: the chosen start IS the month's first paycheck.

        2026-07-02 is July's first paycheck, so July qualifies and nothing is
        skipped -- the neighbouring case to the one above, which is where an
        off-by-one would hide.
        """
        periods = _schedule(datetime.date(2026, 3, 26), 20)
        derived = _derive(
            "Monthly First", periods,
            start_period_start=datetime.date(2026, 7, 2),
        )
        assert derived["anchor_date"] == datetime.date(2026, 7, 1)


class TestUpgradeRefusesAnUnrestorableInterval:
    """The guard that makes ``downgrade``'s ``interval_n`` restore exact.

    The backfill rewrites ``interval_n`` to 3 / 6 for Quarterly /
    Semi-Annual.  That is reversible only because every value it overwrites
    was the column default, so the upgrade refuses to run otherwise instead of
    leaving ``downgrade`` to guess.
    """

    def test_it_raises_and_names_the_offending_rules(self, app, db, seed_user):
        """A Quarterly rule with ``interval_n = 7`` aborts the migration."""
        with app.app_context():
            rule_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 7, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Quarterly' RETURNING id"
            ), {"u": seed_user["user"].id}).scalar()
            with pytest.raises(RuntimeError, match="refuses to run") as excinfo:
                _MIGRATION._refuse_rewritten_intervals(db.session)  # pylint: disable=protected-access
            assert f"id={rule_id}" in str(excinfo.value), (
                "the refusal must name the rule so the operator can fix it"
            )
            db.session.rollback()

    def test_it_passes_on_a_compliant_rule(self, app, db, seed_user):
        """A Quarterly rule at the column default passes the guard.

        Constructed rather than asserted against the suite's own rows: the
        test template carries ZERO recurrence rules, so a bare call would pass
        vacuously and prove only that the query runs.
        """
        with app.app_context():
            db.session.execute(text(
                "INSERT INTO budget.recurrence_rules "
                "  (user_id, pattern_id, interval_n, offset_periods) "
                "SELECT :u, id, 1, 0 FROM ref.recurrence_patterns "
                " WHERE name = 'Quarterly'"
            ), {"u": seed_user["user"].id})
            _MIGRATION._refuse_rewritten_intervals(db.session)  # pylint: disable=protected-access
            db.session.rollback()


class TestBackfillEndToEnd:
    """Run the shipped ``_backfill`` against constructed rules and read it back.

    **These replace three tests that were vacuous.**  An earlier version
    queried ``budget.recurrence_rules`` unfiltered and asserted the result set
    was clean; the suite's database carries ZERO recurrence rules, so all
    three asserted ``[] == []`` and would have passed with the whole backfill
    deleted.  An adversarial review measured that and it is the reason this
    class builds its own rows.

    The schedule is ``bare_periods``: ten 14-day periods from 2026-01-02, so
    index 0 is 2026-01-02..01-15, index 3 is 2026-02-13..02-26 and index 5 is
    2026-03-13..03-26.  Every expected value below is hand-computed against
    those dates.
    """

    @staticmethod
    def _insert_rule(db, user_id, pattern_name, **columns):
        """Insert one old-vocabulary rule and return its id."""
        cols = {"day_of_month": None, "month_of_year": None,
                "start_period_index": None, "start_date": None,
                "interval_n": 1, "offset_periods": 0}
        cols.update(columns)
        start_period_id = None
        if cols["start_period_index"] is not None:
            start_period_id = db.session.execute(text(
                "SELECT id FROM budget.pay_periods "
                " WHERE user_id = :u AND period_index = :i"
            ), {"u": user_id, "i": cols["start_period_index"]}).scalar()
        return db.session.execute(text(
            "INSERT INTO budget.recurrence_rules "
            "  (user_id, pattern_id, interval_n, offset_periods, "
            "   day_of_month, month_of_year, start_period_id, start_date) "
            "SELECT :u, id, :n, :off, :dom, :moy, :sp, :sd "
            "  FROM ref.recurrence_patterns WHERE name = :p RETURNING id"
        ), {
            "u": user_id, "p": pattern_name, "n": cols["interval_n"],
            "off": cols["offset_periods"], "dom": cols["day_of_month"],
            "moy": cols["month_of_year"], "sp": start_period_id,
            "sd": cols["start_date"],
        }).scalar()

    @staticmethod
    def _read_back(db, rule_id):
        """Return the backfilled tuple for *rule_id*, ref ids resolved to names."""
        return db.session.execute(text(
            "SELECT r.interval_n, u.name, r.anchor_date, p.name, s.name, "
            "       m.nominal_day "
            "  FROM budget.recurrence_rules r "
            "  LEFT JOIN ref.recurrence_units u ON u.id = r.unit_id "
            "  LEFT JOIN ref.period_placements p ON p.id = r.placement_id "
            "  LEFT JOIN ref.business_day_shifts s ON s.id = r.shift_id "
            "  LEFT JOIN budget.recurrence_month_anchors m "
            "         ON m.recurrence_rule_id = r.id "
            " WHERE r.id = :r"
        ), {"r": rule_id}).fetchone()

    def test_the_backfill_derives_every_family_correctly(
        self, app, db, bare_user, bare_periods,
    ):
        """One rule per family, run through the real ``_backfill``.

        Hand-computed against the 2026-01-02 + 14n schedule:

        * Every Period, no bounds -> the first period's start, 2026-01-02.
        * Monthly day 15, no bounds -> 2026-01-15 (January's 15th has not
          passed the 2026-01-02 bound).
        * Monthly day 31 starting at index 3 (2026-02-13) -> February 2026
          holds no 31st, so the anchor CLAMPS to 2026-02-28 and a month
          anchor row must record the nominal 31.  This is ruling R-R3's
          branch, which no live rule reaches.
        * Quarterly month 2 day 10 -> fires in Feb/May/Aug/Nov; February 10
          is after the bound, so 2026-02-10, interval 3.
        * Semi-Annual month 1 day 5 -> Jan/Jul; 2026-01-05, interval 6.
        * Annual month 1 day 1 -> 2026-01-01 is BEFORE the 2026-01-02 bound,
          so it rolls a whole year to 2027-01-01, interval 1, unit year.
        * Monthly First -> January's first paycheck is 2026-01-02, which is
          the bound itself, so January qualifies: 2026-01-01, placed by
          ``period_starting_on_or_after``.
        * Once at index 5 (2026-03-13) -> the inert period tuple, anchored on
          that period's start (ruling R-R4).
        """
        assert bare_periods[0].start_date == datetime.date(2026, 1, 2)
        assert bare_periods[3].start_date == datetime.date(2026, 2, 13)
        assert bare_periods[5].start_date == datetime.date(2026, 3, 13)
        with app.app_context():
            user_id = bare_user["user"].id
            cases = {
                "every": ("Every Period", {}, (
                    1, "period", datetime.date(2026, 1, 2),
                    "containing_date", "none", None)),
                "monthly": ("Monthly", {"day_of_month": 15}, (
                    1, "month", datetime.date(2026, 1, 15),
                    "containing_date", "none", None)),
                "clamped": ("Monthly",
                            {"day_of_month": 31, "start_period_index": 3}, (
                                1, "month", datetime.date(2026, 2, 28),
                                "containing_date", "none", 31)),
                "quarterly": ("Quarterly",
                              {"month_of_year": 2, "day_of_month": 10}, (
                                  3, "month", datetime.date(2026, 2, 10),
                                  "containing_date", "none", None)),
                "semi": ("Semi-Annual",
                         {"month_of_year": 1, "day_of_month": 5}, (
                             6, "month", datetime.date(2026, 1, 5),
                             "containing_date", "none", None)),
                "annual": ("Annual",
                           {"month_of_year": 1, "day_of_month": 1}, (
                               1, "year", datetime.date(2027, 1, 1),
                               "containing_date", "none", None)),
                "first": ("Monthly First", {}, (
                    1, "month", datetime.date(2026, 1, 1),
                    "period_starting_on_or_after", "none", None)),
                "once": ("Once", {"start_period_index": 5}, (
                    1, "period", datetime.date(2026, 3, 13),
                    "containing_date", "none", None)),
            }
            rule_ids = {
                label: self._insert_rule(db, user_id, pattern, **columns)
                for label, (pattern, columns, _expected) in cases.items()
            }
            _MIGRATION._backfill(db.session)   # pylint: disable=protected-access
            for label, (_pattern, _columns, expected) in cases.items():
                actual = tuple(self._read_back(db, rule_ids[label]))
                assert actual == expected, (
                    f"{label}: backfill wrote {actual}, expected {expected}"
                )
            db.session.rollback()

    def test_a_period_family_anchor_can_precede_its_start_date(
        self, app, db, bare_user, bare_periods,
    ):
        """The loan bound is NOT subsumed by the anchor, and this pins it.

        An adversarial review refuted the claim that ``anchor_date >=
        start_date`` holds by construction.  It holds for the calendar
        family, where the anchor is a target date at or after the bound.  It
        does NOT hold for the period family: ``match_periods`` admits a period
        whose END is on or after the bound, and the anchor is that period's
        START.  With ``start_date`` mid-period at 2026-02-20, the qualifying
        period is index 3 (2026-02-13..02-26) and the anchor is 2026-02-13 --
        a week EARLIER than the bound.

        Asserted rather than avoided: the generated period set is identical
        either way, so the derivation is right, but ``rule.start_date``
        remains the loan's origination bound and must not be dropped on the
        strength of the anchor alone (ledger row D6).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            period_rule = self._insert_rule(
                db, user_id, "Every Period",
                start_date=datetime.date(2026, 2, 20),
            )
            calendar_rule = self._insert_rule(
                db, user_id, "Monthly", day_of_month=20,
                start_date=datetime.date(2026, 2, 20),
            )
            _MIGRATION._backfill(db.session)   # pylint: disable=protected-access
            assert self._read_back(db, period_rule)[2] == datetime.date(
                2026, 2, 13,
            ), "the period-family anchor is its qualifying period's start"
            assert self._read_back(db, calendar_rule)[2] == datetime.date(
                2026, 2, 20,
            ), "the calendar-family anchor is at or after the bound"
            db.session.rollback()

    def test_a_user_with_no_schedule_keeps_its_nulls(
        self, app, db, bare_user,
    ):
        """No pay periods -> nothing to anchor against, so nothing is guessed.

        ``bare_user`` deliberately has no periods here (``bare_periods`` is
        not requested), which is the only state the backfill declines.
        """
        with app.app_context():
            rule_id = self._insert_rule(
                db, bare_user["user"].id, "Monthly", day_of_month=15,
            )
            _MIGRATION._backfill(db.session)   # pylint: disable=protected-access
            interval, unit, anchor, placement, shift, nominal = (
                self._read_back(db, rule_id)
            )
            assert (unit, anchor, placement, shift, nominal) == (
                None, None, None, None, None,
            ), "a rule with no schedule must keep every new column NULL"
            assert interval == 1, "interval_n is untouched, not nulled"
            db.session.rollback()

    def test_the_backfill_is_idempotent(
        self, app, db, bare_user, bare_periods,   # pylint: disable=unused-argument
    ):
        """Re-deriving over already-derived rows converges instead of colliding.

        Not about deploy retries -- PostgreSQL's transactional DDL rolls a
        failed migration back whole, so a retry starts clean.  It is about
        step R2c, which must re-derive EVERY rule (an edit through the old
        form leaves a stale tuple that is indistinguishable from a fresh one),
        and would hit ``uq_recurrence_month_anchors_rule`` on the second pass
        without the ``ON CONFLICT`` clause.
        """
        with app.app_context():
            rule_id = self._insert_rule(
                db, bare_user["user"].id, "Monthly",
                day_of_month=31, start_period_index=3,
            )
            _MIGRATION._backfill(db.session)   # pylint: disable=protected-access
            first = tuple(self._read_back(db, rule_id))
            _MIGRATION._backfill(db.session)   # pylint: disable=protected-access
            assert tuple(self._read_back(db, rule_id)) == first
            anchors = db.session.execute(text(
                "SELECT count(*) FROM budget.recurrence_month_anchors "
                " WHERE recurrence_rule_id = :r"
            ), {"r": rule_id}).scalar()
            assert anchors == 1, (
                f"re-running the backfill left {anchors} month-anchor rows; "
                f"a rule carries at most one"
            )
            db.session.rollback()


class TestDowngradeIsAReversal:
    """``downgrade`` undoes every part of ``upgrade``, not just the DDL.

    Parsed from the AST rather than grepped for a quoted string: an earlier
    version asserted ``'"unit_id"' in downgrade_body``, which a mention in a
    COMMENT satisfies.  Executing the real downgrade is not possible in an
    xdist worker -- it would drop tables the whole session's ORM depends on --
    so the executable round trip is a development-time step, run against a
    throwaway ``pg_restore`` clone of the prod-clone dev database and
    independently reproduced by review: 17 columns -> 11 -> 17, 8 CHECKs ->
    5 -> 8, 2 subtype tables -> 0 -> 2, 42 audit triggers -> 40 -> 42, and
    every rewritten ``interval_n`` back to 1 and then re-derived.
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
        """Each of the five columns is the target of a real ``drop_column``."""
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
        """Both new CHECKs go, and nothing pre-existing is touched.

        ``ck_recurrence_rules_end_after_anchor`` is deliberately absent from
        both directions -- see the acceptance test above for why R2b does not
        add it.
        """
        dropped = {
            args[0] for args in self._downgrade_calls("drop_constraint")
            if args
        }
        assert dropped == {
            "ck_recurrence_rules_single_end_bound",
            "ck_recurrence_rules_positive_max_occurrences",
        }, f"downgrade drops constraints {sorted(dropped)}"

    def test_it_restores_the_rewritten_intervals(self):
        """The value change is reverted, not only the schema change.

        A downgrade that dropped the columns and left Quarterly rules at
        ``interval_n = 3`` would leave the database in a state neither
        revision describes.  The restore is EXACT rather than a guess because
        ``_refuse_rewritten_intervals`` proved on the way up that every value
        overwritten held the column default.
        """
        assert _MIGRATION._REWRITTEN_INTERVALS == (   # pylint: disable=protected-access
            ("Quarterly", 3), ("Semi-Annual", 6),
        )
        tree = ast.parse(_MIGRATION_SOURCE)
        body = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        names = {
            node.id for node in ast.walk(body) if isinstance(node, ast.Name)
        }
        assert "_REWRITTEN_INTERVALS" in names, (
            "downgrade does not iterate the patterns whose interval_n the "
            "backfill rewrote, so it cannot restore them"
        )
