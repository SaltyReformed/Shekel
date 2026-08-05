"""add the two-axis recurrence columns, the anchor subtypes, and the backfill

Plan step **R2b** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- the column half of R2, split from R2a because tightening the new columns to
NOT NULL drags every writer along with it (ruling R-R5).  This revision adds
the columns NULLABLE and BACKFILLS every existing rule; step R2c routes the
writers through one authoring seam and tightens them.

**Nothing reads any of it yet.**  ``recurrence_engine.match_periods`` still
dispatches on ``pattern_id`` and consults ``day_of_month`` /
``month_of_year`` / ``offset_periods`` / ``start_period_id`` / ``start_date``.
The R1 behaviour baseline (``tests/oracles/recurrence_baseline.txt``) is
therefore byte-identical across this revision, and a moved line would mean
this migration touched something it should not have.

What it creates
---------------

On ``budget.recurrence_rules``, all nullable:

* ``unit_id``      -> ``ref.recurrence_units`` (RESTRICT).  The axis the old
  eight-name set lacked: Monthly / Quarterly / Semi-Annual / Annual are one
  idea with the integer baked into the NAME.
* ``anchor_date``  -- the FIRST occurrence, so it carries the rule's phase AND
  its opening bound in one value.  That is what retires ``start_period_id``
  (weak: a caller's own ``effective_from`` silently overrides it, defect D2)
  and ``offset_periods`` (an INDEX, which a schedule rebuild invalidates while
  a date survives it, defect D1).
* ``placement_id`` -> ``ref.period_placements`` (RESTRICT).
* ``shift_id``     -> ``ref.business_day_shifts`` (RESTRICT), backfilled to
  ``none`` for every rule so step R8 turns behaviour ON rather than adding a
  column to a populated table.
* ``max_occurrences`` -- the count-bounded end, with
  ``CHECK (max_occurrences IS NULL OR max_occurrences > 0)``.

Plus ONE rule-level CHECK, added after the backfill so the backfill's own
output is what it validates: ``end_date IS NULL OR max_occurrences IS NULL``
(at most one closing bound).  ``max_occurrences`` has no writer until step R8,
so it can only be NULL and this cannot fire on a live edit.

**Its sibling ``end_date >= anchor_date`` is deliberately NOT added here**,
and the reason is measured.  ``anchor_date`` is DERIVED and inert;
``end_date`` is user-authored and live.  Fourteen live rules carry a derived
anchor in the future (rule 34 at 2027-03-16, rule 8 at 2027-01-15, ...), and
setting an earlier end date -- exactly what the field's own help text invites,
"entries won't be generated after this date" -- succeeds on production today.
With the CHECK in place it becomes a ``CheckViolation`` raised from
``update_template``'s autoflush, which no handler catches
(``_commit_helpers`` catches only ``StaleDataError``), so the user cannot stop
an annual bill and the projection keeps charging it.  A CHECK with no
Marshmallow mirror surfaces as a 500, and there is nothing to mirror it
against until the form collects the anchor.  It belongs to step R7.

And two 0-or-1 subtype tables, ``budget.recurrence_weekday_anchors`` (nothing
writes it until step R8; created here so that step adds behaviour rather than
a table) and ``budget.recurrence_month_anchors`` (this migration's own
backfill is its first writer).  Both are audited.

**Both subtypes carry a surrogate ``id``, not the plan's
``recurrence_rule_id PK``.**  ``system.audit_trigger_func`` assigns
``v_row_id := NEW.id``; on a table without that column every INSERT dies with
``record "new" has no field "id"`` -- measured against a probe table on the
dev database (2026-08-05) rather than inferred.  ``UNIQUE
(recurrence_rule_id)`` enforces the identical 0-or-1 cardinality and matches
every other table in the schema.  For the same house-consistency reason the
day/week columns are ``INTEGER`` rather than the plan sketch's ``SMALLINT``:
``day_of_month`` and ``month_of_year`` on this very table are ``INTEGER``, no
table in the project uses ``SMALLINT``, and the CHECK constraints -- not the
physical width -- are what bound the domain.

The backfill's four derivations
-------------------------------

1. **The effective start** every anchor is measured from is the GREATEST of
   ``rule.start_date``, ``rule.start_period.start_date`` and the user's
   earliest pay-period start.  That single maximum reproduces both of the
   engine's branches, because ``match_periods`` applies the ``start_date``
   filter AND an ``effective_from`` that ``resolve_generation_plan`` always
   supplies (``recurrence_engine.py:481,488`` and ``:121-124``).

   **It does NOT follow that ``anchor_date >= start_date``.**  That claim
   holds for the calendar family, where the anchor IS a target date at or
   after the bound, and FAILS for the period family, where the anchor is the
   START of the first qualifying period and a period qualifies on its END
   (``:488``) -- so a rule with ``start_date = 2026-04-15`` anchors on
   2026-04-09 if that is when its period opened.  The generated period set is
   unchanged either way, which is why the live rules all reproduce; what does
   not survive is the loan's origination bound as an exact date, so
   ``rule.start_date`` remains the C9a bound and must not be dropped on the
   strength of the anchor alone (ledger row D6).
2. **A pay-period-space rule** (Every Period / Every N Periods / Once) anchors
   on the START of the first period the engine would have matched -- the first
   period ending on or after the effective start, additionally phase-filtered
   by ``(period_index - offset_periods) % interval_n == 0`` for Every N.
   Anchoring on a period start rather than on the raw effective date is what
   keeps a PERIOD-unit anchor addressable after a schedule rebuild.
3. **A calendar rule** anchors on the first date matching its
   ``(month_of_year, day_of_month)`` cycle on or after the effective start,
   month-end clamped exactly as ``_match_monthly`` clamps
   (``min(day, monthrange(...))``).  ``or 1`` mirrors the engine's own
   coercion of a malformed rule (``recurrence_engine.py:504-518``) rather than
   inventing a different one.

   This is the ONE derivation that deliberately does not reproduce the engine
   in every case, and it is ruling **R-R6** (ledger row D5): the engine bounds
   PERIODS, admitting a period whose END is on or after the bound, so a target
   date EARLIER in that period than the bound still fires -- a monthly-15th
   rule starting 2026-04-20 generates a row dated 2026-04-15.  Anchoring on
   the first target at or after the bound drops exactly those rows, which is
   the point: no row outside the window the user stated.  Unreachable from
   today's writers (loan sync keeps ``start_date.day == day_of_month``, and a
   start-period bound is always a period start), and step R4 is where the
   frozen baseline moves for it.
4. **A Monthly First rule** anchors on the 1st of the first month whose OWN
   first paycheck falls on or after the effective start (developer ruling,
   2026-08-05).  "The 1st of the first covered month" was ambiguous: for a
   rule starting mid-month it would place the first row in a paycheck EARLIER
   than the one the user chose, because the placement rule is "the first
   period starting on or after the occurrence".  Skipping to the first month
   the rule can honour is what makes every generated row genuinely its
   month's first paycheck.  Zero live rules are affected -- the one Monthly
   First rule starts at period index 0.

A ``recurrence_month_anchors`` row is written **iff** the derived anchor is
the last day of its month AND the rule's nominal day exceeds it, i.e. the
clamp lost information (ruling R-R3).  Measured: zero live rules qualify (the
only day-31 rule is annual in March), so the backfill writes no rows on this
database; the branch is covered by a constructed rule in the tests.

Why ``interval_n`` is rewritten, and why that stays reversible
-------------------------------------------------------------

``interval_n`` is reused rather than duplicated: its OLD meaning is "repeat
every N pay periods" and is read only in ``match_periods``' EVERY_N_PERIODS
branch, so for every other pattern the column is meaningless today.  The
backfill gives it the two-axis meaning -- 3 for Quarterly, 6 for Semi-Annual,
1 elsewhere -- which is inert for every current reader (``amount_to_monthly``
consults it only for EVERY_N_PERIODS, and ``_recurrence_macros.html`` renders
it only inside the EVERY_N branch).  The one unconditional reader was the
edit form's hidden ``interval_n`` input, which prefilled from the rule
whatever the pattern; the same commit makes that prefill pattern-scoped, so
the rewrite is invisible on every surface.

``upgrade`` REFUSES to run if any non-EVERY_N_PERIODS rule already carries
``interval_n <> 1``.  That guard is what makes ``downgrade``'s restore exact
rather than a guess: every value this migration overwrites was the column
default, so putting 1 back restores precisely what was there.  Verified
against ``shekel-prod-db`` and the dev clone on 2026-08-05 -- all 50 rules
carry ``interval_n = 1``.

**No ``Review:`` line, and the reason is not that nobody looked.**  The
project's migration rules define destructive as drops, renames, type changes
and constraint removals; this revision does none of those.  It adds columns
and tables, and it UPDATEs columns whose prior value was either NULL (the four
new ones) or the provably-restorable default (``interval_n``, guarded above).
The five ORPHANED recurrence rules an earlier draft of the plan deleted here
are deliberately NOT touched: they leak because ``templates.hard_delete_template``
deletes a template and leaves its rule, so deleting them belongs in the commit
that closes that hole, not in an additive schema migration (developer ruling,
2026-08-05).  They are backfilled like every other rule.

**Self-contained dependency policy.**  Imports nothing from ``app`` -- not
models, not enums, not ``ref_cache``.  Reference ids are resolved by NAME
here, which is the only option below the ref-cache layer and the same thing
``e7a4d95c2b18`` does; the resulting ids are written as integers, and every
application reader compares them as integers.

Revision ID: c8f2b6a41d93
Revises: e7a4d95c2b18
Create Date: 2026-08-05

"""
import calendar
import datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f2b6a41d93'
down_revision = 'e7a4d95c2b18'
branch_labels = None
depends_on = None


# --- Derivation vocabulary -------------------------------------------------
#
# Pattern names are matched as STRINGS here because a migration runs below the
# ref-cache layer and cannot import ``app.enums``.  The names are the seeded
# ``ref.recurrence_patterns.name`` values; an unrecognised one raises rather
# than silently leaving a rule un-backfilled.

#: How each old pattern maps onto ``(interval_n, unit, placement, family)``.
#: ``interval_n`` is ``None`` for Every N Periods, which keeps the rule's own.
#: ``month_step`` is the interval expressed in MONTHS for the calendar family
#: (the residue class its occurrence months fall in) and ``None`` elsewhere.
_PATTERN_DERIVATIONS: dict[str, dict] = {
    "Every Period": {
        "interval_n": 1, "unit": "period",
        "placement": "containing_date", "family": "period", "month_step": None,
    },
    "Every N Periods": {
        "interval_n": None, "unit": "period",
        "placement": "containing_date", "family": "period", "month_step": None,
    },
    # ``Once`` does not recur, so no honest cadence exists for it.  It gets
    # INERT two-axis values rather than a deletion (ruling R-R4): 2 of the 4
    # live Once rules hang off transfer templates whose form has no null
    # option, so deleting them would pull step R7's form work in here.
    # ``pattern_id = Once`` REMAINS what suppresses generation until step R9.
    "Once": {
        "interval_n": 1, "unit": "period",
        "placement": "containing_date", "family": "period", "month_step": None,
    },
    "Monthly": {
        "interval_n": 1, "unit": "month",
        "placement": "containing_date", "family": "calendar", "month_step": 1,
    },
    "Monthly First": {
        "interval_n": 1, "unit": "month",
        "placement": "period_starting_on_or_after",
        "family": "first_of_month", "month_step": None,
    },
    "Quarterly": {
        "interval_n": 3, "unit": "month",
        "placement": "containing_date", "family": "calendar", "month_step": 3,
    },
    "Semi-Annual": {
        "interval_n": 6, "unit": "month",
        "placement": "containing_date", "family": "calendar", "month_step": 6,
    },
    "Annual": {
        "interval_n": 1, "unit": "year",
        "placement": "containing_date", "family": "calendar", "month_step": 12,
    },
}

#: The units whose anchor can be month-end clamped, and therefore the only
#: ones that can need a ``recurrence_month_anchors`` row.
_CLAMPABLE_UNITS = ("month", "year")

#: Upper bound on the month-ordinal walk in :func:`_calendar_anchor`.  Two
#: candidates always suffice (the effective month's own occurrence, then one
#: cycle later), so anything beyond this is a broken derivation, not a slow
#: one -- it raises instead of spinning.
_MAX_MONTH_PROBES = 4

#: Patterns whose ``interval_n`` this migration rewrites, and the value each
#: gets.  ``downgrade`` restores 1 for exactly these pairs.
_REWRITTEN_INTERVALS = (("Quarterly", 3), ("Semi-Annual", 6))


def _load_ref_ids(bind, table: str) -> dict[str, int]:
    """Return ``{name: id}`` for a ``ref`` lookup table.

    The table name is interpolated rather than bound because it names a
    RELATION, which no parameter placeholder can carry; the three values it
    is ever called with are module-level literals below, never request data.
    """
    rows = bind.execute(sa.text(f"SELECT name, id FROM ref.{table}")).fetchall()
    return {row[0]: row[1] for row in rows}


def _periods_by_user(bind) -> dict[int, list]:
    """Return each user's pay periods, ordered by ``period_index``.

    One query for the whole table rather than one per rule: the backfill
    consults the same user's schedule for every rule that user owns.
    """
    rows = bind.execute(sa.text(
        "SELECT user_id, period_index, start_date, end_date "
        "FROM budget.pay_periods ORDER BY user_id, period_index"
    )).fetchall()
    by_user: dict[int, list] = {}
    for row in rows:
        by_user.setdefault(row[0], []).append(row)
    return by_user


def _effective_start(rule_start_date, start_period_start, periods):
    """Return the date the rule's first occurrence is measured from.

    ``match_periods`` filters candidates on ``effective_from`` AND on
    ``rule.start_date`` (``recurrence_engine.py:481,488``), and
    ``resolve_generation_plan`` ALWAYS supplies an ``effective_from`` -- the
    start period's start when the rule has one, else the earliest pay period's
    (``:121-124``).  The composite bound is therefore the maximum of all three,
    and taking the earliest period start into the maximum is not cosmetic: a
    rule carrying a ``start_date`` but NO start period is exactly what
    ``routes/loan/payment_transfer.py`` builds, and a loan originated in 2019
    would otherwise anchor 86 months before the user's schedule begins.
    Because a start period is always one of the user's own periods, its start
    dominates the earliest period start whenever it is present, so the single
    maximum reproduces both of the engine's branches.

    ``effective_from`` is a per-call argument a caller MAY override -- and
    ``regenerate_for_template`` does, which is defect D2 -- so this reproduces
    the DEFAULT path, the one that decides where a rule's occurrences begin
    when nobody overrides it.

    Returns:
        The bound, or ``None`` when the user has no schedule at all, in which
        case there is nothing to anchor against and the row keeps its NULLs.
    """
    if not periods:
        return None
    bounds = [periods[0][2]]
    bounds.extend(
        d for d in (rule_start_date, start_period_start) if d is not None
    )
    return max(bounds)


def _period_anchor(periods, effective, interval_n, offset_periods, phased):
    """Return the START of the first period the engine would have matched.

    Mirrors ``match_periods``: a period qualifies when its END is on or after
    the bound, so a period CONTAINING the bound still counts.  *phased* adds
    the EVERY_N_PERIODS residue filter.

    Returns:
        A ``date``, or ``None`` when no period qualifies (the schedule ends
        before the bound).
    """
    for _user_id, period_index, start_date, end_date in periods:
        if end_date < effective:
            continue
        if phased and (period_index - offset_periods) % interval_n != 0:
            continue
        return start_date
    return None


def _calendar_anchor(effective, month_step, base_month, nominal_day):
    """Return the first ``(base_month, nominal_day)`` occurrence >= *effective*.

    Walks absolute month ordinals in the rule's residue class.  Because
    ``month_step`` divides 12 for every calendar pattern, a residue over month
    ordinals is the same set as the engine's residue over month NUMBERS -- so
    "every third month starting in April" names the identical months either
    way.  The day is clamped per month exactly as ``_match_monthly`` clamps it.
    """
    start_ordinal = effective.year * 12 + (effective.month - 1)
    target_residue = (base_month - 1) % month_step
    ordinal = start_ordinal + (
        (target_residue - start_ordinal % month_step) % month_step
    )
    for _probe in range(_MAX_MONTH_PROBES):
        year, month_index = divmod(ordinal, 12)
        month = month_index + 1
        day = min(nominal_day, calendar.monthrange(year, month)[1])
        candidate = datetime.date(year, month, day)
        if candidate >= effective:
            return candidate
        ordinal += month_step
    raise RuntimeError(
        f"c8f2b6a41d93: no calendar anchor found within {_MAX_MONTH_PROBES} "
        f"cycles of {effective} for month_step={month_step} "
        f"base_month={base_month} nominal_day={nominal_day}.  Two candidates "
        f"always suffice, so this is a derivation bug, not a data one."
    )


def _first_of_month_anchor(periods, effective):
    """Return the 1st of the first month whose own first paycheck qualifies.

    Developer ruling, 2026-08-05: a Monthly First rule fires on each month's
    FIRST pay period, so the first month it can honour is the first one whose
    earliest period start is on or after the effective start.  Anchoring on
    the effective month instead would place the first row in a paycheck
    earlier than the one the user chose.

    Returns:
        A ``date`` on the 1st of that month, or ``None`` when no month
        qualifies (the schedule ends before the bound).
    """
    earliest_by_month: dict[tuple[int, int], datetime.date] = {}
    for _user_id, _period_index, start_date, _end_date in periods:
        key = (start_date.year, start_date.month)
        if key not in earliest_by_month:
            earliest_by_month[key] = start_date
    for year, month in sorted(earliest_by_month):
        if earliest_by_month[(year, month)] >= effective:
            return datetime.date(year, month, 1)
    return None


def _derive_rule(rule, periods, units, placements):
    """Return the two-axis values for one rule, or ``None`` to leave it NULL.

    Args:
        rule: The rule row, with its start period's start date joined on.
        periods: The owning user's pay periods, ordered by ``period_index``.
        units: ``{name: id}`` for ``ref.recurrence_units``.
        placements: ``{name: id}`` for ``ref.period_placements``.

    Returns:
        A dict of column values plus the derived ``nominal_day``, or ``None``
        when no anchor is derivable (a user with no pay periods, or a schedule
        that ends before the rule's bound).
    """
    derivation = _PATTERN_DERIVATIONS[rule.pattern_name]
    effective = _effective_start(rule.start_date, rule.start_period_start, periods)
    if effective is None:
        return None

    nominal_day = rule.day_of_month or 1
    family = derivation["family"]
    if family == "period":
        anchor = _period_anchor(
            periods, effective, rule.interval_n, rule.offset_periods,
            phased=rule.pattern_name == "Every N Periods",
        )
        nominal_day = None
    elif family == "first_of_month":
        anchor = _first_of_month_anchor(periods, effective)
        nominal_day = None
    else:
        anchor = _calendar_anchor(
            effective, derivation["month_step"], rule.month_of_year or 1,
            nominal_day,
        )
    if anchor is None:
        return None

    interval_n = derivation["interval_n"]
    return {
        "rule_id": rule.id,
        "interval_n": rule.interval_n if interval_n is None else interval_n,
        "unit_id": units[derivation["unit"]],
        "anchor_date": anchor,
        "placement_id": placements[derivation["placement"]],
        "unit_name": derivation["unit"],
        "nominal_day": nominal_day,
    }


def _needs_month_anchor(derived) -> bool:
    """Return True when the anchor month CLAMPED the rule's nominal day.

    Presence is the discriminator (ruling R-R3): the subtype row exists
    exactly when ``anchor_date.day`` is no longer the day the user meant,
    which happens iff the anchor lands on its month's last day AND the nominal
    day is larger.  A rule whose day is 1-28 can never be clamped and costs
    nothing.
    """
    if derived["unit_name"] not in _CLAMPABLE_UNITS:
        return False
    if derived["nominal_day"] is None:
        return False
    anchor = derived["anchor_date"]
    last_day = calendar.monthrange(anchor.year, anchor.month)[1]
    return anchor.day == last_day and derived["nominal_day"] > anchor.day


def _refuse_rewritten_intervals(bind) -> None:
    """Abort unless every non-EVERY_N rule carries the default ``interval_n``.

    The backfill rewrites ``interval_n`` for Quarterly and Semi-Annual rules.
    That is only REVERSIBLE if the value it overwrites was the column default,
    so this refuses to proceed otherwise rather than making ``downgrade`` a
    guess.  Verified empty against production and the dev clone on 2026-08-05.
    """
    rows = bind.execute(sa.text(
        "SELECT r.id, p.name, r.interval_n "
        "  FROM budget.recurrence_rules r "
        "  JOIN ref.recurrence_patterns p ON p.id = r.pattern_id "
        " WHERE p.name <> 'Every N Periods' AND r.interval_n <> 1 "
        " ORDER BY r.id"
    )).fetchall()
    if rows:
        detail = ", ".join(f"id={r[0]} {r[1]} interval_n={r[2]}" for r in rows)
        raise RuntimeError(
            "c8f2b6a41d93 refuses to run: these recurrence rules carry a "
            "non-default interval_n under a pattern that does not use it, so "
            "the two-axis backfill would overwrite a value downgrade cannot "
            f"restore -- {detail}.  Reset them to 1 (the column default and "
            "the value every reader assumes for these patterns) and re-run:\n"
            "  UPDATE budget.recurrence_rules r SET interval_n = 1 "
            "FROM ref.recurrence_patterns p WHERE p.id = r.pattern_id "
            "AND p.name <> 'Every N Periods' AND r.interval_n <> 1;"
        )


def _report_window_inversions(bind) -> None:
    """Name any rule whose derived anchor falls AFTER its own end date.

    Such a rule is already retired: under the two-axis model it can never
    fire, and under the current engine it generates nothing either.  It is
    also a state the application permits TODAY -- the end-date field accepts
    any date and nothing compares it to a first occurrence -- so this REPORTS
    rather than aborts.  Refusing here would fail a deploy over pre-existing
    data the user was entitled to create.

    The matching ``end_date >= anchor_date`` CHECK is deliberately NOT added
    by this migration: ``anchor_date`` is derived and inert while ``end_date``
    is user-authored and live, so the constraint would turn "stop this
    recurring bill" into an unhandled IntegrityError for the 14 live rules
    whose derived anchor is in the future.  It belongs to step R7, which is
    where the form starts collecting the anchor and can validate the pair.
    """
    rows = bind.execute(sa.text(
        "SELECT id, anchor_date, end_date FROM budget.recurrence_rules "
        " WHERE anchor_date IS NOT NULL AND end_date IS NOT NULL "
        "   AND end_date < anchor_date ORDER BY id"
    )).fetchall()
    if rows:
        detail = ", ".join(
            f"id={r[0]} anchor={r[1]} end={r[2]}" for r in rows
        )
        print(
            "c8f2b6a41d93: these rules end BEFORE their derived first "
            f"occurrence and can never fire -- {detail}.  Left as they are; "
            "step R7 is where the form gains the anchor and can refuse the "
            "pair at the door."
        )


def _backfill(bind) -> None:
    """Derive and write the two-axis values for every existing rule."""
    units = _load_ref_ids(bind, "recurrence_units")
    placements = _load_ref_ids(bind, "period_placements")
    shifts = _load_ref_ids(bind, "business_day_shifts")
    periods_by_user = _periods_by_user(bind)

    rules = bind.execute(sa.text(
        "SELECT r.id, r.user_id, p.name AS pattern_name, r.interval_n, "
        "       r.offset_periods, r.day_of_month, r.month_of_year, "
        "       r.start_date, sp.start_date AS start_period_start "
        "  FROM budget.recurrence_rules r "
        "  JOIN ref.recurrence_patterns p ON p.id = r.pattern_id "
        "  LEFT JOIN budget.pay_periods sp ON sp.id = r.start_period_id "
        " ORDER BY r.id"
    )).fetchall()

    updates, month_anchors, skipped = [], [], []
    for rule in rules:
        if rule.pattern_name not in _PATTERN_DERIVATIONS:
            raise RuntimeError(
                f"c8f2b6a41d93: recurrence rule id={rule.id} carries pattern "
                f"{rule.pattern_name!r}, which this backfill has no derivation "
                f"for.  Add it to _PATTERN_DERIVATIONS -- leaving the rule "
                f"un-backfilled would hand step R2c a NULL it cannot resolve."
            )
        derived = _derive_rule(rule, periods_by_user.get(rule.user_id, []),
                               units, placements)
        if derived is None:
            skipped.append(rule.id)
            continue
        if _needs_month_anchor(derived):
            month_anchors.append({
                "rule_id": derived["rule_id"],
                "nominal_day": derived["nominal_day"],
            })
        updates.append({
            "rule_id": derived["rule_id"],
            "interval_n": derived["interval_n"],
            "unit_id": derived["unit_id"],
            "anchor_date": derived["anchor_date"],
            "placement_id": derived["placement_id"],
            # Every rule starts unshifted so step R8 turns behaviour on.
            "shift_id": shifts["none"],
        })

    if updates:
        bind.execute(sa.text(
            "UPDATE budget.recurrence_rules "
            "   SET interval_n = :interval_n, unit_id = :unit_id, "
            "       anchor_date = :anchor_date, placement_id = :placement_id, "
            "       shift_id = :shift_id "
            " WHERE id = :rule_id"
        ), updates)
    if month_anchors:
        # ``ON CONFLICT`` so re-deriving converges rather than colliding with
        # the UNIQUE constraint: a rule carries at most one month anchor, and
        # step R2c re-derives every rule over rows this pass already wrote.
        bind.execute(sa.text(
            "INSERT INTO budget.recurrence_month_anchors "
            "            (recurrence_rule_id, nominal_day) "
            "     VALUES (:rule_id, :nominal_day) "
            "ON CONFLICT (recurrence_rule_id) "
            "  DO UPDATE SET nominal_day = EXCLUDED.nominal_day"
        ), month_anchors)

    print(
        f"c8f2b6a41d93: backfilled {len(updates)} of {len(rules)} recurrence "
        f"rules; {len(month_anchors)} needed a month anchor."
    )
    if skipped:
        # Not fatal at R2b -- the columns are nullable and nothing reads them.
        # Step R2c's re-backfill sees these again and its NOT NULL tightening
        # is where an unresolvable rule becomes an error.
        print(
            f"c8f2b6a41d93: left NULL (no pay-period schedule to anchor "
            f"against): rule ids {skipped}."
        )


def upgrade():
    """Add the two-axis columns and subtype tables, then backfill every rule."""
    bind = op.get_bind()
    _refuse_rewritten_intervals(bind)

    # -- Step 1: the new columns on budget.recurrence_rules -------------
    op.add_column(
        "recurrence_rules",
        sa.Column("unit_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "recurrence_rules",
        sa.Column("anchor_date", sa.Date(), nullable=True),
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
        sa.Column("max_occurrences", sa.Integer(), nullable=True),
        schema="budget",
    )
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
    op.create_check_constraint(
        "ck_recurrence_rules_positive_max_occurrences", "recurrence_rules",
        "max_occurrences IS NULL OR max_occurrences > 0", schema="budget",
    )

    # -- Step 2: the two 0-or-1 subtype tables --------------------------
    op.create_table(
        "recurrence_weekday_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=False),
        sa.Column("nth_week", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "nth_week BETWEEN -1 AND 5 AND nth_week <> 0",
            name="ck_recurrence_weekday_anchors_nth_week",
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_recurrence_weekday_anchors_weekday",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"], ["budget.recurrence_rules.id"],
            name="fk_recurrence_weekday_anchors_rule_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id", name="uq_recurrence_weekday_anchors_rule",
        ),
        schema="budget",
    )
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

    # -- Step 3: attach the audit triggers ------------------------------
    # Both tables hold user-controlled budget state, so both are in
    # AUDITED_TABLES.  The shared trigger function is already in place from
    # the rebuild migration (a5be2a99ea14); the DROP IF EXISTS pair makes the
    # step idempotent against a re-run.  Trigger name ``audit_<table>``
    # matches the convention the entrypoint health check counts.
    for table in ("recurrence_weekday_anchors", "recurrence_month_anchors"):
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
        op.execute(
            f"CREATE TRIGGER audit_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        )

    # -- Step 4: backfill, then report on what it wrote -----------------
    _backfill(bind)
    _report_window_inversions(bind)

    # -- Step 5: the one rule-level CHECK this step may safely add ------
    # ``max_occurrences`` has no writer at all until step R8, so it can only
    # be NULL and this constraint cannot fire on a live edit.  Its sibling
    # ``end_date >= anchor_date`` is NOT added here; see
    # ``_report_window_inversions`` for why a derived column must not bound a
    # user-authored one, and step R7 for where it lands.
    op.create_check_constraint(
        "ck_recurrence_rules_single_end_bound", "recurrence_rules",
        "end_date IS NULL OR max_occurrences IS NULL", schema="budget",
    )


def downgrade():
    """Remove everything ``upgrade`` added and restore the rewritten intervals.

    Exact, not approximate: ``_refuse_rewritten_intervals`` guaranteed on the
    way up that every ``interval_n`` this migration overwrote held the column
    default, so restoring 1 for precisely the (pattern, value) pairs it wrote
    puts the table back byte for byte.  The four new columns and both subtype
    tables carried no pre-existing data by construction, and dropping a table
    drops its audit trigger with it.
    """
    bind = op.get_bind()
    for pattern_name, written_value in _REWRITTEN_INTERVALS:
        bind.execute(sa.text(
            "UPDATE budget.recurrence_rules r SET interval_n = 1 "
            "  FROM ref.recurrence_patterns p "
            " WHERE p.id = r.pattern_id "
            "   AND p.name = :pattern_name "
            "   AND r.interval_n = :written_value"
        ), {"pattern_name": pattern_name, "written_value": written_value})

    op.drop_constraint(
        "ck_recurrence_rules_single_end_bound", "recurrence_rules",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_recurrence_rules_positive_max_occurrences", "recurrence_rules",
        schema="budget", type_="check",
    )

    op.drop_table("recurrence_month_anchors", schema="budget")
    op.drop_table("recurrence_weekday_anchors", schema="budget")

    # Dropped one call per column, mirroring ``upgrade``'s adds, so the
    # reversal is auditable line by line -- and so a test can confirm each
    # column is really dropped rather than merely mentioned.  Each drop takes
    # its own foreign key with it.  Reverse order of creation.
    op.drop_column("recurrence_rules", "max_occurrences", schema="budget")
    op.drop_column("recurrence_rules", "shift_id", schema="budget")
    op.drop_column("recurrence_rules", "placement_id", schema="budget")
    op.drop_column("recurrence_rules", "anchor_date", schema="budget")
    op.drop_column("recurrence_rules", "unit_id", schema="budget")
