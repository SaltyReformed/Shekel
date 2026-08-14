"""the recurrence's opening bound becomes a DATE

Plan step **R7b-4** of ``docs/plans/implementation_plan_recurrence_redesign.md``
section 4.  The recurrence form's "First paycheck" affordance -- a pay-period FK
in ``budget.recurrence_rules.start_period_id`` -- becomes "Starts on", written
to the ``start_date`` column that already exists.  **Plan ledger rows D2 and D30
close here.**

Review: Josh, 2026-08-14 -- APPROVED: the fold, and the ruling that the phase an
``Every N Periods`` rule fires on is DERIVED from that date rather than read
back off ``offset_periods`` (Option C of the 2026-08-14 design question).  Also
approved with it: this downgrade leaves ``start_period_id`` NULL rather than
re-deriving it, on the proof set out under "Downgrade" below.

DESTRUCTIVE: it empties a populated column on a financial table.

**One statement moves data, and it writes the FEWEST rows that reproduce the
reader.**  ``recurrence._resolution._effective_start`` answered
``max(schedule opening, start_date, start_period.start_date)``, so a start
period is only worth writing down when it is the term that DECIDES that
maximum.  Folding one that is dominated would preserve today's answer while
destroying information -- see ``_FOLD_SQL`` for the two shapes that matters
for, and for why an unconditional fold would have written 43 rows where 3 are
correct.

The step's specification originally named ``COALESCE``, which is wrong for a
different reason: it takes ``start_date`` whenever it is set, and two live
rules carry both values with the PERIOD dominating.  The predicate here is a
maximum, as the reader is.

**Measured before it shipped**, on a 2026-08-14 clone of production, through the
app's own doors (``recurrence_spec`` -> ``build_transient_rule`` ->
``rule_occurrences``) rather than a hand-rolled walk: 46 rules, 880 placed
occurrences, **0 moved**.  Every live rule carries ``interval_n = 1`` and
``offset_periods = 0``, for which the phase derivation short-circuits to 0
whatever it reads -- so the phase ruling costs ``$0.00`` today and is about
rules authored from here on.

**Why the column is EMPTIED rather than dropped.**  Dropping it belongs with the
four other closed-set columns plan step R7c drops in one transaction
(``pattern_id``, ``day_of_month``, ``month_of_year``, ``offset_periods``).  What
this step owes is that nothing reads or writes it, which the same commit
delivers: the write door stops assigning it, ``resolve`` stops looking it up,
the ``RecurrenceRule.start_period`` relationship is deleted (**D30** -- it was
``lazy="joined"`` with zero readers), ``PeriodLockReason.RECURRENCE_ANCHOR`` is
deleted, and ``pay_period_admin``'s capture-and-re-point pair is deleted.
Leaving values in a column nothing reads would be a fact with no reader; NULLing
it is what makes "no rule has a start period" true rather than merely unused.

**What the RECURRENCE_ANCHOR lock was protecting, and why it goes.**  That lock
refused to delete a pay period some rule pointed at, because the FK is
``ON DELETE SET NULL`` -- deleting the period silently erased the rule's opening
bound.  A date cannot be cascaded, so the bound now survives any schedule
operation and the lock guards a loss that cannot happen.

**Not a new audit surface.**  ``budget.recurrence_rules`` is already in
``app.audit_infrastructure.AUDITED_TABLES``, so both statements below are
recorded per row in ``system.audit_log`` -- which is what makes this fold
reversible by inspection even though the downgrade does not restore the column.
``EXPECTED_TRIGGER_COUNT`` is unchanged; no table is created or dropped.

**Downgrade**: it restores the SCHEMA (nothing changed) and deliberately does
NOT re-populate ``start_period_id``.  That is a behaviour-preserving choice
rather than a shortcut, and the proof is that the old code read the column for
exactly two things:

  * the opening bound, ``max(opening, start_date, start_period.start_date)`` --
    and ``start_date`` now HOLDS that maximum, so the answer is identical with
    the FK NULL;
  * the ``Every N Periods`` phase, which fell back to the ``offset_periods``
    COLUMN when no start period was named -- and the write door has always
    written that column with ``start_period.period_index % interval_n``, the
    very value the FK would have derived.  So the fallback answers what the
    lookup answered.

Re-deriving the FK instead would NOT be neutral: a rule that always had a
``start_date`` and never had a start period -- every loan payment -- would newly
acquire one, and its phase would change from the stored value to the containing
period's ordinal.  ``$0.00`` on today's data (all 46 rules are interval 1) but
not provably so, where leaving it NULL is.  What a rollback loses is the
"Rule start" lock badge on the settings page, which locked nothing a date bound
can lose.

The ``start_date`` values this migration wrote are also left in place on
downgrade, for the same reason: each equals a term the old maximum already took,
so restoring the NULLs would change no answer and re-running the upgrade stays
idempotent.

Revision ID: c4e1a8b70f36
Revises: b3f7c2a9d514
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4e1a8b70f36"
down_revision = "b3f7c2a9d514"
branch_labels = None
depends_on = None


#: Fold the start period into the rule's ``start_date`` -- but ONLY where it
#: is the term that actually decides the bound.
#:
#: ``_effective_start`` was ``max(opening payday, start_date,
#: start_period.start_date)``.  A start period at or below the other two
#: contributes NOTHING to that maximum, so writing it down would preserve
#: today's answer while destroying information:
#:
#:   * **40 of the 43 folded rules point at period index 0** -- the schedule's
#:     own opening.  That is not a stated start, it is the ABSENCE of one, and
#:     ``reset_pay_periods`` re-pointed the FK on every rebuild so the meaning
#:     travelled with the schedule.  Writing the opening payday as an absolute
#:     date would pin those rules to it: a schedule rebuilt to an EARLIER first
#:     payday would then generate nothing before the old opening, where the
#:     re-point produced rows from the new one.  Leaving ``start_date`` NULL is
#:     what keeps "start with the owner's schedule" saying that.
#:   * **Rule 40 (Mortgage)** carries ``start_date = 2019-01-01``, written by
#:     ``loan_recurrence_sync`` as the loan's FIRST CONTRACTUAL INSTALLMENT,
#:     beside a start period opening 2026-03-26.  The maximum is the opening,
#:     which the reader takes anyway -- so folding would overwrite a column
#:     another module OWNS with a value that changes no answer.
#:
#: The predicate is therefore "the period strictly dominates", which reproduces
#: the maximum exactly and writes the fewest rows that can: **3 of 46**, where
#: the unconditional fold wrote 43.  ``GREATEST`` skips NULLs in PostgreSQL, so
#: a rule with no ``start_date`` compares against the opening alone.
#:
#: **Rule 48 (Van Payment) IS written**, and it is the one place the collapse
#: to a single column is visible: its start period (2026-04-09) genuinely
#: dominates its loan installment (2023-03-22), so today's answer needs the
#: period -- and the next loan chokepoint will restore the installment, because
#: that module owns this column for a loan payment (which is why the form
#: renders it read-only).  Measured: the anchor is 2026-04-22 either way, since
#: the rule fires on day 22 and both bounds precede it.  ``$0.00``.
#:
#: ``p.user_id = r.user_id`` because ``calendar.period_by_id`` searched only
#: the OWNER's periods, so a cross-user FK contributed nothing to the old
#: maximum and must contribute nothing here.  None exist (all 46 rules and all
#: 62 periods are user 1); the clause costs nothing and states the reader's
#: own scope.
_FOLD_SQL = """
    UPDATE budget.recurrence_rules AS r
    SET start_date = p.start_date
    FROM budget.pay_periods AS p
    WHERE r.start_period_id = p.id
      AND p.user_id = r.user_id
      AND p.start_date > GREATEST(
              r.start_date,
              (SELECT MIN(o.start_date)
                 FROM budget.pay_periods AS o
                WHERE o.user_id = r.user_id)
          )
"""

#: Empty the folded column, scoped to the rows the fold could RESOLVE.
#:
#: The scoping is what makes the assertion below able to fire.  An
#: unconditional ``SET start_period_id = NULL`` would erase a dangling or
#: cross-user FK too -- the one case where the fold silently loses a bound --
#: and the survivor query would then find nothing and report success.  A guard
#: that runs after its own evidence is destroyed asserts nothing.
_CLEAR_SQL = """
    UPDATE budget.recurrence_rules AS r
    SET start_period_id = NULL
    FROM budget.pay_periods AS p
    WHERE r.start_period_id = p.id
      AND p.user_id = r.user_id
"""

#: The post-condition, and it is REACHABLE.  A rule still naming a start period
#: after both statements is one whose FK did not resolve to a pay period this
#: owner has -- dangling, or another user's -- so neither statement touched it
#: and its opening bound was never folded.  Continuing would leave a rule the
#: application still reads a ``start_period_id`` off, on a column nothing reads
#: any more.
_SURVIVOR_SQL = """
    SELECT id FROM budget.recurrence_rules
    WHERE start_period_id IS NOT NULL
    ORDER BY id
"""


def upgrade():
    """Fold every rule's start period into its ``start_date``, then clear it.

    Raises:
        RuntimeError: When any rule still names a start period afterwards,
            naming the offending ids.  Both statements are scoped to a FK that
            resolves to one of the OWNER's pay periods, so a survivor is a
            dangling or cross-user ``start_period_id`` -- a bound the fold
            could not read and must not silently discard.
    """
    connection = op.get_bind()
    connection.execute(sa.text(_FOLD_SQL))
    connection.execute(sa.text(_CLEAR_SQL))

    survivors = [row[0] for row in connection.execute(sa.text(_SURVIVOR_SQL))]
    if survivors:
        raise RuntimeError(
            f"recurrence rules {survivors} still name a start period after the "
            f"R7b-4 fold, so their opening bound was never read.  Both "
            f"statements join budget.pay_periods on the OWNER's rows, so a "
            f"survivor's start_period_id is dangling or belongs to another "
            f"user.  recurrence_rules_start_period_id_fkey (ON DELETE SET "
            f"NULL) makes the first unreachable and no writer produced the "
            f"second, "
            f"so this is a broken invariant rather than a case to paper over. "
            f"Diagnose with: SELECT r.id, r.start_period_id, r.user_id, "
            f"p.user_id FROM budget.recurrence_rules r LEFT JOIN "
            f"budget.pay_periods p ON p.id = r.start_period_id WHERE "
            f"r.start_period_id IS NOT NULL;"
        )


def downgrade():
    """Restore nothing, because the fold is reversible without restoring it.

    The schema is unchanged by :func:`upgrade` -- no column is added, dropped
    or re-typed -- so there is no DDL to undo.  Why the DATA is deliberately
    left as the fold wrote it, and why that is behaviour-preserving rather
    than lossy, is set out in full in this module's docstring under
    "Downgrade".  Re-deriving ``start_period_id`` from ``start_date`` would
    change an ``Every N Periods`` loan payment's phase; leaving it NULL
    provably changes nothing.

    **This body is EMPTY on purpose, and the emptiness is the whole of the
    downgrade rather than an omission.**  ``.claude/rules/database.md`` refuses
    a bare ``pass`` because a silent no-op reads exactly like a forgotten
    downgrade; it also refuses ``NotImplementedError`` where a rollback is
    legitimately possible, and this one is -- ``flask db downgrade`` was run
    against the production clone and the app resolves every rule to the same
    anchor afterwards.  Raising here would block a rollback that works.  The
    statement is therefore made where a reader looks for it, and the audit
    trail is what makes it checkable: ``budget.recurrence_rules`` is audited,
    so ``system.audit_log`` holds one row per column this migration wrote and
    the fold can be reversed by inspection if it ever has to be.
    """
