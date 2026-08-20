"""a match may not lose its bank lines

Plan step **bank_import:X-f6a-4** of
``docs/plans/implementation_plan_bank_import.md``, "The steps".

Review: Josh, 2026-08-20 -- APPROVED: the database refuses to orphan a match,
so the reader's defensive branch can be deleted rather than trusted.

**One constraint, changed from CASCADE to NO ACTION.**
``fk_statement_match_members_line_account`` held a match member's bank line.
On CASCADE, removing a bank line silently removed the member and left the ACT
standing -- and a match with no line left is invisible and permanent:
``_accepted_view.accepted_groups`` cannot render an act that asserts nothing
about a bank, so no release button ever exists for it, while
``_candidates.matched_subjects`` reads the member rows directly and goes on
reporting its transactions as already matched.  Those app rows can then never
be offered or matched again, by any door, forever.

**MEASURED on a production clone 2026-08-20**, before this change: a match
naming one bank line and one transaction, then ``DELETE FROM
budget.statement_imports WHERE id = 1`` --

    BEFORE delete | members 2 | line_members 1 | txn_members 1
    AFTER  delete | members 1 | line_members 0 | txn_members 1
    match row survives | 1        lines remaining | 0

Nothing in ``app/`` deleted a bank line at that revision, which is why the
state had never been produced.  **This step is what would have made it
reachable**: it gives an import a delete door (finding **N-302**).  The door
releases every affected match through ``release_match`` before it removes a
line, and this constraint is what says so structurally rather than leaving the
guarantee to one function's discipline.

**NO ACTION rather than RESTRICT, and the difference is load-bearing.**  Both
refuse the delete; RESTRICT is checked per row as the delete happens, where NO
ACTION defers to the end of the statement.  A whole-account delete cascades to
``statement_matches`` (taking its members) and to ``statement_imports`` (taking
its lines) within ONE statement, and only the deferred check tolerates that
ordering.  Verified against a production clone with the constraint already
changed: deleting the account left 0 accounts, 0 imports, 0 lines, 0 matches,
0 members and 0 identities, with no error.

**Reversible and data-lossless.**  The downgrade restores ``ON DELETE
CASCADE``.  Neither direction reads or writes a row: this is a constraint
definition, and no existing row can violate the stricter form -- every member
naming a line names one that exists, which is what the foreign key already
guaranteed under CASCADE.
"""
from alembic import op


revision = 'e4a7c0f13b92'
down_revision = 'c8e2f5a94d17'
branch_labels = None
depends_on = None


#: The constraint being redefined, and the columns it spans.  Named once so
#: the upgrade and the downgrade cannot drift about which key they mean.
_CONSTRAINT = 'fk_statement_match_members_line_account'
_TABLE = 'statement_match_members'
_LOCAL = ['bank_statement_line_id', 'account_id']
_REMOTE = ['id', 'account_id']


def upgrade():
    """Stop a bank line's deletion from silently emptying a match."""
    op.drop_constraint(
        _CONSTRAINT, _TABLE, schema='budget', type_='foreignkey',
    )
    op.create_foreign_key(
        _CONSTRAINT, _TABLE, 'bank_statement_lines',
        _LOCAL, _REMOTE,
        source_schema='budget', referent_schema='budget',
    )


def downgrade():
    """Restore the cascade, and with it the state this revision forbids."""
    op.drop_constraint(
        _CONSTRAINT, _TABLE, schema='budget', type_='foreignkey',
    )
    op.create_foreign_key(
        _CONSTRAINT, _TABLE, 'bank_statement_lines',
        _LOCAL, _REMOTE,
        source_schema='budget', referent_schema='budget',
        ondelete='CASCADE',
    )
