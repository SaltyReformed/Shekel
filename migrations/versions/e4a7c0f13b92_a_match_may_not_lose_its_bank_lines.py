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

**It REPAIRS before it constrains, and that half was missing.**  A foreign key
validates dangling REFERENCES; it cannot see a match with ZERO line members,
because that is an absence rather than a violation.  So the constraint is
forward-only: it stops the state being produced and says nothing about rows
that already carry it -- and the recipe is printed in this docstring's own
measurement above.  It is reachable on any database where someone ran the SQL
the pre-repair ``StatementLineConflict`` message invited ("this needs a human
before anything overwrites it").  The upgrade therefore deletes any act that
holds no bank line, which is exactly what ``release_match`` would do to it and
what frees the app rows ``matched_subjects`` is otherwise still claiming.

Measured on the 2026-08-20 production clone before writing it: **0 such acts**
(production holds 0 imports and 0 matches), so the statement is a no-op there
and is here for the databases that are not production.  Found by adversarial
financial review 2026-08-20, which planted the state and showed
``accepted_groups`` raising ``ValueError`` -- taking down the whole review
surface for that account, with the release button that would repair it
rendered by the function that raises.

**Reversible.**  The downgrade restores ``ON DELETE CASCADE``.  It does not
restore the deleted acts, and could not: an act with no bank line asserts
nothing about a bank, and its members are gone with it.  ``system.audit_log``
holds what each removed row said.
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


#: Acts that already hold no bank line, which the new constraint cannot see.
#:
#: An empty side is an ABSENCE, so no foreign key can reject it -- the check is
#: `NOT EXISTS`, and it has to run before the constraint rather than being
#: implied by it.  Deleting the act is what ``release_match`` does, and the
#: member rows go with it through
#: ``fk_statement_match_members_match_account``'s own cascade.
_REPAIR_LINELESS_ACTS = """
DELETE FROM budget.statement_matches m
 WHERE NOT EXISTS (
   SELECT 1 FROM budget.statement_match_members
    WHERE match_id = m.id AND bank_statement_line_id IS NOT NULL)
"""


def upgrade():
    """Repair any act that already lost its lines, then stop it recurring."""
    op.execute(_REPAIR_LINELESS_ACTS)
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
