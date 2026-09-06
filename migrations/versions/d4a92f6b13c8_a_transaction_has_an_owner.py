"""A transaction HAS an owner, and it is a column

Revision ID: d4a92f6b13c8
Revises: bba3bd6a6c44
Create Date: 2026-09-02 17:30:00.000000

Plan step ``pay_calendar:C13-a``.  Ruling **R-PC32**.

**What was wrong.**  ``budget.transactions`` carried no owner.  A row's owner
was its pay period's -- and NOTHING required that owner to be its ACCOUNT's,
so a row filed in one person's paycheck against another person's account was
an EXPRESSIBLE state.  Two consequences follow, and the second is the
expensive one:

  * the state is only ever refused by whichever door happens to look, and
  * every door that refuses a foreign row has to STATE the relationship by
    hand.  Finding **P75** counts nineteen such comparisons in ``app/`` --
    eleven walking ``X.pay_period.user_id`` and eight re-fetching the row by
    primary key to compare -- each one a place a future door can forget, and
    each one another home for a fact with no single one.

**What this does.**  Gives the row its owner as a COLUMN and holds it equal to
BOTH parents at once, with a composite foreign key per parent:

  * ``uq_pay_periods_id_user`` -- the superkey the second key needs.  It
    constrains nothing (``id`` is already the primary key) and exists only
    because PostgreSQL requires a UNIQUE over exactly the referenced columns.
    ``uq_accounts_id_user``, which the first key targets, already exists.
  * ``budget.transactions.user_id`` -- NULLABLE, backfilled, then ``SET NOT
    NULL``, with ``fk_transactions_user_id`` to ``auth.users``,
    ``ON DELETE RESTRICT``.
  * ``fk_transactions_owner_account`` -- ``(account_id, user_id)`` REFERENCES
    ``budget.accounts (id, user_id)``, ``ON DELETE RESTRICT``.
  * ``fk_transactions_owner_period`` -- ``(pay_period_id, user_id)``
    REFERENCES ``budget.pay_periods (id, user_id)``, ``ON DELETE CASCADE``.

With both, the two parents cannot disagree: either key ALONE leaves the other
parent free to belong to someone else, and the pair is what makes the state
unconstructible rather than merely unwritten.

*Unconstructible by every writer that reaches the table as the application*,
which is the claim this revision is entitled to.  ``SET session_replication
_role = 'replica'`` suppresses referential triggers and a superuser can still
force the row -- ``shekel_app`` is not one (``rolsuper = f``), but
``shekel_user``, which runs migrations and the test suite, is, and
``tests/test_scripts/test_integrity_check.py`` uses exactly that technique on
purpose.  A constraint bounds the application, never the DBA.

**It is a CO-LOCATED KEY, not the cached copy CLAUDE.md rule 14 forbids**, and
the distinction is the whole argument for the column.  A cache is a second
home for a value plus a contract that some writer keeps them in step; the
tell rule 14 names is exactly that -- "where a rule says two places must
always agree, they are one value with two homes and a maintenance contract".
Here the maintenance contract does not vanish -- it MOVES, from the readers to
the database.  *A first draft of this paragraph said there was "no contract and
no writer", and this revision's own diff refutes it*: nine writers in ``app/``
now state the owner, each reading it off a different object
(``current_user.id``, ``source_txn.user_id``, ``template.user_id``,
``scope.owner_id``, ``xfer.user_id``), and 338 test sites do the same.  What
changed is WHO enforces the agreement.  Before, nineteen readers each restated
the rule and any one of them could forget; now a writer that gets it wrong is
an ``IntegrityError`` at flush, and no reader has to know.  That is exactly the
rule's stated preferred end state -- *an invariant that cannot be violated
because there is nothing to violate is worth more than one a reconciler
enforces* -- and it is a stronger claim than the one it replaces, not a weaker
one.

**The other half of rule 14 is owed an answer too**: ``user_id`` IS functionally
determined by ``pay_period_id`` once ``fk_transactions_owner_period`` exists, so
this is a stored copy of a derivable value.  What makes it legal under the rule
is that the derivation and the copy cannot disagree -- the key is the
reconciler, running on every write, in the database.  A stored copy rule 14
forbids is one whose source can move underneath it; this one's cannot, because
moving it is the write the key refuses.  The same construction already stands three times in
this schema, all of them holding an OWNER to an account:
``fk_account_external_identities_owner``, ``fk_statement_matches_owner``,
``fk_merchant_rules_owner``, ``fk_merchant_rules_category_owner`` and
``fk_merchant_rules_income_category_owner``.  A composite key of the same
CONSTRUCTION over a different fact stands twice more on this very table's
family -- ``fk_transactions_reconciled_by`` and
``fk_transaction_entries_parent_account`` -- and it is the second of those that
this revision follows for the ON DELETE rule below.  *A first draft of this
sentence said "three times" and counted the account co-location among the owner
ones.*

*Rejected: the two remedies row P75 listed.*  Both -- resolving the owner's
CALENDAR, and a ``user_id``-filtered JOIN -- are ways of ASKING the question at
each door, and ruling for either would have left two structural answers to one
question on adjacent doors, which is the denormalisation this arc exists to
remove.  Ruling **R-PC32**, Josh, 2026-08-27.

**The backfill reads the PAY PERIOD, and the account key is what grades it.**
``user_id`` comes from ``budget.pay_periods`` because that is where a
transaction's owner has always been defined -- every one of P75's eleven
relationship walks reads ``txn.pay_period.user_id``, and
``utils/auth_helpers.get_accessible_transaction``, the canonical route-boundary
door, is one of them.  ``fk_transactions_owner_account`` then validates every
backfilled row against the OTHER parent, so a database holding a cross-owner
row does not get a quietly-picked winner: the ``ADD CONSTRAINT`` aborts and
names the key.  That is the correct outcome.  Such a row means the two doors
have been disagreeing about who owns money, and a migration is not the place
to decide which of them was right.

**Measured before it was written: ZERO mismatched rows.**  Production 1,028
transactions / 0 mismatched, dev clone 1,057 / 0 (2026-09-02, re-measuring
R-PC32's own 2026-08-27 count).  The expression is
``JOIN accounts a ON a.id = t.account_id JOIN pay_periods p ON p.id =
t.pay_period_id WHERE a.user_id IS DISTINCT FROM p.user_id``.  So the
constraints take clean here, and the abort arm above exists for a database
this chain has not seen.

**The single-column keys STAY, and that is not redundancy.**
``transactions_account_id_fkey`` and ``transactions_pay_period_id_fkey`` are
about the PARENT'S EXISTENCE; the two added here are about AGREEMENT, and each
new key carries the SAME ``ON DELETE`` action as the one it sits beside --
RESTRICT for the account, CASCADE for the pay period.  Two keys over one
column deleting differently would make a parent delete's outcome depend on
which PostgreSQL evaluated.  ``budget.transaction_entries`` states the same
rule for ``fk_transaction_entries_parent_account``, and the single-column key
is also what the ORM relationship declares as its join path, which is what
keeps ``user_id`` from being a column two relationships try to write.

**``fk_transactions_user_id`` is ``ON DELETE RESTRICT``, and it is a RULING
rather than an inheritance** (Josh, 2026-09-02).  The obvious shape was
:class:`~app.models.mixins.UserScopedMixin`, which twenty-two model classes
share and which would have made this table stop being the one the mixin's
docstring lists as an exception.  **Its ``CASCADE`` was
measured first, and it was the only candidate that changed what a user delete
does.**  Driven on a clone of the developer's database at this revision's
``down_revision``, one shape at a time, each inside a rolled-back transaction::

    DELETE FROM auth.users WHERE id = 1;

    this key                  outcome
    ------------------------  ----------------------------------------------
    none (the state before)   REFUSED, transactions_account_id_fkey RESTRICT
    CASCADE (the mixin's)     SUCCEEDS -- see below
    absent, composites only   REFUSED, transactions_account_id_fkey RESTRICT
    RESTRICT (this one)       REFUSED, fk_transactions_user_id RESTRICT

*What CASCADE would have cost, measured rather than argued.*  Under it that
one statement returns ``DELETE 1`` and leaves **1,057 transactions, 9
accounts, 63 pay periods, 1,342 journal entries, 184 purchase entries, 82
balance assertions and 175 transfers all at zero** -- because with the
transactions cascading directly, the ``budget.accounts`` RESTRICT that refuses
the statement today no longer has a row left to object about.  So the DRY
answer would have used a step about OWNERSHIP to open a path that empties the
database, and neither the plan nor the ruling asked for that.

*Why RESTRICT rather than simply omitting the key* (the third row above).  **A
first draft answered "both refuse, this one just refuses earlier", and an
adversarial review measured that FALSE.**  Rows 1 and 3 of the table are
properties of THIS database's constraint-creation ORDER, not of the shape:
their refusal depends on ``accounts_user_id_fkey``'s referential trigger having
a lower OID than ``pay_periods_user_id_fkey``'s, so the accounts cascade is
processed while transactions still exist.  Drop and re-create
``accounts_user_id_fkey`` -- which any future migration touching that key does
-- and the composites-only shape stops refusing: ``DELETE 1``, every count at
zero.  The shipped shape was re-driven under BOTH trigger orderings and refused
under both, naming ``fk_transactions_user_id`` each time.

So the third key is not cosmetic and the argument for it is stronger than the
one it replaces: **it is what makes the refusal ORDER-INDEPENDENT.**  It also
gives ``user_id`` its own guarantee of naming a real user, where omitting the
key leaves that guarantee transitive through ``fk_transactions_owner_account``
and losing it whenever that key is dropped.  And every other document in this
project that leans on "the user delete is already refused" -- ruling
**R-PC41** among them -- is leaning on row 1, which is an artifact of migration
order rather than a property of the schema.
The argument for the action itself is **R-PC41**'s, taken one table over:
nothing in ``app/`` deletes a user, so the event is reachable only by a bug, a
hand-run statement, or a future door whose author has not thought about it,
and each of those wants a loud refusal.  Whether a user SHOULD be deletable is
a product question with a door nobody has built; this revision declines to
answer it by side effect.

*A first version of this docstring claimed the CASCADE "does not make a user
deletable, and does not change whether one is."*  That was written from the
pre-change measurement and is FALSE, which driving the statement against the
post-change schema is what showed.  The name is ``fk_transactions_user_id``
rather than the dialect default because C-43's convention governs a key
declared explicitly, and this one is.

**No index on ``user_id`` alone.**  A referencing-side index is what makes a
parent's delete check cheap, and every key reading this column leads with one
that is already indexed: ``idx_transactions_account`` serves the account key
and ``idx_transactions_period_scenario`` serves the pay-period key.  What is
left is ``fk_transactions_user_id``'s own check on a user delete, which this
revision makes the FIRST refusal rather than a later one.  ``budget.transaction_entries`` and ``budget.statement_matches``
carry the same column with no index of its own.

**The downgrade is value-lossless and its inverse is CONDITIONAL, which it
says rather than pretending otherwise.**  It drops the three keys, the column
and the superkey -- and with the keys gone the cross-owner row is writable
again.  A re-``upgrade`` therefore re-derives ``user_id`` from the pay period
and ``fk_transactions_owner_account`` REFUSES any row written cross-owner in
the window, which is the same abort the first upgrade would have taken.
Nothing silently repairs itself and nothing silently picks a winner.

**Locking.**  ``ADD COLUMN`` with no default is a catalog-only change in
PostgreSQL 11+.  The backfill ``UPDATE`` rewrites every live row (1,028 on
production); ``SET NOT NULL`` scans once; each ``ADD CONSTRAINT ... FOREIGN
KEY`` takes SHARE ROW EXCLUSIVE on both tables and scans
``budget.transactions`` once to validate.  At this table's size that is
instantaneous, and a ``NOT VALID`` + ``VALIDATE`` split would be complexity
bought for a table that does not need it.

**What grades this revision.**  ``tests/test_models/test_c13a_owner_backfill.py``
drives the shipped ``upgrade`` / ``downgrade`` over a database that HOLDS rows,
which the test template cannot: the chain is built against an empty one, so the
backfill's join would otherwise never touch a row.  It covers the round trip,
the per-ROW backfill (with a second owner, because on a single-owner database a
constant and the join return the same integer), and the ABORT arm with its
control.  All five were shown to FIRE against three planted defects -- a
constant-owner backfill, a missing account key, a downgrade that forgets the
superkey -- on 2026-09-02.  ``test_c13a_transaction_owner_key.py`` grades the
KEYS this installs.

**RE-PARENTED from ``b7a41e2c9d63`` to ``bba3bd6a6c44`` before this branch's
PR.**  Three branches authored a revision against ``b7a41e2c9d63`` in parallel
-- ``d7b2e6c1a483`` (``balance:X-au-d``), ``bba3bd6a6c44``
(``bank_import:X-gj-4a``) and this one -- so merging any two as authored would
have left ``dev`` with two alembic heads and a failing ``flask db upgrade``,
which production runs on every deploy.  The three were serialised in merge
order and this one is last.  **The three were also driven together before the
re-point**: all three ``upgrade()``s in order on a probe at the shared parent,
then all three ``downgrade()``s newest-first, ending at 1,057 transactions with
0 unowned.  Each had been driven only against ``b7a41e2c9d63`` ALONE, which is
three measurements of a state only the first would ever meet.

*Every measurement below was taken against the schema at ``b7a41e2c9d63`` and
is not restated for the new parent.*  Neither intervening revision touches this
table's ownership columns, nor the unique constraints on
``budget.pay_periods``.  The first nulls ``estimated_amount`` on salary rows;
the second adds a bank-line disposition.  The composition run above is what
checks that claim rather than the claim checking itself.

*The two are named by their plan steps above rather than repeated as revision
ids here, and that is not style*: ``gitleaks`` reads a twelve-character
hexadecimal id sitting beside the word "keys" as a ``generic-api-key`` and
refuses the commit.  The rule is right about the shape and wrong about this
string, and rewording costs nothing -- where an allowlist entry would have
bought a permanent exemption for a file that contains no secret.

Review: two neutral adversarial reviews, 2026-09-02 -- one on the design and
this docstring, one on the test changes.  Between them they measured five
claims in this file FALSE (the CASCADE's effect on a user delete, "both
refuse", a constraint name that does not exist, a count of fifteen, a count of
three) and one clause guarding an impossible state; every one is corrected
above, and the corrections are marked so the reader can see what was wrong.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a92f6b13c8'
down_revision = 'bba3bd6a6c44'
branch_labels = None
depends_on = None


#: Every transaction's owner, taken from the pay period it is filed in.
#:
#: A single set-based ``UPDATE ... FROM``: ``budget.transactions.pay_period_id``
#: is ``NOT NULL`` with a foreign key, so the join reaches exactly one row for
#: every transaction and none is left NULL for ``SET NOT NULL`` to trip on.
#: The ACCOUNT is deliberately not consulted here -- see the docstring for why
#: the account key grades this answer instead of competing with it.
#:
#: *A first draft added ``AND t.user_id IS DISTINCT FROM p.user_id``, on the
#: stated ground that it made "a re-run after a partial failure inert".  There
#: is no partial failure to re-run against*: ``migrations/env.py`` wraps the
#: whole upgrade in one transaction with no ``transaction_per_migration``, so
#: this revision is all-or-nothing, and on the only run that exists every row
#: is NULL and the predicate excludes nothing.  It was a clause guarding an
#: impossible state, which is what CLAUDE.md rule 13 is about.
_BACKFILL_OWNER_SQL = (
    "UPDATE budget.transactions t "
    "   SET user_id = p.user_id "
    "  FROM budget.pay_periods p "
    " WHERE p.id = t.pay_period_id"
)


def upgrade():
    """Give the row its owner, then make a disagreeing one unstorable."""
    # The superkey first: ``fk_transactions_owner_period`` cannot be created
    # until PostgreSQL has a UNIQUE over exactly ``(id, user_id)`` to target.
    op.create_unique_constraint(
        constraint_name='uq_pay_periods_id_user',
        table_name='pay_periods',
        columns=['id', 'user_id'],
        schema='budget',
    )
    # NULLABLE first: a NOT NULL column with no default cannot be added to a
    # populated table at all.
    op.add_column(
        'transactions',
        sa.Column('user_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(_BACKFILL_OWNER_SQL)
    op.alter_column(
        'transactions', 'user_id',
        existing_type=sa.Integer(),
        nullable=False,
        schema='budget',
    )
    op.create_foreign_key(
        constraint_name='fk_transactions_user_id',
        source_table='transactions',
        referent_table='users',
        local_cols=['user_id'],
        remote_cols=['id'],
        source_schema='budget',
        referent_schema='auth',
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        constraint_name='fk_transactions_owner_account',
        source_table='transactions',
        referent_table='accounts',
        local_cols=['account_id', 'user_id'],
        remote_cols=['id', 'user_id'],
        source_schema='budget',
        referent_schema='budget',
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        constraint_name='fk_transactions_owner_period',
        source_table='transactions',
        referent_table='pay_periods',
        local_cols=['pay_period_id', 'user_id'],
        remote_cols=['id', 'user_id'],
        source_schema='budget',
        referent_schema='budget',
        ondelete='CASCADE',
    )


def downgrade():
    """Drop the owner column and the three keys that hold it in place."""
    op.drop_constraint(
        'fk_transactions_owner_period', 'transactions',
        schema='budget', type_='foreignkey',
    )
    op.drop_constraint(
        'fk_transactions_owner_account', 'transactions',
        schema='budget', type_='foreignkey',
    )
    op.drop_constraint(
        'fk_transactions_user_id', 'transactions',
        schema='budget', type_='foreignkey',
    )
    op.drop_column('transactions', 'user_id', schema='budget')
    op.drop_constraint(
        'uq_pay_periods_id_user', 'pay_periods',
        schema='budget', type_='unique',
    )
