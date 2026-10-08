"""a transfer side's payment hangs off the transfer

Revision ID: e616adf7fe22
Revises: 1f431fec6547
Create Date: 2026-10-08
Review: developer, 2026-09-20 / 2026-09-22 / 2026-09-30 (ruling R-BAL88:
``transaction_entries.transaction_id`` made nullable under an exactly-one-parent
CHECK, and the two side keys; R-BAL107: the transfer owner keys; R-BAL168: the
side keys ON UPDATE CASCADE; R-CC89 / R-CC92 carried to a transfer: the
deleted-row triggers re-created with their transfer arm)

Plan step **balance:X-bi-6-4d-2** of ``docs/audits/balance_architecture/README.md``
(design ``X-bi-6-4d`` D1).  **A transfer side's payment record is filed under
the TRANSFER, not under the hidden twin row the transfer service keeps for that
side.**  Until now each side's record hung off its twin
(``transaction_entries.transaction_id`` = the twin's id), so every reader of a
transfer's money walked a row whose only job was to copy the transfer.  After
this revision the record names its transfer by one of two SIDE LINKS, and the
twins hold nothing.  **No figure, day, account or statement link moves**: the
same 44 rows keep their ids, amounts, days, bases, accounts, matches and
postings, and only the column that says what they are filed under changes.

  1. **``budget.transfers``** gains the superkeys ``uq_transfers_id_from_account``
     / ``uq_transfers_id_to_account`` and the owner keys
     ``fk_transfers_owner_from_account`` / ``fk_transfers_owner_to_account``
     onto ``uq_accounts_id_user`` (ruling **R-BAL107**: a transfer naming another
     user's account is unstorable).
  2. **``budget.transaction_entries``** gains ``expense_transfer_id`` and
     ``income_transfer_id``, each keyed WITH ``account_id`` onto its side's
     superkey (ruling **R-BAL88**), so a side's record on any account but that
     side's endpoint is unstorable.  Both keys are ``ON UPDATE CASCADE`` (ruling
     **R-BAL168**: an endpoint move carries the side's record) and NO ACTION on
     delete (**R-CC54**).  ``transaction_id`` becomes NULLABLE under
     ``ck_transaction_entries_one_parent`` (exactly one of the three is set);
     ``ck_transaction_entries_side_link_is_a_record`` makes every side link a
     settlement record; ``uq_transaction_entries_one_expense_side_record`` /
     ``..._one_income_side_record`` allow one per side.  Every new constraint
     holds on the stored rows before the backfill (every link is NULL), so the
     DDL runs first and the backfill is graded row by row as it writes.
  3. **The backfill**: every movement under a twin takes that twin's transfer
     as its side link -- the INCOME twin is the to-side, the expense twin the
     from-side, which is how the fold and the ledger have always read a twin --
     and its ``transaction_id`` goes NULL, in ONE statement.
  4. **The deleted-row rule gains its transfer arm**
     (:mod:`app.deleted_row_infrastructure`, ``TRANSFER_ARM``): a payment may
     not arrive under a deleted transfer, read under the same row lock (ruling
     **R-CC96**), and a transfer may not be hidden while a side links one.

**The upgrade REFUSES, writing nothing, while any stored row could not take its
link**, each named with the query that finds it (fail-closed, the shape
``c4a4e7d1b9f2`` uses for ruling **R-CC82**): a movement under a twin that is
not a settlement record, not on its side's endpoint, or under a deleted twin or
a deleted transfer (the last two would be a hidden parent holding money, the
state the transfer arm forbids); two records on one side of one transfer; a
transfer naming an account its owner does not hold.  Every count is 0 where the
transfer service wrote the rows.

**Measured on the 2026-10-08 14:34 EDT production dump** (at ``1f431fec6547``):
179 transfers, 358 twins, 44 movements under a twin -- 22 per side, every one a
dated settlement record on its side's endpoint, under a live twin of a live
Paid transfer; 0 transfers naming another owner's account; 19 statement match
members naming one of the 44 (they key by the movement's id and account, which
do not change).  0 refused.  The migration prints its own counts so the operator
compares them with the rehearsal's.

**The downgrade re-attaches each record to its side's twin** -- the live one,
else the lowest id -- and refuses, writing nothing, while a record's side has no
twin at all (a state only a LATER revision that deletes the twins can leave,
and that revision's downgrade rebuilds them first).  Then it withdraws the
transfer arm, drops the new constraints and columns, and restores
``transaction_id``'s NOT NULL, refusing if any row would violate it.  A record
re-attached to a deleted twin is the pre-step shape of finding BAL-532 and is
allowed, as the older code allowed it.

``tests/test_models/test_a_transfer_side_s_payment_hangs_off_the_transfer.py``
drives the shipped ``upgrade`` / ``downgrade`` and each refusal.
"""
from alembic import op
import sqlalchemy as sa

from app.deleted_row_infrastructure import (
    ROW_ARM,
    TRANSFER_ARM,
    apply_deleted_row_infrastructure,
)


# revision identifiers, used by Alembic.
revision = "e616adf7fe22"
down_revision = "1f431fec6547"
branch_labels = None
depends_on = None


#: The INCOME transaction type's id, by name: a migration resolves a ref row by
#: name because its id differs between databases.  An income twin is the
#: to-side; every other twin is the from-side.
_INCOME = "(SELECT id FROM ref.transaction_types WHERE name = 'Income')"

#: Every movement under a twin that cannot take its side link as it stands,
#: with the reason: not a settlement record, not on its side's endpoint, or
#: under a deleted twin or a deleted transfer.
_UNLINKABLE_SQL = f"""
SELECT e.id, t.transfer_id,
       CASE
           WHEN NOT e.covers_settlement THEN 'not a settlement record'
           WHEN e.account_id <> CASE WHEN t.transaction_type_id = {_INCOME}
                                     THEN x.to_account_id
                                     ELSE x.from_account_id END
               THEN 'not on its side''s account'
           WHEN t.is_deleted THEN 'under a deleted twin'
           ELSE 'under a deleted transfer'
       END AS reason
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
  JOIN budget.transfers x ON x.id = t.transfer_id
 WHERE NOT e.covers_settlement
    OR e.account_id <> CASE WHEN t.transaction_type_id = {_INCOME}
                            THEN x.to_account_id
                            ELSE x.from_account_id END
    OR t.is_deleted
    OR x.is_deleted
 ORDER BY e.id
"""

#: Every transfer side holding more than one movement across its twins.
_DOUBLED_SIDES_SQL = f"""
SELECT t.transfer_id, (t.transaction_type_id = {_INCOME}) AS is_income,
       count(*) AS movements
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
 WHERE t.transfer_id IS NOT NULL
 GROUP BY 1, 2
HAVING count(*) > 1
 ORDER BY 1, 2
"""

#: Every transfer naming an account its owner does not hold (ruling R-BAL107).
_FOREIGN_ACCOUNT_SQL = """
SELECT x.id
  FROM budget.transfers x
  JOIN budget.accounts f ON f.id = x.from_account_id
  JOIN budget.accounts t ON t.id = x.to_account_id
 WHERE f.user_id <> x.user_id OR t.user_id <> x.user_id
 ORDER BY x.id
"""

#: How many movements hang off a twin, per side -- the backfill's census and,
#: after it, the count that must read zero.
_UNDER_TWINS_SQL = f"""
SELECT count(*) FILTER (WHERE t.transaction_type_id <> {_INCOME}),
       count(*) FILTER (WHERE t.transaction_type_id = {_INCOME})
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
 WHERE t.transfer_id IS NOT NULL
"""

#: How many movements a side link names, per side.
_SIDE_LINKED_SQL = """
SELECT count(expense_transfer_id), count(income_transfer_id)
  FROM budget.transaction_entries
"""

#: The backfill, both sides in one statement.
_BACKFILL_SQL = f"""
UPDATE budget.transaction_entries e
   SET expense_transfer_id = CASE WHEN t.transaction_type_id = {_INCOME}
                                  THEN NULL ELSE t.transfer_id END,
       income_transfer_id = CASE WHEN t.transaction_type_id = {_INCOME}
                                 THEN t.transfer_id ELSE NULL END,
       transaction_id = NULL
  FROM budget.transactions t
 WHERE t.id = e.transaction_id
   AND t.transfer_id IS NOT NULL
"""

#: Every side-linked movement whose side has no twin to go back under.
_TWINLESS_SQL = f"""
SELECT e.id, coalesce(e.expense_transfer_id, e.income_transfer_id)
  FROM budget.transaction_entries e
 WHERE coalesce(e.expense_transfer_id, e.income_transfer_id) IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM budget.transactions t
        WHERE t.transfer_id = coalesce(e.expense_transfer_id,
                                       e.income_transfer_id)
          AND (t.transaction_type_id = {_INCOME})
              = (e.income_transfer_id IS NOT NULL)
   )
 ORDER BY e.id
"""

#: The downgrade's re-attach: each side-linked movement under its side's twin,
#: the live one first.
_REATTACH_SQL = f"""
UPDATE budget.transaction_entries e
   SET transaction_id = (
           SELECT t.id FROM budget.transactions t
            WHERE t.transfer_id = coalesce(e.expense_transfer_id,
                                           e.income_transfer_id)
              AND (t.transaction_type_id = {_INCOME})
                  = (e.income_transfer_id IS NOT NULL)
            ORDER BY t.is_deleted, t.id
            LIMIT 1
       ),
       expense_transfer_id = NULL,
       income_transfer_id = NULL
 WHERE coalesce(e.expense_transfer_id, e.income_transfer_id) IS NOT NULL
"""

#: The two side keys, as ``(name, link column, referenced endpoint column)``.
_SIDE_KEYS = (
    ("fk_transaction_entries_expense_side", "expense_transfer_id",
     "from_account_id"),
    ("fk_transaction_entries_income_side", "income_transfer_id",
     "to_account_id"),
)

#: The one-record-per-side indexes, as ``(name, link column)``.
_SIDE_INDEXES = (
    ("uq_transaction_entries_one_expense_side_record", "expense_transfer_id"),
    ("uq_transaction_entries_one_income_side_record", "income_transfer_id"),
)

#: The transfer table's superkeys and owner keys, as ``(endpoint column,
#: superkey name, owner key name)``.
_ENDPOINTS = (
    ("from_account_id", "uq_transfers_id_from_account",
     "fk_transfers_owner_from_account"),
    ("to_account_id", "uq_transfers_id_to_account",
     "fk_transfers_owner_to_account"),
)


def refuse_unlinkable_rows(bind) -> None:
    """Refuse the upgrade while any stored row could not take its side link.

    **Module-level so a test can DRIVE each refusal** (the chain's own pattern,
    ``c4a4e7d1b9f2.refuse_hidden_rows_holding_movements``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending movement, side or transfer and the
            query that finds it.  Nothing has been written.
    """
    unlinkable = [tuple(row) for row in bind.execute(sa.text(_UNLINKABLE_SQL))]
    doubled = [tuple(row) for row in bind.execute(sa.text(_DOUBLED_SIDES_SQL))]
    foreign = [row[0] for row in bind.execute(sa.text(_FOREIGN_ACCOUNT_SQL))]
    problems = []
    if unlinkable:
        problems.append(
            f"{len(unlinkable)} movement(s) under a twin cannot take a side "
            f"link (entry id, transfer id, reason: {unlinkable}; diagnose "
            f"with: {_UNLINKABLE_SQL.strip()})"
        )
    if doubled:
        problems.append(
            f"{len(doubled)} transfer side(s) hold more than one movement "
            f"(transfer id, is the to-side, count: {doubled}; diagnose with: "
            f"{_DOUBLED_SIDES_SQL.strip()})"
        )
    if foreign:
        problems.append(
            f"{len(foreign)} transfer(s) name an account their owner does not "
            f"hold (ids {foreign}; diagnose with: {_FOREIGN_ACCOUNT_SQL.strip()})"
        )
    if problems:
        raise RuntimeError(
            "X-bi-6-4d-2 refuses: " + "; ".join(problems) + ".  Nothing was "
            "written; each is the developer's to rule (rulings R-BAL88, "
            "R-BAL107, R-CC82)."
        )


def refuse_twinless_sides(bind) -> None:
    """Refuse the downgrade while any side-linked record has no twin to go back under.

    Module-level so a test can DRIVE the refusal, as
    :func:`refuse_unlinkable_rows`.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each such movement and its transfer.  Nothing has
            been written.
    """
    twinless = [tuple(row) for row in bind.execute(sa.text(_TWINLESS_SQL))]
    if twinless:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade refuses: {len(twinless)} side-linked "
            f"payment(s) have no twin row on their side to go back under "
            f"(entry id, transfer id: {twinless}; diagnose with: "
            f"{_TWINLESS_SQL.strip()}).  Downgrade the revision that deleted "
            "the twins first; it rebuilds them.  Nothing was written."
        )


def _counts(bind, sql: str) -> tuple[int, int]:
    """Return a ``(from-side, to-side)`` count pair from *sql*."""
    expense, income = bind.execute(sa.text(sql)).one()
    return int(expense), int(income)


def upgrade():
    """File every transfer side's payment under its transfer, by a side link."""
    bind = op.get_bind()
    refuse_unlinkable_rows(bind)
    expected = _counts(bind, _UNDER_TWINS_SQL)
    for column, superkey, owner_key in _ENDPOINTS:
        op.create_unique_constraint(
            superkey, "transfers", ["id", column], schema="budget",
        )
        op.create_foreign_key(
            owner_key, "transfers", "accounts", [column, "user_id"],
            ["id", "user_id"], source_schema="budget",
            referent_schema="budget", ondelete="RESTRICT",
        )
    for _name, link, _endpoint in _SIDE_KEYS:
        op.add_column(
            "transaction_entries", sa.Column(link, sa.Integer(), nullable=True),
            schema="budget",
        )
    op.alter_column(
        "transaction_entries", "transaction_id", existing_type=sa.Integer(),
        nullable=True, schema="budget",
    )
    op.create_check_constraint(
        "ck_transaction_entries_one_parent", "transaction_entries",
        "num_nonnulls(transaction_id, expense_transfer_id, "
        "income_transfer_id) = 1",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transaction_entries_side_link_is_a_record", "transaction_entries",
        "(expense_transfer_id IS NULL AND income_transfer_id IS NULL) "
        "OR covers_settlement",
        schema="budget",
    )
    for name, link, endpoint in _SIDE_KEYS:
        op.create_foreign_key(
            name, "transaction_entries", "transfers", [link, "account_id"],
            ["id", endpoint], source_schema="budget",
            referent_schema="budget", onupdate="CASCADE",
        )
    for name, link in _SIDE_INDEXES:
        op.create_index(
            name, "transaction_entries", [link], unique=True, schema="budget",
            postgresql_where=sa.text(f"{link} IS NOT NULL"),
        )
    op.execute(_BACKFILL_SQL)
    linked = _counts(bind, _SIDE_LINKED_SQL)
    left = _counts(bind, _UNDER_TWINS_SQL)
    if linked != expected or left != (0, 0):
        raise RuntimeError(
            f"X-bi-6-4d-2's backfill linked {linked} (from-side, to-side) "
            f"where {expected} hung off a twin, and {left} still do; the "
            "transaction rolls back."
        )
    apply_deleted_row_infrastructure(op.execute, arms=(ROW_ARM, TRANSFER_ARM))
    print(
        f"X-bi-6-4d-2: {linked[0]} from-side and {linked[1]} to-side payment "
        "record(s) now hang off their transfer by a side link; 0 under a twin; "
        "0 refused.  A transfer's accounts are its owner's, and a payment "
        "arriving under a deleted transfer, or a transfer hidden while it holds "
        "one, is refused."
    )


def downgrade():
    """Put every side-linked payment back under its side's twin."""
    bind = op.get_bind()
    refuse_twinless_sides(bind)
    apply_deleted_row_infrastructure(op.execute, arms=(ROW_ARM,))
    op.execute(_REATTACH_SQL)
    orphaned = bind.execute(sa.text(
        "SELECT count(*) FROM budget.transaction_entries "
        "WHERE transaction_id IS NULL"
    )).scalar_one()
    if orphaned:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade left {orphaned} movement(s) with no "
            "parent row (SELECT id FROM budget.transaction_entries WHERE "
            "transaction_id IS NULL); the transaction rolls back."
        )
    for name, _link in _SIDE_INDEXES:
        op.drop_index(name, table_name="transaction_entries", schema="budget")
    for name, _link, _endpoint in _SIDE_KEYS:
        op.drop_constraint(
            name, "transaction_entries", schema="budget", type_="foreignkey",
        )
    op.drop_constraint(
        "ck_transaction_entries_side_link_is_a_record", "transaction_entries",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_transaction_entries_one_parent", "transaction_entries",
        schema="budget", type_="check",
    )
    for _name, link, _endpoint in _SIDE_KEYS:
        op.drop_column("transaction_entries", link, schema="budget")
    op.alter_column(
        "transaction_entries", "transaction_id", existing_type=sa.Integer(),
        nullable=False, schema="budget",
    )
    for _column, superkey, owner_key in _ENDPOINTS:
        op.drop_constraint(
            owner_key, "transfers", schema="budget", type_="foreignkey",
        )
        op.drop_constraint(
            superkey, "transfers", schema="budget", type_="unique",
        )
