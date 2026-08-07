"""retire the Once recurrence pattern's rules

Plan step **R2e-3** of ``docs/plans/implementation_plan_recurrence_redesign.md``
(ruling **R-R4**, amended by **R-R11**).

Review: Josh, 2026-08-07

``Once`` was a row in the recurrence table that meant "does not recur".  Four
guards existed only to suppress generation for it, and a consumer holding just
a resolved recurrence could not tell it from ``Every Period`` -- they derive to
byte-identical two-axis values.  The application models "does not recur" the
way transaction templates always have: ``recurrence_rule_id IS NULL``.  This
revision makes the data match, so the ``RecurrencePatternEnum.ONCE`` member
deleted in the same commit leaves nothing behind that reads it.

What it does
------------

1. NULLs ``recurrence_rule_id`` on every ``budget.transaction_templates`` and
   ``budget.transfer_templates`` row naming a ``Once`` rule.
2. DELETEs every ``Once`` rule from ``budget.recurrence_rules``.

Both FKs are ``ON DELETE SET NULL``, so step 2 alone would null them anyway.
Step 1 is written out regardless: the statement order of a destructive
operation should be legible here rather than being a property of the FK's
``ondelete`` clause -- the same argument
``_recurrence_form_helpers._clear_recurrence_rule`` makes on the write side.

Measured on production and on the dev clone (2026-08-07, identical): 4 ``Once``
rules (ids 41, 43, 46, 50), referenced by 2 transfer templates (id 7 -> rule 46,
id 10 -> rule 50) and ZERO transaction templates.  Rules 41 and 43 are
unreferenced orphans, so this takes them out of finding F-6's set of 5, leaving
it 4, 44 and 47.  Both live transfers are ``Paid`` (immutable) and keep their
``transfer_template_id``; nothing here touches ``budget.transfers`` or its
shadow transactions.  Verified by running this revision against a restore of
the dev clone: 0 / 2 / 4 rows touched, 50 rules -> 46, the ``ref`` row intact,
both transfers and all four shadow transactions unchanged, and the orphan set
down to {4, 44, 47}.

Selection is by pattern NAME through a subquery, not by the literal ids above.
The ids happen to agree on both live databases, but a database built through
the migration chain is not in the same id order (``a3b1c2d4e5f6`` appends
``quarterly`` and ``semi_annual`` after the initial seed), and the correct
statement of intent is "every rule naming the retired pattern" -- a rule that
survived would be unresolvable, because ``resolve`` raises for a pattern no
enum member names.

Why the ``ref.recurrence_patterns`` row SURVIVES
------------------------------------------------

**Deliberately not deleted here.  Do not add it.**  Ruled 2026-08-07
(developer); see ruling R-R11.  The reasoning, measured rather than assumed:

* **The image this revision ships with does not need the row at all.**
  ``ref_cache.init`` iterates the RUNNING image's enum
  (``app/ref_cache.py``: ``for member in spec.enum``) and never reads back the
  rows it did not match, so a SURPLUS row is invisible to it.
* **The PREVIOUS image does** -- the one ``shekel-deploy`` rolls back to on an
  unhealthy deploy.  Its enum still names ``ONCE``, so ``ref_cache.init``
  raises for it.
* **And that image cannot heal itself.**  Its own ``app/ref_seeds.py`` still
  lists ``"Once"``, so the upsert WOULD restore the row -- but
  ``scripts/seed_ref_tables.py`` boots the full app with plain
  ``create_app()`` (unlike ``scripts/init_database.py``, which passes
  ``init_ref_cache=False``), so the raise happens at entrypoint step 4 BEFORE
  the seed runs.  Under the entrypoint's ``set -eEuo pipefail`` the container
  dies.  This, not the migrations-before-seed ordering, is the mechanism: an
  earlier draft of this docstring had the causation backwards.

**One measured caveat, so nobody trusts a safety net that is not there.**  The
auto-rollback is ALREADY blocked one step earlier, at entrypoint step 3:
``init_database.py`` runs ``command.upgrade(cfg, "head")`` unguarded, and the
previous image's Alembic tree cannot resolve a DB stamped ``d4a71f6e30bb``
(probed: ``CommandError: Can't locate revision identified by
'd4a71f6e30bb'``).  That is true of every migration-bearing release, not of
this one -- it is finding F-8 in the plan's ledger.  So keeping the row
protects a restore-from-backup recovery rather than the auto-rollback, and it
costs nothing until R9 drops the table.

The surviving row is unreachable rather than merely unused: plan step R2e-2
drove every recurrence surface off ``RecurrencePatternEnum`` instead of the
table, so it is not offered by the picker, not accepted by the write doors'
schema field, and not previewed.

Revision ID: d4a71f6e30bb
Revises: c8f2b6a41d93
Create Date: 2026-08-07

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd4a71f6e30bb'
down_revision = 'c8f2b6a41d93'
branch_labels = None
depends_on = None


# The retired pattern's ``ref.recurrence_patterns.name``.  A literal because a
# migration is a frozen snapshot: the enum member this revision's commit
# deletes cannot be imported here, now or later.
ONCE_PATTERN_NAME = "Once"

# The two tables carrying a ``recurrence_rule_id``.  A fixed tuple, so the
# table name interpolated into the UPDATE below is never user input.
_TEMPLATE_TABLES = ("transaction_templates", "transfer_templates")

# Every rule naming the retired pattern.  Reused verbatim by the two FK
# NULL-outs and the DELETE so all three act on exactly one set of rows.
_ONCE_RULE_IDS = """
    SELECT id
      FROM budget.recurrence_rules
     WHERE pattern_id = (
        SELECT id FROM ref.recurrence_patterns WHERE name = :once_name
     )
"""


def upgrade():
    """Detach and delete every ``Once`` recurrence rule.

    A no-op on a database with no ``Once`` rows -- including one where the
    ``ref`` row itself is already gone, since ``recurrence_rules.pattern_id``
    is ``ON DELETE RESTRICT`` and so no rule can outlive its pattern row.
    """
    bind = op.get_bind()
    for table in _TEMPLATE_TABLES:
        detached = bind.execute(
            sa.text(
                f"UPDATE budget.{table} SET recurrence_rule_id = NULL "
                f"WHERE recurrence_rule_id IN ({_ONCE_RULE_IDS})"
            ).bindparams(once_name=ONCE_PATTERN_NAME)
        )
        print(f"R2e-3: detached {detached.rowcount} '{table}' row(s)")
    deleted = bind.execute(
        sa.text(
            f"DELETE FROM budget.recurrence_rules WHERE id IN ({_ONCE_RULE_IDS})"
        ).bindparams(once_name=ONCE_PATTERN_NAME)
    )
    print(f"R2e-3: deleted {deleted.rowcount} 'Once' recurrence rule(s)")


def downgrade():
    """Refuse to auto-revert.  The deleted rules cannot be reconstructed.

    The upgrade DELETEs rows and NULLs the foreign keys that named them.
    Nothing in the schema records which template held which rule afterwards,
    so an automatic downgrade would have to invent both the rules and the
    pairing -- and re-creating a ``Once`` rule would in any case re-introduce
    a cadence the application no longer models, which
    ``app.services.recurrence.resolve`` raises on.

    Both affected tables are audited, so the pre-upgrade state IS recoverable
    by hand from ``system.audit_log``.  To revert manually:

      1. Read back the deleted rules (``old_data`` carries every column):

           SELECT old_data
           FROM system.audit_log
           WHERE table_schema = 'budget'
             AND table_name = 'recurrence_rules'
             AND operation = 'DELETE'
           ORDER BY id DESC;

      2. Re-insert each one, keeping its original id so step 3 can name it:

           INSERT INTO budget.recurrence_rules
               (id, user_id, pattern_id, interval_n, offset_periods,
                day_of_month, due_day_of_month, month_of_year,
                start_period_id, start_date, end_date, max_occurrences,
                created_at)
           VALUES (<the values from old_data>);

           SELECT setval(
               pg_get_serial_sequence('budget.recurrence_rules', 'id'),
               (SELECT max(id) FROM budget.recurrence_rules));

      3. Re-point the templates whose FK this migration nulled:

           SELECT row_id, old_data->>'recurrence_rule_id' AS was
           FROM system.audit_log
           WHERE table_schema = 'budget'
             AND table_name IN ('transaction_templates', 'transfer_templates')
             AND operation = 'UPDATE'
             AND 'recurrence_rule_id' = ANY (changed_fields)
             AND new_data->>'recurrence_rule_id' IS NULL
           ORDER BY id DESC;

           UPDATE budget.transfer_templates
           SET recurrence_rule_id = <was>
           WHERE id = <row_id>;

      4. Downgrade past this revision, then restore the previous application
         image -- the ``Once`` enum member has to exist again for those rules
         to be readable at all.
    """
    raise NotImplementedError(
        "Migration d4a71f6e30bb has no safe automatic downgrade.  The upgrade "
        "deleted every 'Once' budget.recurrence_rules row and nulled the "
        "transaction_templates / transfer_templates foreign keys that named "
        "them; the rules' column values and the template-to-rule pairing "
        "survive only in system.audit_log.  See this function's docstring for "
        "the literal SQL to reconstruct both by hand."
    )
