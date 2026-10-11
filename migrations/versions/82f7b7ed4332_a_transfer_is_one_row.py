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
     named with the query that finds it: a twin any foreign key still names
     (every key into ``budget.transactions``, read from the catalog: a journal
     entry, a statement-match creation, a movement or a card payback at this
     revision -- a delete would null, cascade or refuse each); a transfer whose
     twins are not exactly one expense and one income (Transfer Invariant 1
     broken; the kept copy could not then be restored as a pair); or a twin
     whose MIRRORED fields (:data:`_MIRRORED_FIELDS`: ``user_id``, its side's
     ``account_id``, ``pay_period_id``, ``scenario_id``, ``status_id``,
     ``due_date``, ``is_override``, ``is_deleted``) are not its transfer's,
     named as ``(transfer, twin, [fields])``.  The step's premise is that a
     twin is a copy of its transfer, so deleting it loses nothing; a twin
     that disagrees is no copy, and its delete would erase the last trace of
     the disagreement unseen.  Every count is 0 where the transfer service
     wrote the rows.  A transfer holding NO twin passes: it is already the
     shape this revision makes (the create door at this revision, run against
     a database still at ``e616adf7fe22``, writes one), and the downgrade
     builds its two.
  2. **It keeps a copy of every twin** in ``system.transfer_twin_purge``: the
     whole row as ``jsonb`` (the shape ``b2d8f3a6c541``'s
     ``system.pre_origination_purge`` keeps), with its transfer's version
     counter and whether the twin's side held a payment record at that moment,
     which the downgrade reads (ruling **R-BAL260**, "Keep a copy").
  3. **It deletes every twin** and adds ``ck_transactions_names_no_transfer``
     (``transfer_id IS NULL``), so a row naming a transfer is unstorable from
     here on (ruling **R-BAL258**, "Database refuses").  Plan step
     ``X-bi-6-5`` drops ``transactions.transfer_id`` with this CHECK, its
     index, its partial unique index, its foreign key and its term in
     ``ck_transactions_one_pricing_link``.

**No money moves.**  No balance reads a twin (Transfer Invariant 5: a
projected leg is drawn off its transfer and a settled one is its side's dated
movement), and a twin held no record (``e616adf7fe22`` moved every one onto
its side link).  A ``$0.00`` close's twins alone held that close's day, basis
and statement link, which no reader at this revision reads (the close is its
transfer's settled status and no movement) and which the copy keeps for the
downgrade.  The deletes write audit rows, as any delete does.  A twin's
FIGURE was its transfer's: each was born declaring ``parent_transfer`` and
storing none, and the update below this revision wrote a figure the owner
typed onto both legs as well (``_amount.apply_amount_ownership``).  The
category "Transfers: Incoming" that older income twins carried in place of
their transfer's own (finding **BAL-575**) is no longer used by any row it
was used only by, so the category screen may then delete it; the downgrade
then files those twins under their transfer's category (ruling **R-BAL261**,
below).

**The downgrade restores the kept copy EXACTLY where nothing has changed**
(ruling **R-BAL260**): a twin whose transfer was not written since the upgrade
comes back with every column it was kept with -- its own id, creation and
update times, version counter, type, name, category, figure and day -- so a
downgrade straight after the upgrade reproduces every twin column for column
(the upgrade let through only twins whose mirrored fields were their
transfer's), and an older revision's own downgrade that finds a twin by its id
(``c4e91a7b2d38``'s ``system.loan_due_date_backfill``) still finds it.  What
the transfer and its side's record hold NOW is read live: the mirrored fields
from the transfer, and the side's payment record's day, basis and link when
the side holds one now (a ``$0.00`` close kept with its day keeps it while its
side still holds no record and its transfer is still in the settled status it
was kept in).  Each re-read changes nothing while the transfer and the record
are as the upgrade left them.  It answers what the version counter below
cannot see: a raw statement or a later revision that wrote the transfer
without raising its counter, and the record, which is a row of its own.  Such
a twin comes back saying what its transfer and record say now -- the code
below this revision reads a twin for exactly that -- and the downgrade prints
how many came back different from their kept row.  Only the mirrored fields
answer a change of the transfer this way: its label, category and figure come
back as kept.  The kept day's two guards (the twin was kept in its
transfer's present status, and its side held no record then) answer only
such changes: a raw statement on the transfer's status, and a record removed
without writing its transfer.  The figure (``estimated_amount``,
``amount_source_id``) is not a mirrored field: it is neither refused nor
re-read, so an unwritten transfer's twins bring back exactly the figure the
copy kept, whatever it was.

**A twin whose transfer WAS written since is brought in line with it** by the
ONE rule that also builds the twins of a transfer created since
(:data:`_BUILT_SQL`, the create door below this revision): its side's endpoint
and label ("Transfer to <to>" on the from-side, "Transfer from <from>" on the
to-side), the transfer's period, scenario, status, due date, flags and
category (on BOTH sides), a DERIVED figure (``parent_transfer``, nothing
stored), and its side's record's day, basis and link, or none.  The transfer's
version counter, kept in the copy, is the test of "written since": every ORM
flush that changes a transfer raises it (``version_id_col``) and the app
holds no bulk transfer update, so it sees what the code below wrote onto both
twins at a full-edit save, a restore, or an A-to-B-to-A edit, which comparing
the transfer's values with the kept ones cannot.  A save that changed nothing
on its transfer writes no row and leaves no trace, so its twins come back as
kept: nothing changed on the transfer for ruling R-BAL260 to bring them in
line with.  A derived figure prices exactly
what an owned copy of the parent's would (a leg reads its parent) and cannot
disagree with it; a ``$0.00`` close made after the upgrade has no day anywhere
(ruling **R-BAL230**), so its twins come back settled with no day, the
dateless settled pair the code below calls legacy (finding **N-181**).  **Such
a twin keeps its kept id, creation and update times and version counter while
its content changes.**  That is safe because no form below this revision
pins a TWIN's counter: the transaction doors refuse a row naming a transfer
(Transfer Invariant 4), and the transfer popovers pin the TRANSFER's counter,
so no stale twin counter can be compared against the rebuilt row.

**A kept value that no longer fits comes back as the transfer says** (ruling
**R-BAL261**, "Use the transfer's").  The upgrade releases what the twins held
in place, so an ordinary act can leave a kept value pointing at nothing: a kept
category the category screen has since deleted comes back as the transfer's
category; a kept ``$0.00``-close day that now falls on or before its
account's books opening (an opening restated after the upgrade) comes back
with no day, basis or link; a kept statement link whose statement is gone
comes back with none.  The downgrade prints how many twins came back so.

``e616adf7fe22``'s downgrade, which runs next, requires exactly this: every
twin saying its transfer's status, and its side's record's day, basis and link
unless it is a twin of a settled transfer whose side holds no record (a
``$0.00`` close, read for its status alone).  A transfer created after the
upgrade has no kept twins, so it gets two new ones by the same rule (new ids,
version 1, created when the transfer was, updated at the downgrade).  A kept
twin whose transfer was since hard-deleted is not restored: the code below
this revision would have cascaded it away with its transfer.  The copy is
dropped last.

**A missing copy is read as an empty one**: the downgrade restores none and
builds two twins for every transfer, so the undo finishes (ruling R-BAL261).
A database built AT this revision (``scripts/init_database.py``:
``create_all``, then a stamp) never ran the upgrade and so holds no copy and
no twin.  The downgrade cannot tell that database from one that ran the
upgrade and LOST its copy since (dropped by hand, a partial restore, a later
revision), so its print says only that no copy table was found.  **For plan
step X-bi-6-5, which decides the copy's fate: dropping the copy turns this
downgrade's exact restore into a rebuild of every twin, with no error** --
new ids, the transfer's category, derived figures, no ``$0.00``-close day --
and ``c4e91a7b2d38``'s downgrade, which writes old due dates back by the twin
ids its backfill table names, then updates no twin.

``tests/test_models/test_a_transfer_is_one_row.py`` drives the shipped
``upgrade`` / ``downgrade``, each refusal (the drift refusal field by field),
the exact round trip, the live re-read of each mirrored field, the rebuild of
a written transfer's twins and each value that no longer fits.
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

#: Every foreign key into ``budget.transactions.id``, read from the CATALOG:
#: ``(referencing table, its column)``, each quoted for SQL.  A delete of a
#: twin would set the referencing column NULL, cascade the referencing row away
#: or be refused, so a twin any of them names refuses the upgrade instead.
#: Read rather than listed so a key the schema gains is covered without a
#: second list to keep in step with it; a composite key contributes the column
#: paired with ``id``.  DISTINCT because one column can carry two keys
#: (``transaction_entries.transaction_id`` holds a single-column key and the
#: composite owner key), and one reference is one reference.
_FOREIGN_KEYS_INTO_TRANSACTIONS_SQL = """
SELECT DISTINCT quote_ident(n.nspname) || '.' || quote_ident(cl.relname),
       quote_ident(a.attname)
  FROM pg_constraint c
  JOIN pg_class cl ON cl.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = cl.relnamespace
  CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(local, referenced)
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.local
  JOIN pg_attribute r ON r.attrelid = c.confrelid AND r.attnum = k.referenced
 WHERE c.contype = 'f'
   AND c.confrelid = 'budget.transactions'::regclass
   AND r.attname = 'id'
 ORDER BY 1, 2
"""


def foreign_keys_into_transactions(bind) -> list[tuple[str, str]]:
    """Return every ``(table, column)`` whose foreign key names a transaction.

    Module-level so a test can grade the catalog read itself: a query that
    found nothing would leave the refusal below asking nothing.

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        Each referencing table (schema-qualified) and column, quoted for SQL,
        in name order.
    """
    return [
        (table, column) for table, column in bind.execute(
            sa.text(_FOREIGN_KEYS_INTO_TRANSACTIONS_SQL),
        )
    ]


def referenced_twins_sql(bind) -> str:
    """Return the query naming every twin a foreign key still points at.

    Its rows are ``(twin id, transfer id, "table.column" naming it)``.  One
    arm per key :func:`foreign_keys_into_transactions` reads, every arm the
    same ``EXISTS`` over its own column.

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        The query, ready to run.
    """
    arms = " UNION ALL ".join(
        f"SELECT '{table}.{column}' AS what WHERE EXISTS "
        f"(SELECT 1 FROM {table} r WHERE r.{column} = t.id)"
        for table, column in foreign_keys_into_transactions(bind)
    )
    return (
        "SELECT t.id AS twin_id, t.transfer_id, r.what "
        "FROM budget.transactions t "
        f"JOIN LATERAL ({arms}) r ON TRUE "
        "WHERE t.transfer_id IS NOT NULL ORDER BY t.id, r.what"
    )


#: Every transfer holding twins that are not exactly one expense and one
#: income, live or deleted: ``(transfer id, expense twins, income twins)``.  A
#: transfer holding none is not one of them (the module docstring, item 1).
_UNPAIRED_SQL = f"""
SELECT x.id,
       count(t.id) FILTER (WHERE t.transaction_type_id <> {_INCOME}),
       count(t.id) FILTER (WHERE t.transaction_type_id = {_INCOME})
  FROM budget.transfers x
  JOIN budget.transactions t ON t.transfer_id = x.id
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
    transfer_version_id integer NOT NULL,
    side_held_record boolean NOT NULL
)
"""

_COMMENT_COPY_SQL = """
COMMENT ON TABLE system.transfer_twin_purge IS
'Every transfer twin row (budget.transactions with transfer_id set) as revision
82f7b7ed4332 (plan step balance:X-bi-6-4d-3, ruling R-BAL260) deleted it: the
whole row as jsonb, its transfer''s version counter and whether the twin''s side
held a payment record at that moment.  Read by that revision''s downgrade,
which restores each twin from it, and inherited by plan step balance:X-bi-6-5,
which decides its fate when it drops transactions.transfer_id.'
"""

#: The copy: each twin whole, its transfer's version counter (the downgrade's
#: test of "written since"), and whether its side held a payment record (a side
#: link on the twin's side of its transfer).
_KEEP_COPY_SQL = f"""
INSERT INTO system.transfer_twin_purge
       (row_id, transfer_id, row_data, transfer_version_id, side_held_record)
SELECT t.id, t.transfer_id, to_jsonb(t), x.version_id,
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

#: Whether the copy is missing: a database built AT this revision by
#: ``scripts/init_database.py`` never ran the upgrade that creates it (the
#: module docstring's last paragraph).
_COPY_ABSENT_SQL = "SELECT to_regclass('system.transfer_twin_purge') IS NULL"

#: THE ONE RULE for a twin the downgrade writes from its transfer: each side of
#: each transfer as the create door below this revision built it, with the
#: side's record's day, basis and link (``e616adf7fe22``'s contract).  Both
#: restore paths read it -- a kept twin whose transfer was written since takes
#: every column it states, a transfer created since gets two rows of it -- so
#: "in line with its transfer" is spelled once.  ``twin`` holds every column of
#: ``budget.transactions`` but the four a row's identity is made of (``id``,
#: ``created_at``, ``updated_at``, ``version_id``), which each path states.
_BUILT_SQL = f"""
SELECT x.id AS transfer_id, side.is_income, side.account_id, x.status_id,
       x.version_id, x.created_at, e.id IS NOT NULL AS has_record,
       jsonb_build_object(
         'user_id', x.user_id,
         'account_id', side.account_id,
         'template_id', NULL,
         'pay_period_id', x.pay_period_id,
         'scenario_id', x.scenario_id,
         'status_id', x.status_id,
         'name', side.label,
         'category_id', x.category_id,
         'transaction_type_id', side.type_id,
         'estimated_amount', NULL,
         'amount_source_id', {_PARENT_TRANSFER},
         'is_override', x.is_override,
         'is_deleted', x.is_deleted,
         'transfer_id', x.id,
         'credit_payback_for_id', NULL,
         'notes', NULL,
         'due_date', x.due_date,
         'occurs_on', NULL,
         'settled_on', e.settled_on,
         'settled_day_basis_id', e.settled_day_basis_id,
         'reconciled_by_id', e.reconciled_by_id
       ) AS twin
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
"""

#: The MIRRORED fields: the columns a twin copied from its transfer (its side's
#: endpoint for ``account_id``), each as :data:`_BUILT_SQL` states it.  ONE
#: list for both directions: the upgrade refuses a twin whose value of any of
#: them is not its transfer's, and the downgrade re-reads every one of them
#: from the transfer, so what the upgrade guarantees is exactly what the
#: downgrade derives.
_MIRRORED_FIELDS = (
    "user_id", "account_id", "pay_period_id", "scenario_id", "status_id",
    "due_date", "is_override", "is_deleted",
)

#: One arm per mirrored field: its name when the twin's value (``r.twin``) is
#: not what the build rule says (``b.twin``).  Both are ``jsonb`` read the
#: same way, so a ``NULL`` due date on both is no difference.
_MIRROR_DRIFT_ARMS = ", ".join(
    f"CASE WHEN r.twin->'{field}' IS DISTINCT FROM b.twin->'{field}' "
    f"THEN '{field}' END"
    for field in _MIRRORED_FIELDS
)

#: Every twin whose mirrored fields are not its transfer's, each compared with
#: what the ONE build rule says that side's twin holds: ``(transfer id, twin
#: id, the fields that differ)``, the fields in :data:`_MIRRORED_FIELDS`'
#: order.
_MIRROR_DRIFT_SQL = f"""
SELECT transfer_id, twin_id, fields FROM (
SELECT t.transfer_id, t.id AS twin_id,
       array_remove(ARRAY[{_MIRROR_DRIFT_ARMS}], NULL) AS fields
  FROM budget.transactions t
  CROSS JOIN LATERAL (SELECT to_jsonb(t) AS twin) r
  JOIN ({_BUILT_SQL}) b
    ON b.transfer_id = t.transfer_id
   AND b.is_income = (t.transaction_type_id = {_INCOME})
) twins
 WHERE cardinality(fields) > 0
 ORDER BY transfer_id, twin_id
"""

#: The unwritten branch's re-read of every mirrored field from the build rule,
#: as ``jsonb_build_object`` arguments.
_MIRRORED_REREAD = ", ".join(
    f"'{field}', b.twin->'{field}'" for field in _MIRRORED_FIELDS
)

#: Each kept twin whose transfer still exists, back into the table (the module
#: docstring's rules), in ONE statement that also counts what it did:
#: ``(restored, rebuilt because written since, of an unwritten transfer and
#: different from its kept row, of those because a kept value no longer
#: fits)``.  ``gate`` asks "written since" once (the transfer's version counter
#: against the kept one); ``kept`` reads the copy's values once; ``fit`` asks
#: each of ruling R-BAL261's questions once.  A kept
#: ``$0.00``-close day fits while ``budget.books_hold`` admits it on its
#: account (the comparison ``ck_movement_after_books_open`` asks at COMMIT),
#: and a kept link while its statement still exists on that account (the pair
#: ``fk_transactions_reconciled_by`` names; a statement never changes
#: accounts, so the account term only states the key's own question).  A
#: written transfer's twins take the transfer's category whatever the kept one
#: was, so ``category_gone`` is asked of an unwritten one alone and the printed
#: count names only the twins a fallback actually changed.
_RESTORE_KEPT_SQL = f"""
WITH staged AS (
    SELECT p.row_id, gate.written, p.row_data AS kept_row,
           fit.category_gone OR fit.day_unfit OR fit.link_gone AS unfit,
           p.row_data || CASE
               WHEN gate.written THEN b.twin
               ELSE jsonb_build_object(
                   {_MIRRORED_REREAD},
                   'category_id', CASE WHEN fit.category_gone
                                       THEN b.twin->'category_id'
                                       ELSE p.row_data->'category_id' END,
                   'settled_on', CASE
                       WHEN b.has_record THEN b.twin->'settled_on'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                           THEN p.row_data->'settled_on' END,
                   'settled_day_basis_id', CASE
                       WHEN b.has_record THEN b.twin->'settled_day_basis_id'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                           THEN p.row_data->'settled_day_basis_id' END,
                   'reconciled_by_id', CASE
                       WHEN b.has_record THEN b.twin->'reconciled_by_id'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                            AND NOT fit.link_gone
                           THEN p.row_data->'reconciled_by_id' END
               ) END AS row_data
      FROM system.transfer_twin_purge p
      JOIN ({_BUILT_SQL}) b
        ON b.transfer_id = p.transfer_id
       AND b.is_income = ((p.row_data->>'transaction_type_id')::integer
                          = {_INCOME})
      JOIN ref.statuses s ON s.id = b.status_id
      CROSS JOIN LATERAL (
            SELECT p.transfer_version_id <> b.version_id AS written
           ) gate
      CROSS JOIN LATERAL (
            SELECT (NOT gate.written
                    AND NOT b.has_record
                    AND NOT p.side_held_record
                    AND s.is_settled
                    AND (p.row_data->>'status_id')::integer = b.status_id)
                   AS day_applies,
                   (p.row_data->>'category_id')::integer AS category_id,
                   (p.row_data->>'settled_on')::date AS settled_on,
                   (p.row_data->>'reconciled_by_id')::integer
                   AS reconciled_by_id
           ) kept
      CROSS JOIN LATERAL (
            SELECT NOT gate.written
                   AND kept.category_id IS NOT NULL
                   AND NOT EXISTS (
                         SELECT 1 FROM budget.categories c
                          WHERE c.id = kept.category_id
                       ) AS category_gone,
                   kept.day_applies
                   AND kept.settled_on IS NOT NULL
                   AND NOT COALESCE(budget.books_hold(
                         budget.account_books_opened_on(b.account_id),
                         kept.settled_on
                       ), TRUE) AS day_unfit
           ) fit_day
      CROSS JOIN LATERAL (
            SELECT fit_day.category_gone, fit_day.day_unfit,
                   kept.day_applies
                   AND NOT fit_day.day_unfit
                   AND kept.reconciled_by_id IS NOT NULL
                   AND NOT EXISTS (
                         SELECT 1 FROM budget.account_anchor_history h
                          WHERE h.id = kept.reconciled_by_id
                            AND h.account_id = b.account_id
                       ) AS link_gone
           ) fit
), restored AS (
    INSERT INTO budget.transactions
    SELECT r.*
      FROM staged s
      CROSS JOIN LATERAL jsonb_populate_record(
            NULL::budget.transactions, s.row_data
           ) r
     ORDER BY s.row_id
    RETURNING 1
)
SELECT (SELECT count(*) FROM restored),
       count(*) FILTER (WHERE s.written),
       count(*) FILTER (WHERE NOT s.written AND s.row_data <> s.kept_row),
       count(*) FILTER (WHERE s.unfit)
  FROM staged s
"""

#: Two new twins for each transfer the copy holds none of (one created after
#: the upgrade, or one the upgrade met holding none), by :data:`_BUILT_SQL`:
#: new ids, version 1, created when the transfer was, updated now.  ``built``
#: draws each id ONCE, in transfer-then-side order (PostgreSQL evaluates a
#: volatile select-list call after the sort), and is materialized so the row
#: expansion below cannot re-run it: ``(f(x)).*`` expands into one call of
#: ``f`` per column, which drew 25 ids per twin.
_BUILD_NEW_SQL = f"""
WITH built AS MATERIALIZED (
    SELECT b.twin, b.created_at,
           nextval(pg_get_serial_sequence('budget.transactions', 'id')) AS id
      FROM ({_BUILT_SQL}) b
     WHERE NOT EXISTS (
             SELECT 1 FROM system.transfer_twin_purge p
              WHERE p.transfer_id = b.transfer_id
           )
     ORDER BY b.transfer_id, b.is_income
)
INSERT INTO budget.transactions
SELECT r.*
  FROM built
  CROSS JOIN LATERAL jsonb_populate_record(
        NULL::budget.transactions,
        built.twin || jsonb_build_object(
          'id', built.id,
          'created_at', built.created_at,
          'updated_at', now(),
          'version_id', 1
        )
       ) r
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
    referenced_sql = referenced_twins_sql(bind)
    referenced = [tuple(row) for row in bind.execute(sa.text(referenced_sql))]
    unpaired = [tuple(row) for row in bind.execute(sa.text(_UNPAIRED_SQL))]
    drifted = [tuple(row) for row in bind.execute(sa.text(_MIRROR_DRIFT_SQL))]
    problems = []
    if referenced:
        problems.append(
            f"{len(referenced)} twin reference(s) would be nulled, cascaded "
            f"or refused by the delete (twin id, transfer id, what names it; "
            f"first {_NAMED_AT_MOST}: {referenced[:_NAMED_AT_MOST]}; diagnose "
            f"with: {referenced_sql})"
        )
    if unpaired:
        problems.append(
            f"{len(unpaired)} transfer(s) hold other than one expense and one "
            f"income twin (transfer id, expense twins, income twins; first "
            f"{_NAMED_AT_MOST}: {unpaired[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_UNPAIRED_SQL.strip()})"
        )
    if drifted:
        problems.append(
            f"{len(drifted)} twin(s) are not a copy of their transfer: a "
            f"mirrored field differs (transfer id, twin id, the fields; first "
            f"{_NAMED_AT_MOST}: {drifted[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_MIRROR_DRIFT_SQL.strip()})"
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
    """Restore every kept twin, build the missing ones, drop the copy."""
    bind = op.get_bind()
    copy_absent = bind.execute(sa.text(_COPY_ABSENT_SQL)).scalar_one()
    if copy_absent:
        op.execute(_CREATE_COPY_SQL)
    op.drop_constraint(
        "ck_transactions_names_no_transfer", "transactions",
        schema="budget", type_="check",
    )
    restored, rebuilt, changed, unfit = bind.execute(
        sa.text(_RESTORE_KEPT_SQL),
    ).one()
    built = bind.execute(sa.text(_BUILD_NEW_SQL)).rowcount
    op.execute("DROP TABLE system.transfer_twin_purge")
    print(
        f"X-bi-6-4d-3 downgrade: {restored} kept twin(s) restored: {rebuilt} "
        f"in line with a transfer written since; {changed} of an unwritten "
        "transfer differ from their kept row, saying what the transfer and "
        f"its side's record say now ({unfit} of them because a kept value no "
        "longer fits, ruling R-BAL261); the rest exactly as kept.  "
        f"{built} built new for transfers holding none"
        + (" (no copy table was found: none restored, every twin built new)."
           if copy_absent else ".")
    )
