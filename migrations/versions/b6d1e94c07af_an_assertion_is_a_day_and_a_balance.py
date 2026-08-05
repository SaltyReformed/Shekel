"""an assertion is a day and a balance, not a row filed in a pay period

Plan step X-f1c3b of ``docs/audits/balance_architecture/README.md``, ruling
**R-EO** (2026-08-04).

**A balance assertion is a fact about a BANK.**  "On day D, account A held $B"
is true whatever the user's paychecks are scheduled to do -- the lender and the
bank have never heard of a pay period.  ``account_anchor_history.pay_period_id``
filed that fact under a BUDGETING artifact, on an ``ON DELETE CASCADE`` foreign
key, so a pay-period operation could destroy the record of what the bank said.

**It has no reader, and the codebase said so before this migration was
written.**  ``app/services/cash_ledger/_events.py``'s ``CashAnchorFact``
docstring states it in those words -- "a CACHE of a derivation, not an
independent fact, and no reader of THIS FIELD survives in ``app/``" (finding
N-169) -- and ``account_posting_service/_anchors.py`` refuses the column BY
NAME, deriving a correction's period from ``observed_on`` instead, because
projecting the stored value "put the posted ledger at odds with the grid's Book
vs bank row by the whole correction".  The one surviving reader was
``cash_ledger.resolve_anchor``, comparing it against the
``accounts.current_anchor_period_id`` cache; plan step X-f1c3a deleted that
comparison, leaving the column with no reader at all.

**And it is already WRONG on production data.**  Measured read-only on
``shekel-prod-db`` 2026-08-04: **2 of 78 assertions carry a period their own
``observed_on`` falls outside** -- row 45 (account 8, day ``2026-05-21``, filed
in the ``2026-03-26``..``2026-04-08`` period) and row 50 (account 1, day
``2026-06-03``, filed in the ``2026-06-04``..``2026-06-17`` period).  Those are
finding N-168's two rows.  This migration deletes the defect class rather than
repairing two self-contradictory rows.

**What the CASCADE cost, measured.**  ``pay_period_admin.reset_pay_periods``
wipes every pay period, so the cascade took **all 78 of the developer's
assertions**, and ``_reanchor_accounts`` wrote **9 synthetic
``"origination (pay-period reset)"`` rows** carrying only each account's last
balance: 69 real observations of what the bank said, deleted to satisfy a
foreign key.  Not reachable on today's data -- 158 settled transactions block
the reset -- and reachable for any user who resets before settling anything,
which is why it is fixed rather than noted.  With no FK the assertions simply
survive a schedule rebuild and
``account_posting_service.resync_user_account_anchor_postings`` re-derives their
corrections onto the new periods from the facts themselves.

**The unique index is re-keyed and gets STRICTLY TIGHTER.**
``uq_anchor_history_account_period_balance_day`` keyed
``(account_id, pay_period_id, anchor_balance, observed_on)`` and now keys
``(account_id, anchor_balance, observed_on)``.  Measured: **0 of the 78
production rows are rejected by the narrower key**, and that is not luck -- the
period was DERIVED from the day, so two rows sharing a day shared a period
except across a schedule rebuild, which is the one case the narrower key now
also catches.  The index NAME is deliberately unchanged: renaming it would
touch ``anchor_service.ANCHOR_HISTORY_UNIQUE_INDEX``, the model's
``__table_args__`` and two earlier migrations, for a word.

**Destructive, and the downgrade WORKS -- but it does not restore two rows
byte-for-byte, and that is stated rather than glossed.**  ``pay_period_id`` is
an exact function of ``observed_on`` (the period containing the day, else the
user's earliest -- ``account_service.resolve_anchor_period_id``'s rule, which
wrote every one of these values), so the downgrade recomputes it rather than
raising.  The two rows above will come back with the period their own day falls
in, which is not the period they carried: a downgrade that FIXES N-168's rows on
the way back is the honest outcome of reversing a cache, and refusing the
downgrade outright would be a worse trade for a column nothing reads.

Review: developer, 2026-08-04 (ruling R-EO, taken on the "which option is what I
should do if I were building everything from scratch" framing after both offered
options -- guard the CASCADE, or retarget the FK to NO ACTION -- were refused as
protecting a column nothing reads; the destructive drop, the re-keyed unique
index, the deletion of ``PeriodLockReason.ACCOUNT_ANCHOR`` and the two rows the
downgrade would change were all stated before the ruling was taken).

Revision ID: b6d1e94c07af
Revises: a3f7c8e21b64
Create Date: 2026-08-04 10:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'b6d1e94c07af'
down_revision = 'a3f7c8e21b64'
branch_labels = None
depends_on = None


_INDEX_NAME = "uq_anchor_history_account_period_balance_day"
_FK_NAME = "account_anchor_history_pay_period_id_fkey"


# The downgrade's backfill: ``account_service.resolve_anchor_period_id``'s rule
# expressed in SQL -- the period CONTAINING the assertion's own day, else the
# user's earliest period.  That function wrote every value this column ever
# held, so this is the derivation being reversed rather than a new guess.  The
# join to ``accounts`` is what scopes "the user's periods" to the assertion's
# own owner; a period belonging to someone else must never be selectable here.
_DOWNGRADE_BACKFILL = sa.text("""
    UPDATE budget.account_anchor_history h
    SET pay_period_id = COALESCE(
        (
            SELECT p.id FROM budget.pay_periods p
            WHERE p.user_id = a.user_id
              AND h.observed_on BETWEEN p.start_date AND p.end_date
            ORDER BY p.period_index
            LIMIT 1
        ),
        (
            SELECT p.id FROM budget.pay_periods p
            WHERE p.user_id = a.user_id
            ORDER BY p.period_index
            LIMIT 1
        )
    )
    FROM budget.accounts a
    WHERE a.id = h.account_id
""")

# The downgrade's post-backfill gate.  An account whose owner has NO pay periods
# at all cannot resolve one, and the column is about to become NOT NULL, so the
# downgrade fails loudly with the diagnostic rather than installing a constraint
# it cannot satisfy.
_UNRESOLVED = sa.text("""
    SELECT h.id, h.account_id, h.observed_on
    FROM budget.account_anchor_history h
    WHERE h.pay_period_id IS NULL
    ORDER BY h.id
""")


def upgrade():
    """Drop the assertion's pay-period link and re-key its uniqueness guard."""
    # The index first: it depends on the column, and dropping it explicitly
    # keeps the recreate below symmetric with the drop rather than relying on
    # PostgreSQL's implicit cascade from the column drop.
    op.drop_index(
        _INDEX_NAME,
        table_name="account_anchor_history",
        schema="budget",
    )
    op.drop_constraint(
        _FK_NAME,
        "account_anchor_history",
        schema="budget",
        type_="foreignkey",
    )
    op.drop_column("account_anchor_history", "pay_period_id", schema="budget")
    op.create_index(
        _INDEX_NAME,
        "account_anchor_history",
        ["account_id", "anchor_balance", "observed_on"],
        unique=True,
        schema="budget",
    )


def downgrade():
    """Restore the column, deriving each value from the assertion's own day."""
    op.drop_index(
        _INDEX_NAME,
        table_name="account_anchor_history",
        schema="budget",
    )
    op.add_column(
        "account_anchor_history",
        sa.Column("pay_period_id", sa.Integer(), nullable=True),
        schema="budget",
    )

    connection = op.get_bind()
    connection.execute(_DOWNGRADE_BACKFILL)
    unresolved = connection.execute(_UNRESOLVED).fetchall()
    if unresolved:
        raise RuntimeError(
            "downgrade b6d1e94c07af: "
            f"{len(unresolved)} account_anchor_history rows could not resolve "
            "a pay period from their observed_on, so pay_period_id cannot be "
            "made NOT NULL.  Their owners have no pay periods at all.  "
            f"Offending rows (id, account_id, observed_on): {unresolved}.  "
            "Generate pay periods for those owners and re-run the downgrade."
        )

    op.alter_column(
        "account_anchor_history",
        "pay_period_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="budget",
    )
    op.create_foreign_key(
        _FK_NAME,
        "account_anchor_history",
        "pay_periods",
        ["pay_period_id"],
        ["id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="CASCADE",
    )
    op.create_index(
        _INDEX_NAME,
        "account_anchor_history",
        ["account_id", "pay_period_id", "anchor_balance", "observed_on"],
        unique=True,
        schema="budget",
    )
