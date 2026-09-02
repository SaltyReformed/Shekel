"""A pay period is ONE fact: drop end_date and period_index

Revision ID: b7a41e2c9d63
Revises: c9a4e7b21d58
Create Date: 2026-09-01 21:40:00.000000

Plan step ``pay_calendar:C4-c``.  Closes findings **P1**, **P4**, **P5**,
**P9**, and with them **P33**.

**What was wrong.**  ``budget.pay_periods`` stored three values per row and
only ``start_date`` was a fact -- the day money arrived.  ``end_date`` is
``lead(start_date) - 1`` and ``period_index`` is ``row_number() - 1``, both
DERIVED from the owner's payday set and both stored beside it with nothing in
the schema reconciling them.  That is finding **P1**, and every other symptom
the arc recorded is one face of it: a stored end BELOW the next payday is a
GAP, a day funded by no paycheck; at or ABOVE it is an OVERLAP, a day funded by
two; and a stored ordinal out of payday order is the balance resolver walking
money out of calendar order.

Because the schema could not make the three agree, the application grew five
runtime fences policing one functional dependency
(``docs/plans/implementation_plan_pay_calendar.md`` section 1).  None of them
has a subject after this revision.

**What this does.**  Drops ``end_date`` and ``period_index``, and the three
constraints that exist only to bound them:

  * ``uq_pay_periods_user_index`` -- a duplicate ORDINAL.  An ordinal is a
    position in a sort now; nothing can repeat it.
  * ``ck_pay_periods_positive_index`` -- a NEGATIVE ordinal.  ``row_number()``
    starts at 1 and the derivation subtracts one, so 0 is the floor by
    construction.
  * ``ck_pay_periods_date_order`` -- ``start_date < end_date``.  This is
    finding **P9**: it forbids a ONE-DAY pay period, which two paydays a day
    apart legitimately produce.  ``budget.pay_schedule.cadence_days`` has
    always accepted 1 and the derivation has always handled it; what could not
    hold one was a stored end.  Dropping the column legalises the cycle, which
    is also what closes finding **P33** -- an owner holding cadence 1 met a
    500 on ``/grid`` and ``/dashboard``, permanently, because the rolling
    top-up ran on a read path and the writer refused to materialise the value
    the schedule column admitted.

``uq_pay_periods_user_start`` STAYS and is now the table's only uniqueness
rule: one period per owner per opening day, which is the payday model's exact
key.  It also still leads with ``user_id``, so ``fk_pay_periods_schedule``'s
delete-time check keeps the referencing-side index that revision
``f1c8b3d5e920`` declined to add a second copy of.

**Provably free on this data, measured rather than argued.**  Against
production at ``f1c8b3d5e920`` (2026-09-01): 63 pay periods, and
comparing every stored value with the derivation over the owner's own paydays
gives **0** end mismatches, **0** index mismatches, **0** gaps, **0** overlaps
and **0** one-day periods.  So no consumer's answer moves: every reader was
already taking the derived value (the ``C4-a`` leaf span moved the last of them
at ``95b2dc67``), and the columns this drops held exactly what that derivation
computes.

**The downgrade restores the same bytes on real data, measured.**  Production's
dump was restored into a probe database, brought to this revision, downgraded
one step, and the resulting ``(id, end_date, period_index)`` triples diffed
against ``shekel-prod-db``: **63 rows, byte-identical, zero differences**.
All three constraints come back with them.

*Re-run 2026-09-02 across the RE-PARENTED edge* -- ``c9a4e7b21d58`` became this
revision's parent when ``balance:X-au-g-2c-2`` merged first, and an edge nothing
has walked is exactly where a downgrade breaks -- from the 2026-09-01 production
dump: ``flask db upgrade`` from empty over all 171 revisions, then
``downgrade c9a4e7b21d58``, then ``upgrade`` again.  Same 63 rows, same zero
differences, and the pair is a fixed point: the two columns and the three
constraints are gone again afterwards.

**That run is BLIND to the one defect worth fearing, and it was given a control
rather than trusted.**  Production's schedule is perfectly regular -- 63
paydays, all fourteen days apart -- so ``lead(start_date) - 1`` and
``start_date + (cadence_days - 1)`` give the SAME answer on every row.  A
rebuild that took the projection branch everywhere, which is precisely the
pre-normalization defect this arc exists to remove, reproduces that clone
byte-identically.  So the round trip was re-run with one OFF-CADENCE payday
planted -- 2028-08-31, three weeks after production's last -- where the two
branches differ by seven days.  The rebuilt 2028-08-10 period came back ending
**2028-08-30**, its successor's payday minus a day, not the 2028-08-23 the
projection would have written; and the same diff against production then
reported the difference rather than staying silent.  **A comparison whose only
reachable answer is the one you expect proves nothing**, and this one was shown
able to say DISAGREE before its agreement was read as evidence.

**It is still NOT unconditionally lossless, and this says so rather than
claiming a reversibility it does not have.**  Three things it cannot promise:

  * **A one-day period breaks the re-added CHECK.**  ``start_date < end_date``
    is exactly what this revision legalises past, so an owner who records two
    paydays a day apart AFTER this upgrade cannot be downgraded.  *Driven
    rather than asserted*: on the same probe, inserting the payday 2028-08-11
    beside production's last one (2028-08-10) and downgrading raises
    ``psycopg2.errors.CheckViolation: check constraint
    "ck_pay_periods_date_order" of relation "pay_periods" is violated by some
    row``.  PostgreSQL's DDL is transactional, so the failed downgrade rolls
    back whole -- the probe was still at this revision with 64 rows afterwards
    -- which makes the abort a refusal rather than a half-applied schema.  That
    is the correct outcome: the alternative is inventing a day of coverage the
    owner never had.  And it is a real state rather than a hypothetical one,
    because a cadence of 1 is now a legal thing to choose.
  * **The LAST row's rebuilt end is a PROJECTION.**  Every other end is
    dictated by the next payday and is a fact; the last has no successor, so it
    is ``start_date + (cadence_days - 1)`` read from ``budget.pay_schedule`` at
    DOWNGRADE time.  If the cadence has been changed since the upgrade, that
    end is the new cadence's, not the one the row was written under -- and the
    live mechanism for that is the ordinary cadence rule, not a defect: any
    batch that RECORDS a payday persists the cadence it recorded at
    (``pay_period_write._apply``), so generate, regenerate and reset all move
    it legitimately.  *This paragraph cited finding **P12** -- a generate post
    naming an existing payday reaching ``upsert_schedule`` with nothing
    created -- and that was CLOSED at C3-b (``7e3fb33b``); ``_apply`` now gates
    the call on ``change.recording`` being non-empty.  Corrected by an
    adversarial review, 2026-09-01.*  The application reads the same projection
    for that period either way, so the rebuilt column agrees with what every
    screen shows -- it just is not a photograph of what the column held
    before.
  * **The downgrade WRITES an audit row per pay period.**
    ``budget.pay_periods`` carries ``audit_pay_periods AFTER INSERT OR DELETE
    OR UPDATE``, and the rebuild is one ``UPDATE`` over every row -- 63 of them
    on production -- attributed to no user, because ``app.current_user_id`` is
    unset under Alembic.  A down/up cycle is therefore a fixed point of the
    SCHEMA, which is what the round trip above measures, and not of the
    database.  Nothing reads those rows for a figure; they are noise in a log a
    person reads.
  * **The physical column ORDER changes.**  ``ADD COLUMN`` appends, so the
    rebuilt pair sits after ``created_at`` rather than in its original
    positions 4 and 5 (observed on the probe).  Every consumer in ``app/``
    names its columns, and ``pg_dump`` writes an explicit column list, so
    nothing reads position -- but a ``SELECT *`` in a hand-run script would
    see a different tuple shape, and that is worth one sentence rather than a
    surprise.

**The backfill INNER JOINs ``budget.pay_schedule`` deliberately.**  An owner
holding paydays without a cadence row is unstorable since ``f1c8b3d5e920``, and
that key is still in place when this downgrade runs (Alembic downgrades
newest-first, and that revision is an earlier one).  So the join loses nothing; where
it somehow would, the row keeps a NULL ``end_date`` and the following
``SET NOT NULL`` aborts loudly rather than quietly writing an invented span.

**Chain order keeps ``f75485db6757`` and ``e5f6a7b8c9d0`` working.**  Both add
constraints this revision drops -- the unique ordinal and the two CHECKs -- and
both have downgrades that drop them again.  Downgrades run newest-first, so
this revision's ``downgrade()`` has re-added all three by the time either of
those runs, and their own ``DROP``s find what they expect.  Tested rather than
argued -- but only one of the two can be DRIVEN, and
``tests/test_models/test_c4c_pay_period_is_one_fact.py`` says which.
``f75485db6757``'s ``downgrade()`` is run after this one's and its restored
index read from ``pg_indexes``.  ``e5f6a7b8c9d0``'s is not: it drops
constraints across eleven tables and one of them was itself dropped by the
LATER ``f2b7c40d918e``, so at this point in a partial chain it dies on an
object that has nothing to do with pay periods.  Its two CHECK names are read
out of its source and asserted PRESENT instead, which is the claim this
revision actually owes it.

**Locking.**  ``DROP COLUMN`` is a catalog-only operation in PostgreSQL -- the
column is marked dropped and the heap is untouched -- so it takes ACCESS
EXCLUSIVE for the length of the catalog update and nothing more.  Production
holds 63 rows; the downgrade's ``ADD COLUMN`` + ``UPDATE`` + two
``ADD CONSTRAINT`` scans are equally instantaneous at that size.

Review: PENDING -- the revision drops two columns and three constraints from
``budget.pay_periods``, and its ``downgrade`` re-adds exactly those five
objects and no others.  The pre-state measurements were taken 2026-09-01
against ``shekel-prod-db``; every round-trip figure, the off-cadence control
and the one-day refusal were RE-TAKEN 2026-09-02 on the re-parented chain,
against a probe restored from production's own 2026-09-01 dump.
"""
import logging

from alembic import op
import sqlalchemy as sa

logger = logging.getLogger("alembic.runtime.migration")


# revision identifiers, used by Alembic.
revision = 'b7a41e2c9d63'
# RE-PARENTED onto ``c9a4e7b21d58`` (balance:X-au-g-2c-2, PR #190) when that
# branch merged first.  Both were authored against ``b8e4c1f7a903``, and two
# revisions chaining off one parent is a CLEAN git merge that leaves the chain
# with two heads -- a break git cannot see and that surfaces only at
# ``flask db upgrade`` and in ``scripts/build_test_template.py``.
down_revision = 'c9a4e7b21d58'
branch_labels = None
depends_on = None


#: Rebuild both derived columns from the paydays, for :func:`downgrade`.
#:
#: This IS the definition the upgrade deletes, written as the SQL the plan
#: states it in::
#:
#:     period_index = row_number() over (partition by user_id
#:                                       order by start_date) - 1
#:     end_date     = coalesce(lead(start_date) over (...) - 1,
#:                             start_date + cadence_days - 1)
#:
#: ``lead(start_date) - 1`` and not ``- INTERVAL '1 day'``: the interval form
#: returns a ``timestamp without time zone``, so the expression's type stops
#: matching the column's and the statement leans on an assignment cast to get
#: back to a ``DATE``.  *It does NOT place a day's money wrongly, and an
#: earlier draft of this line said it did* (adversarial review, 2026-09-01):
#: PostgreSQL applies that cast in an ``UPDATE``'s ``SET`` list and stores the
#: identical day, measured on 18.4.  The integer form is right because it needs
#: no cast at all, which is a smaller claim than the one this used to make --
#: and the money claim was true of a DIFFERENT check, ``pay_period_write.
#: _reject_undatable_payday``, where a ``datetime`` really does flow through
#: Python arithmetic and get compared.
#:
#: The window is computed in a subquery keyed on ``id`` because a window
#: function may not appear in an ``UPDATE``'s ``SET`` list.
#:
#: **Both windows are ONE named ``WINDOW`` clause, and the schedule is an
#: explicit ``JOIN`` inside the subquery**, which is a shape rather than a
#: preference (adversarial review, 2026-09-01).  The first draft wrote the
#: partition out twice and put the owner-scoping predicate in a comma join's
#: ``WHERE``, four lines below the table it scoped.  Two mutations of that
#: draft were driven and BOTH corrupted spans while every test stayed green:
#: dropping ``AND sch.user_id = p.user_id`` wrote one owner's forecast cadence
#: onto another owner's row, and dropping ``PARTITION BY`` from the ``lead``
#: window alone gave a period an end on a day a DIFFERENT owner was paid.
#: Naming the window once makes the second unconstructible -- there is no
#: second copy to forget -- and the ``JOIN`` puts the first where it cannot be
#: read as an afterthought.
#:
#: Ordering on ``start_date`` is this arc's rule: the payday is the fact, so
#: ordering by the ordinal would sort by the answer -- and at this point in the
#: downgrade there is no ordinal to sort by.
_REBUILD_DERIVED_COLUMNS_SQL = (
    "UPDATE budget.pay_periods p "
    "   SET period_index = d.derived_index, "
    "       end_date = COALESCE(d.next_payday - 1, "
    "                           p.start_date + (d.cadence_days - 1)) "
    "  FROM ( "
    "    SELECT pp.id, "
    "           lead(pp.start_date) OVER w AS next_payday, "
    "           (row_number() OVER w) - 1 AS derived_index, "
    "           sch.cadence_days AS cadence_days "
    "      FROM budget.pay_periods pp "
    "      JOIN budget.pay_schedule sch ON sch.user_id = pp.user_id "
    "    WINDOW w AS (PARTITION BY pp.user_id ORDER BY pp.start_date) "
    "  ) d "
    " WHERE d.id = p.id"
)


#: Every row whose STORED pair disagrees with the derivation over its owner's
#: own paydays -- the premise the docstring's "provably free" rests on, asked
#: of the database this revision is actually running against.
#:
#: The same window as :data:`_REBUILD_DERIVED_COLUMNS_SQL`, which is the point:
#: what the downgrade would write back is compared against what is stored, so a
#: row this reports is a row whose end or ordinal the drop is about to discard.
_STORED_VS_DERIVED_SQL = (
    "SELECT p.id, p.user_id, p.start_date, p.end_date, p.period_index, "
    "       d.derived_end, d.derived_index "
    "  FROM budget.pay_periods p "
    "  JOIN ( "
    "    SELECT pp.id, "
    "           COALESCE(lead(pp.start_date) OVER w - 1, "
    "                    pp.start_date + (sch.cadence_days - 1)) AS derived_end, "
    "           (row_number() OVER w) - 1 AS derived_index "
    "      FROM budget.pay_periods pp "
    "      JOIN budget.pay_schedule sch ON sch.user_id = pp.user_id "
    "    WINDOW w AS (PARTITION BY pp.user_id ORDER BY pp.start_date) "
    "  ) d ON d.id = p.id "
    " WHERE p.end_date <> d.derived_end "
    "    OR p.period_index <> d.derived_index "
    " ORDER BY p.user_id, p.start_date"
)


def _report_stored_versus_derived(bind):
    """Log every row whose stored pair disagrees with its owner's paydays.

    **The docstring's "provably free" claim, asked of THIS database rather than
    quoted from one measurement** (adversarial review, 2026-09-01).  Production
    was measured clean on 2026-09-01 -- 63 rows, zero mismatches -- but a
    developer restoring a pre-C3-b dump, or upgrading a hand-driven database,
    runs the same one-way ``DROP COLUMN`` over data nobody censused.  Without
    this the disagreement is normalised away in silence and the ``downgrade``
    will never reproduce it.

    **It REPORTS and proceeds rather than refusing**, which is
    ``f2b7c40d918e``'s ruled shape for the same problem -- a derived column
    being dropped whose stored value may disagree.  A row here is an owner
    whose schedule was ALREADY wrong, and this revision is what stops it being
    expressible; refusing would leave them broken and block the deploy over a
    state that moves no figure, since every reader took the derivation at
    ``95b2dc67``.  ``f75485db6757`` raises instead, and correctly: there the
    disagreement makes its new constraint unsatisfiable, so the migration
    cannot proceed at all.  Here it can.

    Args:
        bind: The Alembic connection.
    """
    disagreeing = bind.execute(sa.text(_STORED_VS_DERIVED_SQL)).fetchall()
    for row in disagreeing:
        logger.warning(
            "C4-c: pay period %s (user %s, payday %s) is stored ending %s at "
            "ordinal %s, where that owner's own paydays derive %s and %s.  "
            "The stored pair is being dropped and is not carried forward; the "
            "application has read the derived value since 95b2dc67, so no "
            "figure moves -- but this schedule was already disagreeing with "
            "itself, which is finding P1.",
            row.id, row.user_id, row.start_date, row.end_date,
            row.period_index, row.derived_end, row.derived_index,
        )
    if not disagreeing:
        logger.info(
            "C4-c: every stored end and ordinal agrees with the owner's own "
            "paydays; the drop discards nothing that was not already derived."
        )


def upgrade():
    """Drop the two derived columns and the three constraints that bound them.

    Constraints first, columns second.  PostgreSQL would drop a constraint
    depending on a dropped column on its own, but naming all five objects is
    what makes this revision's ``downgrade`` provably the inverse: the reader
    can count them.

    :func:`_report_stored_versus_derived` runs FIRST, so the one thing this
    revision destroys is written down before it goes.
    """
    _report_stored_versus_derived(op.get_bind())
    op.drop_constraint(
        'uq_pay_periods_user_index', 'pay_periods',
        schema='budget', type_='unique',
    )
    op.drop_constraint(
        'ck_pay_periods_positive_index', 'pay_periods',
        schema='budget', type_='check',
    )
    op.drop_constraint(
        'ck_pay_periods_date_order', 'pay_periods',
        schema='budget', type_='check',
    )
    op.drop_column('pay_periods', 'period_index', schema='budget')
    op.drop_column('pay_periods', 'end_date', schema='budget')


def downgrade():
    """Rebuild both columns from the paydays, then re-add the three constraints.

    Value-lossless for every schedule the application can produce and NOT
    unconditionally lossless; the module docstring states which two promises
    are missing and why.  The columns are added NULLABLE, filled by
    :data:`_REBUILD_DERIVED_COLUMNS_SQL`, and only then made ``NOT NULL`` --
    so a row the rebuild could not reach aborts the downgrade by name rather
    than being written a made-up span.
    """
    op.add_column(
        'pay_periods',
        sa.Column('end_date', sa.Date(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'pay_periods',
        sa.Column('period_index', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(_REBUILD_DERIVED_COLUMNS_SQL)
    op.alter_column(
        'pay_periods', 'end_date', nullable=False, schema='budget',
    )
    op.alter_column(
        'pay_periods', 'period_index', nullable=False, schema='budget',
    )
    op.create_check_constraint(
        'ck_pay_periods_date_order', 'pay_periods',
        'start_date < end_date', schema='budget',
    )
    op.create_check_constraint(
        'ck_pay_periods_positive_index', 'pay_periods',
        'period_index >= 0', schema='budget',
    )
    op.create_unique_constraint(
        'uq_pay_periods_user_index', 'pay_periods',
        ['user_id', 'period_index'], schema='budget',
    )
