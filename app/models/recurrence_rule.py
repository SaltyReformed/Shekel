"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).

**This table is mid-redesign and carries BOTH vocabularies.**  Plan step R2b
of ``docs/plans/implementation_plan_recurrence_redesign.md`` added the
two-axis columns -- :attr:`unit_id`, :attr:`anchor_date`,
:attr:`placement_id`, :attr:`shift_id`, :attr:`max_occurrences` -- beside the
closed ``pattern_id`` set they replace, and gave the pre-existing
``interval_n`` its second meaning.

Until step R4, these are the columns anything READS:
``recurrence_engine.match_periods`` dispatches on ``pattern_id`` and consults
``day_of_month``, ``month_of_year``, ``start_date``, ``end_date``, and --
in the ``EVERY_N_PERIODS`` branch alone -- ``interval_n`` and
``offset_periods``.  ``start_period_id`` is read separately, by
``resolve_generation_plan``, as the default for its ``effective_from``
argument.  The five columns above are populated (R2b backfilled every
existing row) and read by nothing; four of them are NULLABLE until step R2c
routes every writer through one authoring seam and tightens them.

**A rule edited through the OLD form goes stale, uniformly.**  The update
path rewrites ``pattern_id`` / ``day_of_month`` / ``month_of_year`` without
touching the two-axis tuple, and ``loan_recurrence_sync._sync_loan_cadence``
and ``pay_period_admin._repoint_recurrence_rules`` do the same.  A stale
tuple is indistinguishable from a fresh one, which is why step R2c must
re-derive EVERY rule rather than only the rows still carrying NULLs.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class RecurrenceRule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A recurrence pattern attached to a transaction template."""

    __tablename__ = "recurrence_rules"
    __table_args__ = (
        db.CheckConstraint("interval_n > 0", name="ck_recurrence_rules_positive_interval"),
        db.CheckConstraint("offset_periods >= 0", name="ck_recurrence_rules_valid_offset"),
        # At most ONE closing bound.  A rule that both ends on a date and
        # after N occurrences has two answers to "when does this stop", and
        # the engine would have to pick one; the schema refuses the question.
        db.CheckConstraint(
            "end_date IS NULL OR max_occurrences IS NULL",
            name="ck_recurrence_rules_single_end_bound",
        ),
        db.CheckConstraint(
            "max_occurrences IS NULL OR max_occurrences > 0",
            name="ck_recurrence_rules_positive_max_occurrences",
        ),
        # **There is deliberately NO ``end_date >= anchor_date`` CHECK**, and
        # the redesign's END state (plan section 3) is where it belongs, not
        # here.  ``anchor_date`` is DERIVED and inert; ``end_date`` is
        # user-authored and live.  Fourteen live rules carry a derived anchor
        # in the future, so setting an earlier end date -- which is exactly
        # what the field invites, "entries won't be generated after this
        # date" -- would raise a CheckViolation out of ``update_template``'s
        # autoflush, where no handler catches it.  The user would be unable
        # to stop an annual bill and the projection would keep charging it.
        # Step R7 adds it, once the form collects the anchor and Marshmallow
        # can refuse the pair at the door instead of Postgres refusing it at
        # the flush.
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(
        db.Integer, db.ForeignKey("ref.recurrence_patterns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Used by 'every_n_periods': repeat every N periods.
    interval_n = db.Column(
        db.Integer, nullable=False, default=1, server_default=db.text("1"),
    )
    # Offset within the interval cycle (0 or 1).
    offset_periods = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    # Used by 'monthly' and 'annual' patterns.
    day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
            name="ck_recurrence_rules_dom",
        ),
    )
    # Optional: the actual bill due day when it differs from the
    # pay-period scheduling day.  When NULL, day_of_month serves as
    # both the scheduling day and the due date.
    due_day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "due_day_of_month IS NULL OR "
            "(due_day_of_month >= 1 AND due_day_of_month <= 31)",
            name="ck_recurrence_rules_due_dom",
        ),
    )
    # Used by 'annual' pattern.
    month_of_year = db.Column(
        db.Integer,
        db.CheckConstraint(
            "month_of_year IS NULL OR (month_of_year >= 1 AND month_of_year <= 12)",
            name="ck_recurrence_rules_moy",
        ),
    )
    # Optional: the pay period where recurrence should begin.  A WEAK bound --
    # it seeds ``effective_from`` only when the caller passes none
    # (``recurrence_engine.resolve_generation_plan``), so a caller supplying its
    # own effective_from (``transfer_recurrence.regenerate_for_template``,
    # the unarchive path) silently bypasses it.  It is the form's "First
    # paycheck" affordance, NOT a validity bound; ``start_date`` below is the
    # bound.
    start_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional start date -- recurrence generates nothing whose period ends
    # before this date.  NULL means unbounded (no start), which is every rule
    # the user configures by hand.
    #
    # The SYMMETRIC partner of ``end_date`` below, and enforced the same way:
    # ``recurrence_engine.match_periods`` filters candidate periods on it
    # UNCONDITIONALLY, so -- unlike ``start_period_id`` -- no caller can bypass
    # it by supplying its own ``effective_from``.  Together the two columns are
    # the rule's validity window.
    #
    # Written only by ``loan_recurrence_sync.sync_recurring_payment_bounds``,
    # which derives it from the loan's FIRST CONTRACTUAL INSTALLMENT (plan step
    # C9a): a loan payment cannot precede the loan.  A payment generated before
    # origination is erased by the fold (it splits against a zero balance and
    # the origination anchor then resets over it), so it debits cash for a loan
    # that does not exist yet -- measured at $3,220.92 on a mortgage closing one
    # month out.
    start_date = db.Column(db.Date, nullable=True)
    # Optional end date -- recurrence stops generating after this date.
    # NULL means indefinite (no end).
    end_date = db.Column(db.Date, nullable=True)

    # ── Two-axis model (plan step R2b) ───────────────────────────────
    #
    # All five are NULLABLE only because they were added to a populated
    # table: step R2b backfilled every existing row and step R2c tightens
    # the four non-optional ones to NOT NULL once every writer supplies
    # them (``max_occurrences`` stays genuinely optional -- it is one of
    # the two mutually exclusive closing bounds).  A rule created between
    # those two steps by a writer that predates the seam carries NULLs and
    # is inert: nothing READS these columns until step R4.

    # The cadence UNIT ``interval_n`` counts.  The axis the old pattern set
    # lacked: Monthly / Quarterly / Semi-Annual / Annual were one idea with
    # the integer baked into the NAME, so "every other month" had nowhere
    # to live.  RESTRICT because the four seeded units are application
    # invariants -- deleting one would orphan every rule naming it.
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.recurrence_units.id",
            name="fk_recurrence_rules_unit_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    # The FIRST occurrence, and therefore the rule's phase AND its opening
    # bound in one value: occurrences are ``anchor_date`` plus multiples of
    # ``interval_n`` units, so nothing before it can be generated.  That is
    # what retires ``start_period_id`` (weak -- a caller's own
    # ``effective_from`` silently overrides it, defect D2) and
    # ``offset_periods`` (an INDEX, which a schedule rebuild invalidates
    # while a date survives it, defect D1).
    #
    # An "occurrence" here is the calendar date the rule TARGETS, which
    # ``placement_id`` then carries onto a pay period; it is not itself a
    # payday.  For a ``period``-unit rule the anchor is the start of the
    # first qualifying period, which may fall BEFORE ``start_date`` -- a
    # period qualifies on its end date -- so ``start_date`` remains the
    # loan's origination bound and the anchor does not subsume it.
    #
    # For a MONTH/YEAR-unit rule whose nominal day is 29-31, the anchor
    # month may have CLAMPED that day (April has no 31st).  The nominal day
    # is then carried by a ``budget.recurrence_month_anchors`` row, present
    # exactly when the clamp lost information; see
    # :class:`~app.models.recurrence_anchors.RecurrenceMonthAnchor`.
    anchor_date = db.Column(db.Date, nullable=True)
    # How an occurrence DATE maps onto the pay PERIOD a row lives in.  A
    # real user choice, not a derived detail: it is the axis today's
    # Monthly and Monthly First patterns differ on.
    placement_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.period_placements.id",
            name="fk_recurrence_rules_placement_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    # Weekend/holiday adjustment for the occurrence date.  Backfilled to
    # ``none`` for every rule so step R8 turns behaviour ON rather than
    # adding a column to a populated table.
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id",
            name="fk_recurrence_rules_shift_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    # Count-bounded end: stop after this many occurrences.  Genuinely
    # optional and mutually exclusive with ``end_date`` (see the
    # ``ck_recurrence_rules_single_end_bound`` CHECK above).  No writer
    # sets it until step R8; NULL means "not count-bounded".
    max_occurrences = db.Column(db.Integer, nullable=True)

    # Relationships
    pattern = db.relationship("RecurrencePattern", lazy="joined")
    start_period = db.relationship("PayPeriod", lazy="joined")
    # The two 0-or-1 subtypes.  ``uselist=False`` because the UNIQUE
    # constraint on each child's ``recurrence_rule_id`` makes at most one
    # row possible, and ``lazy="select"`` (not ``joined`` like the two
    # above) because nothing reads them until steps R4/R8 -- an eager join
    # here would cost every rule load for no reader.  ``passive_deletes``
    # defers to the FK's ON DELETE CASCADE rather than loading the child to
    # delete it.
    #
    # A rule carries at most ONE of these: it fires on a day-of-month OR on
    # an nth-weekday, never both.  DDL cannot say "at most one row across
    # two tables", so that exclusivity is a write-door invariant -- step R8
    # owns it in the authoring seam.
    weekday_anchor = db.relationship(
        "RecurrenceWeekdayAnchor",
        uselist=False, lazy="select",
        cascade="all, delete-orphan", passive_deletes=True,
        back_populates="rule",
    )
    month_anchor = db.relationship(
        "RecurrenceMonthAnchor",
        uselist=False, lazy="select",
        cascade="all, delete-orphan", passive_deletes=True,
        back_populates="rule",
    )

    def __repr__(self):
        return f"<RecurrenceRule id={self.id} pattern={self.pattern_id}>"
