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
its earliest movement on 2026-04-06 while its books said 2026-05-01, so its
opening entry was dated OUTSIDE any earlier statement: the 2026-04-08 balance
sheet carried the account at ``$500.00`` -- the one transfer by then and no
opening equity at all -- against the bank's own ``$5,363.56``.  It reads
``$5,379.26`` after this migration, and both assets and equity gain the
``$4,879.26`` that was missing from each.  The other four restatements move no
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

Review: Claude Opus 5, 2026-08-31 -- no column is dropped, renamed or retyped
and no table is altered.  It appends rows to an append-only table and calls
``apply_opening_infrastructure(arms=(MOVEMENT_ARM,))``, which for that arm set
creates FIVE functions (``budget.books_hold`` is the fifth) and three
constraint triggers, and additionally issues guarded DROPs for the arm it does
NOT install -- two ``DROP TRIGGER IF EXISTS`` on
``budget.statement_match_members`` and ``budget.bank_statement_lines``, and
four ``DROP FUNCTION IF EXISTS``.  Those drops are no-ops on any database
reaching this revision in order, and they are what makes the builder's
statement TOTAL rather than additive.
*This line read "not required ... four functions and three triggers" until
plan step balance:X-f3c-2b-2b made the builder arm-explicit.  It was exactly
right when written and went stale because a SHARED builder grew underneath it
-- which is the same decay this revision's own print statement was corrected
for, and why the count now names the arm rather than the module.*

Revision ID: d3b6f1c8a274
Revises: c8e5a2f31b47 (RE-PARENTED from a7c41f9d2b60 when recurrence:R17
landed; see finding balance:N-385)
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

from app.opening_infrastructure import (
    GOVERNING_ORDER_SQL,
    MOVEMENT_ARM,
    SETTLED_MOVEMENTS_SQL,
    apply_opening_infrastructure,
    remove_opening_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "d3b6f1c8a274"
down_revision = "c8e5a2f31b47"
branch_labels = None
depends_on = None


#: Every account whose records reach into its own opening, with the day its
#: books must move back to.
#:
#: ``governing`` is the opening record in force -- the table is append-only and
#: the latest RECORDING instant governs (ruling R-HE).  ``movements`` is the
#: earliest day the account records cash moving, over BOTH movement tables: a
#: settled transaction and a posted purchase are one kind of fact to the fold
#: (ruling **R-FM**), so a rule that read only ``budget.transactions`` would
#: legalise an account its purchases still violate.
#:
#: **BOTH come from :mod:`app.opening_infrastructure` rather than being spelled
#: again here**, which is what makes "the two must agree" a property instead of
#: a hope: this revision and the constraint it installs four lines later now
#: read one statement of each rule.  Hand-spelling them was live duplication no
#: gate could see -- ``duplicate-code`` does not read SQL inside a string
#: literal, and a migration is outside ``app/`` besides (adversarial review,
#: 2026-08-28).
#:
#: The predicate is ``<=`` and not ``<``: an opening equity is the closing
#: balance for its own day, so a movement dated ON it is inside it (R-HG).
#:
#: **SOFT-DELETED rows are counted here too**, because
#: :data:`~app.opening_infrastructure.SETTLED_MOVEMENTS_SQL` counts them -- see
#: ``budget.assert_account_books_hold_its_movements`` for why the constraint's
#: row set is deliberately wider than the fold's.
_ACCOUNTS_TO_RESTATE = f"""
    WITH governing AS (
        SELECT DISTINCT ON (account_id)
               account_id, opened_on, opening_equity, source_id
          FROM budget.account_openings
         ORDER BY account_id, {GOVERNING_ORDER_SQL}
    ),
    movements AS (
        SELECT account_id, MIN(settled_on) AS earliest
          FROM ({SETTLED_MOVEMENTS_SQL}) AS dated
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
        "legalise N-378 for them rather than close it.\n\n"
        "This is reachable BEFORE this deploy lands: account_service."
        "create_account writes a user_declared opening dated on the account's "
        "own first assertion, so an owner who creates an account and settles a "
        "row on that same day produces it.  The refusal that makes it "
        "unstorable ships in this revision, not before it.\n\n"
        "To decide each figure, read what the account records inside its own "
        "opening:\n"
        "  SELECT t.id, t.name, t.settled_on, t.settled_amount\n"
        "    FROM budget.transactions t\n"
        "   WHERE t.account_id = <id> AND t.settled_on IS NOT NULL\n"
        "   ORDER BY t.settled_on;\n"
        "Then either append a corrected opening (the figure the owner states "
        "for a day BEFORE the earliest row above), or re-date the rows.  "
        "Plan step X-f3c-2b-2 builds the door that does this without SQL; "
        "until it ships the repair is by hand and the figure is the owner's."
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

    # **This revision installs the MOVEMENT arm and only it**, which is the
    # arm it censused and repaired above.  It named no arms at all until plan
    # step balance:X-f3c-2b-2b, and the builder is imported LIVE from ``app/``
    # -- so when that step added the matched-line arm, THIS call silently
    # started installing it too -- from HERE, which is five revisions before
    # ``d1f6a83c9e47`` runs the census that decides whether the rows already
    # there can satisfy them.  Measured on a clone stopped at
    # ``c9f4b1e78d02``: it came up carrying ``ck_matched_line_after_books_open``
    # and ``ck_line_day_after_books_open`` LIVE.  (That stamp is one revision
    # before the census, not five; the five is the span from this revision,
    # where the arm actually went in.)  A constraint ahead of its census is what
    # finding N-400 is made of, reached from the migration side.  Naming the
    # arm is what makes this revision install what it declared.
    apply_opening_infrastructure(op.execute, arms=(MOVEMENT_ARM,))
    print("X-f3c-2b: books-boundary MOVEMENT arm installed")


#: Every opening this migration COULD have written, for the downgrade to report.
#:
#: **It is a report and never a predicate**, which is the whole of what the
#: 2026-08-28 ruling changed.  A row is named here when it governs an account,
#: sits the day before that account's earliest movement, and supersedes an older
#: row of identical equity and provenance -- the shape the upgrade leaves.  The
#: shape does NOT identify the writer: a hand restatement made BEFORE the
#: upgrade satisfies every clause of it, which is why nothing acts on this set.
_RESTATEMENTS_STANDING = f"""
    WITH ordered AS (
        SELECT id, account_id, opened_on, opening_equity, source_id,
               ROW_NUMBER() OVER (
                   PARTITION BY account_id
                   ORDER BY {GOVERNING_ORDER_SQL}
               ) AS recency
          FROM budget.account_openings
    ),
    movements AS (
        SELECT account_id, MIN(settled_on) AS earliest
          FROM ({SETTLED_MOVEMENTS_SQL}) AS dated
         GROUP BY account_id
    )
    SELECT o.account_id, o.opened_on, o.opening_equity
      FROM ordered o
      JOIN ordered older
        ON older.account_id = o.account_id
       AND older.recency > o.recency
       AND older.opening_equity = o.opening_equity
       AND older.source_id IS NOT DISTINCT FROM o.source_id
      JOIN movements m ON m.account_id = o.account_id
     WHERE o.recency = 1
       AND o.opened_on = (m.earliest - 1)::date
     GROUP BY o.account_id, o.opened_on, o.opening_equity
     ORDER BY o.account_id
"""


def _report_restatements_standing(bind) -> None:
    """Print every opening restatement this downgrade is leaving in place.

    The downgrade removes the CONSTRAINT and no data, so this is the whole of
    what it says about ``budget.account_openings``: which accounts still carry
    a restatement in this migration's shape, and that they were left alone
    deliberately.

    Args:
        bind: The Alembic connection.
    """
    for row in bind.execute(sa.text(_RESTATEMENTS_STANDING)):
        print(
            f"X-f3c-2b downgrade: account {row.account_id} keeps its opening "
            f"at {row.opened_on} (equity {row.opening_equity}); the books "
            "boundary is no longer enforced but the day is left as it stands"
        )


def downgrade():
    """Remove the constraint.  Delete nothing.

    **THE UPGRADE'S DATA HALF HAS NO INVERSE AND THIS NO LONGER PRETENDS
    OTHERWISE** (developer ruling 2026-08-28, taken on a reproduction).  An
    earlier draft re-ran the upgrade's own join against the CURRENT state and
    deleted whatever matched, on the argument that the target set is a pure
    function of movement data a downgrade does not change.  Both halves were
    false, and the first was measured on a production clone:

    * **A SHAPE IS NOT AN IDENTITY.**  The upgrade writes no marker, and
      ``budget.account_openings`` rows carry no column that could hold one --
      the whole chain runs in ONE Alembic transaction, so every backfilled and
      restated row shares one ``now()``.  A hand restatement made BEFORE the
      upgrade -- exactly what
      :func:`app.services.cash_ledger.reject_movement_before_books_open`'s own
      message tells an owner to write -- satisfies every clause.  Planting one
      for account 10 and running upgrade then downgrade: the upgrade correctly
      restated FOUR accounts and never touched account 10, and the downgrade
      then deleted the human's row while printing that it had withdrawn a
      restatement nobody made.  It left that account holding a movement inside
      its opening -- finding **N-378**, re-opened -- with the constraint gone.
      The ``expected != actual`` guard could never catch it: deleting the row
      is what puts the account back in the violating set, so the two sides
      agree.
    * **Movement data DOES change.**  ``transaction_service._delete``,
      ``entry_service._doors`` and ``pay_period_write`` each hard-delete rows
      carrying ``settled_on``, so ``MIN(settled_on)`` can move between the
      upgrade and the downgrade -- and then the join misses a row the upgrade
      really did write.

    **What is left instead is a correction, not a residue.**  The upgrade moved
    a stored DAY to meet a figure that already meant that day, so an opening it
    restated is closer to the truth than the one it superseded; reverting it
    would re-introduce a day already known wrong, and ``account_openings`` is
    append-only precisely so a restatement stays visible.  Removing only the
    triggers and functions leaves the database legal under the older schema --
    the fold seeds at the equity, which no restatement changed -- and makes a
    re-upgrade a clean no-op, because the accounts are already legal and
    :data:`_ACCOUNTS_TO_RESTATE` selects nothing.

    Every restatement standing is REPORTED, so an operator who does want a day
    returned knows which accounts to look at and can decide each one.
    """
    remove_opening_infrastructure(op.execute)
    print(
        "X-f3c-2b: books-boundary constraint removed "
        "(app.opening_infrastructure, every table it covers)"
    )
    _report_restatements_standing(op.get_bind())
