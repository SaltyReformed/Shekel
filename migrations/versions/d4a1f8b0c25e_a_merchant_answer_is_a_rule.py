"""a merchant answer is a rule

Revision ID: d4a1f8b0c25e
Revises: b7c3d9e41a06
Create Date: 2026-08-26

Plan step **bank_import:X-gd-2** of
``docs/plans/implementation_plan_bank_import.md``, "The steps", first commit.
Ruling **R-GI** (developer, 2026-08-24) renamed what this table holds: a stated
merchant answer is a standing RULE, not a destination the screen consults.

**A pure rename.  No column changes, no data changes, no answer changes.**  The
four objects a reader can name -- the table, its constraints, its index and its
audit trigger -- take the vocabulary the ruling uses, so an error message, a
model docstring and a plan document all say the same word.  The second commit of
this step is what changes the answer set; keeping the two apart is what lets
either be reviewed without the other.

**Why every dependent object is renamed explicitly.**  PostgreSQL's
``ALTER TABLE ... RENAME TO`` renames the table and NOTHING else -- measured on
a clone of the developer's own database, 2026-08-26, rather than assumed: after
the table moved, all six of PostgreSQL 18's own ``..._not_null`` constraints,
both auto-named foreign keys, the primary key, the index, the owned sequence
and the audit trigger still carried the word ``destinations``.  A model
declaring ``ck_merchant_rules_one_answer`` against a database holding
``ck_merchant_destinations_one_answer`` is a disagreement that surfaces as a
puzzling error message on the day someone violates it, and PostgreSQL 18 puts
the NOT NULL constraint's name in that message too.

**The NOT NULL constraints are LISTED even though PostgreSQL invented their
names.**  A revision transforms one KNOWN schema state into one other, so the
six are a closed set rather than a variable one: reading them out of
``pg_constraint`` in a loop would defend against a column this revision can
never meet, since a column added after it is named from ``merchant_rules`` at
birth.

**Reversible exactly.**  The downgrade is the same list backwards; nothing about
this revision loses information in either direction.
"""
from alembic import op


revision = 'd4a1f8b0c25e'
down_revision = 'b7c3d9e41a06'
branch_labels = None
depends_on = None


#: Every constraint on this table whose name the MODEL states, old name first.
#:
#: The nine PostgreSQL names for itself ride along -- the primary key, the two
#: single-column foreign keys the scoping mixins declare without a name, and the
#: six NOT NULL constraints PostgreSQL 18 records in the catalogue -- because a
#: reader meeting ``merchant_destinations_pkey`` on a table called
#: ``merchant_rules`` has to go and find out why, and because PostgreSQL 18 puts
#: a NOT NULL constraint's name in the error it raises.
_CONSTRAINTS: "tuple[tuple[str, str], ...]" = (
    ("ck_merchant_destinations_envelope_name_not_blank",
     "ck_merchant_rules_envelope_name_not_blank"),
    ("ck_merchant_destinations_one_answer",
     "ck_merchant_rules_one_answer"),
    ("fk_merchant_destinations_category_owner",
     "fk_merchant_rules_category_owner"),
    ("fk_merchant_destinations_merchant_account",
     "fk_merchant_rules_merchant_account"),
    ("fk_merchant_destinations_owner",
     "fk_merchant_rules_owner"),
    ("fk_merchant_destinations_template_account",
     "fk_merchant_rules_template_account"),
    ("merchant_destinations_account_id_fkey",
     "merchant_rules_account_id_fkey"),
    ("merchant_destinations_account_id_not_null",
     "merchant_rules_account_id_not_null"),
    ("merchant_destinations_created_at_not_null",
     "merchant_rules_created_at_not_null"),
    ("merchant_destinations_id_not_null",
     "merchant_rules_id_not_null"),
    ("merchant_destinations_merchant_id_not_null",
     "merchant_rules_merchant_id_not_null"),
    ("merchant_destinations_pkey",
     "merchant_rules_pkey"),
    ("merchant_destinations_updated_at_not_null",
     "merchant_rules_updated_at_not_null"),
    ("merchant_destinations_user_id_fkey",
     "merchant_rules_user_id_fkey"),
    ("merchant_destinations_user_id_not_null",
     "merchant_rules_user_id_not_null"),
    ("uq_merchant_destinations_account_merchant",
     "uq_merchant_rules_account_merchant"),
)


def _rename(old_table: str, new_table: str, forward: bool) -> None:
    """Move the table and everything named after it to the other vocabulary.

    Args:
        old_table: The table name to rename FROM.
        new_table: The table name to rename TO.
        forward: Whether this is the upgrade, which decides which end of each
            pair in :data:`_CONSTRAINTS` is the source.  **One function for
            both directions** so the two lists cannot drift: a downgrade that
            forgot one name would leave the database in a state neither
            revision describes, and it is the direction nobody runs until they
            need it most.
    """
    op.execute(
        f"ALTER TABLE budget.{old_table} RENAME TO {new_table}"
    )
    for before, after in _CONSTRAINTS:
        source, target = (before, after) if forward else (after, before)
        op.execute(
            f"ALTER TABLE budget.{new_table} "
            f"RENAME CONSTRAINT {source} TO {target}"
        )
    index_before, index_after = (
        "idx_merchant_destinations_account", "idx_merchant_rules_account",
    ) if forward else (
        "idx_merchant_rules_account", "idx_merchant_destinations_account",
    )
    op.execute(
        f"ALTER INDEX budget.{index_before} RENAME TO {index_after}"
    )
    op.execute(
        f"ALTER SEQUENCE budget.{old_table}_id_seq "
        f"RENAME TO {new_table}_id_seq"
    )
    # The trigger name is what the entrypoint's health check enumerates
    # (``tgname LIKE 'audit_%'``) and what
    # ``app.audit_infrastructure.AUDITED_TABLES`` derives, so it moves with the
    # table or the rebuild migration would create a SECOND trigger beside it.
    op.execute(
        f"ALTER TRIGGER audit_{old_table} ON budget.{new_table} "
        f"RENAME TO audit_{new_table}"
    )


def upgrade():
    """Rename the destinations table and everything named after it to rules."""
    _rename("merchant_destinations", "merchant_rules", forward=True)


def downgrade():
    """Put the destinations vocabulary back, exactly."""
    _rename("merchant_rules", "merchant_destinations", forward=False)
