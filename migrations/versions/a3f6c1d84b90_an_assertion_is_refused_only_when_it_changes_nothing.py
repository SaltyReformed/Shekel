"""an assertion is refused only when it changes nothing: drop both anchor unique indexes

Plan step X-f1c4b of ``docs/audits/balance_architecture/README.md``, ruling
**R-EQ** (2026-08-04).

**A content key cannot express idempotency, and these two tried.**  Each anchor
table carried a UNIQUE index over the row's own values, and each write door
translated the violation into "idempotent success":

* ``uq_anchor_history_account_period_balance_day`` on
  ``budget.account_anchor_history (account_id, anchor_balance, observed_on)``
* ``uq_loan_anchor_events_acct_date_bal_day`` on ``budget.loan_anchor_events
  (account_id, anchor_date, anchor_balance, ((created_at AT TIME ZONE
  'UTC')::date))``

Their job was to absorb a double-click, a network retry and a
back-and-resubmit.  **A transport retry and a deliberate re-assertion carry
IDENTICAL values by construction**, so a key over those values must
mis-classify one of the two -- and it mis-classified the correction.  Assert
``$500`` for a day, correct it to ``$600``, then re-assert ``$500`` for that
day: the third write was rejected, ``anchor_service`` reported it as saved, and
every surface kept rendering ``$600``.  Measured on the 2026-08-04 production
clone, account 1 carries 2-3 assertions on **3 of its 50** assertion days, so
the shape is ordinary rather than exotic; the loan door has the same exposure
today, on a form where the date is already user-supplied.

The rule moved to the write doors, which can answer it exactly: an assertion is
appended only when it differs from the assertion GOVERNING THE DAY IT ASSERTS
(``anchor_service.stage_anchor_true_up`` and
``anchor_service._append_loan_anchor_and_sync``), read under the owner's write
lock so a concurrent pair cannot both see the pre-state.

**The horizon is load-bearing and is the reason this claim can be made at all.**
Comparing against the account's LATEST assertion instead has the index's fault
mirrored: a submission for an EARLIER day can never equal the latest, so a
double-click on a back-dated correction appends every time.  That was the first
version of this step and two independent reviews reproduced it on the loan door.
Scoped to the submitted day, the compare refuses exactly the inputs the index
refused CORRECTLY (an identical row that is what stands for that day) and
accepts exactly the ones it refused wrongly (an identical row that has since
been superseded).

**No figure moves and no row moves.**  This drops two indexes and adds nothing;
every existing row is untouched.

**The surplus row the index existed to prevent is financially inert**, which is
why losing the database-level backstop costs nothing even in the interleaving
the lock closes: a duplicate assertion's anchor-correction delta is zero, a zero
delta emits no legs (``account_posting_service._anchors``), same-day corrections
merge on one key, and no cash surface renders the assertion list.

Review: Josh, 2026-08-04.  Destructive: two unique indexes are dropped.
Approved on the measurement above -- a false refusal renders a wrong balance,
the duplicate it prevents posts ``$0.00``.

Revision ID: a3f6c1d84b90
Revises: c81f0a5b3e27
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3f6c1d84b90'
down_revision = 'c81f0a5b3e27'
branch_labels = None
depends_on = None


#: The two indexes this migration drops, with the exact expression each was
#: created from, so ``downgrade`` recreates the shape that existed rather than
#: an approximation of it.  ``account_anchor_history``'s last term was a
#: ``created_at`` truncation until migration ``c4a19e7b2d80`` gave the row a
#: stored ``observed_on``, and it lost ``pay_period_id`` at ``b6d1e94c07af``;
#: the columns below are the state at THIS revision's parent, which is what a
#: downgrade must restore.
_ANCHOR_HISTORY_INDEX = "uq_anchor_history_account_period_balance_day"
_ANCHOR_HISTORY_COLUMNS = "account_id, anchor_balance, observed_on"
_LOAN_ANCHOR_INDEX = "uq_loan_anchor_events_acct_date_bal_day"
_LOAN_ANCHOR_COLUMNS = (
    "account_id, anchor_date, anchor_balance, "
    "((created_at AT TIME ZONE 'UTC')::date)"
)


def upgrade():
    """Drop both anchor uniqueness indexes (ruling R-EQ)."""
    op.drop_index(
        _ANCHOR_HISTORY_INDEX,
        table_name="account_anchor_history",
        schema="budget",
    )
    op.drop_index(
        _LOAN_ANCHOR_INDEX,
        table_name="loan_anchor_events",
        schema="budget",
    )


def _refuse_if_duplicates(table: str, columns: str, index_name: str) -> None:
    """Raise before recreating *index_name* when its key is no longer unique.

    A downgrade re-imposes a constraint the running application deliberately
    stopped honouring, so rows that ruling R-EQ permits may exist by then: two
    assertions with the same key, legitimately appended because each changed
    what governed at the time it was written.  ``CREATE UNIQUE INDEX`` would
    fail on those with PostgreSQL's own message, which names the duplicated
    VALUES but not the operator's choice.

    Fail here instead, with the diagnostic query and both options stated, so
    whoever runs the downgrade decides which rows to remove rather than
    discovering the problem from a constraint violation.

    Args:
        table: The unqualified table name in the ``budget`` schema.
        columns: The index's key expression, as a comma-separated SQL list.
        index_name: The index being recreated, for the message.

    Raises:
        RuntimeError: When any key value appears more than once.
    """
    duplicates = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM (SELECT {columns} FROM budget.{table} "
        f"GROUP BY {columns} HAVING count(*) > 1) AS dupes"
    )).scalar()
    if duplicates:
        raise RuntimeError(
            f"Cannot recreate {index_name}: {duplicates} key value(s) in "
            f"budget.{table} now appear more than once, because ruling R-EQ "
            "permits an assertion that changes what governs even when an "
            "identical row exists earlier in the history.  Inspect them with:\n"
            f"  SELECT {columns}, count(*), array_agg(id) FROM budget.{table}\n"
            f"  GROUP BY {columns} HAVING count(*) > 1;\n"
            "Then either delete the superseded duplicates (the LOWER id of "
            "each pair is the older assertion and is not what governs), or "
            "stay on this revision -- the write doors refuse a no-op assertion "
            "without the index."
        )


def downgrade():
    """Recreate both anchor uniqueness indexes.

    Working rather than refusing: at this revision's parent both indexes
    existed, so restoring them restores the schema exactly.  It is guarded
    because the application ran without them in between and may have written
    rows they would reject; see :func:`_refuse_if_duplicates`.
    """
    _refuse_if_duplicates(
        "account_anchor_history", _ANCHOR_HISTORY_COLUMNS, _ANCHOR_HISTORY_INDEX,
    )
    _refuse_if_duplicates(
        "loan_anchor_events", _LOAN_ANCHOR_COLUMNS, _LOAN_ANCHOR_INDEX,
    )
    op.execute(
        f"CREATE UNIQUE INDEX {_ANCHOR_HISTORY_INDEX} "
        f"ON budget.account_anchor_history ({_ANCHOR_HISTORY_COLUMNS})"
    )
    op.execute(
        f"CREATE UNIQUE INDEX {_LOAN_ANCHOR_INDEX} "
        f"ON budget.loan_anchor_events ({_LOAN_ANCHOR_COLUMNS})"
    )
