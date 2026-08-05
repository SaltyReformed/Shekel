"""the anchor balance has one home: the assertion, not a column beside it

Plan step X-f1c3c of ``docs/audits/balance_architecture/README.md``, ruling
**R-EH** (2026-08-03), with **R-EN** riding along.

**They were a denormalized copy of a row, and the row already won.**
``budget.accounts.current_anchor_balance`` / ``current_anchor_period_id`` held
the newest ``account_anchor_history`` row's balance and period.
``cash_ledger/_facts.py`` said so in those words, and when the two disagreed the
resolver logged ``EVT_ANCHOR_CACHE_RECONCILED``, let the history row win, and
left the copy wrong -- a divergence detected and never repaired (finding
**cash D4**).  **24 read sites across 16 modules, plus 12 template references**,
took the copy instead of the fact -- counted by AST, where an earlier draft said
"twelve rendering surfaces" and could not be checked from the artifact.
Plan step X-f1c3a pointed every one of them at
:func:`app.services.cash_ledger.resolve_anchor`; this drops what they stopped
reading.

**Measured before the drop, read-only on ``shekel-prod-db`` 2026-08-04: 9
accounts, 78 assertions, and the copy agreed with the latest assertion on every
single one.**  Zero divergences, so no figure moves.

**What goes with them, because it existed only for them:**

* ``ck_accounts_anchor_balance_present``, the redundant ``IS NOT NULL`` CHECK
  beside a ``NOT NULL`` column (E-19 / Commit 3).
* ``accounts_current_anchor_period_id_fkey``, declared
  ``ON DELETE NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` by migration
  ``d410f6b9caa3`` for ONE caller: ``pay_period_admin.reset_pay_periods``
  issued ``SET CONSTRAINTS ... DEFERRED`` so it could delete a user's anchor
  period and re-point every account inside one transaction.  With no column to
  re-point there is nothing to defer, and the reset's whole re-anchoring pass
  is deleted in the same commit.
* ``PeriodLockReason.ACCOUNT_ANCHOR``, the app-tier refusal this FK backstopped.
  It becomes unreachable rather than relaxed: an account no longer references a
  pay period and (since ruling R-EO, migration ``b6d1e94c07af``) neither does an
  assertion, so no period delete can take one.  What is still worth protecting
  is the period's POSTED state, and ``LEDGER_POSTINGS`` -- which already
  outranked ``ACCOUNT_ANCHOR`` in the precedence -- covers it: measured, all 10
  periods holding an assertion carry an unbalanced ledger account.
* ``account_service.resolve_anchor_period_id``, callerless once neither the
  account nor the assertion needs a period.  **That closes finding N-170
  structurally**: the app carried TWO day-to-period derivations that disagreed
  for a day past the schedule (the writer's "containing, else EARLIEST" against
  the ledger's "containing, else LATEST ending before"), and deleting the
  writer's leaves one.

**Ruling R-EN rides along, and it is a behaviour change to a UI contract.**  The
C-17 optimistic lock leaves the cash true-up path.  ``version_id`` increments
when the ORM UPDATEs ``accounts`` and on nothing else, so once a true-up only
INSERTs an assertion, ``StaleDataError`` is structurally unreachable there --
**measured against the dev database and rolled back: a history INSERT plus flush
leaves ``version_id`` at 33, and the very next line writing
``current_anchor_balance`` takes it to 34.**  The step accepts what that means
rather than working around it: an assertion history is APPEND-ONLY, so a second
tab overwrites no ASSERTION, two assertions of different balances are two facts,
the later-observed one is current and neither is lost.  That is verbatim the
contract ``apply_loan_anchor_true_up`` has documented since Commit 16.  The
``STALE_CONFLICT`` outcome, the 409 conflict cell, the form's hidden
``version_id`` and ``AnchorUpdateSchema.version_id`` all go.  Same-balance
double-submits stay idempotent: the F-103 unique index catches those.
``AccountUpdateSchema`` keeps ITS ``version_id`` -- the full account-edit form
writes real columns and still has a row to guard.

**And the deleted lock WAS load-bearing, for something other than the column it
guarded -- a defect this ruling opened and the same step closed.**  Append-only
is true of ``account_anchor_history`` and false of the transaction a true-up
runs: it also RECONCILES the account's posted corrections, a read-modify-write
against ``budget.journal_entries`` / ``budget.account_postings`` with no unique
index to catch a racing duplicate, and the ``version_id`` UPDATE had been
serialising it by accident (it autoflushed and took a row lock before the walk).
Measured with the interleave forced at the reconcile's read: two concurrent
true-ups on an account reconciled at ``$4,000.00`` both answer 200, both
assertions survive, and the linked ledger settles at ``$1,000.00`` against a
resolved ``$2,000.00`` -- trial balance still ``$0.00``, because the
anchor-equity leg mirrors the error.  The replacement is a per-owner advisory
lock taken INSIDE the reconcile
(:func:`app.services.user_write_lock.lock_user_writes`), so it protects the
window itself rather than one of its doors, and it covers the loan reconcile
too -- which has carried the identical race since Commit 16, unserialised, as
the very contract this ruling cited as precedent.

**Destructive, and the downgrade WORKS.**  Both columns are an exact function of
a surviving fact: the newest ``account_anchor_history`` row by
``(observed_on, created_at, id)`` DESC -- the ordering
:func:`app.services.cash_ledger.resolve_anchor` reads and, since migration
``b6d1e94c07af``, the only place that ordering exists.  Every account is
guaranteed at least an origination assertion (E-19 / Commit 3), so the
derivation is total.  The restored ``current_anchor_period_id`` is resolved from
that assertion's ``observed_on`` -- the period containing the day, else the
user's earliest -- which is exactly what ``resolve_anchor_period_id`` computed
when it still existed.

**Exercised both directions on a production clone 2026-08-04, and one restored
value differs from what was dropped.**  All 9 balances come back to the cent.
Eight of the nine periods come back identical; account 8's comes back as period
5 rather than the period 1 it carried, because its assertion's ``observed_on``
(``2026-05-21``) falls inside period 5 and the stored value never did -- finding
N-168's row 45, which migration ``b6d1e94c07af``'s downgrade repairs one table
over for the same reason.  Stated rather than glossed: a downgrade that
recomputes a cache from its source restores the CORRECT value, not the wrong
one that was there, and a reader comparing a pre-drop dump against a
post-downgrade dump must expect that single row to differ.

Review: developer, 2026-08-03 (ruling R-EH, taken on the root-cause framing over
an offered guard: writing the cache only when the new assertion is the latest
was correct and ~5 lines, and was declined as maintaining a denormalization this
arc's own root cause 1 names.  Ruling R-EN taken 2026-08-04 with the deletion of
the 409 conflict UX, the append-only concurrency semantics and the measured
``version_id`` probe all stated before the ruling).

Revision ID: c81f0a5b3e27
Revises: b6d1e94c07af
Create Date: 2026-08-04 11:05:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'c81f0a5b3e27'
down_revision = 'b6d1e94c07af'
branch_labels = None
depends_on = None


_FK_NAME = "accounts_current_anchor_period_id_fkey"
_CHECK_NAME = "ck_accounts_anchor_balance_present"


# **The one statement of "which assertion is this account's current one".**
# ``DISTINCT ON`` with the resolver's own ordering -- ``observed_on DESC,
# created_at DESC, id DESC``, which is what ``cash_ledger.resolve_anchor``
# reads and, since migration ``b6d1e94c07af``, the only place that ordering
# exists.  Both backfill UPDATEs below build on THIS text rather than each
# spelling the subquery out, because the two copies could otherwise drift and a
# downgrade would restore one column from a different assertion than the other.
#
# It is a standalone SELECT, and that is deliberate: it can be executed
# directly against a live database and compared row-for-row against
# ``resolve_anchor``, which is how ``tests/test_models/test_anchor_cache_
# downgrade.py`` grades the ordering without needing the DDL this migration's
# ``downgrade()`` runs.  A source-only check could not see an ordering that had
# drifted.
_CURRENT_ASSERTION_SQL = """
    SELECT DISTINCT ON (account_id)
           account_id, anchor_balance, observed_on
    FROM budget.account_anchor_history
    ORDER BY account_id, observed_on DESC, created_at DESC, id DESC
"""

# The downgrade's backfill.  The current assertion names the balance; the
# period is then resolved from that assertion's day exactly as
# ``resolve_anchor_period_id`` did -- the period CONTAINING it, else the
# owner's earliest.  Two statements rather than one because the period lookup
# is scoped to the ACCOUNT'S OWNER, and a period belonging to someone else must
# never be selectable here.
_DOWNGRADE_BALANCE = sa.text(f"""
    UPDATE budget.accounts a
    SET current_anchor_balance = latest.anchor_balance
    FROM ({_CURRENT_ASSERTION_SQL}) latest
    WHERE latest.account_id = a.id
""")

# The period half, also a standalone SELECT and for the same reason: the
# OWNER-SCOPING on both subqueries (``p.user_id = a.user_id``) is the part a
# source-level check cannot grade, and dropping it would anchor an account to
# another user's pay period on every downgrade, silently.  Executed directly by
# ``tests/test_models/test_anchor_cache_downgrade.py`` against a two-user
# fixture.
_RESOLVED_PERIOD_SQL = f"""
    SELECT a.id AS account_id,
           COALESCE(
               (
                   SELECT p.id FROM budget.pay_periods p
                   WHERE p.user_id = a.user_id
                     AND latest.observed_on BETWEEN p.start_date AND p.end_date
                   ORDER BY p.period_index
                   LIMIT 1
               ),
               (
                   SELECT p.id FROM budget.pay_periods p
                   WHERE p.user_id = a.user_id
                   ORDER BY p.period_index
                   LIMIT 1
               )
           ) AS pay_period_id
    FROM budget.accounts a
    JOIN ({_CURRENT_ASSERTION_SQL}) latest ON latest.account_id = a.id
"""

_DOWNGRADE_PERIOD = sa.text(f"""
    UPDATE budget.accounts a
    SET current_anchor_period_id = resolved.pay_period_id
    FROM ({_RESOLVED_PERIOD_SQL}) resolved
    WHERE resolved.account_id = a.id
""")

# The downgrade's post-backfill gate.  An account with no assertion at all, or
# whose owner has no pay periods, cannot resolve either column, and both are
# about to become NOT NULL -- so the downgrade fails loudly with the diagnostic
# rather than installing constraints it cannot satisfy.
_UNRESOLVED = sa.text("""
    SELECT id, user_id, name
    FROM budget.accounts
    WHERE current_anchor_balance IS NULL
       OR current_anchor_period_id IS NULL
    ORDER BY id
""")


def upgrade():
    """Drop the anchor cache columns, their CHECK and their deferrable FK."""
    op.drop_constraint(
        _CHECK_NAME, "accounts", schema="budget", type_="check",
    )
    op.drop_constraint(
        _FK_NAME, "accounts", schema="budget", type_="foreignkey",
    )
    op.drop_column("accounts", "current_anchor_period_id", schema="budget")
    op.drop_column("accounts", "current_anchor_balance", schema="budget")


def downgrade():
    """Restore both columns from each account's current balance ASSERTION."""
    op.add_column(
        "accounts",
        sa.Column("current_anchor_balance", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    op.add_column(
        "accounts",
        sa.Column("current_anchor_period_id", sa.Integer(), nullable=True),
        schema="budget",
    )

    connection = op.get_bind()
    connection.execute(_DOWNGRADE_BALANCE)
    connection.execute(_DOWNGRADE_PERIOD)
    unresolved = connection.execute(_UNRESOLVED).fetchall()
    if unresolved:
        raise RuntimeError(
            "downgrade c81f0a5b3e27: "
            f"{len(unresolved)} accounts could not resolve an anchor balance "
            "or period, so the columns cannot be made NOT NULL.  Each either "
            "carries no account_anchor_history row (which E-19 / Commit 3 "
            "makes unreachable) or belongs to a user with no pay periods.  "
            f"Offending rows (id, user_id, name): {unresolved}.  Repair those "
            "accounts and re-run the downgrade."
        )

    op.alter_column(
        "accounts", "current_anchor_balance",
        existing_type=sa.Numeric(12, 2), nullable=False, schema="budget",
    )
    op.alter_column(
        "accounts", "current_anchor_period_id",
        existing_type=sa.Integer(), nullable=False, schema="budget",
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "accounts",
        "current_anchor_balance IS NOT NULL",
        schema="budget",
    )
    # Restored with the action and the deferrability migration d410f6b9caa3
    # installed: NO ACTION rather than RESTRICT because only NO ACTION can be
    # deferred, which the pay-period reset needed.
    op.create_foreign_key(
        _FK_NAME,
        "accounts",
        "pay_periods",
        ["current_anchor_period_id"],
        ["id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="NO ACTION",
        deferrable=True,
        initially="IMMEDIATE",
    )
