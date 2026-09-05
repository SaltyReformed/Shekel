"""The MATCHED-LINE arm: a matched bank line may not predate its books.

Installed by revision ``d1f6a83c9e47``, which censuses the matched lines
already in the way and REFUSES rather than repairing -- the two ways out are
the owner's decision.  The package docstring
(:mod:`app.opening_infrastructure`) carries the argument, including why this is
not implied by the movement arm; this file is the DDL for one arm, and the
openings-side predicate that grades it lives in :mod:`._openings`.
"""

from __future__ import annotations

from ._base import _BOOKS_HOLD_FUNCTION, _OPENED_ON_FUNCTION

#: The MATCHED-LINE rule, as ONE predicate over a LINE id and two thin trigger
#: functions that reach it.
#:
#: **Two attachments, because the fact and the day live on different tables**
#: -- which is the asymmetry with the movement arm and the reason this needed
#: a predicate of its own.  ``settled_on`` sits on the very table the movement
#: trigger is attached to, so an ``UPDATE`` of it fires that trigger.  A
#: member row carries no day at all: it names a line, and the day is
#: ``budget.bank_statement_lines.posted_on`` one table over.  So a trigger on
#: the members table alone leaves
#: ``UPDATE budget.bank_statement_lines SET posted_on = '2020-01-01'`` on an
#: already-matched line committing cleanly into exactly the state this arm
#: forbids -- found by adversarial design review 2026-08-31, against a module
#: docstring that claimed the state was unstorable by any client.
_MATCHED_LINE_PREDICATE = "budget.assert_matched_line_holds_books"

_MATCH_MEMBER_FUNCTION = "budget.assert_match_member_after_books_open"

_LINE_DAY_FUNCTION = "budget.assert_line_day_after_books_open"

_MATCH_MEMBER_TRIGGER = "ck_matched_line_after_books_open"

_LINE_DAY_TRIGGER = "ck_line_day_after_books_open"

#: The table asserting that a bank line IS one of the owner's rows.
_MATCH_MEMBERS_TABLE = "budget.statement_match_members"

#: The table carrying the DAY that rule grades.  Attached to as well as
#: the members table, because a day that can move independently of the
#: fact that grades it is a day the fact cannot bound.
_BANK_LINES_TABLE = "budget.bank_statement_lines"

#: Every MATCHED bank line's posting day, by account, as ONE SQL expression.
#:
#: **PUBLIC for the reason** :data:`SETTLED_MOVEMENTS_SQL` **is**: the Python
#: reader (``cash_ledger.earliest_matched_line_day``) interpolates it too, so
#: the service refuses exactly what the trigger refuses rather than two authors
#: agreeing to keep two queries in step.
#:
#: **A member row names exactly ONE subject**
#: (``ck_statement_match_members_one_subject``), so the ``IS NOT NULL`` narrows
#: to the LINE members and skips the ones that name an app row -- those are
#: money, and :data:`SETTLED_MOVEMENTS_SQL` already bounds them from the other
#: side.  **About HALF the table, measured**: 226 of 447 member rows name an
#: app row on the developer's dev database, 2026-08-31.  (This said "two in
#: three" until that count was taken; a match must balance and names at least
#: one line and at least one app row, so an ordinary one-line/one-row match is
#: 50%, and no measurement supported the higher figure.)
#:
#: **Why a matched line bounds an opening at all, which is the whole of plan
#: step balance:X-f3c-2b-2b's second half.**  A match settles every member on
#: the LATEST of its bank days, so a group's earliest line can post strictly
#: BEFORE the row that explains it settles.  The books may therefore be
#: restated into the gap between the two -- legal under every movement bound,
#: because the movement is later -- and the earlier line's money is then inside
#: the opening equity AND inside a settled row.  The movement row set cannot
#: see that state, which is why this is a second expression rather than a
#: widening of the first.
MATCHED_LINE_DAYS_SQL = """
        SELECT members.account_id, lines.posted_on
          FROM budget.statement_match_members AS members
          JOIN budget.bank_statement_lines AS lines
            ON lines.id = members.bank_statement_line_id
         WHERE members.bank_statement_line_id IS NOT NULL
"""

_CREATE_MATCHED_LINE_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION {_MATCHED_LINE_PREDICATE}(p_line_id INTEGER)
RETURNS VOID AS $$
DECLARE
    v_opened_on DATE;
    v_posted_on DATE;
    v_account_id INTEGER;
BEGIN
    -- **The rule over ONE LINE, asked by both attachments.**  The fact (a
    -- match member) and the day (``bank_statement_lines.posted_on``) live on
    -- different tables, so either can move without the other -- and a
    -- predicate on the members table alone would leave an ``UPDATE`` of the
    -- day committing into the state this forbids.  Stating it once over the
    -- LINE is what lets one rule guard both.
    SELECT lines.posted_on, lines.account_id
      INTO v_posted_on, v_account_id
      FROM budget.bank_statement_lines AS lines
     WHERE lines.id = p_line_id;

    -- The line is gone.  Unreachable through a member row -- the composite
    -- foreign key to ``budget.bank_statement_lines(id, account_id)`` is NOT
    -- DEFERRABLE, so it is checked at the end of the STATEMENT, strictly
    -- before this deferred trigger runs at COMMIT.  (It said "at the same
    -- COMMIT" until a review checked the constraint definition; the
    -- conclusion is unchanged and the mechanism is stronger, but if the FK
    -- really did run at COMMIT its ordering against this event would be a
    -- live question rather than a settled one.)  The honest answer either
    -- way: a line that does not exist bounds nothing.
    IF v_posted_on IS NULL THEN
        RETURN;
    END IF;

    -- **Only a MATCHED line is bounded.**  An ordinary imported line the
    -- owner has not explained is evidence and not a record, so its day is
    -- free; what the books cannot hold is a line some row of theirs CLAIMS.
    IF NOT EXISTS (
        SELECT 1 FROM budget.statement_match_members AS members
         WHERE members.bank_statement_line_id = p_line_id
    ) THEN
        RETURN;
    END IF;

    v_opened_on := {_OPENED_ON_FUNCTION}(v_account_id);

    -- An account carrying NO opening record is a broken invariant and is
    -- deliberately not raised here, exactly as the movement function argues.
    IF v_opened_on IS NULL THEN
        RETURN;
    END IF;

    IF NOT {_BOOKS_HOLD_FUNCTION}(v_opened_on, v_posted_on) THEN
        RAISE EXCEPTION
            'bank statement line % is dated % on account %, on or before the '
            'day that account''s books open (%); the opening equity is the '
            'closing balance for its own day, so a row explaining that line '
            'would record money the opening already holds',
            p_line_id, v_posted_on, v_account_id, v_opened_on;
    END IF;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_MATCH_MEMBER_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_MATCH_MEMBER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- DISPATCH ONLY.  A member naming an app ROW never reaches here: the
    -- trigger's WHEN clause states that, and states it ONCE, for the reason
    -- the movement trigger's does -- an early RETURN cannot stop a deferred
    -- event being QUEUED, and a queued event reserves the table against DDL.
    -- About HALF this table names a transaction or a purchase (226 of 447,
    -- dev, 2026-08-31).
    PERFORM {_MATCHED_LINE_PREDICATE}(NEW.bank_statement_line_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_LINE_DAY_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_LINE_DAY_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- DISPATCH ONLY, from the other side: the DAY moved rather than the fact.
    -- Without this attachment
    -- ``UPDATE budget.bank_statement_lines SET posted_on = ...`` on an
    -- already-matched line commits cleanly into the state the members trigger
    -- exists to forbid, because the day it grades is not on the table it is
    -- attached to.  Found by adversarial design review 2026-08-31.
    PERFORM {_MATCHED_LINE_PREDICATE}(NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

#: The MATCHED-LINE rule's FIRST attachment: the fact appearing.
#:
#: ``AFTER INSERT OR UPDATE OF bank_statement_line_id, account_id`` rather than
#: a bare ``AFTER INSERT OR UPDATE``, and ``WHEN (NEW.bank_statement_line_id IS
#: NOT NULL)`` beside it, for the two reasons
#: :func:`_create_movement_trigger_sql` gives: those columns are the whole of
#: what the predicate reads, and a deferred event is QUEUED at statement time
#: -- so a member naming a transaction or a purchase, which is about HALF the
#: rows this table holds (226 of 447, dev, 2026-08-31), would otherwise reserve
#: it against DDL for the rest of its transaction.
#:
#: **No DELETE arm**, and the reason is the movement trigger's: unmatching can
#: only ever move ``MIN(posted_on)`` LATER, so it cannot break the invariant,
#: and a CASCADE disposal must not have to satisfy it mid-flight.
_CREATE_MATCH_MEMBER_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_MATCH_MEMBER_TRIGGER} "
    "AFTER INSERT OR UPDATE OF bank_statement_line_id, account_id "
    f"ON {_MATCH_MEMBERS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.bank_statement_line_id IS NOT NULL) "
    f"EXECUTE FUNCTION {_MATCH_MEMBER_FUNCTION}()"
)

#: The MATCHED-LINE rule's SECOND attachment: the DAY moving under the fact.
#:
#: **The trigger the arm was missing until an adversarial design review found
#: it on 2026-08-31.**  The members trigger grades a day that lives on ANOTHER
#: table, so ``UPDATE budget.bank_statement_lines SET posted_on = '2020-01-01'``
#: on an already-matched line committed cleanly into the state this arm
#: forbids -- against a module docstring claiming the state was unstorable by
#: any client.  The movement arm has no such hole because ``settled_on`` sits
#: on the table its trigger is attached to.
#:
#: **UPDATE only, with no INSERT arm**, and that is the difference that keeps
#: an import cheap: a line cannot be matched at the instant it is inserted --
#: the member row naming it must come after, and that INSERT is graded by the
#: attachment above.  An INSERT arm would queue one deferred event per
#: imported line -- **376 for the developer's largest single import, measured
#: on dev 2026-08-31** (378 lines across two imports; the older 361 in
#: ``statement_import`` and ``statement_match`` are earlier counts of the same
#: growing export, which is why this one carries its date) -- and reserve the
#: table against DDL for the whole import.
_CREATE_LINE_DAY_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_LINE_DAY_TRIGGER} "
    "AFTER UPDATE OF posted_on, account_id "
    f"ON {_BANK_LINES_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.posted_on IS DISTINCT FROM OLD.posted_on "
    "OR NEW.account_id IS DISTINCT FROM OLD.account_id) "
    f"EXECUTE FUNCTION {_LINE_DAY_FUNCTION}()"
)
