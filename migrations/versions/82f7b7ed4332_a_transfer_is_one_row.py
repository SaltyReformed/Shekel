"""a transfer is one row: its twin rows are deleted and refused

Revision ID: 82f7b7ed4332
Revises: e616adf7fe22
Create Date: 2026-10-09
Review: developer, 2026-09-30 (ruling R-BAL166: the twins are deleted in the
release that stops their upkeep) and 2026-10-09 (ruling R-BAL258: the database
refuses a new twin; ruling R-BAL260: the downgrade restores each twin from a
kept copy)

Plan step **balance:X-bi-6-4d-3** of ``docs/audits/balance_architecture/README.md``
(design ``X-bi-6-4d`` D6).  **Every transfer had two hidden twin
``budget.transactions`` rows**, one on each account, which the transfer service
created with it and kept equal to it.  Since ``e616adf7fe22`` (the step before,
in the same release) each side's payment record hangs off the transfer by a side
link and nothing reads a twin, so this revision deletes them:

  1. **It refuses, writing nothing, while a twin could not go cleanly**, each
     named with the query that finds it: a twin something else still names (a
     journal entry, a statement-match creation, a movement or a card payback --
     a delete would null, cascade or refuse each of those), or a transfer whose
     twins are not exactly one expense and one income (Transfer Invariant 1
     broken; the kept copy could not then be restored as a pair).  Every count
     is 0 where the transfer service wrote the rows.
  2. **It keeps a copy of every twin** in ``system.transfer_twin_purge``: the
     whole row as ``jsonb`` (the shape ``b2d8f3a6c541``'s
     ``system.pre_origination_purge`` keeps), with the transfer's category and
     whether the twin's side held a payment record at that moment, which the
     downgrade's re-sync reads (ruling **R-BAL260**, "Keep a copy").
  3. **It deletes every twin** and adds ``ck_transactions_names_no_transfer``
     (``transfer_id IS NULL``), so a row naming a transfer is unstorable from
     here on (ruling **R-BAL258**, "Database refuses").  Plan step
     ``X-bi-6-5`` drops ``transactions.transfer_id`` with this CHECK, its
     index, its partial unique index, its foreign key and its term in
     ``ck_transactions_one_pricing_link``.

**No money moves.**  A twin held no figure (each declared ``parent_transfer``
and stored none), no record (``e616adf7fe22`` moved every one onto its side
link) and no statement link of its own that its side's record does not also
carry; the deletes write audit rows, as any delete does.  The category
"Transfers: Incoming" that older income twins carried in place of their
transfer's own (finding **BAL-575**) is no longer used by any row it was used
only by, so the category screen may then delete it.

**The downgrade restores the kept copy EXACTLY where nothing has changed**
(ruling **R-BAL260**): every twin whose transfer still exists comes back with
its own id, creation and update times, version counter, type, name, category
and day, so a downgrade straight after the upgrade, with no write between,
reproduces every twin column for column, and an older revision's own
downgrade that finds a twin by its id (``c4e91a7b2d38``'s
``system.loan_due_date_backfill``) still finds it.  **What the app changed
since is brought back in line, column by column** -- the one place the round
trip is not exact, by design:

  * **from the transfer**: ``user_id``, ``account_id`` (the side's endpoint,
    which an endpoint move changes), ``pay_period_id``, ``scenario_id``,
    ``status_id``, ``due_date``, ``is_override`` and ``is_deleted`` -- the
    fields the code below this revision mirrored onto both twins;
  * ``name``: the kept name, unless either endpoint moved, when BOTH twins
    take their side's label from the endpoints' current names ("Transfer to
    <to>" on the from-side, "Transfer from <from>" on the to-side), as the
    endpoint move below this revision re-derived both;
  * ``category_id``: the from-side twin takes the transfer's; the to-side twin
    keeps its kept category unless the transfer's category changed since, when
    it takes the transfer's, as the update below this revision wrote both;
  * ``settled_on``, ``settled_day_basis_id`` and ``reconciled_by_id``: the
    side's payment record's when it holds one; when it holds none, the kept
    values if the twin held none either and its transfer is still in the
    settled status it was kept in (a ``$0.00`` close unchanged since), and
    otherwise none -- a ``$0.00`` close made after the upgrade has no day
    anywhere (ruling **R-BAL230**), so its twins come back settled with no
    day, the dateless settled pair the code below calls legacy (finding
    **N-181**).

``e616adf7fe22``'s downgrade, which runs next, requires exactly this: every
twin saying its transfer's status and its side's record's day, basis and link.
A transfer created after the upgrade has no kept twins, so it gets two new ones,
built from it the way the create door below this revision built them (new
ids, version 1, created when the transfer was, updated at the downgrade).
A kept twin whose transfer was since hard-deleted is not restored: the code
below this revision would have cascaded it away with its transfer.  The copy is
dropped last.

``tests/test_models/test_a_transfer_is_one_row.py`` drives the shipped
``upgrade`` / ``downgrade``, each refusal, the exact round trip and the re-sync.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "82f7b7ed4332"
down_revision = "e616adf7fe22"
branch_labels = None
depends_on = None


#: The INCOME transaction type's id, by name: a migration resolves a ref row by
#: name because its id differs between databases.  An income twin is the
#: to-side; every other twin is the from-side (``e616adf7fe22``'s rule).
_INCOME = "(SELECT id FROM ref.transaction_types WHERE name = 'Income')"

#: The EXPENSE transaction type's id, by name, for a twin built new.
_EXPENSE = "(SELECT id FROM ref.transaction_types WHERE name = 'Expense')"

#: The ``parent_transfer`` amount source, by name: every twin declared it.
_PARENT_TRANSFER = (
    "(SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer')"
)

#: How many offending rows a refusal names; the diagnostic query finds the rest.
_NAMED_AT_MOST = 20

#: Every twin another table still names, with the column that names it.  A
#: delete would set a journal entry's or a card payback's link NULL, cascade
#: away a statement-match creation, or be refused by a movement or a payback
#: row, so each is refused here instead, before anything is written.
_REFERENCED_TWINS_SQL = """
SELECT t.id AS twin_id, t.transfer_id, r.what
  FROM budget.transactions t
  JOIN LATERAL (
        SELECT 'journal_entries.transaction_id' AS what
         WHERE EXISTS (SELECT 1 FROM budget.journal_entries j
                        WHERE j.transaction_id = t.id)
        UNION ALL
        SELECT 'statement_match_creations.transaction_id'
         WHERE EXISTS (SELECT 1 FROM budget.statement_match_creations c
                        WHERE c.transaction_id = t.id)
        UNION ALL
        SELECT 'transaction_entries.transaction_id'
         WHERE EXISTS (SELECT 1 FROM budget.transaction_entries e
                        WHERE e.transaction_id = t.id)
        UNION ALL
        SELECT 'transaction_entries.credit_payback_id'
         WHERE EXISTS (SELECT 1 FROM budget.transaction_entries e
                        WHERE e.credit_payback_id = t.id)
        UNION ALL
        SELECT 'transactions.credit_payback_for_id'
         WHERE EXISTS (SELECT 1 FROM budget.transactions p
                        WHERE p.credit_payback_for_id = t.id)
       ) r ON TRUE
 WHERE t.transfer_id IS NOT NULL
 ORDER BY t.id, r.what
"""

#: Every transfer whose twins are not exactly one expense and one income, live
#: or deleted: ``(transfer id, expense twins, income twins)``.
_UNPAIRED_SQL = f"""
SELECT x.id,
       count(t.id) FILTER (WHERE t.transaction_type_id <> {_INCOME}),
       count(t.id) FILTER (WHERE t.transaction_type_id = {_INCOME})
  FROM budget.transfers x
  LEFT JOIN budget.transactions t ON t.transfer_id = x.id
 GROUP BY x.id
HAVING count(t.id) FILTER (WHERE t.transaction_type_id <> {_INCOME}) <> 1
    OR count(t.id) FILTER (WHERE t.transaction_type_id = {_INCOME}) <> 1
 ORDER BY x.id
"""

_CREATE_COPY_SQL = """
CREATE TABLE system.transfer_twin_purge (
    row_id integer PRIMARY KEY,
    transfer_id integer NOT NULL,
    row_data jsonb NOT NULL,
    transfer_category_id integer,
    side_held_record boolean NOT NULL
)
"""

_COMMENT_COPY_SQL = """
COMMENT ON TABLE system.transfer_twin_purge IS
'Every transfer twin row (budget.transactions with transfer_id set) as revision
82f7b7ed4332 (plan step balance:X-bi-6-4d-3, ruling R-BAL260) deleted it: the
whole row as jsonb, the transfer''s category and whether the twin''s side held
a payment record at that moment.  Read by that revision''s downgrade, which
restores each twin from it, and inherited by plan step balance:X-bi-6-5, which
decides its fate when it drops transactions.transfer_id.'
"""

#: The copy: each twin whole, its transfer's category, and whether its side
#: held a payment record (a side link on the twin's side of its transfer).
_KEEP_COPY_SQL = f"""
INSERT INTO system.transfer_twin_purge
       (row_id, transfer_id, row_data, transfer_category_id, side_held_record)
SELECT t.id, t.transfer_id, to_jsonb(t), x.category_id,
       EXISTS (
         SELECT 1 FROM budget.transaction_entries e
          WHERE CASE WHEN t.transaction_type_id = {_INCOME}
                     THEN e.income_transfer_id
                     ELSE e.expense_transfer_id END = x.id
       )
  FROM budget.transactions t
  JOIN budget.transfers x ON x.id = t.transfer_id
"""

_DELETE_TWINS_SQL = "DELETE FROM budget.transactions WHERE transfer_id IS NOT NULL"

#: Each kept twin whose transfer still exists, re-synced (the module
#: docstring's list) and expanded back into the table.  ``side`` is the
#: twin's endpoint and its side's record, read once.
_RESTORE_KEPT_SQL = f"""
INSERT INTO budget.transactions
SELECT (jsonb_populate_record(
          NULL::budget.transactions,
          p.row_data || jsonb_build_object(
            'user_id', x.user_id,
            'account_id', side.account_id,
            'pay_period_id', x.pay_period_id,
            'scenario_id', x.scenario_id,
            'status_id', x.status_id,
            'due_date', x.due_date,
            'is_override', x.is_override,
            'is_deleted', x.is_deleted,
            'name', CASE WHEN moved.either THEN side.label
                         ELSE p.row_data->>'name' END,
            'category_id', CASE
                WHEN NOT side.is_income THEN x.category_id
                WHEN x.category_id IS NOT DISTINCT FROM p.transfer_category_id
                    THEN (p.row_data->>'category_id')::integer
                ELSE x.category_id END,
            'settled_on', CASE
                WHEN side.record_id IS NOT NULL THEN side.settled_on
                WHEN kept_day.keeps THEN (p.row_data->>'settled_on')::date
                END,
            'settled_day_basis_id', CASE
                WHEN side.record_id IS NOT NULL THEN side.basis_id
                WHEN kept_day.keeps
                    THEN (p.row_data->>'settled_day_basis_id')::integer
                END,
            'reconciled_by_id', CASE
                WHEN side.record_id IS NOT NULL THEN side.reconciled_by_id
                WHEN kept_day.keeps
                    THEN (p.row_data->>'reconciled_by_id')::integer
                END
          )
        )).*
  FROM system.transfer_twin_purge p
  JOIN budget.transfers x ON x.id = p.transfer_id
  JOIN ref.statuses s ON s.id = x.status_id
  JOIN budget.accounts fa ON fa.id = x.from_account_id
  JOIN budget.accounts ta ON ta.id = x.to_account_id
  CROSS JOIN LATERAL (
        SELECT (p.row_data->>'transaction_type_id')::integer = {_INCOME}
               AS is_income
       ) kind
  CROSS JOIN LATERAL (
        SELECT kind.is_income,
               CASE WHEN kind.is_income THEN x.to_account_id
                    ELSE x.from_account_id END AS account_id,
               CASE WHEN kind.is_income THEN 'Transfer from ' || fa.name
                    ELSE 'Transfer to ' || ta.name END AS label,
               e.id AS record_id, e.settled_on,
               e.settled_day_basis_id AS basis_id, e.reconciled_by_id
          FROM (SELECT 1) one
          LEFT JOIN budget.transaction_entries e
                 ON CASE WHEN kind.is_income THEN e.income_transfer_id
                         ELSE e.expense_transfer_id END = x.id
       ) side
  CROSS JOIN LATERAL (
        SELECT (NOT p.side_held_record
                AND s.is_settled
                AND (p.row_data->>'status_id')::integer = x.status_id)
               AS keeps
       ) kept_day
  CROSS JOIN LATERAL (
        -- Either endpoint moved since: this twin's kept account is not its
        -- side's endpoint now, or its sibling's is not the other side's.  The
        -- upgrade refused any transfer whose twins were not one per side, so
        -- the sibling is exactly one kept row.
        SELECT (p.row_data->>'account_id')::integer <> side.account_id
               OR (q.row_data->>'account_id')::integer
                  <> CASE WHEN kind.is_income THEN x.from_account_id
                          ELSE x.to_account_id END
               AS either
          FROM system.transfer_twin_purge q
         WHERE q.transfer_id = p.transfer_id AND q.row_id <> p.row_id
       ) moved
 ORDER BY p.row_id
"""

#: Two new twins for each transfer the copy holds none of (one created after
#: the upgrade), built as the create door below this revision built them, with
#: each side's day, basis and link from its record.
_BUILD_NEW_SQL = f"""
INSERT INTO budget.transactions (
    account_id, template_id, pay_period_id, scenario_id, status_id, name,
    category_id, transaction_type_id, estimated_amount, is_override,
    is_deleted, transfer_id, credit_payback_for_id, notes, created_at,
    updated_at, due_date, version_id, settled_on, amount_source_id,
    reconciled_by_id, settled_day_basis_id, occurs_on, user_id
)
SELECT side.account_id, NULL, x.pay_period_id, x.scenario_id, x.status_id,
       side.label, x.category_id, side.type_id, NULL, x.is_override,
       x.is_deleted, x.id, NULL, NULL, x.created_at, now(), x.due_date, 1,
       e.settled_on, {_PARENT_TRANSFER}, e.reconciled_by_id,
       e.settled_day_basis_id, NULL, x.user_id
  FROM budget.transfers x
  JOIN budget.accounts fa ON fa.id = x.from_account_id
  JOIN budget.accounts ta ON ta.id = x.to_account_id
  CROSS JOIN LATERAL (
        VALUES (FALSE, x.from_account_id, 'Transfer to ' || ta.name,
                {_EXPENSE}),
               (TRUE, x.to_account_id, 'Transfer from ' || fa.name,
                {_INCOME})
       ) side(is_income, account_id, label, type_id)
  LEFT JOIN budget.transaction_entries e
         ON CASE WHEN side.is_income THEN e.income_transfer_id
                 ELSE e.expense_transfer_id END = x.id
 WHERE NOT EXISTS (
         SELECT 1 FROM system.transfer_twin_purge p WHERE p.transfer_id = x.id
       )
 ORDER BY x.id, side.is_income
"""


def refuse_twins_that_cannot_go(bind) -> None:
    """Refuse the upgrade while a twin could not be deleted and restored cleanly.

    **Module-level so a test can DRIVE each refusal** (the chain's own pattern,
    ``e616adf7fe22.refuse_unlinkable_rows``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending twin or transfer and the query that
            finds it.  Nothing has been written.
    """
    referenced = [tuple(row) for row in bind.execute(sa.text(_REFERENCED_TWINS_SQL))]
    unpaired = [tuple(row) for row in bind.execute(sa.text(_UNPAIRED_SQL))]
    problems = []
    if referenced:
        problems.append(
            f"{len(referenced)} twin reference(s) would be nulled, cascaded "
            f"or refused by the delete (twin id, transfer id, what names it; "
            f"first {_NAMED_AT_MOST}: {referenced[:_NAMED_AT_MOST]}; diagnose "
            f"with: {_REFERENCED_TWINS_SQL.strip()})"
        )
    if unpaired:
        problems.append(
            f"{len(unpaired)} transfer(s) hold other than one expense and one "
            f"income twin (transfer id, expense twins, income twins; first "
            f"{_NAMED_AT_MOST}: {unpaired[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_UNPAIRED_SQL.strip()})"
        )
    if problems:
        raise RuntimeError(
            "X-bi-6-4d-3 refuses: " + "; ".join(problems) + ".  Nothing was "
            "written; each is the developer's to rule (rulings R-BAL166, "
            "R-BAL260)."
        )


def upgrade():
    """Keep a copy of every transfer twin, delete them, and refuse a new one."""
    bind = op.get_bind()
    refuse_twins_that_cannot_go(bind)
    op.execute(_CREATE_COPY_SQL)
    op.execute(_COMMENT_COPY_SQL)
    kept = bind.execute(sa.text(_KEEP_COPY_SQL)).rowcount
    deleted = bind.execute(sa.text(_DELETE_TWINS_SQL)).rowcount
    if deleted != kept:
        raise RuntimeError(
            f"X-bi-6-4d-3 kept {kept} twin(s) and deleted {deleted}; the "
            "transaction rolls back."
        )
    op.create_check_constraint(
        "ck_transactions_names_no_transfer", "transactions",
        "transfer_id IS NULL", schema="budget",
    )
    print(
        f"X-bi-6-4d-3: {deleted} transfer twin row(s) kept in "
        "system.transfer_twin_purge and deleted; 0 refused.  A transaction "
        "naming a transfer is now refused (ck_transactions_names_no_transfer)."
    )


def downgrade():
    """Restore every kept twin, re-synced, build the missing ones, drop the copy."""
    bind = op.get_bind()
    op.drop_constraint(
        "ck_transactions_names_no_transfer", "transactions",
        schema="budget", type_="check",
    )
    restored = bind.execute(sa.text(_RESTORE_KEPT_SQL)).rowcount
    built = bind.execute(sa.text(_BUILD_NEW_SQL)).rowcount
    op.execute("DROP TABLE system.transfer_twin_purge")
    print(
        f"X-bi-6-4d-3 downgrade: {restored} kept twin(s) restored, re-synced "
        f"with their transfers; {built} built new for transfers created since."
    )
