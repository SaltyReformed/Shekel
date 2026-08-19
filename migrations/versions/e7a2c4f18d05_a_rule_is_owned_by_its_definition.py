"""a rule is owned by its definition

Plan step **recurrence:R-F6** of
``docs/plans/implementation_plan_recurrence_redesign.md``, section 4 -- finding
**F-6**: *a hard-deleted template leaves its recurrence rule behind forever.*

Review: Josh, 2026-08-18 -- APPROVED: inverting the ownership FK over patching
the two delete routes, and dropping ``recurrence_rules.user_id`` in favour of a
property read through the owner.

**The leak was the FK's DIRECTION, and this turns it around.**  A recurrence
rule is a satellite of exactly one recurring definition, but the FK pointed
from the definition at the rule
(``transaction_templates.recurrence_rule_id ON DELETE SET NULL``, and its
transfer twin).  ``SET NULL`` on that side fires when a RULE is deleted, so no
``ondelete`` in that direction could ever dispose of the rule when its
definition went -- ``templates.hard_delete_template`` and
``transfers.hard_delete_transfer_template`` each had to remember, and neither
did.  Three rows had accumulated on production (ids 4, 44 and 47).

Two more defects followed from the same direction, and both close here:

  * **1:1 was unstateable.**  Two definitions COULD name one rule, so
    ``_recurrence_form_helpers._rule_is_exclusively_owned`` counted the
    referencing templates on every clear before daring to delete, and
    ``scripts/integrity_check`` check **OR-02** scanned for the orphans
    afterwards.  ``uq_recurrence_rules_transaction_template_id`` and its
    transfer twin say what the census asked; the arc says what the scan
    looked for.  Both are deleted in this commit.
  * **``recurrence_rules.user_id`` was a second copy of the definition's
    own**, kept in step by nothing.  It is dropped, and
    :attr:`app.models.recurrence_rule.RecurrenceRule.user_id` reads the
    owner's instead, so the two cannot disagree.

The shape is ``budget.template_amount_versions``' exactly (developer,
2026-08-11), the OTHER satellite of these same two parents: an exclusive arc of
two nullable typed FKs under one CHECK, ``ON DELETE CASCADE`` on each arm, and
a partial unique index per arm.  Two satellites of one pair of parents now have
one ownership shape rather than two opposite ones.

**No figure moves.**  Nothing reads a recurrence rule's ``user_id`` column
except to fetch the owner's pay calendar, which the property answers with the
same value (graded to 0 disagreements below before the column is dropped); the
correspondence between rules and definitions is preserved row for row; and the
three rows deleted are referenced by nothing at all.  Measured on production
2026-08-18: 46 rules, 43 referenced by exactly one definition each, 3 referenced
by none, 0 referenced by two, 0 whose ``user_id`` differs from their owner's.

**DESTRUCTIVE**, in two ways, and the downgrade handles them differently:

  1. Two columns and one more are DROPPED.  The downgrade recreates all three
     and refills them from the arc, so this half round-trips exactly.
  2. Three rows are DELETED, and no downgrade can invent them back.  BOUNDED
     by :func:`_refuse_wholesale_orphaning`, which refuses when the orphans
     outnumber the owned rules -- the shape of a database that lost its links
     rather than one that leaked.  That is
     deliberate -- ruling **R-R7** (2026-08-05) moved their disposal out of the
     additive step and into this one so the cleanup and its cause are reviewed
     together -- and it is recoverable by hand rather than automatically:
     ``budget.recurrence_rules`` is in
     ``app.audit_infrastructure.AUDITED_TABLES``, so each DELETE below writes
     the whole row to ``system.audit_log.old_data``.  The literal SQL is in
     :func:`downgrade`'s docstring.

Revision ID: e7a2c4f18d05
Revises: f2b7c40d918e
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7a2c4f18d05"
down_revision = "f2b7c40d918e"
branch_labels = None
depends_on = None


#: Every rule and the definitions naming it, one row per (rule, owner) pair.
#: The grading queries below all read from this shape rather than each
#: rebuilding the union, so "which definitions name this rule" has one
#: spelling in this module.
_OWNERSHIP_PAIRS = """
    SELECT r.id AS rule_id,
           t.id AS owner_id,
           'transaction' AS owner_kind
      FROM budget.recurrence_rules r
      JOIN budget.transaction_templates t ON t.recurrence_rule_id = r.id
     UNION ALL
    SELECT r.id, t.id, 'transfer'
      FROM budget.recurrence_rules r
      JOIN budget.transfer_templates t ON t.recurrence_rule_id = r.id
"""


def _refuse_shared_rules(bind):
    """Refuse to proceed when any rule is named by more than one definition.

    The backfill below is an ``UPDATE ... FROM``, which picks an ARBITRARY row
    when the join matches several -- so a shared rule would be re-homed onto
    one definition silently and the other would lose its cadence with nothing
    said.  The old schema permitted the state (nothing constrained it, which is
    half of what this migration fixes), so it is graded rather than assumed.

    Args:
        bind: The Alembic connection.

    Raises:
        RuntimeError: A rule is named by two or more definitions, naming each
            offending rule id and the diagnostic SELECT.
    """
    shared = bind.execute(sa.text(f"""
        SELECT rule_id, count(*) AS namers
          FROM ({_OWNERSHIP_PAIRS}) pairs
         GROUP BY rule_id
        HAVING count(*) > 1
         ORDER BY rule_id
    """)).fetchall()
    if not shared:
        return
    offenders = ", ".join(
        f"rule {row.rule_id} named by {row.namers} definitions"
        for row in shared
    )
    raise RuntimeError(
        "cannot invert recurrence-rule ownership: a rule may belong to at "
        "most one definition and these do not -- "
        f"{offenders}.  Give each definition its own rule first (the "
        "application authors one fresh rule per template, so this state "
        "means a hand edit or a restore).  Diagnostic:\n"
        f"SELECT rule_id, count(*) FROM ({_OWNERSHIP_PAIRS}) pairs "
        "GROUP BY rule_id HAVING count(*) > 1;"
    )


def _refuse_wholesale_orphaning(bind):
    """Refuse to delete the orphans when they outnumber the owned rules.

    **The DELETE below is unbounded and this migration runs UNATTENDED** --
    ``entrypoint.sh`` upgrades to head on every production boot -- so the one
    question worth asking first is whether the rows about to go are the residue
    finding **F-6** describes or a database that has lost its links.  Those two
    look identical to a query counting orphans and completely different in
    shape: a leak accumulates one row per hard-deleted definition beside a
    table of owned ones, while a botched restore or a half-applied schema
    leaves nothing owned at all.

    Measured by an adversarial review of this step: on a clone where every
    template's ``recurrence_rule_id`` was NULL, the DELETE removed all 46 rules
    and the downgrade could not bring one of them back.

    So the bound is the SHAPE rather than a count: more rules orphaned than
    owned is not leakage.  It scales with the table instead of ageing into a
    magic number, and production sits at 3 of 46 with the leak adding one per
    hard-delete -- so the refusal is far from a healthy database and adjacent
    to a broken one.

    Args:
        bind: The Alembic connection.

    Raises:
        RuntimeError: The orphans outnumber the owned rules, naming both counts
            and the diagnostic SELECT.
    """
    owned, orphaned = bind.execute(sa.text("""
        SELECT count(*) FILTER (
                   WHERE transaction_template_id IS NOT NULL
                      OR transfer_template_id IS NOT NULL) AS owned,
               count(*) FILTER (
                   WHERE transaction_template_id IS NULL
                     AND transfer_template_id IS NULL) AS orphaned
          FROM budget.recurrence_rules
    """)).one()
    if orphaned <= owned:
        return
    raise RuntimeError(
        f"refusing to delete {orphaned} orphaned recurrence rule(s) beside "
        f"only {owned} owned one(s).  Finding F-6's orphans are a RESIDUE -- "
        f"one row per hard-deleted definition -- so more orphans than owners "
        f"is a database that has lost its links rather than one that leaked, "
        f"and this migration would destroy rows no downgrade can restore.  "
        f"Reconcile the links first, or delete the rows deliberately and "
        f"re-run.  Diagnostic:\n"
        f"SELECT r.id, t.id AS names_it FROM budget.recurrence_rules r "
        f"LEFT JOIN budget.transaction_templates t "
        f"ON t.recurrence_rule_id = r.id;"
    )


def _refuse_owner_user_mismatch(bind):
    """Refuse to drop ``user_id`` while any rule's differs from its owner's.

    The column is dropped because the value is derivable from the owner, and
    that claim is graded rather than asserted: if a rule and its definition
    name different users, the derivation is NOT equal to what is stored and
    dropping the column would silently move the rule to another owner's
    pay calendar.

    Runs AFTER the arc is backfilled, so it compares against the owner this
    migration has just recorded rather than re-deriving the join.

    Args:
        bind: The Alembic connection.

    Raises:
        RuntimeError: A rule's ``user_id`` differs from its owner's, naming
            each offending rule and the diagnostic SELECT.
    """
    diagnostic = """
        SELECT r.id AS rule_id, r.user_id AS rule_user, o.user_id AS owner_user
          FROM budget.recurrence_rules r
          JOIN LATERAL (
                SELECT t.user_id FROM budget.transaction_templates t
                 WHERE t.id = r.transaction_template_id
                 UNION ALL
                SELECT x.user_id FROM budget.transfer_templates x
                 WHERE x.id = r.transfer_template_id
               ) o ON TRUE
         WHERE r.user_id <> o.user_id
    """
    mismatched = bind.execute(sa.text(f"{diagnostic} ORDER BY r.id")).fetchall()
    if not mismatched:
        return
    offenders = ", ".join(
        f"rule {row.rule_id} (user {row.rule_user}) owned by a definition of "
        f"user {row.owner_user}"
        for row in mismatched
    )
    raise RuntimeError(
        "cannot drop budget.recurrence_rules.user_id: it is dropped because "
        "the owner's answers it, and here the two disagree -- "
        f"{offenders}.  Reconcile each rule against its definition first.  "
        f"Diagnostic:\n{diagnostic.strip()};"
    )


def upgrade():
    """Move the owning FK onto the rule, delete the orphans, drop ``user_id``.

    Ordered so that nothing is dropped before its replacement is populated and
    graded: add the arc, fill it, refuse what the arc cannot express, delete
    what has no owner, constrain, and only then drop the old columns.
    """
    bind = op.get_bind()

    # ---- 1. The arc, nullable and unconstrained while it fills -----------
    op.add_column(
        "recurrence_rules",
        sa.Column("transaction_template_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("transfer_template_id", sa.Integer(), nullable=True),
        schema="budget",
    )

    # ---- 2. Refuse a state the arc cannot hold, BEFORE filling it --------
    _refuse_shared_rules(bind)

    # ---- 3. Backfill: the definition that names a rule now owns it -------
    op.execute("""
        UPDATE budget.recurrence_rules r
           SET transaction_template_id = t.id
          FROM budget.transaction_templates t
         WHERE t.recurrence_rule_id = r.id
    """)
    op.execute("""
        UPDATE budget.recurrence_rules r
           SET transfer_template_id = t.id
          FROM budget.transfer_templates t
         WHERE t.recurrence_rule_id = r.id
    """)

    # ---- 4. The orphans: rules no definition names ----------------------
    #
    # Finding **F-6** itself, 3 rows on production.  Deleted rather than
    # adopted because there is nothing to adopt them onto -- their definitions
    # were hard-deleted, which is the leak this migration closes -- and
    # deleted HERE rather than in an earlier additive step because ruling
    # R-R7 put the cleanup in the same commit as its cause.  Every DELETE
    # writes the full row to ``system.audit_log.old_data``; see
    # :func:`downgrade`.
    #
    # BOUNDED first: this is the one irreversible statement in the migration
    # and it runs unattended on every production boot.
    _refuse_wholesale_orphaning(bind)
    orphans = bind.execute(sa.text("""
        DELETE FROM budget.recurrence_rules
         WHERE transaction_template_id IS NULL
           AND transfer_template_id IS NULL
        RETURNING id
    """)).fetchall()
    print(
        f"R-F6: deleted {len(orphans)} orphaned recurrence rule(s)"
        + (f" (ids {', '.join(str(row.id) for row in orphans)})" if orphans else "")
    )

    # ---- 5. Constrain what is now true ----------------------------------
    op.create_check_constraint(
        "ck_recurrence_rules_one_owner",
        "recurrence_rules",
        "(transaction_template_id IS NULL) <> (transfer_template_id IS NULL)",
        schema="budget",
    )
    op.create_index(
        "uq_recurrence_rules_transaction_template_id",
        "recurrence_rules",
        ["transaction_template_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text("transaction_template_id IS NOT NULL"),
    )
    op.create_index(
        "uq_recurrence_rules_transfer_template_id",
        "recurrence_rules",
        ["transfer_template_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text("transfer_template_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_recurrence_rules_transaction_template_id",
        "recurrence_rules", "transaction_templates",
        ["transaction_template_id"], ["id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_recurrence_rules_transfer_template_id",
        "recurrence_rules", "transfer_templates",
        ["transfer_template_id"], ["id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )

    # ---- 6. The old direction goes, and the copied owner with it --------
    #
    # Dropping the column drops its FK
    # (``transaction_templates_recurrence_rule_id_fkey``) with it.
    op.drop_column("transaction_templates", "recurrence_rule_id", schema="budget")
    op.drop_column("transfer_templates", "recurrence_rule_id", schema="budget")

    _refuse_owner_user_mismatch(bind)
    op.drop_column("recurrence_rules", "user_id", schema="budget")


def downgrade():
    """Point the FK back at the rule and restore the copied ``user_id``.

    Every correspondence this migration recorded is restored row for row: each
    definition regains its ``recurrence_rule_id``, and each rule regains a
    ``user_id`` refilled from the owner it belonged to -- which is exactly the
    value the dropped column held, because :func:`upgrade` refused to drop it
    otherwise.

    **The three orphaned rows are NOT restored, and cannot be.**  They named no
    definition, so the arc could not hold them and they were deleted; nothing
    in the post-upgrade database remembers them.  They are recoverable by hand
    from the audit trail, which is why the deletion goes through the ORM-facing
    table rather than around it -- ``budget.recurrence_rules`` is audited, so
    each deleted row survives whole in ``system.audit_log.old_data``::

        INSERT INTO budget.recurrence_rules
            (id, user_id, interval_n, unit_id, placement_id, shift_id,
             starts_on, nominal_day, due_day_of_month, end_date,
             max_occurrences, created_at)
        SELECT (old_data->>'id')::int,
               (old_data->>'user_id')::int,
               (old_data->>'interval_n')::int,
               (old_data->>'unit_id')::int,
               (old_data->>'placement_id')::int,
               (old_data->>'shift_id')::int,
               (old_data->>'starts_on')::date,
               (old_data->>'nominal_day')::smallint,
               (old_data->>'due_day_of_month')::int,
               (old_data->>'end_date')::date,
               (old_data->>'max_occurrences')::int,
               (old_data->>'created_at')::timestamptz
          FROM system.audit_log
         WHERE table_schema = 'budget'
           AND table_name = 'recurrence_rules'
           AND operation = 'DELETE'
           AND executed_at >= <the timestamp this migration ran>;

    Restoring them is a repair of a defect, not a rollback: nothing read them
    before this migration and nothing would read them after, which is what
    made them orphans.
    """
    # ---- 1. The old direction, nullable and unconstrained while it fills -
    op.add_column(
        "transaction_templates",
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "transfer_templates",
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute("""
        UPDATE budget.transaction_templates t
           SET recurrence_rule_id = r.id
          FROM budget.recurrence_rules r
         WHERE r.transaction_template_id = t.id
    """)
    op.execute("""
        UPDATE budget.transfer_templates t
           SET recurrence_rule_id = r.id
          FROM budget.recurrence_rules r
         WHERE r.transfer_template_id = t.id
    """)
    # Under the dialect-default names the pre-C-43 schema gave them, so the
    # schema round-trips to exactly what it was rather than to an equivalent
    # shape under different names.
    op.create_foreign_key(
        "transaction_templates_recurrence_rule_id_fkey",
        "transaction_templates", "recurrence_rules",
        ["recurrence_rule_id"], ["id"],
        source_schema="budget", referent_schema="budget",
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "transfer_templates_recurrence_rule_id_fkey",
        "transfer_templates", "recurrence_rules",
        ["recurrence_rule_id"], ["id"],
        source_schema="budget", referent_schema="budget",
        ondelete="SET NULL",
    )

    # ---- 2. The copied owner, refilled from the arc before it goes -------
    op.add_column(
        "recurrence_rules",
        sa.Column("user_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute("""
        UPDATE budget.recurrence_rules r
           SET user_id = o.user_id
          FROM (
                SELECT id AS owner_id, user_id, 'transaction' AS kind
                  FROM budget.transaction_templates
                 UNION ALL
                SELECT id, user_id, 'transfer'
                  FROM budget.transfer_templates
               ) o
         WHERE (o.kind = 'transaction' AND o.owner_id = r.transaction_template_id)
            OR (o.kind = 'transfer' AND o.owner_id = r.transfer_template_id)
    """)
    remaining = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM budget.recurrence_rules WHERE user_id IS NULL"
    )).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} recurrence rule(s) have no owner to take a user_id "
            "from, so the NOT NULL cannot be restored.  Every row should carry "
            "exactly one owner under ck_recurrence_rules_one_owner; a row "
            "without one means that constraint was dropped or bypassed.  "
            "Diagnostic:\nSELECT id, transaction_template_id, "
            "transfer_template_id FROM budget.recurrence_rules "
            "WHERE user_id IS NULL;"
        )
    op.alter_column(
        "recurrence_rules", "user_id",
        existing_type=sa.Integer(), nullable=False, schema="budget",
    )
    op.create_foreign_key(
        "recurrence_rules_user_id_fkey",
        "recurrence_rules", "users",
        ["user_id"], ["id"],
        source_schema="budget", referent_schema="auth",
        ondelete="CASCADE",
    )

    # ---- 3. The arc goes ------------------------------------------------
    op.drop_constraint(
        "fk_recurrence_rules_transaction_template_id",
        "recurrence_rules", schema="budget", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_recurrence_rules_transfer_template_id",
        "recurrence_rules", schema="budget", type_="foreignkey",
    )
    op.drop_index(
        "uq_recurrence_rules_transaction_template_id",
        table_name="recurrence_rules", schema="budget",
    )
    op.drop_index(
        "uq_recurrence_rules_transfer_template_id",
        table_name="recurrence_rules", schema="budget",
    )
    op.drop_constraint(
        "ck_recurrence_rules_one_owner",
        "recurrence_rules", schema="budget", type_="check",
    )
    op.drop_column("recurrence_rules", "transaction_template_id", schema="budget")
    op.drop_column("recurrence_rules", "transfer_template_id", schema="budget")
