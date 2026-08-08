"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).

**This table holds only what a user AUTHORS.**  Plan step R2d of
``docs/plans/implementation_plan_recurrence_redesign.md`` (developer ruling,
2026-08-07) settled that the redesign's two-axis values -- the cadence unit,
the first occurrence, the placement, the business-day shift -- are a
DERIVATION over the columns below plus the owner's pay-period schedule, and
that a derivation must not be stored beside its own inputs.  A stored
derivation is a cache; a cache drifts the moment one writer moves one side
alone, and nothing can police that completely.  So it is not stored:
:func:`app.services.recurrence.resolve` computes the two-axis view on demand,
from one producer, and there is no second copy to disagree with the first.

Those four values become columns -- authored, NOT NULL, from one backfill, in
the same transaction that drops the closed-set columns they were derived from
-- at plan step R7c, where the recurrence form starts collecting them.

What READS this table today: ``recurrence_engine.match_periods``, which since
plan step R4a reads the row WHOLE -- it builds a ``RecurrenceSpec`` from every
authored column and hands it to ``app.services.recurrence.resolve``.  There is
no per-pattern dispatch and no branch: ``interval_n`` is read (and refused when
below 1) for every pattern, ``day_of_month`` / ``month_of_year`` are read for
the calendar families and refused outside their CHECK domains for all of them,
``start_period_id`` is read HERE now rather than only by
``resolve_generation_plan``, and ``offset_periods`` is read only when the
schedule handed in does not contain the start period (plan ledger row D24).
``max_occurrences`` has no reader or writer until step R8.

**Every write goes through one door** (:mod:`app.services.recurrence`).  A
caller states what it AUTHORS -- a ``RecurrenceSpec``, never a column -- and
the seam writes the whole spec, deriving ``offset_periods`` from the chosen
start period on every write.  That derivation on every write, rather than only
on create, is what closes defect **D1**: the edit path used to write the
schema default unconditionally, re-phasing every future occurrence of an
``Every N Periods`` rule on an amount-only edit.  Nothing in ``app/`` or
``scripts/`` constructs or mutates this model outside that seam.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class RecurrenceRule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A recurrence pattern attached to a transaction template.

    **Written only through :mod:`app.services.recurrence`**, which takes a
    complete authored spec rather than a column at a time.  See the module
    docstring for why the two-axis values the redesign adds are computed
    rather than stored.
    """

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
        # there is no ``anchor_date`` column for one to name: the anchor is
        # computed, not stored (plan step R2d).  The constraint lands with the
        # column at step R7c, together with the Marshmallow validator that can
        # refuse the pair at the door -- because ``end_date`` is user-authored
        # and live, and a CHECK against a value the form does not yet collect
        # would surface as an unhandled CheckViolation out of
        # ``update_template``'s autoflush, leaving the user unable to stop a
        # recurring bill.
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
    # Phase within the interval cycle: an ``Every N Periods`` rule fires where
    # ``(period_index - offset_periods) % interval_n == 0``.  DERIVED from the
    # rule's start period on every write (no form renders an input for it), so
    # a stale phase is not a state the row can reach -- defect D1.
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
    # The SYMMETRIC partner of ``end_date`` below, and unbypassable the same
    # way: since plan step R4a both bounds bind the OCCURRENCE rather than the
    # candidate period -- this one through the anchor
    # ``app.services.recurrence.resolve`` derives (the GREATEST of the
    # schedule's opening, this date, and the start period's start), that one
    # through the occurrence engine's stopping bound.  Neither is expressible
    # through a caller's ``effective_from``, so -- unlike ``start_period_id``
    # -- no caller can bypass them.  Together the two columns are the rule's
    # validity window.
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
    # Count-bounded end: stop after this many occurrences.  Genuinely
    # optional and mutually exclusive with ``end_date`` (see the
    # ``ck_recurrence_rules_single_end_bound`` CHECK above).  No writer
    # sets it until plan step R8; NULL means "not count-bounded".
    max_occurrences = db.Column(db.Integer, nullable=True)

    # Relationships
    pattern = db.relationship("RecurrencePattern", lazy="joined")
    start_period = db.relationship("PayPeriod", lazy="joined")
    # The two 0-or-1 subtypes.  ``uselist=False`` because the UNIQUE
    # constraint on each child's ``recurrence_rule_id`` makes at most one
    # row possible, and ``lazy="select"`` (not ``joined`` like the two
    # above) because nothing reads them until steps R7c/R8 -- an eager join
    # here would cost every rule load for no reader.  ``passive_deletes``
    # defers to the FK's ON DELETE CASCADE rather than loading the child to
    # delete it.
    #
    # BOTH ARE EMPTY, and their first writers are plan steps R7c
    # (``month_anchor``, once ``anchor_date`` becomes a stored column and can
    # therefore clamp) and R8 (``weekday_anchor``).  A rule carries at most
    # ONE of them: it fires on a day-of-month OR on an nth-weekday, never
    # both.  DDL cannot say "at most one row across two tables", so that
    # exclusivity is a write-door invariant -- step R8 owns it in the
    # authoring seam.
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
