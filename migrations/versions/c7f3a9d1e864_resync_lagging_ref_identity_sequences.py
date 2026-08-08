"""re-sync the five ref identity sequences that sit behind their own data

Plan step **R-F1** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- finding **F-1**.  Changes no schema and no row: it moves five identity
sequences forward so the next id-less ``INSERT`` into those tables cannot
collide with a row that already exists.

**The defect.**  Three early migrations seeded their ref tables with LITERAL
ids: ``1dc0e7a1b9e4`` for ``ref.goal_modes`` AND ``ref.income_units``
(``INSERT INTO ref.goal_modes (id, name) VALUES (1, 'Fixed'), ...``),
``91fda897a32d`` for ``ref.compounding_frequencies`` and
``ref.employer_contribution_types``, and ``b961beb0edf6`` for
``ref.user_roles``.  Supplying the id does not advance the column's identity
sequence, so each of those sequences still reports
``last_value = 1, is_called = false`` -- it would hand out id 1 to the next
caller.  **Those three are the only migrations in the chain that supply an
explicit id to a ``ref`` table**: measured by scanning the ``INSERT INTO ref.``
column lists of all 126 migrations, 2026-08-08.  Every other ref seed --
``e7a4d95c2b18``, ``f5037400dc5e``, ``97bc03c2aa4c``, ``f8e025a8be41``,
``d1b22f59ba5b``, ``a4c8e2f6b1d3``, ``07198f0d6716``, ``a3b1c2d4e5f6``,
``a1c8e4f2b7d6``, ``415c517cf4a4`` and the ``account_types`` group -- inserts
by name alone, so the cause is historical rather than ongoing.

**The failure mode is a DEPLOY, not a request.**  ``ref_seeds`` only INSERTs a
MISSING row, and none is missing, so nothing exercises the sequence today.  It
bites the first time a value is ADDED to one of those five enums:
``seed_reference_data`` emits an id-less INSERT for the new name, PostgreSQL
asks the sequence for an id, gets 1, and the statement fails on the primary
key.  That runs at entrypoint step 4 (``scripts/seed_ref_tables.py``), after
the migrations of step 3 and before the health check -- so the deploy aborts
with the schema already moved, which is the exact state whose auto-rollback
cannot work (finding F-8).

**Measured 2026-08-08.**  A census of EVERY serial/identity sequence in all
five application schemas (``ref``, ``auth``, ``budget``, ``salary``,
``system``) on ``shekel-prod-db`` found exactly five behind their data, and no
others anywhere in the database::

    ref.goal_modes                    max(id) 2   next 1
    ref.income_units                  max(id) 2   next 1
    ref.user_roles                    max(id) 2   next 1
    ref.compounding_frequencies       max(id) 3   next 1
    ref.employer_contribution_types   max(id) 3   next 1

The dev clone and ``shekel_test_template`` (built by ``alembic upgrade head``)
reported the identical five, which is what makes the widened gate in
``tests/test_models/test_ref_identity_sequences.py`` a control that fires: it
failed on every migration-built database until this revision ran.  **That is a
one-time measurement, dated, and no longer reproducible in place** -- every one
of those databases is now at this revision.  What remains checkable is the
reason: the three literal-id migrations above are the only writers of these
five sequences, so any database built by the chain must show the same five.

**Why a migration and not ``ref_seeds.seed_reference_data``.**  Re-syncing a
sequence is ``setval``, which needs UPDATE on the sequence.  The least-
privilege application role holds USAGE only -- measured,
``has_sequence_privilege('shekel_app', 'ref.goal_modes_id_seq', 'UPDATE')`` is
false (``scripts/init_db_role.sql`` grants ``USAGE ON ALL SEQUENCES``) -- and
``seed_reference_data`` is called from the application factory's dev/test
bootstrap, which in a container runs as that role (``entrypoint.sh`` exports
``DATABASE_URL_APP`` at step 9).  There it would raise ``InsufficientPrivilege``
straight into the factory's existing ``except ProgrammingError``, i.e. become a
silent no-op.  Migrations run at step 3 under the owner role, which is where a
privileged one-time repair belongs.

**The statement.**  It is written in terms of the NEXT id rather than the last
one, which is what makes it exact instead of exact-where-measured::

    SELECT setval('ref.<t>_id_seq',
                  GREATEST(COALESCE((SELECT max(id) FROM ref.<t>), 0) + 1,
                           (SELECT CASE WHEN is_called THEN last_value + 1
                                        ELSE last_value END
                              FROM ref.<t>_id_seq)),
                  false);

Both arguments to ``GREATEST`` are "the next id this side wants": ``max(id)+1``
from the data, and the sequence's own next value.  The third ``setval``
argument is ``false``, so the chosen value IS handed out next rather than
skipped.  **The ``is_called`` term is load-bearing.** The simpler
``GREATEST(max(id), last_value)`` with a two-argument ``setval`` -- this
migration's first draft -- reads ``last_value`` as "the last id issued", which
is only true when ``is_called`` is true.  On a virgin sequence it is the NEXT
id, so that form consumed it: a freshly created, not-yet-seeded ref table
would have started at id 2.  Adversarial review measured exactly that on a
scratch table.  With the form above, the three properties hold in ALL eight
``(is_called, last_value vs max(id))`` states, and a test drives each:

* **Monotone.**  It can never lower the next id, because the sequence's own
  next value is one of the two candidates.  A database whose sequence sits
  AHEAD of its data -- rows inserted and later deleted -- keeps its position.
  (Scope: single-writer.  The read and the ``setval`` are not one atomic step,
  so a concurrent ``nextval`` between them would be clobbered.  Unreachable
  here: migrations run at entrypoint step 3, and the only other process is the
  app role, which measurably holds no INSERT on these tables.)
* **Idempotent.**  Not merely stable at the fixpoint -- ``f(f(x)) = f(x)`` from
  any starting state, because after one run the sequence's next value already
  equals the target.
* **Total.**  It cannot raise, given the sequence exists and shares the
  column's integer domain -- which all five do (``id`` is ``integer``, each
  sequence is ``integer`` with ``max_value`` 2147483647, so ``max(id)+1`` can
  never exceed MAXVALUE).  ``COALESCE`` covers the emptied table, whose
  ``max(id)`` is NULL, and keeps the value at or above MINVALUE.

Two consequences of ``setval`` being NON-transactional are deliberate: a
failure later in this migration leaves the earlier sequences re-synced
(harmless -- the statement is monotone and idempotent, so the retry
converges), and the id the sequence hands out is a position, not data, so no
row is written or read here.

**No ``Review:`` line.**  The migration rules require operator sign-off for
DESTRUCTIVE changes -- drops, renames, type changes, constraint removals.
This one drops nothing, renames nothing, and writes no row; it only moves five
counters forward.

**Downgrade refuses.**  Reverting means putting the sequences back behind their
data, which is re-arming a defect that aborts a deploy.  The refusal carries
the literal SQL, per the migration rules.

Revision ID: c7f3a9d1e864
Revises: a3f8b1c40d92
Create Date: 2026-08-08
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'c7f3a9d1e864'
down_revision = 'a3f8b1c40d92'
branch_labels = None
depends_on = None


# The five ``ref`` tables whose identity sequence sits behind their data, as
# measured on production, on the dev clone and in the test template (see the
# module docstring).  Held as data rather than five hand-written statements so
# the statement itself exists once; the test asserts this tuple is exactly the
# measured set, so widening it silently is not possible.
_LAGGING_REF_TABLES: tuple[str, ...] = (
    "goal_modes",
    "income_units",
    "user_roles",
    "compounding_frequencies",
    "employer_contribution_types",
)


def _resync_sql(table: str) -> str:
    """Return the re-sync statement for one ``ref`` table.

    The sequence is named literally as ``ref.<table>_id_seq``.  That is
    PostgreSQL's own naming for a ``SERIAL`` column and it was MEASURED to be
    the real sequence for all 23 ``ref`` tables on production, on the dev clone
    and in the test template (each agrees with
    ``pg_get_serial_sequence('ref.<t>', 'id')``, each is ``deptype = 'a'``).
    The measurement is what makes the literal safe, and
    ``test_the_hardcoded_sequence_name_is_the_columns_real_sequence`` pins it:
    a table renamed without its sequence could otherwise leave a
    conventionally-named decoy for this statement to hit.  Resolving through
    ``pg_get_serial_sequence`` inside the statement is not the safer
    alternative -- it returns NULL for a column it cannot resolve, and
    ``setval(NULL, ...)`` is a SILENT no-op.

    Args:
        table: Bare table name inside the ``ref`` schema.  Comes from the
            module-level ``_LAGGING_REF_TABLES`` literal, never from input.

    Returns:
        A single ``SELECT setval(...)`` statement.
    """
    return (
        f"SELECT setval('ref.{table}_id_seq', GREATEST("
        f"COALESCE((SELECT max(id) FROM ref.{table}), 0) + 1, "
        f"(SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END "
        f"FROM ref.{table}_id_seq)), false)"
    )


#: The statements ``upgrade`` executes, one per lagging table.  Exposed at
#: module level so the test re-executes what SHIPS rather than a retyped copy.
_RESYNC_SQL: tuple[str, ...] = tuple(
    _resync_sql(table) for table in _LAGGING_REF_TABLES
)


def upgrade():
    """Move each lagging ``ref`` identity sequence past its table's max id."""
    for statement in _RESYNC_SQL:
        op.execute(statement)


def downgrade():
    """Refuse: reverting re-arms a primary-key collision on the next deploy.

    Raises:
        NotImplementedError: Always.  Putting these sequences back behind
            their data restores the state in which adding one value to any of
            the five enums aborts a deploy at entrypoint step 4.  Nothing
            downstream reads a sequence POSITION, so there is no schema or
            data reason to revert; the literal SQL is in the message for an
            operator who needs to reproduce the old state deliberately.
    """
    raise NotImplementedError(
        "Refusing to revert c7f3a9d1e864: it moved five ref identity "
        "sequences past their own max(id) so the next id-less INSERT cannot "
        "collide on the primary key.  Reverting restores a latent failure "
        "that aborts the deploy (entrypoint step 4, scripts/seed_ref_tables"
        ".py) the first time a value is added to ref.goal_modes, "
        "ref.income_units, ref.user_roles, ref.compounding_frequencies or "
        "ref.employer_contribution_types.\n\n"
        "The SQL below is NOT a general inverse.  (1, false) is the position "
        "MEASURED on production, the dev clone and shekel_test_template on "
        "2026-08-08, where none of the five had ever been read.  On any other "
        "database, capture the real position first --\n"
        "  SELECT last_value, is_called FROM ref.<table>_id_seq;\n"
        "-- and pass those two values instead: applying the literals below to "
        "a sequence that had legitimately been consumed pushes it BEHIND live "
        "ids and creates the very collision this revision removed.\n\n"
        "To reproduce the measured pre-upgrade state by hand, per table:\n"
        "  SELECT setval('ref.goal_modes_id_seq', 1, false);\n"
        "  SELECT setval('ref.income_units_id_seq', 1, false);\n"
        "  SELECT setval('ref.user_roles_id_seq', 1, false);\n"
        "  SELECT setval('ref.compounding_frequencies_id_seq', 1, false);\n"
        "  SELECT setval('ref.employer_contribution_types_id_seq', 1, "
        "false);\n\n"
        "No row is written or deleted by the upgrade, so nothing has to be "
        "restored from system.audit_log."
    )
