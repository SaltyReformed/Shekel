"""A skipped bank line is a recorded act: budget.statement_line_skips

Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.  One table, one
subject: a bank line the owner has decided explains nothing they budget for.

**It is the fourth verb's act row.**  Ruling **R-HP** says every bank line ends
on exactly one of MATCH, ADD, TRANSFER or SKIP.  Three of the four already have
a record -- ``budget.statement_matches`` is what a match and a recorded purchase
both leave behind -- and SKIP is the one that names no app row, which is exactly
why it cannot reuse that table: ``statement_match._candidates
.act_still_names_a_row`` deliberately treats an act with no app-side member as
NOT A CLAIM, so a match holding a line alone leaves the line reading unexplained
forever.

**Why a table and not a column on ``budget.bank_statement_lines``.**  Every
column there is a fact the SOURCE stated, and ``statement_import._record
._absorb_gained_facts`` fills a NULL from what a later export states.  A
*the owner skipped this* column would be the first there NO source can ever
state.  (An earlier draft called that a loop a new column would have to be
excluded from; it is five explicit ``if recorded.X is None`` arms, so such a
column would simply have no arm.  The argument stands on the two legs below.)
The line table also carries no ``user_id``, so who decided would be
unrecordable.

**Why it is not append-only.**  Undoing a skip DELETES the row, and the
forensic record is infrastructure that already exists: this table joins
``app.audit_infrastructure.AUDITED_TABLES``, whose DELETE arm writes
``to_jsonb(OLD)`` and the acting ``user_id`` into ``system.audit_log``.  An
*unskipped* row would add only a history the app could display, which nothing
asks for, at the price of a "latest row per line" read on the review pass and on
the grid's badge count.

**It MOVES NO MONEY and holds no figure.**  A skip does not change what the bank
showed, does not record a movement in the books and does not close the
difference between them: ``bank_agreement`` goes on reporting the line's amount
as a disagreement, which is right.  What it changes is that the Reconcile inbox
stops asking.

**The keys, and what each is for.**

* ``uq_statement_line_skips_line`` -- one decision per line, structurally.
  TOTAL rather than partial, because the column is NOT NULL, and it is what
  makes a double-submitted press unable to record the same answer twice.
* ``fk_statement_line_skips_line_account`` -- keyed onto
  ``uq_bank_statement_lines_id_account``, the superkey
  ``statement_match_members`` already keys onto, so this skip's ``account_id``
  IS its line's rather than a copy a writer maintains.  **CASCADE, where the
  members' line key deliberately takes NO ACTION**: a match that has lost its
  lines still claims app rows and reports them as explained while no screen can
  render or release it, and a skip claims nothing but its own line, so a skip
  with no line is not a dangerous record -- it is no record at all, and refusing
  to remove one would block the repair door ``delete_import`` exists to be.
  That door counts what it takes (``ImportRemoval.skips_forgotten``) and the
  receipt says so.
* ``fk_statement_line_skips_owner`` -- keyed onto ``uq_accounts_id_user``, the
  same construction ``fk_statement_matches_owner`` uses, so a skip recorded
  under one user against another's account is unrepresentable rather than
  merely unwritten.
* ``idx_statement_line_skips_account`` -- the pass and the grid's badge each
  ask for one account's skipped lines.

**Audit.**  ``budget.statement_line_skips`` joins ``AUDITED_TABLES``, so
``EXPECTED_TRIGGER_COUNT`` moves with ``len(AUDITED_TABLES)`` and the entrypoint
health check enumerates one more trigger.  It is audited because it records a
DECISION the owner made about money the bank moved: undoing one is a delete, and
the audit row is what preserves that it was ever taken.

**The downgrade drops the table, and what that costs is stated rather than
guarded.**  It destroys the owner's skip decisions, so the lines return to the
Reconcile inbox and are asked about again.  It is NOT refused the way
``b8e4c1f7a903``'s is, and the difference is the kind of loss: that downgrade
would make an existing row UNREPRESENTABLE, so PostgreSQL would refuse it
anyway with a message naming nothing; this one is perfectly expressible and
merely lossy, in the same way dropping ``bank_statement_lines`` is.  Nothing
derived from a skip survives it either, because nothing is derived from one --
no figure, no day, no posting.  The rows themselves remain readable in
``system.audit_log`` (``table_name = 'statement_line_skips'``, one INSERT row
each with the whole record).

Revision ID: bba3bd6a6c44
Revises: d7b2e6c1a483
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'bba3bd6a6c44'
# **RE-PARENTED AT THE MERGE, AND THE PREDICTION THAT SAID IT WOULD NOT NEED TO
# BE WAS WRONG.**  This note used to read that the three lanes in flight
# (``balance:X-au-k``, ``balance:X-au-g-2c-3b-2``, ``pay_calendar:C4-d``) each
# carried NO alembic revision, so this was the only migration in flight with no
# sibling to collide with.  True when written and false by the time it merged:
# ``balance:X-au-d`` landed ``d7b2e6c1a483`` and ``pay_calendar:C13`` is holding
# ``d4a92f6b13c8``, so THREE revisions ended up declaring ``b7a41e2c9d63`` as
# their parent.  That is an undated measurement quoted as a reason, and the
# reason it is dangerous here is that git merges the three perfectly cleanly --
# they are different files -- while ``flask db upgrade`` then refuses on two
# heads, and production runs migrations on deploy.
#
# So the chain is SERIALIZED in merge order, and this was re-pointed from
# ``b7a41e2c9d63`` to ``d7b2e6c1a483`` as the last edit before the PR, against a
# measured head rather than a remembered one: ``dev`` at ``6d93f9d2`` holds 172
# revisions whose only childless one is ``d7b2e6c1a483``, and with this one re-pointed onto
# it the tree holds 173 with a single head.  A ``down_revision``
# may only name a revision present in the same tree, which is why this is done
# at the merge and never at authoring time.  The precedent and its measurement
# are ``b8e4c1f7a903``'s own note.
down_revision = 'd7b2e6c1a483'
branch_labels = None
depends_on = None


#: The table this migration creates that ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE and the DROP take the same name, and
#: a second spelling is how one of them comes to be missing it.
_AUDITED_NEW_TABLE = 'statement_line_skips'


def upgrade():
    """Create the skip table and attach its audit trigger."""
    op.create_table(
        'statement_line_skips',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bank_statement_line_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        # This skip's account IS its line's, and the skip goes when the line
        # does -- see the module docstring for why CASCADE here and NO ACTION
        # on the match members' line key.
        sa.ForeignKeyConstraint(
            ['bank_statement_line_id', 'account_id'],
            ['budget.bank_statement_lines.id',
             'budget.bank_statement_lines.account_id'],
            name='fk_statement_line_skips_line_account',
            ondelete='CASCADE',
        ),
        # This skip's owner IS its account's, guaranteed rather than
        # maintained -- keyed onto ``uq_accounts_id_user``.
        sa.ForeignKeyConstraint(
            ['account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_statement_line_skips_owner',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # ONE decision per line, structurally rather than by a writer checking
        # first.
        sa.UniqueConstraint(
            'bank_statement_line_id', name='uq_statement_line_skips_line',
        ),
        schema='budget',
    )
    op.create_index(
        'idx_statement_line_skips_account', 'statement_line_skips',
        ['account_id'], unique=False, schema='budget',
    )

    # ── Attach the audit trigger ─────────────────────────────────────────
    #
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).  The
    # shared ``system.audit_trigger_func`` already exists from the rebuild
    # migration; DROP IF EXISTS first so a re-run is idempotent.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_AUDITED_NEW_TABLE} "
        f"ON budget.{_AUDITED_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_AUDITED_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{_AUDITED_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the skip table (its audit trigger goes with it)."""
    op.drop_index(
        'idx_statement_line_skips_account',
        table_name='statement_line_skips', schema='budget',
    )
    op.drop_table('statement_line_skips', schema='budget')
