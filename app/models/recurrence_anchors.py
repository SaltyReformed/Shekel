"""
Shekel Budget App -- The Recurrence Weekday Anchor (budget schema)

The 0-or-1 satellite table of
:class:`~app.models.recurrence_rule.RecurrenceRule`, added by plan step R2b of
``docs/plans/implementation_plan_recurrence_redesign.md``.

**Why a subtype rather than more columns on the rule.**  The defect the
redesign exists to remove is a wide sparse table: eight columns on
``budget.recurrence_rules`` whose validity depended on ``pattern_id``, with no
constraint tying presence to pattern, so the engine papered over a malformed
rule (``rule.month_of_year or 1``) instead of failing loud.  Adding a nullable
``nth_week`` column would rebuild exactly that defect one column at a time.
Here PRESENCE is the discriminator and the FK's UNIQUE constraint enforces the
cardinality, so every column on every row is meaningful.

**HALF of that argument was overturned by ruling R-R16** (2026-08-14), and it
is left standing rather than deleted because the half that survives is the
useful one.  ``budget.recurrence_rules.nominal_day`` is a nullable column on
the rule, and it does NOT rebuild the sparse-table defect -- because its
absence is the discriminator and its presence is CONSTRAINED
(``ck_recurrence_rules_nominal_day``: 29-31, strictly greater than the day
``starts_on`` carries, and only where that month actually clamped).  What made
the old columns a defect was not their nullability; it was that nothing tied
presence to meaning.

**``RecurrenceMonthAnchor`` was this module's SECOND table and plan step R7c-c
deleted it, unwritten** (migration ``d9f5c1a48b73``).  It was to hold the day a
clamped anchor MEANT once the anchor became a stored column, and R-R16 put that
day on the rule instead, so it never gained a writer.
:class:`RecurrenceWeekdayAnchor` stays a real subtype: ``(nth_week, weekday)``
is two fields with their own domain, and ``-1`` (the LAST weekday of the month)
cannot be recovered from a date at all.

**Why it carries a surrogate ``id`` rather than the design's
``recurrence_rule_id PK``.**  It holds user-controlled ``budget`` state, so it
is in ``app.audit_infrastructure.AUDITED_TABLES`` -- and
``system.audit_trigger_func`` assigns ``v_row_id := NEW.id``, which raises
``record "new" has no field "id"`` on a table without that column (measured
against a probe table on the dev database, 2026-08-05).  An ``id`` primary key
with ``UNIQUE (recurrence_rule_id)`` enforces the same 0-or-1 cardinality, and
matches every other table in the schema.

**A rule anchors on a day-of-month OR on an nth-weekday, never both.**  With
one table left that is a CHECK against ``recurrence_rules.nominal_day`` rather
than the "at most one row across two tables" invariant DDL could not express;
step R8, this table's first writer, owns it.
"""

from app.extensions import db


class RecurrenceWeekdayAnchor(db.Model):
    """Nth-weekday-of-month anchor for a recurrence rule (0 or 1 per rule).

    Present exactly when a rule fires on "the third Friday" rather than on a
    day of the month.  The rule's first occurrence still fixes WHEN it starts
    -- this row says how to find the occurrence in every LATER cycle, which a
    date alone cannot: "the 15th" is a stable day number, "the third Friday"
    is not.

    **Created empty, and step R8 is its first writer.**  No rule references it
    before then, so a NULL-safe reader is not needed -- the absence of a row
    simply means the rule anchors on a day of the month.

    Attributes:
        nth_week: Which occurrence of ``weekday`` within the month.  ``1``-``5``
            count forward from the start of the month; ``-1`` means the LAST
            one.  ``0`` is refused by the CHECK -- there is no zeroth Friday.
        weekday: The day of week, in ``datetime.date.weekday()`` terms, so the
            engine can compare without a conversion table: 0 = Monday through
            6 = Sunday.
    """

    __tablename__ = "recurrence_weekday_anchors"
    __table_args__ = (
        db.CheckConstraint(
            "nth_week BETWEEN -1 AND 5 AND nth_week <> 0",
            name="ck_recurrence_weekday_anchors_nth_week",
        ),
        db.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_recurrence_weekday_anchors_weekday",
        ),
        db.UniqueConstraint(
            "recurrence_rule_id",
            name="uq_recurrence_weekday_anchors_rule",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    recurrence_rule_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.recurrence_rules.id",
            name="fk_recurrence_weekday_anchors_rule_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    nth_week = db.Column(db.Integer, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)

    rule = db.relationship("RecurrenceRule", back_populates="weekday_anchor")

    def __repr__(self):
        return (
            f"<RecurrenceWeekdayAnchor rule_id={self.recurrence_rule_id} "
            f"nth={self.nth_week} weekday={self.weekday}>"
        )
