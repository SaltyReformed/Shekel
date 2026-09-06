"""A matched bank line cannot predate the books it is evidence in

Revision ID: d1f6a83c9e47
Revises: c9f4b1e78d02
Create Date: 2026-08-31 10:20:00.000000

Plan step ``balance:X-f3c-2b-2b``, ruling **balance:R-IH**.  Found by that
step's own trace of the import doors; it opens and closes inside the step, so
it is never a ledger row.

**What is wrong.**  ``budget.assert_account_books_hold_its_movements`` bounds a
restatement by ``MIN(settled_on)`` over the two movement tables, and
``statement_match._accept.record_match`` settles every member of a match on the
LATEST of its bank days (``MatchDays.posts_on``).  So the EARLIEST line of a
multi-day group posts strictly before the row that explains it settles, and
every day in that window passes the movement bound -- the movement really is
later.  Restating the books into it puts that line's money inside the new
opening equity AND inside a settled row: the same double count finding
**N-378** is made of, reached from a direction the movement row set cannot see.

**Measured rather than argued, in both directions.**  On a restored clone of
the developer's dev database at ``c9f4b1e78d02`` -- 378 SECU lines over two
imports, 376 of them in the larger -- a group of the 2026-03-26 ``-$15.96``
line and the 2026-08-17 ``-$64.04`` line matched against one ``$80.00``
envelope was ACCEPTED against books opening 2026-03-26 at ``$689.16``,
settling the envelope on 2026-08-17.

The restatement side is REACHABLE rather than instantiated, and the numbers
carry their date because they decay: measured 2026-08-31, account 1 is the
only account holding a matched line at all (221 line members, earliest
``posted_on`` 2026-03-26) and its earliest movement is the same day, so the
window between the two bounds is EMPTY -- and PRODUCTION holds no
``budget.statement_match_members`` row and no ``budget.bank_statement_lines``
row at all, so the arm is vacuous there.  **The whole chain was rehearsed on
that clone the same day and the census PASSED**: ``d3b6f1c8a274`` restates
account 1's books 2026-03-27 -> 2026-03-25, which is strictly below the
2026-03-26 line, so this revision installs cleanly.  ``conventions.md`` rule 8
is explicit that a finding costing ``$0.00`` on today's data is a defect
waiting for the data to change.

**What this installs.**  A fourth arm on the books boundary
(:mod:`app.opening_infrastructure`), on TWO tables: deferred constraint
triggers on ``budget.statement_match_members`` and on
``budget.bank_statement_lines``, both asking one predicate over one LINE, plus
a second openings-side predicate so a RESTATEMENT is bounded by matched lines
as well as by movements.  **Two attachments because the FACT and the DAY live
on different tables** -- the member names the line, the day is
``posted_on`` one table over -- so a trigger on the members table alone left
``UPDATE budget.bank_statement_lines SET posted_on = ...`` on an
already-matched line committing cleanly into the state this forbids.  It also
moves the comparison itself into ``budget.books_hold``, so the SQL tier states
ruling R-HG's ``>`` once rather than in five hand-typed predicates.  The service
tier states the same rule in words
(``cash_ledger.reject_books_open_on_or_after_matched_lines``,
``ReviewScope.reject_line_before_books_open``), which is the pairing this
project keeps everywhere: the door gives a sentence, the database makes the
state unstorable by every client the door does not own.

**The census runs BEFORE the constraint and REFUSES rather than repairs.**  A
constraint that forbids a state existing rows already hold does not enforce an
invariant, it breaks every write on the accounts that hold it -- which is
exactly what finding **N-400** records about the assertion arm this same module
had to withdraw.  So the upgrade counts the violating members first and raises
with the diagnostic query rather than restating anybody's opening: unlike
``d3b6f1c8a274``'s movement census there is no safe automatic repair here,
because the two ways out are to move an opening the owner stated or to undo a
match they made, and neither is a migration's decision.

Review: Claude Opus 5, 2026-08-31 -- it drops and recreates the FIVE books
boundary constraint triggers (two of them new here), and it REPLACES three
existing function bodies: the movement trigger function, the movements
predicate and the openings dispatcher.  The first two change because they now
ask ``budget.books_hold`` rather than spelling ruling R-HG's comparison; the
dispatcher additionally ``PERFORM``s the matched-line predicate.
``apply_opening_infrastructure`` is idempotent and re-pins every trigger to the
in-code definition for the arms it is given.  Declared under
``.claude/rules/database.md`` because drop-and-recreate needs the line whether
or not the recreate is identical, which is the claim ``c9f4b1e78d02`` narrowed
itself to keep true.

**The downgrade was verified by RECONCILIATION on real databases, ONCE, by
hand -- and that is a measurement rather than a gate.**  On 2026-08-31, a clone
of the developer's dev database upgraded only to ``c9f4b1e78d02`` and a second
clone taken to head and back down held byte-identical ``md5(prosrc)`` for all
7 ``budget`` functions and identical ``md5(pg_get_triggerdef)`` for all 49
triggers.  Two limits are worth stating rather than leaving to a reader:

* **Nothing re-runs it.**  ``tests/test_models/test_books_boundary_arms.py``
  pins the SQL this module GENERATES -- arm-by-arm installation, the total
  statement, the drop ordering -- not what a database ends up holding.  No test
  in this suite runs alembic against two databases and compares ``pg_proc``.
  (This paragraph claimed that file pinned the round trip.  It does not, and a
  cited gate that does not exist is worse than an admitted gap.)
* **Both sides come from one producer.**  The reference is
  ``apply_opening_functions(arms=(MOVEMENT_ARM,))`` reached through
  ``c9f4b1e78d02``; the round trip is the same call reached through this
  revision's ``downgrade``.  So the reconciliation proves the two PATHS agree,
  which is exactly the property that replaced the frozen bodies -- and it
  cannot notice a body edited in :mod:`app.opening_infrastructure`, because
  both sides would move together.

*Two corrections this line has already needed, kept because the shape recurs:*
it said "four triggers ... the fourth is new" until an adversarial money review
counted them, and it described a downgrade that restored three hand-frozen
function bodies -- a copy this revision no longer keeps, because the builder
became arm-explicit and there is nothing left to freeze.  A ``Review:`` line
that miscounts what it approved is not an approval.
"""

from alembic import op
import sqlalchemy as sa

from app.opening_infrastructure import (
    MATCHED_LINE_ARM,
    MATCHED_LINE_DAYS_SQL,
    MOVEMENT_ARM,
    apply_opening_functions,
    apply_opening_infrastructure,
)

#: The arms this revision leaves installed.  Named as a LITERAL rather than as
#: ``ALL_ARMS``: a revision declares the boundary its own point in history
#: describes, and reaching for the module's current set is what let this arm go
#: live five revisions early.  See ``opening_infrastructure.ALL_ARMS``.
_ARMS_AFTER = (MOVEMENT_ARM, MATCHED_LINE_ARM)


# revision identifiers, used by Alembic.
revision = "d1f6a83c9e47"
down_revision = "c9f4b1e78d02"
branch_labels = None
depends_on = None


#: The rows this revision's constraint would refuse, with everything an
#: operator needs to decide which way out to take.
#:
#: **It reads the same row set the constraint counts**
#: (:data:`~app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`), interpolated
#: rather than re-spelled, so a census that passes cannot be answering a
#: narrower question than the trigger installed four statements later.  That
#: is the failure ``d3b6f1c8a274`` named when it made that statement public:
#: a migration whose census and whose constraint disagree legalises nothing.
#:
#: **The comparison is ``budget.books_hold`` and not a hand-typed ``<=``**,
#: for the same reason one line up.  This revision installs that function,
#: so the census below runs BEFORE it exists on the database being upgraded
#: -- which is why ``upgrade`` applies the infrastructure functions first
#: and censuses second, and why the ordering is stated there rather than
#: left to the reader.
_CENSUS_SQL = f"""
    SELECT matched.account_id,
           accounts.name,
           MIN(matched.posted_on) AS earliest_line,
           budget.account_books_opened_on(matched.account_id) AS opened_on,
           COUNT(*) AS offending_members
      FROM ({MATCHED_LINE_DAYS_SQL}) AS matched
      JOIN budget.accounts AS accounts
        ON accounts.id = matched.account_id
     WHERE NOT budget.books_hold(
               budget.account_books_opened_on(matched.account_id),
               matched.posted_on)
     GROUP BY matched.account_id, accounts.name
     ORDER BY matched.account_id
"""


def _reject_existing_violations(connection) -> None:
    """Refuse the upgrade when a matched line already predates its books.

    Args:
        connection: The Alembic connection.

    Raises:
        RuntimeError: When any ``budget.statement_match_members`` row names a
            bank line posted on or before its account's books open.  The
            message names every account, its opening day, its earliest
            offending line and how many members are involved, because the two
            ways out -- restate the opening earlier, or undo the match -- are
            the OWNER's decision and a migration may not take it for them.

    Note:
        **The message spells the comparison as a literal ``<=`` rather than
        naming ``budget.books_hold``, and that is not an oversight.**
        ``migrations/env.py`` wraps the whole chain in ONE
        ``context.begin_transaction()``, so raising here rolls back the
        ``CREATE FUNCTION budget.books_hold`` this revision issued eight
        statements earlier (it is the first of the nine
        ``apply_opening_functions`` emits for both arms) -- and on every
        retry.  An operator pasting the
        query out of the refusal would get ``function budget.books_hold(date,
        date) does not exist``, which is a dead end dressed as a diagnostic.
        The census SQL above still asks the function, because it runs while it
        exists; only the text handed to a human outlives the rollback.
    """
    offenders = connection.execute(sa.text(_CENSUS_SQL)).fetchall()
    if not offenders:
        return
    lines = "\n".join(
        f"  account {row.account_id} ({row.name}): books open {row.opened_on}, "
        f"earliest matched line {row.earliest_line}, "
        f"{row.offending_members} member(s)"
        for row in offenders
    )
    raise RuntimeError(
        "d1f6a83c9e47 cannot install the matched-line books boundary: "
        f"{len(offenders)} account(s) already hold a match naming a bank "
        "line posted on or before the day their books open, and the "
        "constraint would abort the next write touching them.\n"
        f"{lines}\n"
        "Repair each through the app's own doors before upgrading -- restate "
        "the account's opening to a day before its earliest matched line, or "
        "undo the match -- then re-run.  The diagnostic query is "
        "budget.assert_account_books_hold_its_matched_lines' own row set: "
        "SELECT m.account_id, l.posted_on FROM "
        "budget.statement_match_members m JOIN budget.bank_statement_lines l "
        "ON l.id = m.bank_statement_line_id WHERE l.posted_on <= "
        "budget.account_books_opened_on(m.account_id)"
    )


def upgrade():
    """Install the FUNCTIONS, census the existing members, then the triggers.

    **That order is forced and is worth stating.**  The census must run before
    the CONSTRAINT, because a constraint trigger validates writes rather than
    existing rows: applying it over a violating database succeeds here and then
    aborts the next COMMIT that touches one of those accounts -- the failure
    mode furthest from where it can be diagnosed.  But the census asks
    ``budget.books_hold``, which this revision is what installs, so it cannot
    run before the functions either.  Functions, census, triggers.

    ``apply_opening_functions`` is called first and
    ``apply_opening_infrastructure`` re-applies those same bodies on its way to
    the triggers.  ``CREATE OR REPLACE FUNCTION`` is idempotent, so the second
    pass costs nothing and asserts nothing false -- and naming the trigger half
    separately would mean a second public entry point for one caller.
    """
    apply_opening_functions(op.execute, arms=_ARMS_AFTER)
    _reject_existing_violations(op.get_bind())
    apply_opening_infrastructure(op.execute, arms=_ARMS_AFTER)


def downgrade():
    """Withdraw the MATCHED-LINE arm, leaving the movement arm standing.

    **One call, because the builder states the boundary TOTALLY**
    (:func:`~app.opening_infrastructure.apply_opening_infrastructure`): naming
    the movement arm alone drops this revision's two constraint triggers and
    its four functions, and REGENERATES the openings dispatcher without the
    matched-line predicate -- in that order, so nothing is dropped while a
    stored body still names it.

    **This used to be a hand-frozen copy of the three previous function
    bodies, and deleting that copy is the point of the change.**  Those bodies
    were correct when written -- reconciled byte-for-byte against
    ``git show HEAD`` -- but nothing pinned them: the next edit to any of the
    three would have rotted the copy silently, and the downgrade would then
    install a body that never existed on any database, with a green suite.
    Making the builder arm-explicit removed the reason to keep a copy at all,
    which is the difference between a fence and a structure.

    **It restores BEHAVIOUR, not bytes**, and that is the honest claim.  The
    movement bodies this leaves standing ask ``budget.books_hold`` -- which is
    BASE and survives, because every arm's predicate asks it -- where the
    bodies originally installed at ``c9f4b1e78d02`` open-coded the same ``>``.
    Ruling **R-HG** is one rule either way; a downgraded database and a
    database freshly upgraded to ``c9f4b1e78d02`` now hold the IDENTICAL
    definitions -- which the frozen copy could not give, and which was
    reconciled by hand on two clones (see the module docstring, including what
    that reconciliation cannot see).
    """
    apply_opening_infrastructure(op.execute, arms=(MOVEMENT_ARM,))
