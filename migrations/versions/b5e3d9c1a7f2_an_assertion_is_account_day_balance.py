"""an assertion is (account, day, balance): drop account_anchor_history.notes

Plan step X-f1e2 of ``docs/audits/balance_architecture/README.md``, ruling
**R-ES** (2026-08-05).

**The column was a free-text provenance label that nothing consulted.**  An AST
census of every ``.notes`` attribute access in ``app/`` returns four hits after
this change -- two on a salary tax checkpoint and two on a transfer -- and none
on an anchor row.  (A transaction's ``notes`` is read in Jinja and through a
dict subscript, never as an attribute in ``app/``; an earlier draft of this
paragraph said "five hits ... on transactions, transfers and tax checkpoints",
counting the WRITE this migration deletes and mis-naming the categories.  Two
adversarial reviews caught it.)  The only reference to this column anywhere in
``app/`` was that write, in ``account_service.create_account``.

Measured by querying **the production database** on 2026-08-05
(``shekel-prod-db``; the dev runtime database is one assertion behind and
answers 77), ``budget.account_anchor_history`` carries 78 rows:

* **76** with ``notes IS NULL``;
* **1** reading ``origination`` (id 63, account 11);
* **1** reading ``origination backfill (E-19, Commit 3)`` (id 45, account 8).

Both labelled rows are the ONLY assertion their account carries, so the label
identifies nothing that the row's position did not already identify.

**It was also a SECOND definition of a word the engine already owns.**
``cash_ledger.cash_anchor_facts`` marks an account's opening POSITIONALLY --
``is_opening = index == 0`` over rows ordered ``(observed_on, created_at, id)``
-- and ``account_posting_service._anchors`` maps that flag to the typed
``account_opening`` / ``account_trueup`` posting source kinds.  A provenance
label answers "which door wrote this row", which is a different question, and a
back-dated assertion (permitted by ``anchor_service.resolve_observation_day``,
whose floor is the schedule's start rather than the account's own opening) puts
the two answers out of step.  One question with two answers is the defect class
this arc exists to delete.

**The forensic trail is unaffected, and the precise claim is narrower than the
obvious one.**  ``budget.account_anchor_history`` is in
``app.audit_infrastructure.AUDITED_TABLES``, so an INSERT lands in
``system.audit_log`` with the complete row as JSONB -- including whatever
``notes`` held, which is why nothing forensic is lost by this drop.  Two limits,
both measured on production rather than assumed: the trigger was attached on
2026-05-07, so 34 of the 78 rows predate it and have no audit row at all; and
``app.current_user_id`` is set only for an authenticated request
(``app/utils/logging_config.py``), so a REGISTRATION's origination -- exactly
the row that carried ``notes="origination (sign-up)"`` -- is audited with a NULL
user.  An adversarial review measured both after a first draft of this paragraph
claimed "every INSERT ... with the acting user".  The claim that survives is the
one that matters here: the column's contents are recoverable from the audit
row's ``new_data`` wherever an audit row exists, and the column itself was read
by nothing either way.

**The loan twin is deliberately NOT touched.**  ``loan_anchor_events.source_id``
is a typed FK into ``ref.loan_anchor_sources`` and it is READ:
``loan_loaders.load_loan_anchor_facts`` distinguishes a ``tracking_start`` from
a ``user_trueup``, ``anchor_service._governing_loan_anchor`` scopes the
duplicate-submit compare per source, and the loan dashboard's drift card
renders the label.  ``AccountAnchorHistory`` carries one kind of fact and needs
no such split -- ``anchor_service``'s own docstring said so before this ruling
applied it.

**No figure moves.**  ``notes`` is read by no producer, no serializer and no
template, so no balance, no projection and no rendered caption changes.

Review: Josh, 2026-08-05.  Destructive: one column is dropped, and the two
non-NULL values above are lost.  Approved on the census -- the column labels 2
of 9 originations, is read by nothing, and duplicates a distinction the ledger
already draws.  ``downgrade`` restores the column but CANNOT restore those two
strings; it prints the literal ``UPDATE`` statements that put them back, and
they are reproduced here so this file alone is enough to do it:

    UPDATE budget.account_anchor_history
       SET notes = 'origination backfill (E-19, Commit 3)' WHERE id = 45;
    UPDATE budget.account_anchor_history
       SET notes = 'origination' WHERE id = 63;

Revision ID: b5e3d9c1a7f2
Revises: a3f6c1d84b90
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5e3d9c1a7f2'
down_revision = 'a3f6c1d84b90'
branch_labels = None
depends_on = None


#: The production rows carrying a non-NULL ``notes`` at this revision's parent,
#: as ``(id, notes)``.  Held here so ``downgrade`` can TELL the operator what it
#: cannot restore, naming the rows rather than saying data was lost.  Measured
#: against PRODUCTION on 2026-08-05; a database where these ids carry
#: different rows is one where the strings were never there to begin with, which
#: is why the downgrade prints them rather than executing them.
_LOST_LABELS: tuple[tuple[int, str], ...] = (
    (45, "origination backfill (E-19, Commit 3)"),
    (63, "origination"),
)


def upgrade():
    """Drop ``budget.account_anchor_history.notes`` (ruling R-ES)."""
    op.drop_column("account_anchor_history", "notes", schema="budget")


def downgrade():
    """Re-add the nullable ``notes`` column; its two values cannot come back.

    The column is restored EMPTY.  Nothing in the application ever read it, so
    an empty column is functionally identical to the populated one for every
    code path that existed -- but "functionally identical" is not "restored",
    and a downgrade that silently drops two recorded strings is the kind of
    quiet loss this project's migration rules exist to prevent.  The literal
    ``UPDATE`` statements that put them back are echoed to the migration's
    console output and are also in this module's docstring.
    """
    op.add_column(
        "account_anchor_history",
        sa.Column("notes", sa.Text(), nullable=True),
        schema="budget",
    )
    restore = "\n".join(
        "  UPDATE budget.account_anchor_history "
        f"SET notes = '{label}' WHERE id = {row_id};"
        for row_id, label in _LOST_LABELS
    )
    print(
        "budget.account_anchor_history.notes is restored EMPTY.  The two "
        "values production carried are not recoverable from the schema; "
        f"re-apply them by hand if they are wanted:\n{restore}"
    )
