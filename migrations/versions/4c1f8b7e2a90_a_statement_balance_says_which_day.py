"""A statement's balance says WHICH DAY it is for, and how firmly.

Plan step **bank_import:X-f6e-1**, ruling **R-GF**.  A bank writes its balance
as of the EXPORT INSTANT and labels it with the export's own day, so the day on
the header is not the day the figure is for.  Measured on the developer's own
SECU exports: the 2026-08-21 file reads ``Balance as of 08/21/2026,2501.310000``
while its last line is 08-18 and ``2501.31`` is 08-18's closing; the 2026-08-16
file reads ``$4,747.63``, which is 2026-08-13's closing, over a list containing
two 2026-08-14 lines worth ``-$1,006.72``.

Three things land, and one leaves.

  1. **ref.statement_balance_evidence**, seeded ``line_chain`` / ``reconciled`` /
     ``assumed_last_day`` -- HOW the day was pinned.
  2. **``balance_effective_on``** on ``budget.statement_imports``: the day the
     stated figure actually IS the balance for, solved from the file's own
     lines.
  3. **``balance_evidence_id``** beside it, naming which of the three.
  4. **``opening_balance`` and ``closing_balance`` are DROPPED.**

**The drop is the point rather than a tidy-up** (developer approval
2026-08-23).  ``closing`` is ``opening + sum(lines)`` and ``opening`` is
``stated - sum(lines up to the effective day)``, so both were derived values
stored beside their own source with nothing reconciling the three -- the root
cause several of this project's arcs exist to remove.  Neither has a reader
outside the import door, its receipt and three tests.

**Nothing is lost that any source states.**  Both columns were only ever
populated from the per-line running-balance CHAIN, which is stored per line in
``budget.bank_statement_lines.running_balance`` and is NOT touched here -- so a
SECU export that regains the column needs no schema change.  The SimpleFIN
protocol ``bank_import:X-f6b`` targets returns ``balance`` and ``balance-date``
(an instant) with no per-transaction running balance and no opening or closing
balance for a period, which is exactly the ``stated_balance`` shape the two new
columns describe.

**Measured before writing it**: both columns are NULL on all 2 rows of the
developer's dev database and production holds 0 statement imports at all, so
the drop destroys no value that exists.  The downgrade restores both with their
ORIGINAL chain-only semantics -- backfilled from the lines' own
``running_balance`` where present, NULL where not, which is precisely what the
pre-migration code wrote -- so it is value-lossless for everything the older
schema could hold.

**The two column drops are the only destructive acts here** (developer
approval 2026-08-23).  ``ck_statement_imports_stated_balance_paired`` was to
have been renamed and widened over all four balance columns; the measurement
above retired that plan, so it stays exactly as it is -- it welds the CLAIM,
which this revision does not touch -- and the anchor gets its own two
constraints beside it.  Nothing is renamed.

**The CLAIM and the ANCHOR are two facts, and the asymmetry is MEASURED.**  A
first draft welded all four columns in one biconditional; the developer then
exported 2026-01-02..2026-03-31 on 2026-08-23 and its header reads
``Balance as of 08/23/2026,2459.600000`` -- **today's** balance, 145 days past
the file's last line and `$255.41` from the `$2,715.01` its own 139 lines
imply.  A DATE-RANGE export states the CURRENT balance, so a real file can make
a claim whose day its own lines never reach and whose anchor is therefore
undeterminable.  Each pair is welded to itself, and an anchor may not outlive a
claim -- an implication, not a biconditional.

**Not audited.**  ``ref.statement_balance_evidence`` is a read-only seed
catalogue, deliberately excluded from ``app.audit_infrastructure.AUDITED_TABLES``
on the same criteria that keep every other ref catalogue out.  No trigger is
attached and ``EXPECTED_TRIGGER_COUNT`` is unchanged.
``budget.statement_imports`` is already audited, so its existing trigger records
the new columns with no change here.

**Inline seed rationale.**  The three rows are seeded here rather than deferred
to the entrypoint's ``seed_reference_data`` pass, so ``ref_cache.init()``
resolves ``StatementBalanceEvidenceEnum`` immediately after a bare
``flask db upgrade`` -- an enum member with no matching row is a fatal
``RuntimeError`` at app start.  ``ON CONFLICT (name) DO NOTHING`` keeps it
idempotent against a re-run and against the entrypoint's later reseed, which
carries the identical rows via ``app/ref_seeds.py``.

**No backfill arm, and that is measured rather than skipped.**  Solving an
existing import's effective day would mean re-running ``_anchor.resolve_anchor``
inside a migration, which may not import ``app/``; and both existing rows carry
``stated_balance IS NULL`` -- the column post-dates them (``af6cb5df0c45``,
2026-08-22 06:36; the imports ran 08-21 11:19 and 08-22 00:02) -- so all three
constraints are satisfied by leaving every one of the four NULL.  Production
has no rows at all.  An owner who wants the anchor for an old import re-imports the file, which
is a no-op on the lines by design.

Review: Josh (developer), 2026-08-23

Revision ID: 4c1f8b7e2a90
Revises: 6376c2b8e6db
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4c1f8b7e2a90"
down_revision = "6376c2b8e6db"
branch_labels = None
depends_on = None


# The rows ``StatementBalanceEvidenceEnum`` names.  Written as literal SQL rather
# than built from a Python tuple, and that is not style: the cross-migration
# inline-seed guard (``tests/test_models/test_posting_ref_seed_parity.py``)
# scans this chain for each enum value as a SINGLE-QUOTED literal inside an
# ``INSERT INTO`` its own ref table, so a value assembled at run time from a
# double-quoted tuple would be invisible to it and the dual seed would go
# unguarded for this table.
_SEED_STATEMENT_BALANCE_EVIDENCE_SQL = (
    "INSERT INTO ref.statement_balance_evidence (name) VALUES "
    "('file_chain'), "
    "('corroborated'), "
    "('uncorroborated') "
    "ON CONFLICT (name) DO NOTHING"
)

#: What the import made of the claim is one fact in two columns, welded to
#: itself exactly as the claim above it already was.
_BALANCE_EVIDENCE_PAIRED = (
    "(balance_effective_on IS NULL) = (balance_evidence_id IS NULL)"
)

#: An anchor comes FROM a claim, so it cannot outlive one.  An implication
#: rather than a biconditional, for the measured reason the docstring gives.
_ANCHOR_NEEDS_A_CLAIM = (
    "balance_effective_on IS NULL OR stated_balance IS NOT NULL"
)

#: The solved day is one the FILE could have pinned.  Both bounds are
#: structural truths about the solve rather than tolerances: it ranges over
#: {the day before the first line} + {every day the file covers}, and a bank
#: cannot state a balance for a day after the one it wrote on the header.
_EFFECTIVE_DAY_WITHIN_FILE = (
    "balance_effective_on IS NULL OR ("
    "balance_effective_on >= period_start - 1 "
    "AND balance_effective_on <= period_end "
    "AND balance_effective_on <= stated_balance_on)"
)

#: What the two dropped columns meant, restated in SQL for the downgrade.  A
#: migration may not import ``app/``, so ``_integrity.opening_balance``'s rule
#: -- the first line's own balance less its own amount -- is spelled here.
#:
#: **Keyed on the import's SPAN, not on which import first recorded a line**,
#: and that distinction is the whole correctness of the restore.  The
#: pre-change door computed both figures from the FILE's lines
#: (``opening_balance(lines)`` over ``parsed.lines``), and a re-import records
#: only what is new -- so a join on ``import_id`` would restore the opening of
#: a SUBSET, and ``NULL`` for the idempotent re-import that recorded nothing,
#: where the old schema held real figures.  Joining on the span reproduces the
#: file's own first and last line whoever recorded them.
#:
#: **Ordered by ``id`` within a day and NOT by ``sequence_in_group``.**  That
#: column is the ordinal within a ``(posted_on, amount)`` GROUP
#: (``_line.group_key``), so it is 0 for most lines and orders nothing across
#: different amounts; on a last day holding two ``-10.00`` lines and one
#: ``+500.00``, ordering by it picks the wrong line.  ``id`` is insertion
#: order, which is file order.  Both faults found by adversarial review
#: 2026-08-23.
_RESTORE_OPENING_SQL = """
UPDATE budget.statement_imports si
SET opening_balance = (
    SELECT l.running_balance - l.amount
    FROM budget.bank_statement_lines l
    WHERE l.account_id = si.account_id
      AND l.posted_on BETWEEN si.period_start AND si.period_end
      AND l.running_balance IS NOT NULL
    ORDER BY l.posted_on, l.id
    LIMIT 1
)
"""

_RESTORE_CLOSING_SQL = """
UPDATE budget.statement_imports si
SET closing_balance = (
    SELECT l.running_balance
    FROM budget.bank_statement_lines l
    WHERE l.account_id = si.account_id
      AND l.posted_on BETWEEN si.period_start AND si.period_end
      AND l.running_balance IS NOT NULL
    ORDER BY l.posted_on DESC, l.id DESC
    LIMIT 1
)
"""


def upgrade():
    """Add the basis catalogue and the anchor columns; drop the two derived ones."""
    op.create_table(
        "statement_balance_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_STATEMENT_BALANCE_EVIDENCE_SQL)

    op.add_column(
        "statement_imports",
        sa.Column("balance_effective_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "statement_imports",
        sa.Column("balance_evidence_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_statement_imports_balance_evidence",
        "statement_imports", "statement_balance_evidence",
        ["balance_evidence_id"], ["id"],
        source_schema="budget", referent_schema="ref",
        ondelete="RESTRICT",
    )

    # ``ck_statement_imports_stated_balance_paired`` STAYS as it is: it welds
    # the claim, which this revision does not change.  The two new columns get
    # their own pair of constraints beside it.
    op.create_check_constraint(
        "ck_statement_imports_balance_evidence_paired",
        "statement_imports", _BALANCE_EVIDENCE_PAIRED, schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_anchor_needs_a_claim",
        "statement_imports", _ANCHOR_NEEDS_A_CLAIM, schema="budget",
    )
    op.create_check_constraint(
        "ck_statement_imports_effective_day_within_file",
        "statement_imports", _EFFECTIVE_DAY_WITHIN_FILE, schema="budget",
    )

    op.drop_column("statement_imports", "closing_balance", schema="budget")
    op.drop_column("statement_imports", "opening_balance", schema="budget")


def downgrade():
    """Restore the two derived columns from the line chain, and the old name."""
    op.add_column(
        "statement_imports",
        sa.Column("opening_balance", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    op.add_column(
        "statement_imports",
        sa.Column("closing_balance", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    # Value-lossless for everything the older schema could hold: those columns
    # were only ever written from the per-line chain, and this reproduces that
    # rule exactly.  An import whose lines carry no running balance gets NULL,
    # which is what the older door wrote for it.
    op.execute(_RESTORE_OPENING_SQL)
    op.execute(_RESTORE_CLOSING_SQL)

    op.drop_constraint(
        "ck_statement_imports_effective_day_within_file",
        "statement_imports", schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_statement_imports_anchor_needs_a_claim",
        "statement_imports", schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_statement_imports_balance_evidence_paired",
        "statement_imports", schema="budget", type_="check",
    )

    op.drop_constraint(
        "fk_statement_imports_balance_evidence",
        "statement_imports", schema="budget", type_="foreignkey",
    )
    op.drop_column("statement_imports", "balance_evidence_id", schema="budget")
    op.drop_column(
        "statement_imports", "balance_effective_on", schema="budget",
    )
    op.drop_table("statement_balance_evidence", schema="ref")
