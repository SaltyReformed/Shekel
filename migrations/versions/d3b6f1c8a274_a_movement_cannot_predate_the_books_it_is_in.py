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

**IT MOVES TWO BALANCE SHEETS, AND THE FIRST DRAFT OF THIS PARAGRAPH CLAIMED IT
MOVED NOTHING** (measured on a production clone 2026-08-28; the wrong claim was
caught by adversarial review the same day).

The claim was "``verify_balance_baseline`` and ``verify_anchor_surfaces`` are
byte-identical, so this is money-neutral".  Both ARE byte-identical, and it is
worth nothing: this migration changes ``opened_on`` alone, and the cash fold
seeds at ``walk.opening.opening_equity`` -- a SCALAR.  No figure either harness
captures can move under a pure day restatement, for any account, ever.  The
positive control offered as proof varied ``opening_equity``, a DIFFERENT
variable, so it demonstrated that the harness sees equity and never that it
sees the day.  Re-measured on the right axis: moving one account's
``opened_on`` by a single day produces **zero diff lines**.  That is the
verification standard's rule 3 -- ask of every harness whether it can SEE the
code under test -- failing in the direction that reads as a free pass.

**The surface that moves is the posted ledger, and it moves at DEPLOY rather
than here.**  ``entrypoint.sh`` runs ``backfill_all_account_anchor_postings``
after the migration chain, which re-dates each restated account's
``account_opening`` journal entry onto its new day and into the pay period
containing it; ``ledger_report_service`` buckets those corrections by
``entry_date``.  Reproducing that whole sequence on both clones,
``tests/manual/verify_statement_baseline.py`` (2 users, 139 statements, 3,915
leaves) moves **38 leaves and gains 4**, and all of it is one change:

* the balance sheets for **2026-04-08** and **2026-04-22** gain
  ``Fidelity Money Market Savings -- Opening`` at **$4,879.26**, lifting assets
  and equity by that amount on each;
* every other moved leaf is the positional shift of one inserted equity line;
* the two-part tie-out still closes on both sides, and no income statement
  moves -- an opening books to equity, never to income.

**That change is the correction this step exists to make.**  Account 10 records
its earliest movement on 2026-04-06 while its books said 2026-05-01, so a
balance sheet dated 2026-04-08 showed the account holding nothing 
after money had already moved in it.  The other four restatements move no
statement: accounts 3 and 8 are loans, which post ``loan_opening`` and are
outside this backfill, and accounts 1 and 2 have no statement date inside the
span their books moved across.

``verify_balance_baseline`` (9 accounts, 441 grid cells, 6,174 daily points)
and ``verify_anchor_surfaces`` (2 users, 9 accounts) are byte-identical after
the deploy backfill too -- reported as what they are, a statement about
surfaces this change cannot reach, rather than as evidence.

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
#:
#: **SOFT-DELETED rows are counted here too**, matching
#: ``budget.assert_account_books_hold_its_movements`` exactly -- see that
#: function for why the constraint's row set is deliberately wider than the
#: fold's.  The two must agree or this migration would move an opening to a
#: day the constraint installed four lines later immediately refuses.
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


#: The provenance every restated figure must carry, and the reason the
#: migration may carry it forward unchanged.
_MIGRATION_DERIVED = "migration_derived"

#: The accounts :data:`_ACCOUNTS_TO_RESTATE` would move whose opening figure a
#: HUMAN stated.  Empty on every database this has been run against, and
#: checked rather than assumed, because it is the one input that would make
#: this migration's whole money argument false.
_DECLARED_OPENINGS_IN_THE_WAY = _ACCOUNTS_TO_RESTATE.replace(
    "WHERE m.earliest <= g.opened_on",
    """WHERE m.earliest <= g.opened_on
       AND g.source_id <> (
             SELECT id FROM ref.account_opening_sources WHERE name = :derived
           )""",
)


def _reject_declared_openings(bind) -> None:
    """Refuse to restate an opening figure a human stated.

    **This migration carries every equity forward verbatim, and that is only
    safe for a DERIVED figure.**  The X-f3c-2a backfill computed each one as
    "the earliest assertion's balance, less the movements it already
    contained", which is by construction the level BEFORE the earliest
    recorded movement -- so moving the DAY back to meet it makes the stored
    pair say what the figure already meant, and no money moves.

    A ``user_declared`` opening carries no such guarantee.  An owner who typed
    "on 2026-05-01 I had $X" for an account that already recorded a 2026-04-20
    settle stated a figure that INCLUDES that settle, and moving the day back
    removes nothing: the fold seeds at the equity as a scalar, so the double
    count survives at exactly its old magnitude -- and the constraint installed
    four lines later guarantees nothing will ever surface it again.  The
    migration would have LEGALISED finding **N-378** for that account instead
    of closing it.

    All nine production accounts are ``migration_derived`` today, so this
    refuses nothing there.  It is stated because the premise is doing
    load-bearing work in a money migration that will also run on dev clones
    and on databases nobody has censused, and because an undated measurement
    quoted as a reason decays invisibly -- nobody re-checks a premise.  Found
    by adversarial review, 2026-08-28.

    Args:
        bind: The Alembic connection.

    Raises:
        RuntimeError: When any account in the way carries a non-derived
            opening, naming each one.
    """
    declared = bind.execute(
        sa.text(_DECLARED_OPENINGS_IN_THE_WAY), {"derived": _MIGRATION_DERIVED},
    ).fetchall()
    if not declared:
        return
    offenders = "; ".join(
        f"account {row.account_id} (books open {row.was_opened_on}, "
        f"equity {row.opening_equity})"
        for row in declared
    )
    raise RuntimeError(
        "X-f3c-2b: these accounts hold a movement inside their opening AND an "
        f"opening figure that was not derived: {offenders}.  Carrying such a "
        "figure forward to an earlier day removes no double count -- the fold "
        "seeds at the equity, not at the day -- so this migration would "
        "legalise N-378 for them rather than close it.  Decide each figure "
        "with the owner (plan step X-f3c-2b-2's restatement door) before "
        "retrying."
    )


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
    _reject_declared_openings(bind)
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


#: An opening still carrying this migration's SIGNATURE -- the day before the
#: account's earliest movement, over an older row of identical equity -- on an
#: account the downgrade did not withdraw.  That is what a hand restatement
#: made after the upgrade leaves behind, and the set comparison below cannot
#: see it: the account appears in neither the withdrawn set nor the violating
#: one, so both stay equal and the row survives in silence.
_RESTATEMENTS_LEFT_BEHIND = """
    WITH ordered AS (
        SELECT id, account_id, opened_on, opening_equity,
               ROW_NUMBER() OVER (
                   PARTITION BY account_id
                   ORDER BY created_at DESC, id DESC
               ) AS recency
          FROM budget.account_openings
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
    SELECT o.account_id, o.opened_on
      FROM ordered o
      JOIN ordered older
        ON older.account_id = o.account_id
       AND older.recency > o.recency
       AND older.opening_equity = o.opening_equity
      JOIN movements m ON m.account_id = o.account_id
     WHERE o.opened_on = (m.earliest - 1)::date
     GROUP BY o.account_id, o.opened_on
     ORDER BY o.account_id
"""


def _report_restatements_left_behind(bind, withdrawn_ids) -> None:
    """Print every restatement this downgrade declined to withdraw.

    The docstring above promises that a row not matching the upgrade's shape is
    "left alone and REPORTED", and until this existed only the first half was
    true: the set comparison that follows compares the withdrawn accounts
    against the still-violating ones, and a row the ``DELETE`` predicate
    skipped appears in NEITHER -- so the two stay equal and nothing prints.
    Found by adversarial review, 2026-08-28.

    Reports rather than raises, which is the same judgement the DELETE makes:
    a hand restatement made after the upgrade is somebody's decision, and a
    downgrade may not guess which day to return it to.  Saying so is the whole
    obligation.

    Args:
        bind: The Alembic connection.
        withdrawn_ids: The account ids this downgrade did withdraw, which are
            reported by their own line and are not left behind.
    """
    left = [
        row for row in bind.execute(sa.text(_RESTATEMENTS_LEFT_BEHIND))
        if row.account_id not in withdrawn_ids
    ]
    for row in left:
        print(
            f"X-f3c-2b downgrade: account {row.account_id} still carries an "
            f"opening at {row.opened_on} in this migration's shape and was "
            "NOT withdrawn -- its books were restated by hand after the "
            "upgrade, so the day to return to is a decision rather than an "
            "inverse.  Left alone; withdraw by hand if that is wrong."
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

    _report_restatements_left_behind(bind, {row.account_id for row in withdrawn})

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
