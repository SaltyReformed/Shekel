"""
Shekel Budget App -- Recurrence Anchor Subtypes (budget schema)

The two 0-or-1 satellite tables of :class:`~app.models.recurrence_rule.RecurrenceRule`,
added by plan step R2b of
``docs/plans/implementation_plan_recurrence_redesign.md``.

**Why subtypes rather than more columns on the rule.**  The defect the
redesign exists to remove is a wide sparse table: eight columns on
``budget.recurrence_rules`` whose validity depends on ``pattern_id``, with no
constraint tying presence to pattern, so the engine papers over a malformed
rule (``rule.month_of_year or 1``) instead of failing loud.  Adding a nullable
``nominal_day`` or ``nth_week`` column would rebuild exactly that defect one
column at a time.  Here PRESENCE is the discriminator and the FK's UNIQUE
constraint enforces the cardinality, so every column on every row is
meaningful.

**Why each carries a surrogate ``id`` rather than the design's
``recurrence_rule_id PK``.**  Both tables hold user-controlled ``budget``
state, so both are in ``app.audit_infrastructure.AUDITED_TABLES`` -- and
``system.audit_trigger_func`` assigns ``v_row_id := NEW.id``, which raises
``record "new" has no field "id"`` on a table without that column (measured
against a probe table on the dev database, 2026-08-05).  An ``id`` primary key
with ``UNIQUE (recurrence_rule_id)`` enforces the same 0-or-1 cardinality, and
matches every other table in the schema.

**A rule carries at most one of the two.**  A recurrence fires on a
day-of-month OR on an nth-weekday-of-month, never both.  DDL cannot express
"at most one row across two tables", so that exclusivity is a WRITE-DOOR
invariant: step R8 owns it in the authoring seam and pins it with a test.
"""

from app.extensions import db


class RecurrenceWeekdayAnchor(db.Model):
    """Nth-weekday-of-month anchor for a recurrence rule (0 or 1 per rule).

    Present exactly when a rule fires on "the third Friday" rather than on a
    day of the month.  The rule's ``anchor_date`` still holds the first
    occurrence -- this row says how to find the occurrence in every LATER
    cycle, which a date alone cannot: "the 15th" is a stable day number,
    "the third Friday" is not.

    **Created empty by step R2b; step R8 is its first writer.**  No rule
    references it before then, so a NULL-safe reader is not needed -- the
    absence of a row simply means the rule anchors on a day of the month.

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


class RecurrenceMonthAnchor(db.Model):
    """The nominal day a rule's anchor month was too short to hold.

    Present exactly when a MONTH- or YEAR-unit rule's day of the month is
    29-31 AND the anchor's own month clamped it -- and absent otherwise, which
    is why the ordinary case costs nothing.

    **This row is what stops a month-end rule from decaying.**  ``anchor_date``
    is a real DATE, so it cannot hold "the 31st" when the anchor month is
    April; it holds 2026-04-30 instead, and an engine reading the day back off
    the anchor would then fire on the 30th forever:

    ```text
    monthly day=31, first occurrence April 2026
      correct : Apr 30  May 31  Jun 30  Jul 31  Aug 31  Sep 30  Oct 31  Nov 30
      from the anchor's own day:
                Apr 30  May 30  Jun 30  Jul 30  Aug 30  Sep 30  Oct 30  Nov 30
                                                    4 of the 8 wrong
    ```

    Five of the twelve possible start months (Feb, Apr, Jun, Sep, Nov) clamp a
    day-31 rule; a day-30 rule anchored in February is wrong 7 times in 8; and
    an annual February-29 rule anchored in a common year would never fire on
    the 29th again.  Zero live rules were affected when this table was created
    (the only day-31 rule is annual in March, which has 31 days), so it exists
    for what the model PERMITS going forward -- and the failure is silent: the
    user sees a plausible date, never an error.

    The resolved nominal day is therefore
    ``month_anchor.nominal_day if month_anchor else anchor_date.day``, clamped
    per occurrence month exactly as ``recurrence_engine._match_monthly`` clamps
    ``day_of_month`` today.

    Attributes:
        nominal_day: The day the user meant, 29-31.  Below 29 no month can
            clamp it, so a row would carry no information and the CHECK
            refuses one.
    """

    __tablename__ = "recurrence_month_anchors"
    __table_args__ = (
        db.CheckConstraint(
            "nominal_day BETWEEN 29 AND 31",
            name="ck_recurrence_month_anchors_nominal_day",
        ),
        db.UniqueConstraint(
            "recurrence_rule_id",
            name="uq_recurrence_month_anchors_rule",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    recurrence_rule_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.recurrence_rules.id",
            name="fk_recurrence_month_anchors_rule_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    nominal_day = db.Column(db.Integer, nullable=False)

    rule = db.relationship("RecurrenceRule", back_populates="month_anchor")

    def __repr__(self):
        return (
            f"<RecurrenceMonthAnchor rule_id={self.recurrence_rule_id} "
            f"nominal_day={self.nominal_day}>"
        )
