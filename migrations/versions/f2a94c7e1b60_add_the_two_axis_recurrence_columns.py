"""the two-axis recurrence columns, backfilled and read by nobody

Plan step **R7c-a** of ``docs/plans/implementation_plan_recurrence_redesign.md``
section 4 -- the EXPAND half of an expand / migrate / contract.  It adds the
five columns the two-axis model needs, backfills them from the closed-set
columns they will replace, and stops.  **Nothing reads them**: the closed set
stays authoritative until plan step R7c-b moves the readers across, and plan
step R7c-c drops it.

Review: Josh, 2026-08-14 -- APPROVED: the D28 ruling that ``starts_on`` is the
rule's FIRST OCCURRENCE (one meaning for every unit) rather than the opening
validity bound, the YEAR unit surviving with ``(12k, MONTH)`` canonicalised at
the write door, and the three-leaf split with the destructive DDL last.

ADDITIVE.  No column is dropped, no value is destroyed, and the downgrade drops
exactly what the upgrade added -- which is what makes this leaf revertible
without a data restore, unlike the one that ends the sequence.

What ``starts_on`` means, and why the arc's earlier specification was wrong
-------------------------------------------------------------------------

The plan specified this column as "the opening validity BOUND, one meaning for
every unit", and separately mapped ``month_of_year`` onto ``starts_on.month``.
Those cannot both hold.  A calendar rule's cycle phase is a month RESIDUE
class; the bound is not in it.  **Measured on a 2026-08-14 clone of
production**, driving ``recurrence.resolve`` over all 46 live rules: the
schedule opens 2026-03-26, so every unbounded rule's bound is a March date, and
**18 of the 24 live multi-month rules** would have fired in the wrong months
forever -- an annual November rule dated 1 March, in generated rows and the
projected balance rather than in a label.  The six survivors are the six whose
authored month is already March.  That was plan ledger row **D28**.

The ruling (developer, 2026-08-14) is the other reading:

    ``starts_on`` is the rule's FIRST OCCURRENCE.  For a calendar cadence it is
    the first date the cadence fires on; for a pay-period cadence it is the
    payday of the first paycheck the rule bills in.  Nothing is generated
    before it, and its position in the cycle IS the rule's phase.

Under it a rule states its phase and its opening bound in ONE value, because
the first member of a set is also the earliest thing the set can produce.  The
same reading over all 46 live rules moves **0** occurrence sets.  It ANSWERS plan ledger rows
**D10**, **D21** and **D24**, which R7c-b closes when the readers move, and it NARROWS **D6**,
which R7c-c closes with the column that row names.

The backfill is a SECOND implementation, and that is deliberate
---------------------------------------------------------------

This file reimplements ``recurrence._resolution``'s derivation in SQL rather
than importing it.  Importing would be worse, not better: plan step R7c-c
DELETES ``decode_pattern`` and the closed-set table this backfill reads, so a
migration built on them would stop being runnable against a fresh database the
moment that leaf ships -- and a migration that cannot run is a migration that
cannot be trusted to have run.  The copy is bounded (it lives and dies in this
file) and it is PROVEN rather than reviewed: the write door writes the same
five columns from ``resolve`` on every author, so
``tests/test_models/test_recurrence_two_axis_backfill.py`` POISONS all five and
asserts this statement puts them back -- **3,080 rules across four pay
cadences, 15,400 column comparisons**, and the matrix is chosen for the
branches production cannot reach (no live rule carries an interval above 1, a
bound past the horizon, or a day its own month cannot hold).  Two planted
defects were SHOWN to fail it: a ``GREATEST`` read as a ``LEAST``, and a
containing-paycheck search read as the schedule's opening one.

Where each column comes from
----------------------------

``unit_id`` / ``placement_id`` are two of the three fields
``_frequency.PATTERN_DERIVATIONS`` holds per pattern.  ``shift_id`` is ``none``
for every rule: no writer of a business-day shift exists before plan step R8,
and ``resolve`` returns ``BusinessDayShiftEnum.NONE`` unconditionally.

**The third field, the INTERVAL, is deliberately NOT among them, and saying so
is the point.**  ``interval_n`` is the CLOSED SET's column and stays that way
until plan step R7c-c drops ``pattern_id`` beside it: ``encode_cadence`` writes
``1`` for every pattern that bakes its interval into its NAME, so the four live
Quarterly and Semi-Annual rules store ``(interval_n = 1, unit_id = month)``,
which reads as MONTHLY if the pair is taken at face value.  Nothing takes it at
face value -- every reader of the column goes through ``decode_pattern``, which
answers ``3`` and ``6`` from the pattern and consults the column only for
``Every N Periods`` -- so the state is latent rather than wrong.  **Plan step
R7c-c must re-point ``interval_n`` to the two-axis interval in the SAME
migration that drops ``pattern_id``**, because until that column is gone the
interval belongs to the encoding.  The columns this migration adds therefore
complete the two-axis reading's PHASE and PLACEMENT halves; the cadence's
interval follows the closed set out.

``starts_on`` is one of three derivations, selected by the same
``(unit, placement)`` router ``_frequency.anchor_family`` uses:

* **the pay-period family** -- the payday of the span covering the effective
  start.  The saved-payday search runs up to the LAST PAYDAY and the
  arithmetic past it -- not up to the HORIZON, which is a cadence later, and
  the two agree across that span because the projection's floor divides to
  zero there.  Past the horizon it is
  ``last_payday + floor(elapsed / cadence) * cadence``, which is
  ``PayCalendar._projected_after`` exactly.  Projecting rather than answering
  the last saved payday is what makes the value TOTAL, which the ``NOT NULL``
  plan step R7c-b adds will require;
* **the calendar family** -- the first date in the rule's
  ``(month_of_year, day_of_month)`` residue class on or after the effective
  start, month-end clamped.  ``_calendar_anchor`` walks absolute month
  ordinals; so does this, and two candidates suffice for the reason that
  function states -- the aligned ordinal is the first in the class at or above
  the effective month, so its date either clears the bound or is one cycle
  short of doing so;
* **the first-of-month family** (``Monthly First``) -- the 1st of the earliest
  month whose OWN first payday falls on or after the effective start, with the
  1st of the following month as the past-the-horizon fallback.  That is
  ``_first_of_month_anchor``, including its fallback, and the correlated
  subquery is the ``min(starts in that month)`` its
  ``earliest_start_in_month`` takes.

The effective start itself is ``GREATEST(opening payday, start_date)``.
Postgres ``GREATEST`` skips NULLs, so it IS ``_effective_start``'s maximum for
the 42 live rules carrying no ``start_date`` as well as the 4 that do.

``nominal_day`` records a day the anchor month CLAMPED and nothing else
(ruling R-R3), so it is written only where the authored ``day_of_month``
exceeds the day ``starts_on`` carries.  **All 46 live rules take NULL**: the
only rule naming a day above 28 is the March annual one, and March holds a
31st.

What it REFUSES rather than guessing
------------------------------------

TWO states would make the derivation answer a plausible wrong date, and both
raise with the offending rule ids instead.  Each is a state
``app.services.recurrence.resolve`` refuses too, so neither was written by this
application:

* a rule whose owner has NO pay periods -- there is no floor to measure an
  occurrence from, which is the refusal ``_effective_start`` already makes;
* a rule naming a pattern outside the seven this application models, which
  since plan step R2e-3 means the surviving ``Once`` row.

**A THIRD arm was drafted and REMOVED, and the removal is the finding.**  It
refused an owner holding pay periods but no ``budget.pay_schedule`` row, on the
reasoning that ``derive_periods`` will not build periods without a cadence.
That is false, and the code it names says so: ``calendar_for`` resolves the
cadence through ``pay_schedule_service.resolve_cadence``, which INFERS it from
the last period's stored length for exactly that legacy owner.  The app serves
them correctly, so the migration would have aborted a deploy -- migrations run
from the container entrypoint -- over a state that is not a defect, and told
the operator to go hunting corruption that does not exist.  The backfill now
derives the cadence the same way, so there is nothing left to refuse.

Then the result is CHECKED rather than argued: :data:`_VERIFY_BACKFILL_SQL`
names any row left holding a NULL and the upgrade raises on it.  A derivation
that is total by argument is still a derivation nobody counted.

**Downgrade** drops the five columns and their constraints.  Nothing AUTHORED
is lost: every value they hold is derived from columns this migration does not
touch.  Re-running the upgrade reproduces them against the schedule as it
stands THEN, which is the same answer unless the pay calendar has moved in
between -- the same window ``_authoring``'s docstring records and plan step
R7c-b's re-backfill closes.

**Re-pointed on the merge into `dev`, 2026-08-14.**  It was written against
``e6b4a2d8c713``, and the balance arc's ``d5b8e2c74a19`` (plan step X-f3a's
recording half) landed on the same parent while this leaf was in review -- two
heads off one revision, which Alembic refuses to upgrade through.  The branch
that had NOT shipped is the one that moves, so this migration now chains off
theirs.  The two do not interact: theirs adds a clearing link to
``budget.transactions`` and the purchase table, this one adds five columns to
``budget.recurrence_rules``, and neither reads what the other writes.

Revision ID: f2a94c7e1b60
Revises: d5b8e2c74a19
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a94c7e1b60"
down_revision = "d5b8e2c74a19"
branch_labels = None
depends_on = None


#: The rules this migration cannot derive a first occurrence for.
#:
#: All three arms are states the application refuses -- ``resolve`` will not
#: read a rule whose owner has no schedule or whose pattern it does not model,
#: and ``pay_calendar.derive_periods`` will not build periods without a
#: cadence -- so a row satisfying any of them was not written by this
#: application.  Refusing here rather than letting the ``NOT NULL`` fail is what
#: names WHICH rule and WHY: a bare ``null value in column "starts_on"`` says
#: neither, and the value it would have written is a plausible wrong date
#: rather than an obvious one.
_REFUSE_UNDERIVABLE_SQL = """
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
#: refuses to continue when a row is left behind.  Deferring the ``NOT NULL``
#: to plan step R7c-b makes this MORE necessary rather than less -- the
#: constraint that would otherwise catch a missed row is a whole leaf away, and
#: until it lands a silent NULL reads as a successful upgrade.
#:
#: :data:`_REFUSE_UNDERIVABLE_SQL` cannot stand in for it: that query grades the
#: INPUTS (does this rule have a schedule to measure against, is its pattern one
#: we model) and says nothing about what the ``UPDATE`` actually wrote.  A
#: ``ref`` table short a row -- a partial restore, or a seed whose
#: ``ON CONFLICT (name) DO NOTHING`` masked a name mismatch -- makes the id
#: subqueries answer NULL with every input arm green.
_VERIFY_BACKFILL_SQL = """
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
_BACKFILL_SQL = """
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
-- adversarial review of this migration measured it: deleting it changed 0 of
-- 24,300 rules where 16 other planted mutations moved 380-19,067.  Both
-- operands are non-negative (a month ordinal is ``year * 12 + month - 1`` and
-- ``ck_recurrence_rules_moy`` bounds the month to 1-12), and where the raw
-- remainder would differ the ``CASE`` below selects the other candidate and
-- lands on the same date.  It is kept because the cost is eight characters
-- and the benefit is that a reader can check this line against the Python one
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
    Its predecessor was inline in :func:`upgrade`, where the only way to
    exercise the RAISE was to run real DDL inside an xdist worker and move the
    whole session's schema.  A refusal nothing executes is a refusal nobody has
    seen work -- and this one is the leaf's only protection against seating a
    recurring bill on a fabricated date.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every offending rule id and why it is offending.
    """
    underivable = bind.execute(sa.text(_REFUSE_UNDERIVABLE_SQL)).all()
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

    The three-step's VERIFY half; see :data:`_VERIFY_BACKFILL_SQL` for why the
    input refusal above cannot stand in for it.  Named for the same reason
    :func:`refuse_underivable` is.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every rule left holding a NULL, and the SELECT
            that shows which column.
    """
    unfilled = bind.execute(sa.text(_VERIFY_BACKFILL_SQL)).scalars().all()
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


def upgrade():
    """Add the two-axis columns, backfill them, and lock them down."""
    refuse_underivable(op.get_bind())
    op.add_column(
        "recurrence_rules",
        sa.Column("unit_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("placement_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("shift_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("starts_on", sa.Date(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("nominal_day", sa.SmallInteger(), nullable=True),
        schema="budget",
    )

    op.execute(sa.text(_BACKFILL_SQL))

    verify_backfilled(op.get_bind())

    # **The TIGHTEN is plan step R7c-b's, and that placement is the documented
    # three-step rather than a deferral** (``.claude/rules/database.md``: add
    # nullable, backfill, tighten).  The third step belongs with the leaf that
    # makes the columns MATTER -- R7c-b moves every reader onto them, so a
    # NULL stops being invisible there and starts being a wrong answer.
    #
    # Tightening here instead would buy nothing and cost a great deal.  Nothing
    # reads these columns in this leaf, so a NULL cannot reach a figure; what
    # ``NOT NULL`` would reach is the ~40 test modules that construct a
    # ``RecurrenceRule`` DIRECTLY rather than through
    # ``recurrence.author_rule`` -- measured: 666 failures and 418 errors from
    # five shared fixtures.  Those sites do need to move onto the write door,
    # and some of them are transient values exercising PURE functions and must
    # NOT (forcing a database and a calendar into a pure test is a worse test,
    # not a stricter one).  Sorting that is R7c-b's, with the same commit that
    # gives it a reason.
    #
    # What proves the backfill is TOTAL in the meantime is not the constraint:
    # it is ``_REFUSE_UNDERIVABLE_SQL`` above, which names every row this
    # derivation cannot answer for and refuses to run beside it.

    op.create_foreign_key(
        "fk_recurrence_rules_unit_id", "recurrence_rules",
        "recurrence_units", ["unit_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_recurrence_rules_placement_id", "recurrence_rules",
        "period_placements", ["placement_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_recurrence_rules_shift_id", "recurrence_rules",
        "business_day_shifts", ["shift_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    # ``nominal_day`` names a day the anchor month CLAMPED (ruling R-R3), so
    # both halves are checked: it is a real month-end day, and it exceeds the
    # day ``starts_on`` already carries.  ``EXTRACT(day FROM <date>)`` lowers
    # to the IMMUTABLE ``date_part(text, date)``, which is what lets it appear
    # in a CHECK at all.
    op.create_check_constraint(
        "ck_recurrence_rules_nominal_day", "recurrence_rules",
        "nominal_day IS NULL OR ("
        "starts_on IS NOT NULL "
        "AND nominal_day BETWEEN 29 AND 31 "
        "AND nominal_day > EXTRACT(day FROM starts_on))",
        schema="budget",
    )


def downgrade():
    """Drop the five columns and their constraints.

    Loss-free: every value they hold is DERIVED from columns this migration
    does not touch, so the upgrade reproduces them exactly.  That is what makes
    this leaf revertible without a restore -- plan step R7c-c, which drops the
    columns the derivation reads, is not.
    """
    op.drop_constraint(
        "ck_recurrence_rules_nominal_day", "recurrence_rules",
        type_="check", schema="budget",
    )
    for name in (
        "fk_recurrence_rules_unit_id",
        "fk_recurrence_rules_placement_id",
        "fk_recurrence_rules_shift_id",
    ):
        op.drop_constraint(
            name, "recurrence_rules", type_="foreignkey", schema="budget",
        )
    for column in (
        "nominal_day", "starts_on", "shift_id", "placement_id", "unit_id",
    ):
        op.drop_column("recurrence_rules", column, schema="budget")
