"""an account's balance assertion carries the civil day it was TRUE

Plan step 2 of ``docs/audits/balance_architecture/anchor_settle_partition.md``,
opening half -- pulled forward from "after the civil-day seam" to "now" by the
developer's ruling on finding N-133 / F1 (2026-07-31).

**What the column is for.**  A cash balance assertion had no date at all: only
``created_at``, the instant the Save button was pressed.  The engine must answer
"is this settled movement already inside the balance the user asserted?", and
with no date on either side it answered by comparing two data-entry timestamps
-- which on production subtracted ``$4,001.42`` of already-cleared payments a
second time (finding N-130).  Ruling R-DH made the comparison civil-day
granular and DERIVED both days; this column stores the assertion's half so the
comparison is between two recorded facts rather than a derivation from a
keystroke.  The loan side has had exactly this since Commit 16
(``LoanAnchorEvent.anchor_date``); the cash side is the half that never got it
(finding X5).

**No figure moves the day this ships.**  The backfill is the derivation it
replaces, verbatim: ``(created_at AT TIME ZONE 'America/New_York')::date``,
the display-timezone civil day ``cash_ledger.cash_anchor_facts`` computes today
(ruling R-DH (b) -- pay-period and due-date columns are plain ``DATE``s meaning
the user's civil days, so deriving an event's day in UTC compares two different
calendars).  Every existing row therefore keeps the day the engine already gave
it.  ``America/New_York`` is pinned as a literal here rather than read from
``app.utils.dates.DISPLAY_TIMEZONE`` because a migration states the rule that
was true when it ran; a later zone change must not silently re-date history.

**The unique index is re-keyed onto the new column** (finding N-133 / F12).
``uq_anchor_history_account_period_balance_day`` guarded double-submits on
``((created_at AT TIME ZONE 'UTC')::date)`` -- a UTC day, while the ruling's day
is the user's.  Two assertions of one balance on two different Eastern days that
share a UTC day (23:00 EDT one evening, 01:00 EDT the next) were rejected as a
same-day duplicate although the ruling calls them two different days' closing
balances.  Keying on ``observed_on`` fixes that and still rejects every
double-submit, because a double-click asserts ONE business day.

**Destructive, in two ways, and the second is the one that matters.**  The
index is dropped and recreated -- derived, and ``downgrade`` rebuilds the
UTC-day expression verbatim.  But ``downgrade`` also DROPS ``observed_on``, and
that column is USER-SUPPLIED: a back-dated opening ("this balance was true on
2026-03-15") is not re-derivable from ``created_at``, so downgrading discards a
fact only the user knows.  ``downgrade`` therefore refuses when any row carries
a day its ``created_at`` would not have produced, and names them.

``downgrade`` refuses in one more case, and it is the one this migration
exists to create.  F12's whole point is that two assertions of one balance on
two different EASTERN days that share a UTC day are legitimate and were wrongly
rejected; after this ships they can be recorded.  Rebuilding the UTC-day index
over such a pair fails with a unique violation, so the check runs first and
tells the operator which rows to reconcile rather than aborting mid-DDL.

Review: developer, 2026-07-31 (ruling on finding N-133 / F1: "Revert + date the
opening now"; F12's re-key approved in the same session).

Revision ID: c4a19e7b2d80
Revises: b2d8f3a6c541
Create Date: 2026-07-31 21:05:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'c4a19e7b2d80'
down_revision = 'b2d8f3a6c541'
branch_labels = None
depends_on = None


_INDEX = "uq_anchor_history_account_period_balance_day"

# The derivation the column replaces, verbatim (ruling R-DH (b)).  Naming the
# zone as a literal is deliberate: see the module docstring.
_BACKFILL = sa.text("""
    UPDATE budget.account_anchor_history
    SET observed_on = (created_at AT TIME ZONE 'America/New_York')::date
    WHERE observed_on IS NULL
""")

_REMAINING_NULLS = sa.text("""
    SELECT count(*) FROM budget.account_anchor_history
    WHERE observed_on IS NULL
""")

# The re-keyed index's own duplicate check, mirroring its columns exactly so
# the operator sees the tuples the CREATE would reject.  Two assertions of one
# balance recorded 19:00 and 21:00 on a single EASTERN day are distinct under
# the outgoing UTC-day key (they straddle midnight UTC) and collide under this
# one -- the mirror image of the case F12 exists to admit, and the one shape
# this re-key can refuse that its predecessor accepted.  The migration that
# created this index (``e8b14f3a7c22``) pre-flights it exactly this way; doing
# less here would abort the deploy on a bare "Key (...) is duplicated" naming
# no row.
_INDEX_DUPES = sa.text("""
    SELECT account_id, pay_period_id, anchor_balance, observed_on,
           COUNT(*) AS cnt
    FROM budget.account_anchor_history
    GROUP BY account_id, pay_period_id, anchor_balance, observed_on
    HAVING COUNT(*) > 1
    ORDER BY account_id, pay_period_id, observed_on
""")

# ``downgrade``'s two refusals.  The first finds rows whose business day is not
# what ``created_at`` derives -- the user typed it, so dropping the column
# discards it.  The second finds rows the pre-F12 UTC-day index cannot hold.
_HAND_DATED = sa.text("""
    SELECT id, account_id, observed_on,
           (created_at AT TIME ZONE 'America/New_York')::date AS derived
    FROM budget.account_anchor_history
    WHERE observed_on
          IS DISTINCT FROM (created_at AT TIME ZONE 'America/New_York')::date
    ORDER BY id
""")

_UTC_DAY_DUPES = sa.text("""
    SELECT account_id, pay_period_id, anchor_balance,
           ((created_at AT TIME ZONE 'UTC')::date) AS utc_day,
           COUNT(*) AS cnt
    FROM budget.account_anchor_history
    GROUP BY account_id, pay_period_id, anchor_balance,
             ((created_at AT TIME ZONE 'UTC')::date)
    HAVING COUNT(*) > 1
    ORDER BY account_id, pay_period_id
""")


def upgrade():
    """Add ``observed_on``, backfill it, enforce NOT NULL, re-key the index."""
    op.add_column(
        "account_anchor_history",
        sa.Column("observed_on", sa.Date(), nullable=True),
        schema="budget",
    )

    connection = op.get_bind()
    connection.execute(_BACKFILL)

    # ``created_at`` is NOT NULL with a NOW() server default (CreatedAtMixin),
    # so the backfill covers every row by construction.  Verify rather than
    # assume: a surviving NULL would fail the ALTER below with a bare
    # constraint error naming no row, and the operator needs the diagnostic.
    remaining = connection.execute(_REMAINING_NULLS).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot set budget.account_anchor_history.observed_on NOT NULL: "
            f"{remaining} row(s) are still NULL after the backfill.  Inspect "
            f"them with: SELECT id, account_id, pay_period_id, created_at "
            f"FROM budget.account_anchor_history WHERE observed_on IS NULL;"
        )

    op.alter_column(
        "account_anchor_history", "observed_on",
        existing_type=sa.Date(), nullable=False, schema="budget",
    )

    # Re-key the double-submit guard from the UTC-day expression onto the
    # stored business date (F12).  Pre-flight the new key BEFORE dropping the
    # old one, so a database carrying a colliding pair keeps its existing guard
    # and the operator gets the offending rows instead of a bare index error
    # from a half-applied migration.
    dupes = connection.execute(_INDEX_DUPES).fetchall()
    if dupes:
        listed = "; ".join(
            f"account={row[0]} period={row[1]} balance={row[2]} "
            f"observed_on={row[3]} count={row[4]}"
            for row in dupes
        )
        raise RuntimeError(
            f"Cannot re-key {_INDEX} onto observed_on: "
            f"{len(dupes)} group(s) would violate it -- {listed}.  These are "
            "assertions of one balance for one account, period and BUSINESS "
            "day that the outgoing UTC-day key allowed because they straddled "
            "midnight UTC.  Resolve by deleting the redundant later row in "
            "each group (the earlier one already recorded the same balance) "
            "before re-running the upgrade."
        )
    op.drop_index(_INDEX, table_name="account_anchor_history", schema="budget")
    op.create_index(
        _INDEX,
        "account_anchor_history",
        ["account_id", "pay_period_id", "anchor_balance", "observed_on"],
        unique=True,
        schema="budget",
    )


def downgrade():
    """Restore the UTC-day functional index and drop ``observed_on``.

    Refuses rather than destroying what it cannot rebuild -- see the module
    docstring for both cases.
    """
    connection = op.get_bind()

    hand_dated = connection.execute(_HAND_DATED).fetchall()
    if hand_dated:
        listed = "; ".join(
            f"id={row[0]} account={row[1]} observed_on={row[2]} "
            f"(created_at derives {row[3]})"
            for row in hand_dated
        )
        raise RuntimeError(
            f"Cannot drop budget.account_anchor_history.observed_on: "
            f"{len(hand_dated)} row(s) carry a USER-SUPPLIED business day that "
            f"``created_at`` does not derive -- {listed}.  Dropping the column "
            "discards a fact only the user knows.  To downgrade anyway, first "
            "reconcile each row by hand (UPDATE ... SET observed_on = "
            "(created_at AT TIME ZONE 'America/New_York')::date), accepting "
            "that each such assertion reverts to the day it was RECORDED "
            "rather than the day it was TRUE."
        )

    utc_day_dupes = connection.execute(_UTC_DAY_DUPES).fetchall()
    if utc_day_dupes:
        listed = "; ".join(
            f"account={row[0]} period={row[1]} balance={row[2]} "
            f"utc_day={row[3]} count={row[4]}"
            for row in utc_day_dupes
        )
        raise RuntimeError(
            f"Cannot rebuild {_INDEX} on the UTC day: {len(utc_day_dupes)} "
            f"group(s) would violate it -- {listed}.  These are the rows F12 "
            "exists to admit: one balance asserted on two different "
            "America/New_York days that share a UTC day.  The pre-F12 guard "
            "cannot represent them.  Delete the redundant row in each group "
            "before downgrading, accepting the loss of that assertion."
        )

    op.drop_index(_INDEX, table_name="account_anchor_history", schema="budget")
    op.create_index(
        _INDEX,
        "account_anchor_history",
        [
            "account_id", "pay_period_id", "anchor_balance",
            sa.text("((created_at AT TIME ZONE 'UTC')::date)"),
        ],
        unique=True,
        schema="budget",
    )
    op.drop_column(
        "account_anchor_history", "observed_on", schema="budget",
    )
