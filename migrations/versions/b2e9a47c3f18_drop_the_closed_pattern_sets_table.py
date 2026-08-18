"""drop the closed pattern set's table

Plan step **R9** of ``docs/plans/implementation_plan_recurrence_redesign.md``
section 4 -- the last of the closed pattern set.  Plan step R7c-c
(``d9f5c1a48b73``) dropped ``budget.recurrence_rules.pattern_id``, the only
column that ever pointed here; this revision drops what it pointed AT, and the
same commit deletes ``RecurrencePatternEnum``, ``ref_cache``'s
``recurrence_pattern_id`` accessor and the ``app/ref_seeds.py`` entry behind
them.

Review: Josh, 2026-08-17 -- APPROVED, one release for the table AND the enum,
on the rollback measurement below rather than on the two-release split the plan
entry had reserved.

What it drops
-------------

``ref.recurrence_patterns``: eight rows, one integer PK, one ``UNIQUE (name)``,
and no inbound foreign key.  Seven of the eight named the closed set's
cadences, which ``(interval_n, unit_id, placement_id)`` states directly since
R7c-c.  The eighth is ``Once``, the "does not recur" row plan step R2e-3
retired from the enum while deliberately keeping the row (ruling **R-R11**);
"does not recur" is ``recurrence_rule_id IS NULL``, the shape transaction
templates always used.

Measured 2026-08-17 on the PRODUCTION database and on the dev clone, both
stamped ``d9f5c1a48b73``: 8 rows at ids 1-8, **0 inbound foreign keys**, no
view, matview, function, rule or trigger referencing the table, no comment on
it, and no column anywhere in the schema whose name contains ``pattern``.  Its
whole ``pg_depend`` closure is its own objects: four ``pg_constraint`` rows, its
sequence, one ``pg_attrdef``.  The drop is lossless.

Why the DROP carries no hand-written guard
------------------------------------------

``op.drop_table`` emits a plain ``DROP TABLE``, never ``CASCADE``, and
PostgreSQL refuses one while any foreign key depends on it -- naming the
constraint.  A Python pre-check here would be a second implementation of a
refusal the database already makes structural, which is the shape this arc
keeps removing rather than adding.

**That refusal was DRIVEN, not argued** (2026-08-17, on the rehearsal clone
below): with a table carrying ``REFERENCES ref.recurrence_patterns(id)``
planted first, ``flask db upgrade`` failed with
``DependentObjectsStillExist: cannot drop table ref.recurrence_patterns
because other objects depend on it``, naming
``planted_pattern_ref_pattern_id_fkey``, and the transaction rolled back
whole -- the stamp stayed at ``d9f5c1a48b73`` and the table was intact.

Why the enum can go in the SAME release, which ruling R-R11 reserved
--------------------------------------------------------------------

**R-R11's hazard does not generalise to a dropped TABLE, and an adversarial
review of this step is what established that.**  This section argued that the
previous image would raise on all seven members; it would raise on none.
``ref_cache._load_rows`` CATCHES ``ProgrammingError``, rolls the session back,
logs, and returns ``None``, and ``init`` records the table "unavailable" and
completes -- pinned by
``tests/test_ref_cache.py::test_init_records_unavailable_table_and_keeps_others_usable``.
The fatal ``RuntimeError`` is for an enum member with no ROW in a table that
EXISTS, which is exactly and only R-R11's case.  So the row R-R11 kept was
load-bearing and dropping the whole table is a DIFFERENT case the cache
tolerates by design.

Three independent reasons the previous image is safe, and the first is the one
that actually fires:

1. **It never reaches ``ref_cache`` at all.**  ``entrypoint.sh`` step 3 runs
   ``scripts/init_database.py``, which builds the app with
   ``init_ref_cache=False`` and then calls ``command.upgrade(cfg, "head")``.
   The previous image's Alembic tree cannot resolve ``b2e9a47c3f18``, so that
   raises, and ``set -eEuo pipefail`` aborts the entrypoint before step 4's
   seed and before any cache is constructed.  This is finding **F-8**.
2. **``shekel-deploy.sh`` refuses to put it there.**  ``repin_is_safe`` re-pins
   the previous digest only when that image's Alembic tree can RESOLVE the
   revision the database is stamped at after the failure; once this revision
   has applied it cannot, so the rollback is REFUSED and the recovery path is
   the pre-deploy dump that same script takes unconditionally -- which restores
   the table with the rest of the schema.  If this revision has NOT applied,
   the stamp is unmoved, the rollback proceeds, and the table is still
   standing.  Those are the only two states the SCRIPT can produce; a
   hand-edited digest plus ``docker compose up -d`` is a third, and reason 1 is
   what covers it.
3. **Even booted, it would degrade rather than die**, by the paragraph above.

Precedent, measured the same day: R7c-a, R7c-b and R7c-c ALL reached production
in one release (PR #102, ``41e09dad``), and R7c-c dropped a column the previous
image's ORM mapped -- the same class of forward-only schema change relying on
the same refusal.

The downgrade WORKS, and the revision below it REQUIRES that
------------------------------------------------------------

Unlike the destructive revisions either side of it, this one restores
everything it removed: the table's shape is two columns and the data is eight
constant strings, none of them derived from anything and none of them
referenced by an id.  Row IDS are not restored to any particular values because
none was ever meaningful -- migration ``d4a71f6e30bb`` records that production
and a chain-built database already disagreed about them, which is why every
statement in this family selects by NAME.

It is also not optional.  ``d9f5c1a48b73``'s own downgrade restores
``pattern_id`` and re-seats every rule on it through
``SELECT id FROM ref.recurrence_patterns WHERE name = :pattern_name``, so
continuing down the chain past this revision reads the table this one dropped.
Alembic runs downgrades in reverse, so the table is back before that statement
runs -- and the seed below must therefore carry every name
``_PATTERN_BY_READING`` there can return, which
``tests/test_models/test_drop_recurrence_patterns_migration.py`` asserts
against that table directly rather than against a copy.

Rehearsed on a production clone, 2026-08-17
--------------------------------------------

A restore of the production database (stamped ``d9f5c1a48b73``: 8 pattern
rows, 46 recurrence rules, 1,012 transactions), driven end to end:

* **upgrade** -> stamp ``b2e9a47c3f18``, table absent, 46 rules and 1,012
  transactions untouched;
* **downgrade** -> table back with the SAME shape production had
  (``id integer NOT NULL DEFAULT nextval(...)``, ``name varchar(20) NOT
  NULL``, ``recurrence_patterns_pkey``, ``recurrence_patterns_name_key``) and
  the same eight names at the same ids, 1 through 8;
* **downgrade one further**, to ``b6d41f0a9c27``, so ``d9f5c1a48b73``'s own
  restore ran against the reseeded table: **0 of 46 rules left with a NULL
  ``pattern_id``**, distributed Annual 20 / Monthly 14 / Every Period 7 /
  Quarterly 2 / Semi-Annual 2 / Monthly First 1;
* **re-upgrade to head** -> table absent again, 46 rules, 1,012 transactions,
  and the interval histogram back at 42 x 1, 2 x 3, 2 x 6 -- which is
  ``d9f5c1a48b73``'s own re-point, unmoved by the round trip.

Revision ID: b2e9a47c3f18
Revises: d9f5c1a48b73
Create Date: 2026-08-17

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b2e9a47c3f18'
down_revision = 'd9f5c1a48b73'
branch_labels = None
depends_on = None


#: The eight names the table carried, in the order the initial schema and
#: ``a3b1c2d4e5f6`` seeded them.  Inline rather than read from
#: ``app/ref_seeds.py``: this revision's own commit DELETES that entry, so a
#: downgrade run from a later checkout would find nothing to restore.  The
#: dependency policy the sibling recurrence migrations state applies too -- a
#: migration imports nothing from ``app``.
#:
#: ``ON CONFLICT (name) DO NOTHING`` for the reason ``e7a4d95c2b18``'s seeds
#: carry it: idempotent against a partial re-run.
_RESEED_SQL = (
    "INSERT INTO ref.recurrence_patterns (name) VALUES "
    "('Every Period'), "
    "('Every N Periods'), "
    "('Monthly'), "
    "('Monthly First'), "
    "('Quarterly'), "
    "('Semi-Annual'), "
    "('Annual'), "
    "('Once') "
    "ON CONFLICT (name) DO NOTHING"
)


def upgrade():
    """Drop ``ref.recurrence_patterns``.

    Un-CASCADEd deliberately: PostgreSQL refuses the DROP while any foreign
    key depends on the table, which is the whole precondition of this step
    made structural rather than asserted.
    """
    op.drop_table("recurrence_patterns", schema="ref")


def downgrade():
    """Recreate ``ref.recurrence_patterns`` and reseed its eight names.

    The PK and the ``UNIQUE (name)`` are created unnamed, taking PostgreSQL's
    generated names, which is the convention for a single-column ``ref``
    lookup key (developer ruling 2026-08-14, ledger row ``recurrence:F-3``).
    """
    op.create_table(
        "recurrence_patterns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_RESEED_SQL)
