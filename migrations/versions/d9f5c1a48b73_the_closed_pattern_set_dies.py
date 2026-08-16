"""the closed pattern set dies

Plan step **R7c-c** of ``docs/plans/implementation_plan_recurrence_redesign.md``
section 4 -- the CONTRACT half of an expand / migrate / contract.  Plan step
R7c-a added the two-axis columns, backfilled them and had the write door keep
them in step while NOTHING read them; R7c-b moved every reader across and locked
them down; this revision drops what they replace.  The destructive DDL is
therefore LAST, and no leaf wrote a translation shim.

Review: Josh, 2026-08-14 -- APPROVED at R7c-a: the three-leaf split with the
destructive DDL last, and this leaf dropping the closed set.
Review: Josh, 2026-08-16 -- APPROVED, and it settles plan ledger row **D37**:
``day_of_month`` is DROPPED here rather than held to plan step R5, and its one
remaining reader (``recurrence_engine.compute_due_date``) reads the derivation
this migration proves the column equal to.  Ledger rows **D6**, **D32** and
**D38** close with it.

What it drops, and what each column was
---------------------------------------

Seven statements of six facts, every one of them derived by the write door from
the columns that survive:

===================  =========================================================
``pattern_id``       A closed set of eight names for cadences ``interval_n``
                     and ``unit_id`` state directly.  Four of the eight --
                     Monthly, Quarterly, Semi-Annual, Annual -- were the same
                     idea with a different integer baked into the NAME, which
                     is this whole arc's root cause.
``day_of_month``     The cycle's day, which the first occurrence carries.
``month_of_year``    The cycle's residue class, which the first occurrence's
                     own month carries -- the SAME class either way, and the
                     only one the model can still express (ruling R-R15).
``start_date``       The opening bound, which the first occurrence IS
                     (ruling R-R16).  No reader and no writer since R7c-b.
``start_period_id``  The paycheck a rule started in.  Retired at plan step
                     R7b-4 and NULL on every live row.
``offset_periods``   The cycle phase, a stored derivative of ``period_index``.
                     Derived from the first occurrence on every read since
                     R7c-b, and 0 on every live row.  Dropping it IS the whole
                     remedy for pay-calendar plan ledger row **P11**: while the
                     ordinal was stored, an inserted payday re-phased every
                     ``Every N Periods`` rule.
``interval_n``       Not dropped -- RE-POINTED.  ``encode_cadence`` wrote ``1``
                     for every pattern whose interval was in its name, so the
                     four live Quarterly and Semi-Annual rules read as MONTHLY
                     at face value: 12 occurrences a year where 4 or 2 are
                     owed, across the whole projection.
===================  =========================================================

and ``budget.recurrence_month_anchors``, created EMPTY at plan step R2b and
never given a writer: ruling **R-R16** put the day a clamped anchor MEANT on
the rule itself, as ``nominal_day``, under a CHECK tying its presence to
meaning.  It leaves ``app.audit_infrastructure.AUDITED_TABLES`` in the same
commit, so ``EXPECTED_TRIGGER_COUNT`` moves from 43 to 42 -- the number the
container entrypoint asserts at start.

The ORDER inside :func:`upgrade` is load-bearing
------------------------------------------------

``interval_n`` is re-pointed BEFORE ``pattern_id`` is dropped, because the
pattern is what says which rules the encoding touched.  And ``day_of_month`` is
GRADED before it is dropped, because a column with a live reader may not be
removed on an argument.

Why ``day_of_month`` can go four steps ahead of its reader
----------------------------------------------------------

``recurrence_engine.compute_due_date`` dates every generated row from that day,
and plan step **R5** is what deletes that function -- four ranks later, behind
the balance arc's cutover.  Plan ledger row **D37** recorded the collision and
named two ways out: hold the column, or swap the ranks.  The developer took a
third on 2026-08-16: the column is a DERIVED ENCODING, so its reader reads the
derivation.

``_authoring._author`` wrote it as one expression --
``resolved.day_of_month if fires_on_day_of_month(unit, placement) else None`` --
whose every input is a column that survives.
``recurrence.scheduling_day_of_month`` is that expression at the read door, and
:func:`refuse_unequal_scheduling_day` below is the same expression a THIRD time,
in SQL, run before the ``ALTER TABLE``: it is a second implementation grading
the first over every live row, which is what the arc's own verification
standard asks of a claim like this.  Measured on a 2026-08-16 production clone:
**0 of 46 rules disagree**.

Measured on that clone (46 live rules)
---------------------------------------

* ``day_of_month`` equals its derivation on 46 of 46, so its reader moves
  without a row changing date;
* ``interval_n`` re-points on **4** rules -- ids 33 and 36 from 1 to 3
  (quarterly), 19 and 30 from 1 to 6 (semi-annual).  No generated row moves:
  the read door already took those intervals through ``stored_interval``, which
  already answered 3 and 6;
* ``start_period_id`` is NULL on 46 of 46 and ``offset_periods`` is 0 on 46 of
  46, so both drops lose nothing that was ever written;
* ``month_of_year`` holds a value on 24 and ``start_date`` on 4, and neither
  has had a reader since ``900e761a``;
* ``budget.recurrence_month_anchors`` holds 0 rows, as it has since it was
  created.

The downgrade RE-DERIVES, and refuses what it cannot
-----------------------------------------------------

Unlike R7c-b's, this downgrade works -- because what it restores is an ENCODING
of values that survive rather than a value nothing maintains.  With ``Once``
retired at plan step R2e-3, ``(interval_n, unit, placement)`` names exactly one
closed-set pattern for every shape the application could author BEFORE this
revision.  A row carrying a cadence the closed set cannot name -- every other
month, a WEEK unit, an interval the set never had -- is unrepresentable, and
this revision is precisely what makes such rows authorable, so
:func:`refuse_unencodable_cadences` raises naming the offending rule ids rather
than seating them on a plausible wrong pattern.

Three columns come back EMPTY rather than guessed, and each says why:
``start_period_id`` (retired at R7b-4; re-deriving it would give every loan
payment a start period it never had, and re-phase), ``offset_periods`` (0 is
the phase the target revision derives for every live shape) and ``start_date``
(no reader at the target revision; the values it held are not recoverable from
what survives, and a restore is the way back to them).

**Exercised in both directions on the 2026-08-16 production clone**, comparing
every restored column against a snapshot of the originals:

* ``pattern_id``, ``interval_n``, ``day_of_month``, ``start_period_id`` and
  ``offset_periods`` come back EXACTLY -- 0 of 46 differ;
* ``month_of_year`` differs on **2** of 46, and they are the two plan ledger
  row **D38** names: id 36 ``Anchor Disposal`` (quarterly, authored March,
  restored June) and id 19 ``Clothes`` (semi-annual, authored March, restored
  September).  Both are in the SAME residue class -- ``3 ≡ 6 (mod 3)`` and
  ``3 ≡ 9 (mod 6)`` -- which is the whole content of ruling **R-R15**: a
  quarterly rule fires in ``{m, m+3, m+6, m+9}`` forever, so naming any one of
  them names the same cycle, and the first occurrence's month is the only one
  the model can still express;
* ``start_date`` differs on the **4** rules that held one, all restored NULL as
  stated above.

And the answer that matters rather than the columns: the downgraded database
generates BYTE-IDENTICALLY to the pre-upgrade one.  Driven through
``rule_occurrences`` and ``compute_due_date`` over all 46 rules -- 880 placed
occurrences with their pay period and due date, plus each rule's resolved
cadence and its Recurring-surface phrase -- against the ``origin/dev`` tree in a
second worktree, so the before side runs the code that shipped rather than this
one.  The FORWARD direction was measured the same way and is identical too.

Revision ID: d9f5c1a48b73
Revises: b6d41f0a9c27
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d9f5c1a48b73"
down_revision = "b6d41f0a9c27"
branch_labels = None
depends_on = None


#: The SQL spelling of ``recurrence.fires_on_day_of_month`` -- whether a
#: ``(unit, placement)`` cadence derives its first occurrence from a day of the
#: month, which is the ANCHOR FAMILY question.
#:
#: **NOT "does this unit have a day-of-month coordinate", and the difference is
#: the one cadence they disagree on.**  A MONTH-unit rule funded from a month's
#: FIRST paycheck fires on days of the month, so the second question answers
#: yes -- but ``day_of_month`` has always been NULL for it, and NULL is what
#: makes ``compute_due_date`` date the row from its PAYCHECK.  Writing the day
#: here would be plan ledger row **D26**'s fix arriving in the wrong step,
#: measured there at 11 rows.
#:
#: Ref rows are matched by NAME, which is what a migration must do: it states
#: the schema as it was at this revision, and ``app.ref_cache`` resolves ids
#: through enums that later steps may re-seed.  The same idiom
#: ``migrations._recurrence_two_axis_backfill`` uses.
_ANCHORS_ON_DAY_OF_MONTH = """
    u.name IN ('month', 'year') AND p.name = 'containing_date'
"""

#: The SQL spelling of ``recurrence.scheduling_day_of_month`` -- the day the
#: dropped ``day_of_month`` column HELD, from the columns that survive.
_DERIVED_SCHEDULING_DAY = f"""
    CASE WHEN {_ANCHORS_ON_DAY_OF_MONTH}
         THEN COALESCE(r.nominal_day, EXTRACT(day FROM r.starts_on))
    END
"""

#: Rules whose stored scheduling day is not what the surviving columns derive.
#:
#: ``IS DISTINCT FROM`` rather than ``<>`` because both sides are nullable and
#: the interesting disagreements are exactly the ones ``<>`` answers NULL for:
#: a stored day beside a derivation of ``None``, and the reverse.
_REFUSE_UNEQUAL_SCHEDULING_DAY_SQL = f"""
SELECT r.id, r.day_of_month, r.starts_on, r.nominal_day,
       u.name AS unit_name, p.name AS placement_name,
       ({_DERIVED_SCHEDULING_DAY}) AS derived_day
FROM budget.recurrence_rules r
JOIN ref.recurrence_units u ON u.id = r.unit_id
JOIN ref.period_placements p ON p.id = r.placement_id
WHERE r.day_of_month IS DISTINCT FROM ({_DERIVED_SCHEDULING_DAY})
ORDER BY r.id
"""

#: The two-axis interval each closed-set pattern NAMED, for the patterns whose
#: interval was in their name rather than in the column.
#:
#: Written out rather than imported from ``recurrence._frequency``, whose table
#: this revision is what deletes: a migration states the mapping as it was at
#: this revision, and an import would make a shipped migration change meaning
#: when a later step edits the code.  Only the two entries that MOVE a row are
#: here -- ``Every Period``, ``Monthly`` and ``Monthly First`` name interval 1
#: and the column already holds 1; ``Annual`` names the YEAR unit at interval 1
#: and the column already holds 1; ``Every N Periods`` is the one pattern that
#: always took its interval FROM the column.
_INTERVAL_REPOINTS: tuple[tuple[str, int], ...] = (
    ("Quarterly", 3),
    ("Semi-Annual", 6),
)

_REPOINT_INTERVAL_SQL = """
UPDATE budget.recurrence_rules AS r
SET interval_n = :interval_n
FROM ref.recurrence_patterns AS p
WHERE p.id = r.pattern_id AND p.name = :pattern_name
"""

#: The columns this revision drops, in the order the model declared them.
_DROPPED_COLUMNS: tuple[str, ...] = (
    "pattern_id",
    "offset_periods",
    "day_of_month",
    "month_of_year",
    "start_period_id",
    "start_date",
)

#: The CHECKs that go with them.  ``ck_recurrence_rules_valid_offset`` is one of
#: the two mirrors plan ledger row **D23** left: the phase is derived on every
#: read and cannot be negative, so the constraint became unviolatable rather
#: than merely unviolated, and it leaves with the column it names.
_DROPPED_CHECKS: tuple[str, ...] = (
    "ck_recurrence_rules_dom",
    "ck_recurrence_rules_moy",
    "ck_recurrence_rules_valid_offset",
)

#: Which closed-set pattern STORES a given ``(interval, unit, placement)``, for
#: :func:`downgrade`.  The inverse of what the application read forward, stated
#: once here and read by both the refusal and the restore so the two cannot
#: disagree about the set.
#:
#: ``Every N Periods`` is keyed on a ``None`` interval: it is the one pattern
#: that names none and takes the authored count from the column, so it is the
#: FALLBACK after an exact match fails.  That order is what makes ``(1, period)``
#: come back as ``Every Period`` rather than as ``Every N Periods`` with N = 1 --
#: both resolve identically, and picking the named one round-trips.
_PATTERN_BY_READING: dict[
    tuple[int | None, str, str], str,
] = {
    (1, "period", "containing_date"): "Every Period",
    (None, "period", "containing_date"): "Every N Periods",
    (1, "month", "containing_date"): "Monthly",
    (1, "month", "period_starting_on_or_after"): "Monthly First",
    (3, "month", "containing_date"): "Quarterly",
    (6, "month", "containing_date"): "Semi-Annual",
    (1, "year", "containing_date"): "Annual",
}

_READ_CADENCES_SQL = """
SELECT r.id, r.interval_n, u.name AS unit_name, p.name AS placement_name
FROM budget.recurrence_rules r
JOIN ref.recurrence_units u ON u.id = r.unit_id
JOIN ref.period_placements p ON p.id = r.placement_id
ORDER BY r.id
"""

_RESTORE_ENCODING_SQL = """
UPDATE budget.recurrence_rules
SET pattern_id = (
        SELECT id FROM ref.recurrence_patterns WHERE name = :pattern_name
    ),
    interval_n = :interval_n
WHERE id = :rule_id
"""

#: ``month_of_year`` came back only for the cadences that HELD one: the ones
#: whose cycle skips months, which is the YEAR unit and a MONTH unit at an
#: interval above 1.  A plain monthly rule fires every month and named none.
_RESTORE_LEGACY_ANCHOR_SQL = f"""
UPDATE budget.recurrence_rules AS r
SET day_of_month = ({_DERIVED_SCHEDULING_DAY}),
    month_of_year = CASE
      WHEN ({_ANCHORS_ON_DAY_OF_MONTH})
       AND (u.name = 'year' OR r.interval_n > 1)
      THEN EXTRACT(month FROM r.starts_on)
    END
FROM ref.recurrence_units u, ref.period_placements p
WHERE u.id = r.unit_id AND p.id = r.placement_id
"""


def refuse_unequal_scheduling_day(bind) -> None:
    """Raise when a stored scheduling day is not what the survivors derive.

    **The grade that lets ``day_of_month`` be dropped four steps ahead of its
    reader.**  ``recurrence_engine.compute_due_date`` dates every generated row
    from that day, and this revision hands it
    ``recurrence.scheduling_day_of_month`` instead; if the two ever disagreed on
    a live row, every row that rule generates would change date silently.  A
    SECOND implementation of the derivation, in SQL, run before the column is
    gone -- never the producer grading itself.

    A named function rather than an inline block for the reason
    ``_recurrence_two_axis_backfill.refuse_underivable`` is: a refusal nothing
    executes is a refusal nobody has seen work, and driving this one from a test
    does not need real DDL inside an xdist worker.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every offending rule, both days and the cadence.
    """
    offenders = bind.execute(
        sa.text(_REFUSE_UNEQUAL_SCHEDULING_DAY_SQL),
    ).all()
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) carry a day_of_month their own cadence columns do "
        "not derive: "
        + "; ".join(
            f"id={row.id} stored={row.day_of_month} "
            f"derived={row.derived_day} starts_on={row.starts_on} "
            f"nominal_day={row.nominal_day} "
            f"cadence=({row.unit_name}, {row.placement_name})"
            for row in offenders
        )
        + ".  This revision drops the column and points "
        "recurrence_engine.compute_due_date at the derivation, so a "
        "disagreement here would silently re-date every row those rules "
        "generate.  Re-author the rule through app.services.recurrence, which "
        "writes both sides from one resolve call, and re-run."
    )


def _pattern_for(interval_n: int, unit_name: str, placement_name: str):
    """Return the closed-set pattern that stores this reading, or ``None``.

    Args:
        interval_n: The rule's two-axis interval.
        unit_name: Its ``ref.recurrence_units`` name.
        placement_name: Its ``ref.period_placements`` name.

    Returns:
        tuple[str, int] | None: The pattern name and the ``interval_n`` the
        column must hold beside it -- the authored count for the one pattern
        that reads the column, and ``1`` for every other -- or ``None`` when no
        pattern stores the reading.
    """
    exact = _PATTERN_BY_READING.get((interval_n, unit_name, placement_name))
    if exact is not None:
        return exact, 1
    free = _PATTERN_BY_READING.get((None, unit_name, placement_name))
    if free is not None:
        return free, interval_n
    return None


def refuse_unencodable_cadences(bind) -> None:
    """Raise when a rule names a cadence the closed pattern set cannot store.

    :func:`downgrade`'s guard, and the reason that function can restore rather
    than refuse outright.  Every shape the application could author BEFORE this
    revision maps to exactly one pattern; this revision is what makes ``(2,
    MONTH)``, a WEEK unit and a quarterly first-paycheck cadence authorable, so
    a row carrying one has no pattern to come back as.

    Refusing NAMES the rules rather than seating them on the nearest pattern:
    the nearest pattern to "every 2 months" is monthly, which would generate
    twice as many rows as the rule says forever.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every rule with no closed-set pattern.
    """
    offenders = [
        row for row in bind.execute(sa.text(_READ_CADENCES_SQL)).all()
        if _pattern_for(
            row.interval_n, row.unit_name, row.placement_name,
        ) is None
    ]
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) name a cadence the closed pattern set cannot "
        "store: "
        + "; ".join(
            f"id={row.id} every {row.interval_n} {row.unit_name} "
            f"funded {row.placement_name}"
            for row in offenders
        )
        + ".  Revision d9f5c1a48b73 is what made these authorable, so there is "
        "no pattern to restore them onto and the nearest one would generate a "
        "rhythm the rule never named.  Re-author or delete them, or REVERT THE "
        "DATA BY RESTORING THE DATABASE."
    )


def upgrade():
    """Re-point the interval, then drop the encoding and the empty subtype."""
    bind = op.get_bind()

    # GRADED before it is dropped: this is the column whose reader moves.
    refuse_unequal_scheduling_day(bind)

    # BEFORE ``pattern_id`` goes, because the pattern is what says which rules
    # the encoding touched.
    for pattern_name, interval_n in _INTERVAL_REPOINTS:
        bind.execute(
            sa.text(_REPOINT_INTERVAL_SQL),
            {"pattern_name": pattern_name, "interval_n": interval_n},
        )

    for check in _DROPPED_CHECKS:
        op.drop_constraint(
            check, "recurrence_rules", type_="check", schema="budget",
        )
    for column in _DROPPED_COLUMNS:
        op.drop_column("recurrence_rules", column, schema="budget")

    # Explicit, though dropping the table would take it: the intent is visible
    # and the step is idempotent if the trigger is already absent.  The same
    # shape ``78782c6ac75e`` used for the last audited table this app dropped.
    op.execute(
        "DROP TRIGGER IF EXISTS audit_recurrence_month_anchors "
        "ON budget.recurrence_month_anchors"
    )
    op.drop_table("recurrence_month_anchors", schema="budget")


def downgrade():
    """Restore the closed-set encoding, refusing the cadences it cannot name."""
    bind = op.get_bind()
    refuse_unencodable_cadences(bind)

    # Recreated WITHOUT an audit trigger, following ``78782c6ac75e``: per-table
    # triggers are governed by ``AUDITED_TABLES`` and the rebuild migration that
    # reads it, not by a table's own create/restore, so a table absent from that
    # list is correctly untriggered.  It has never held a row.
    op.create_table(
        "recurrence_month_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=False),
        sa.Column("nominal_day", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "nominal_day BETWEEN 29 AND 31",
            name="ck_recurrence_month_anchors_nominal_day",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"], ["budget.recurrence_rules.id"],
            name="fk_recurrence_month_anchors_rule_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id", name="uq_recurrence_month_anchors_rule",
        ),
        schema="budget",
    )

    # NULLABLE first, because every value below is derived from a row that
    # already exists.  ``pattern_id`` is tightened after the restore; the other
    # five were nullable at the target revision and stay so.
    op.add_column(
        "recurrence_rules",
        sa.Column("pattern_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column(
            "offset_periods", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("month_of_year", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("start_period_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("start_date", sa.Date(), nullable=True),
        schema="budget",
    )

    # The ANCHOR columns FIRST, and the order is load-bearing rather than
    # stylistic: ``month_of_year`` came back only for a cadence whose cycle
    # SKIPS months, which is read off ``interval_n`` -- and the encoding restore
    # below OVERWRITES that column with the closed set's ``1``.  Running it the
    # other way round left the four quarterly and semi-annual rules with a NULL
    # month, which is exactly the state the pre-R7c-b resolver read as "January"
    # (``rule.month_of_year or 1``).  Measured before it was reordered.
    bind.execute(sa.text(_RESTORE_LEGACY_ANCHOR_SQL))

    for row in bind.execute(sa.text(_READ_CADENCES_SQL)).all():
        pattern_name, interval_n = _pattern_for(
            row.interval_n, row.unit_name, row.placement_name,
        )
        bind.execute(
            sa.text(_RESTORE_ENCODING_SQL),
            {
                "rule_id": row.id,
                "pattern_name": pattern_name,
                "interval_n": interval_n,
            },
        )

    op.alter_column(
        "recurrence_rules", "pattern_id", nullable=False, schema="budget",
    )
    op.create_foreign_key(
        "recurrence_rules_pattern_id_fkey", "recurrence_rules",
        "recurrence_patterns", ["pattern_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "recurrence_rules_start_period_id_fkey", "recurrence_rules",
        "pay_periods", ["start_period_id"], ["id"],
        source_schema="budget", referent_schema="budget", ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_dom", "recurrence_rules",
        "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_moy", "recurrence_rules",
        "month_of_year IS NULL OR "
        "(month_of_year >= 1 AND month_of_year <= 12)",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_valid_offset", "recurrence_rules",
        "offset_periods >= 0", schema="budget",
    )
