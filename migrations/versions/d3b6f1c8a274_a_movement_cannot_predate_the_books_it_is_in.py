"""A movement cannot predate the books it is in.

Plan step **balance:X-f3c-2b**, rulings **balance:R-HG** (the boundary) and
**balance:R-HF** (the repair direction).  Closes finding **balance:N-378**.

**The rule this installs, in one sentence.**  An account's opening equity is the
balance at the CLOSE of ``budget.account_openings.opened_on`` -- the same rule
``account_anchor_history.observed_on`` states for a balance assertion (ruling
R-DH (a)) -- so no cash movement may be dated on or before that day.

**Why the state is a defect and not merely untidy.**  The balance fold seeds at
the opening equity and ``cash_ledger.dated_deltas`` emits every settled source
at its own day, so a movement inside the opening is carried a SECOND time from
its own date until the next assertion resets the running total.  The rendered
balance heals there; the general ledger does not.  The correction that heals it
is posted, and on a MODELLED account ruling **R-FO** books its counter leg to
``unrealized_change`` rather than ``anchor_equity`` -- so a transfer becomes
market performance that never unwinds.  Measured on a fixture: a Roth declared
``$1,000.00`` with a ``$1,000.00`` pre-opening transfer reports ``$850.00`` of
unrealized change against a real ``$150.00``.

**What this migration does, in two steps that must run in this order.**

1. **LEGALISE the existing rows** -- restate every account whose records already
   reach into its opening, moving ``opened_on`` back to the day BEFORE its
   earliest recorded movement and carrying the equity forward unchanged.
2. **INSTALL the constraint** (:func:`app.opening_infrastructure.apply_opening_infrastructure`)
   so the state is unstorable from any client, in both directions.

Step 1 first, because a constraint trigger validates WRITES and not existing
rows: installing it over unrepaired data would leave five accounts whose next
ordinary edit aborts at COMMIT.

**Why the day moves and the figure does not** (ruling **R-HF** rejects the other
direction: re-dating the offending movements onto the opening day, which makes
the app's dates disagree with the bank ON PURPOSE).  Each figure the migration
carries forward is the one the X-f3c-2a backfill derived as "the earliest
assertion's balance, less the movements it already contained" -- which is by
construction the level BEFORE the earliest recorded movement, i.e. the closing
balance of the day before it.  Moving the day is what makes the stored pair say
what the figure already means; it is a correction of the DAY's semantics, not a
restatement of the money.

**MEASURED MONEY-NEUTRAL, both harnesses, on a production clone (2026-08-28).**
``tests/manual/verify_balance_baseline.py`` -- 9 accounts, 441 grid cells, 6,174
daily points -- and ``tests/manual/verify_anchor_surfaces.py`` -- 2 users, 9
accounts -- are byte-identical before and after.  The positive control the
harness docstring requires was run: ``+$0.01`` on one account's opening equity
moves 180 lines, so the empty diff is a measurement rather than a harness that
saw nothing.

The five accounts it restates, and the day each moves to:

===========================  ==============  ==============  ==============
account                      opened_on from  opened_on to    equity (kept)
===========================  ==============  ==============  ==============
1 Checking                   2026-03-27      2026-03-26         ``$689.16``
2 Fidelity Savings           2026-04-06      2026-03-26       ``$4,863.56``
3 Mortgage                   2026-05-01      2026-03-31     ``$174,281.51``
8 Van Loan                   2026-05-21      2026-04-22        ``-$531.94``
10 Fidelity Money Market     2026-05-01      2026-04-05       ``$4,879.26``
===========================  ==============  ==============  ==============

Accounts 4, 5, 6 and 11 carry no settled movement at all and are untouched --
their books already open on the day of their own first assertion, which is what
``account_service.create_account`` writes and what this rule wants.

**Checking is in the list and eight of the twelve rows are not what N-378
counted.**  That finding measured movements dated STRICTLY before their
account's opening: eight rows, every one a transfer leg.  Under R-HG's
closing-balance boundary the four rows Checking dates on its own opening day
(2026-03-27: a ``$2,473.38`` paycheck, a ``$100.00`` allowance, a ``-$15.96``
subscription and a ``-$500.00`` transfer out, netting the ``$2,057.42`` that is
exactly the gap between Checking's ``$689.16`` opening and its ``$2,746.58``
first assertion) are inside the opening too.  Twelve rows, five accounts.

Review: not required -- no column is dropped, renamed or retyped and no table is
altered.  It appends rows to an append-only table and creates three functions
and three triggers.

Revision ID: d3b6f1c8a274
Revises: a7c41f9d2b60
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

from app.opening_infrastructure import (
    apply_opening_infrastructure,
    remove_opening_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "d3b6f1c8a274"
down_revision = "a7c41f9d2b60"
branch_labels = None
depends_on = None


#: Every account whose records reach into its own opening, with the day its
#: books must move back to.
#:
#: ``governing`` is the opening record in force -- the table is append-only and
#: the latest RECORDING instant governs (ruling R-HE), the same order
#: ``cash_ledger.account_opening_fact`` and ``budget.account_books_opened_on``
#: read.  ``movements`` is the earliest day the account records cash moving,
#: over BOTH movement tables: a settled transaction and a posted purchase are
#: one kind of fact to the fold (ruling **R-FM**), so a rule that read only
#: ``budget.transactions`` would legalise an account its purchases still
#: violate.
#:
#: The predicate is ``<=`` and not ``<``: an opening equity is the closing
#: balance for its own day, so a movement dated ON it is inside it (R-HG).
_ACCOUNTS_TO_RESTATE = """
    WITH governing AS (
        SELECT DISTINCT ON (account_id)
               account_id, opened_on, opening_equity, source_id
          FROM budget.account_openings
         ORDER BY account_id, created_at DESC, id DESC
    ),
    movements AS (
        SELECT account_id, MIN(settled_on) AS earliest
          FROM (
                SELECT account_id, settled_on
                  FROM budget.transactions
                 WHERE settled_on IS NOT NULL
                UNION ALL
                SELECT account_id, settled_on
                  FROM budget.transaction_entries
                 WHERE settled_on IS NOT NULL
               ) AS dated
         GROUP BY account_id
    )
    SELECT g.account_id,
           g.opened_on              AS was_opened_on,
           (m.earliest - 1)::date   AS opens_on,
           g.opening_equity,
           g.source_id
      FROM governing g
      JOIN movements m ON m.account_id = g.account_id
     WHERE m.earliest <= g.opened_on
     ORDER BY g.account_id
"""


def _restate(bind) -> list:
    """Append one opening restatement per account whose records reach into it.

    The figure and its provenance are carried forward verbatim: this migration
    corrects what the stored DAY means, and restates no money.  A
    ``migration_derived`` row stays ``migration_derived``, because moving the
    day makes the derivation legible rather than turning it into an
    observation -- findings **N-275** and **N-379** measure two of those
    figures wrong against the owner's own bank, and only the restatement door
    (plan step X-f3c-2b-2) may say otherwise.

    Args:
        bind: The Alembic connection.

    Returns:
        The rows :data:`_ACCOUNTS_TO_RESTATE` selected, so the caller can print
        each change and re-check the post-state against the same set.
    """
    targets = bind.execute(sa.text(_ACCOUNTS_TO_RESTATE)).fetchall()
    for row in targets:
        bind.execute(
            sa.text("""
                INSERT INTO budget.account_openings
                       (account_id, opened_on, opening_equity, source_id,
                        created_at)
                VALUES (:account_id, :opens_on, :opening_equity, :source_id,
                        now())
            """),
            {
                "account_id": row.account_id,
                "opens_on": row.opens_on,
                "opening_equity": row.opening_equity,
                "source_id": row.source_id,
            },
        )
        print(
            f"X-f3c-2b: account {row.account_id} books open "
            f"{row.was_opened_on} -> {row.opens_on} "
            f"(equity {row.opening_equity} unchanged)"
        )
    return targets


def upgrade():
    """Legalise every account's records, then make the state unstorable.

    Raises:
        RuntimeError: When any account still holds a movement dated on or
            before its governing opening day AFTER the restatement.  Fails the
            deploy rather than installing a constraint that would abort the
            first ordinary edit of a row nobody repaired -- the same fail-loud
            posture migration ``a7c41f9d2b60`` takes when its two readings of
            opening equity disagree.
    """
    bind = op.get_bind()
    restated = _restate(bind)
    print(f"X-f3c-2b: {len(restated)} account(s) restated")

    remaining = bind.execute(sa.text(_ACCOUNTS_TO_RESTATE)).fetchall()
    if remaining:
        offenders = "; ".join(
            f"account {row.account_id} (books open {row.was_opened_on}, "
            f"earliest movement {row.opens_on}+1 day)"
            for row in remaining
        )
        raise RuntimeError(
            "X-f3c-2b: after restatement these accounts still hold a movement "
            f"dated on or before the day their books open: {offenders}.  The "
            "constraint below would make their next ordinary edit abort at "
            "COMMIT, so the deploy stops here instead.  Re-run "
            "docs/audits/balance_architecture's census "
            "(tests/manual/verify_balance_baseline.py plus the query in "
            "_ACCOUNTS_TO_RESTATE) and repair by hand before retrying."
        )

    apply_opening_infrastructure(op.execute)
    print(
        "X-f3c-2b: books-boundary constraint installed on "
        "budget.transactions, budget.transaction_entries and "
        "budget.account_openings"
    )


def downgrade():
    """Remove the constraint, then withdraw the restatements this migration made.

    **The inverse is exact rather than heuristic, and it VERIFIES that.**  The
    upgrade's target set is a pure function of the movement data, which a
    downgrade does not change, so re-running :data:`_ACCOUNTS_TO_RESTATE`'s
    ``governing``/``movements`` join against the CURRENT state names precisely
    the rows the upgrade inserted: for each of them the governing record is
    the one whose ``opened_on`` is ``earliest movement - 1`` and whose equity
    and provenance match the row it superseded.  Anything that does not match
    that shape is left alone and reported, so a hand restatement made after
    the upgrade is never silently discarded.

    Deleting rather than appending is deliberate.  The table is append-only to
    the APPLICATION -- ``AccountOpening``'s ORM guards refuse an UPDATE or a
    DELETE so a restatement stays visible -- but a downgrade is the schema
    reverting, not the app restating, and appending the old day back would
    leave the history claiming a decision nobody took.  Migration
    ``a7c41f9d2b60``'s own downgrade drops the whole table for the same reason.

    Raises:
        RuntimeError: When a row it expected to withdraw does not have the
            shape the upgrade wrote, so the correct inverse is unknown.
    """
    remove_opening_infrastructure(op.execute)
    bind = op.get_bind()
    withdrawn = bind.execute(sa.text("""
        WITH ordered AS (
            SELECT id, account_id, opened_on, opening_equity, source_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY account_id
                       ORDER BY created_at DESC, id DESC
                   ) AS recency
              FROM budget.account_openings
        ),
        governing AS (SELECT * FROM ordered WHERE recency = 1),
        superseded AS (SELECT * FROM ordered WHERE recency = 2),
        movements AS (
            SELECT account_id, MIN(settled_on) AS earliest
              FROM (
                    SELECT account_id, settled_on
                      FROM budget.transactions
                     WHERE settled_on IS NOT NULL
                    UNION ALL
                    SELECT account_id, settled_on
                      FROM budget.transaction_entries
                     WHERE settled_on IS NOT NULL
                   ) AS dated
             GROUP BY account_id
        )
        DELETE FROM budget.account_openings ao
         USING governing g, superseded s, movements m
         WHERE ao.id = g.id
           AND s.account_id = g.account_id
           AND m.account_id = g.account_id
           AND g.opened_on = (m.earliest - 1)::date
           AND g.opening_equity = s.opening_equity
           AND g.source_id IS NOT DISTINCT FROM s.source_id
           AND m.earliest <= s.opened_on
        RETURNING ao.account_id, ao.opened_on
    """)).fetchall()
    for row in withdrawn:
        print(
            f"X-f3c-2b downgrade: account {row.account_id} restatement to "
            f"{row.opened_on} withdrawn"
        )

    still_violating = bind.execute(sa.text(_ACCOUNTS_TO_RESTATE)).fetchall()
    expected = {row.account_id for row in withdrawn}
    actual = {row.account_id for row in still_violating}
    if expected != actual:
        raise RuntimeError(
            "X-f3c-2b downgrade: withdrew restatements for accounts "
            f"{sorted(expected)} but the pre-migration violating set is "
            f"{sorted(actual)}.  The two must be equal -- the upgrade inserted "
            "exactly one row per violating account -- so a mismatch means an "
            "opening was restated by hand after the upgrade and this "
            "downgrade cannot know which day to return to.  Inspect "
            "budget.account_openings for the accounts in the symmetric "
            "difference and withdraw by hand."
        )
