"""The two-axis recurrence backfill, as ONE statement two migrations run.

Plan step **R7c-a** wrote it and plan step **R7c-b** re-runs it, and the reason
they must run the SAME text rather than two copies is the reason R7c-b re-runs
it at all.  R7c-a's dual write refreshes ``budget.recurrence_rules``' two-axis
columns on every RULE write and on no other event, so a pay schedule rebuilt
between the two leaves moves the DERIVATION and leaves the column where it was.
Nothing reads the column in between, so nothing is wrong -- until R7c-b makes
the stored value authoritative, at which point it would FREEZE a stale first
occurrence and the cutover would silently move dates.  Re-running the identical
statement first costs nothing and makes the cutover a no-op by construction; a
second statement that merely agreed would be one more thing to keep agreeing.

**It lives under ``migrations/`` rather than in ``app/``, and that is the same
decision R7c-a made when it wrote the derivation in SQL rather than importing
the resolver.**  Plan step R7c-c DELETES ``decode_pattern`` and the closed-set
table this reads, so a backfill built on the application would stop being
runnable against a fresh database the moment that leaf shipped -- and a
migration that cannot run is a migration that cannot be trusted to have run.
This module therefore survives R7c-c: the chain must still be replayable from
empty long after the columns it reads are gone from the live schema, because
every migration between here and there sees them.

``migrations`` is a PEP 420 namespace package -- there is no ``__init__.py`` and
none is needed -- and the repo root is on ``sys.path`` wherever migrations run
(``scripts/init_database.py`` puts it there explicitly for the container, and
the Flask CLI for a host ``flask db upgrade``).  The same mechanism is what lets
three existing migrations import from ``app``.

Pure text and two thin guards: no Alembic import, no ORM, no clock.
"""

import sqlalchemy as sa

#: The rules the derivation below cannot answer for.
#:
#: Both arms are states the application refuses -- ``resolve`` will not read a
#: rule whose owner has no schedule or whose pattern it does not model -- so a
#: row satisfying either was not written by this application.
#:
#: **The two arms fail the backfill DIFFERENTLY, and an adversarial review of
#: R7c-b corrected this comment for claiming they fail the same way.**  It said
#: both "would leave a NULL", which :data:`BACKFILL_SQL` refutes for the second:
#:
#: * an UNMODELLED PATTERN reaches the ``derived`` CTE and matches no arm of its
#:   ``CASE``, so the ``UPDATE`` writes a ``NULL`` -- which
#:   :func:`verify_backfilled` would also catch, one step later and without
#:   naming the reason;
#: * an owner with NO PAY PERIODS is excluded by the ``base`` CTE's inner join
#:   on ``sched``, so the rule is not in the ``UPDATE`` at all.  At R7c-a that
#:   left the brand-new column ``NULL``; at R7c-b it leaves R7c-a's value
#:   STANDING, so ``verify_backfilled`` sees nothing wrong and this refusal is
#:   the only thing between that rule and a stored first occurrence nothing can
#:   re-derive or check -- against a schedule the owner no longer has, which
#:   ``resolve`` refuses to read a pay-period cadence against at all.
#:
#: Refusing rather than letting a ``NOT NULL`` fail is what names WHICH rule and
#: WHY: a bare ``null value in column "starts_on"`` says neither, and the value
#: the second arm would leave standing is a plausible stale date rather than an
#: obvious absence.
#:
#: **A THIRD arm was drafted and REMOVED at R7c-a, and the removal is the
#: finding.**  It refused an owner holding pay periods but no
#: ``budget.pay_schedule`` row, on the reasoning that ``derive_periods`` will
#: not build periods without a cadence.  That is false, and the code it names
#: says so: ``calendar_for`` resolves the cadence through
#: ``pay_schedule_service.resolve_cadence``, which INFERS it from the last
#: period's stored length for exactly that legacy owner.  The app serves them
#: correctly, so the migration would have aborted a deploy -- migrations run
#: from the container entrypoint -- over a state that is not a defect.
#:
REFUSE_UNDERIVABLE_SQL = """
SELECT r.id,
       CASE
         WHEN s.opening IS NULL THEN 'owner has no pay periods'
         ELSE 'pattern is not one this application models'
       END AS reason
FROM budget.recurrence_rules r
LEFT JOIN (
    SELECT user_id, MIN(start_date) AS opening
    FROM budget.pay_periods
    GROUP BY user_id
) s ON s.user_id = r.user_id
WHERE s.opening IS NULL
   OR r.pattern_id NOT IN (
       SELECT id FROM ref.recurrence_patterns
       WHERE name IN ('Every Period', 'Every N Periods', 'Monthly',
                      'Monthly First', 'Quarterly', 'Semi-Annual', 'Annual')
   )
ORDER BY r.id
"""


#: Every rule the backfill left a NULL on.
#:
#: **The three-step's VERIFY half** (``.claude/rules/database.md``,
#: ``docs/coding-standards.md``): a backfill states the count it wrote and
#: refuses to continue when a row is left behind.
#:
#: :data:`REFUSE_UNDERIVABLE_SQL` cannot stand in for it: that query grades the
#: INPUTS (does this rule have a schedule to measure against, is its pattern one
#: we model) and says nothing about what the ``UPDATE`` actually wrote.  A
#: ``ref`` table short a row -- a partial restore, or a seed whose
#: ``ON CONFLICT (name) DO NOTHING`` masked a name mismatch -- makes the id
#: subqueries answer NULL with every input arm green.
VERIFY_BACKFILL_SQL = """
SELECT id FROM budget.recurrence_rules
WHERE starts_on IS NULL
   OR unit_id IS NULL
   OR placement_id IS NULL
   OR shift_id IS NULL
ORDER BY id
"""


#: The two-axis reading of each closed-set pattern, and the first occurrence it
#: implies.
#:
#: ONE statement, read as a CTE chain rather than as four UPDATEs, so a rule
#: cannot take its unit from one pass and its date from another.
#:
#: ``pat`` resolves the seven pattern NAMES to their seeded ids in one place;
#: every ``CASE`` below compares against those columns rather than against a
#: literal id, because a ``ref`` id is a seed artifact and this file has to be
#: correct on any database the migration chain has built.
#:
#: **It is a SECOND implementation of ``recurrence._resolution``'s derivation,
#: and it is PROVEN rather than reviewed**: the write door writes the same five
#: columns from ``resolve`` on every author, so
#: ``tests/test_models/test_recurrence_two_axis_backfill.py`` POISONS all five
#: and asserts this statement puts them back -- 3,080 rules across four pay
#: cadences, 15,400 column comparisons, with the matrix chosen for the branches
#: production cannot reach (no live rule carries an interval above 1, a bound
#: past the horizon, or a day its own month cannot hold).  Two planted defects
#: were SHOWN to fail it: a ``GREATEST`` read as a ``LEAST``, and a
#: containing-paycheck search read as the schedule's opening one.
BACKFILL_SQL = """
WITH pat AS (
    SELECT
      MAX(id) FILTER (WHERE name = 'Every Period')   AS every_period,
      MAX(id) FILTER (WHERE name = 'Every N Periods') AS every_n_periods,
      MAX(id) FILTER (WHERE name = 'Monthly')        AS monthly,
      MAX(id) FILTER (WHERE name = 'Monthly First')  AS monthly_first,
      MAX(id) FILTER (WHERE name = 'Quarterly')      AS quarterly,
      MAX(id) FILTER (WHERE name = 'Semi-Annual')    AS semi_annual,
      MAX(id) FILTER (WHERE name = 'Annual')         AS annual
    FROM ref.recurrence_patterns
),
sched AS (
    SELECT p.user_id,
           MIN(p.start_date) AS opening,
           MAX(p.start_date) AS last_payday
    FROM budget.pay_periods p
    GROUP BY p.user_id
),
base AS (
    SELECT r.id,
           r.user_id,
           r.pattern_id,
           r.day_of_month,
           r.month_of_year,
           -- ``pay_schedule_service.resolve_cadence`` verbatim, INCLUDING its
           -- legacy fallback: an owner with periods but no schedule row --
           -- they generated before that table existed -- has the cadence
           -- INFERRED from the last period's stored length, because the last
           -- period's end is ``start_date + (cadence_days - 1)``.  The app
           -- serves such an owner correctly (``calendar_for`` resolves through
           -- that function), so refusing them here would abort a deploy on a
           -- state the application supports.  Pay-calendar row P8 owns the
           -- fallback; plan step C4 removes it with the column it reads, and
           -- this expression goes at the same time.
           COALESCE(sch.cadence_days, (
               SELECT (p.end_date - p.start_date) + 1
               FROM budget.pay_periods p
               WHERE p.user_id = r.user_id
               ORDER BY p.period_index DESC
               LIMIT 1
           )) AS cadence_days,
           s.last_payday,
           GREATEST(s.opening, r.start_date) AS effective,
           -- The cycle length in months, for the calendar family only.  It is
           -- ``_months.months_per_step(unit, interval_n)``: 1 for Monthly, and
           -- the interval baked into each other pattern's NAME.
           CASE
             WHEN r.pattern_id = pat.monthly     THEN 1
             WHEN r.pattern_id = pat.quarterly   THEN 3
             WHEN r.pattern_id = pat.semi_annual THEN 6
             WHEN r.pattern_id = pat.annual      THEN 12
           END AS month_step,
           pat.every_period, pat.every_n_periods, pat.monthly_first
    FROM budget.recurrence_rules r
    CROSS JOIN pat
    JOIN sched s ON s.user_id = r.user_id
    LEFT JOIN budget.pay_schedule sch ON sch.user_id = r.user_id
),
-- The rule's residue class over ABSOLUTE month ordinals, aligned to the first
-- ordinal at or above the effective month that is in it.  Postgres ``%`` keeps
-- the dividend's sign where Python's does not, so ``+ b.month_step`` normalises
-- the remainder into ``0 .. step-1`` and this expression reads as a
-- transliteration of ``_calendar_anchor``.
--
-- **That normalisation is provably a NO-OP on every reachable input**, and an
-- adversarial review of R7c-a measured it: deleting it changed 0 of 24,300
-- rules where 16 other planted mutations moved 380-19,067.  Both operands are
-- non-negative (a month ordinal is ``year * 12 + month - 1`` and
-- ``ck_recurrence_rules_moy`` bounds the month to 1-12), and where the raw
-- remainder would differ the ``CASE`` below selects the other candidate and
-- lands on the same date.  It is kept because the cost is eight characters and
-- the benefit is that a reader can check this line against the Python one
-- without reconstructing that two-step argument.
aligned AS (
    SELECT b.*,
           (
             EXTRACT(year FROM b.effective)::int * 12
             + EXTRACT(month FROM b.effective)::int - 1
           ) AS eff_ordinal,
           (
             (
               ((COALESCE(b.month_of_year, 1) - 1) % b.month_step)
               - ((
                   EXTRACT(year FROM b.effective)::int * 12
                   + EXTRACT(month FROM b.effective)::int - 1
                 ) % b.month_step)
               + b.month_step
             ) % b.month_step
           ) AS align_offset
    FROM base b
),
candidates AS (
    SELECT a.*,
           -- The rule's day, clamped to each candidate month's own length --
           -- ``_months.clamped_day``, which is what keeps a day-31 rule on the
           -- last day of every month rather than decaying to the 30th.
           make_date(
             (a.eff_ordinal + a.align_offset) / 12,
             (a.eff_ordinal + a.align_offset) % 12 + 1,
             LEAST(
               COALESCE(a.day_of_month, 1),
               EXTRACT(day FROM (
                 make_date(
                   (a.eff_ordinal + a.align_offset) / 12,
                   (a.eff_ordinal + a.align_offset) % 12 + 1, 1
                 ) + INTERVAL '1 month - 1 day'
               ))::int
             )
           ) AS cand_first,
           make_date(
             (a.eff_ordinal + a.align_offset + a.month_step) / 12,
             (a.eff_ordinal + a.align_offset + a.month_step) % 12 + 1,
             LEAST(
               COALESCE(a.day_of_month, 1),
               EXTRACT(day FROM (
                 make_date(
                   (a.eff_ordinal + a.align_offset + a.month_step) / 12,
                   (a.eff_ordinal + a.align_offset + a.month_step) % 12 + 1, 1
                 ) + INTERVAL '1 month - 1 day'
               ))::int
             )
           ) AS cand_next
    FROM aligned a
    WHERE a.month_step IS NOT NULL
),
derived AS (
    SELECT b.id,
           CASE
             -- The pay-period family: the payday of the span covering the
             -- effective start.  Past the last saved payday the schedule is
             -- projected forward at the owner's own cadence, which is
             -- ``PayCalendar._projected_after``.
             WHEN b.pattern_id IN (b.every_period, b.every_n_periods) THEN
               CASE WHEN b.effective <= b.last_payday
                 THEN (
                   SELECT MAX(p.start_date) FROM budget.pay_periods p
                   WHERE p.user_id = b.user_id AND p.start_date <= b.effective
                 )
                 ELSE b.last_payday + (
                   ((b.effective - b.last_payday) / b.cadence_days)
                   * b.cadence_days
                 )
               END
             -- The first-of-month family: the 1st of the earliest month whose
             -- own first payday falls on or after the effective start, else
             -- the 1st of the month after the effective one.
             WHEN b.pattern_id = b.monthly_first THEN
               COALESCE(
                 (
                   SELECT date_trunc('month', p.start_date::timestamp)::date
                   FROM budget.pay_periods p
                   WHERE p.user_id = b.user_id
                     AND p.start_date >= b.effective
                     AND (
                       SELECT MIN(q.start_date) FROM budget.pay_periods q
                       WHERE q.user_id = b.user_id
                         AND date_trunc('month', q.start_date::timestamp)
                             = date_trunc('month', p.start_date::timestamp)
                     ) >= b.effective
                   ORDER BY p.start_date
                   LIMIT 1
                 ),
                 (date_trunc('month', b.effective::timestamp)
                  + INTERVAL '1 month')::date
               )
             -- The calendar family.
             ELSE (
               SELECT CASE WHEN c.cand_first >= b.effective
                           THEN c.cand_first ELSE c.cand_next END
               FROM candidates c WHERE c.id = b.id
             )
           END AS starts_on
    FROM base b
)
UPDATE budget.recurrence_rules r
SET starts_on = d.starts_on,
    unit_id = (
      SELECT u.id FROM ref.recurrence_units u
      WHERE u.name = CASE
        WHEN r.pattern_id = (SELECT MAX(id) FROM ref.recurrence_patterns
                             WHERE name = 'Annual') THEN 'year'
        WHEN r.pattern_id IN (SELECT id FROM ref.recurrence_patterns
                              WHERE name IN ('Every Period',
                                             'Every N Periods')) THEN 'period'
        ELSE 'month' END
    ),
    placement_id = (
      SELECT pl.id FROM ref.period_placements pl
      WHERE pl.name = CASE
        WHEN r.pattern_id = (SELECT MAX(id) FROM ref.recurrence_patterns
                             WHERE name = 'Monthly First')
        THEN 'period_starting_on_or_after' ELSE 'containing_date' END
    ),
    shift_id = (SELECT id FROM ref.business_day_shifts WHERE name = 'none'),
    -- Only where the anchor month actually clamped the authored day: a value
    -- at or below the day ``starts_on`` carries would be a second statement of
    -- a day the date already holds.  The first-of-month family reads no day at
    -- all, so it is excluded rather than compared.
    nominal_day = CASE
      WHEN r.pattern_id NOT IN (
             SELECT id FROM ref.recurrence_patterns
             WHERE name IN ('Every Period', 'Every N Periods', 'Monthly First')
           )
       AND r.day_of_month > EXTRACT(day FROM d.starts_on)
      THEN r.day_of_month
    END
FROM derived d
WHERE d.id = r.id
"""


def refuse_underivable(bind) -> None:
    """Raise when any rule has no derivable first occurrence.

    **A named function rather than an inline block, so a test can drive it.**
    Its predecessor was inline in R7c-a's ``upgrade``, where the only way to
    exercise the RAISE was to run real DDL inside an xdist worker and move the
    whole session's schema.  A refusal nothing executes is a refusal nobody has
    seen work -- and this one is the leaf's only protection against seating a
    recurring bill on a fabricated date.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every offending rule id and why it is offending.
    """
    underivable = bind.execute(sa.text(REFUSE_UNDERIVABLE_SQL)).all()
    if not underivable:
        return
    raise RuntimeError(
        "cannot derive a first occurrence for recurrence rule(s) "
        + "; ".join(f"id={row.id} ({row.reason})" for row in underivable)
        + ".  Both states are ones app.services.recurrence.resolve refuses, "
        "so no application path wrote them; deriving a plausible date for one "
        "would seat a recurring bill on a cadence the rule never named.  "
        "Repair or delete the row and re-run."
    )


def verify_backfilled(bind) -> None:
    """Raise when the backfill left a NULL on any rule.

    The three-step's VERIFY half; see :data:`VERIFY_BACKFILL_SQL` for why the
    input refusal above cannot stand in for it.  Named for the same reason
    :func:`refuse_underivable` is.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every rule left holding a NULL, and the SELECT
            that shows which column.
    """
    unfilled = bind.execute(sa.text(VERIFY_BACKFILL_SQL)).scalars().all()
    if not unfilled:
        return
    raise RuntimeError(
        "the two-axis backfill left a NULL on recurrence rule(s) "
        + ", ".join(str(rule_id) for rule_id in unfilled)
        + ".  Every row that clears the refusal has a derivable first "
        "occurrence, so a NULL here means the statement could not resolve a "
        "``ref`` id -- a seed short a row, or a partial restore.  Diagnose "
        "with: SELECT id, starts_on, unit_id, placement_id, shift_id FROM "
        "budget.recurrence_rules WHERE starts_on IS NULL OR unit_id IS NULL "
        "OR placement_id IS NULL OR shift_id IS NULL;"
    )
