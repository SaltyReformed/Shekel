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

**Every write goes through one door** (plan step R2c-1,
:mod:`app.services.recurrence`).  A rule is authored from a complete
:class:`ResolvedRecurrence`, so the two vocabularies are always the same
function of the same input.  Before that door, five sites constructed a rule
and four mutated one in place -- the form's update path,
``loan_recurrence_sync._sync_loan_cadence``, its end-date sibling, and
``pay_period_admin._repoint_recurrence_rules`` -- each rewriting the closed-set
columns without touching the two-axis tuple, leaving a stale tuple no query
could distinguish from a fresh one.  Step R2c-3 re-derives EVERY rule (not
only the rows still carrying NULLs) and then tightens the four columns.
"""

from dataclasses import dataclass
from datetime import date

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin
from app.models.recurrence_anchors import RecurrenceMonthAnchor


@dataclass(frozen=True)
class ResolvedRecurrence:  # pylint: disable=too-many-instance-attributes
    """Every column of one recurrence rule, in one internally-consistent value.

    The row's complete state as a value, which is why it lives here beside the
    row rather than in the service that computes it: it is what
    :meth:`RecurrenceRule.create` and :meth:`RecurrenceRule.reauthor` accept,
    and the only thing they accept.

    **A rule is written whole or not at all, and that is the point.**  This
    table carries TWO vocabularies for one cadence -- the closed ``pattern_id``
    set the engine still reads, and the two-axis columns plan step R2b added
    beside it -- so a partial write is a rule whose halves disagree, and no
    query can tell a stale half from a fresh one.  Taking the complete value
    means there is no intermediate state to leave behind: the caller never
    holds the halves separately, so it cannot write one and forget the other.
    :func:`app.services.recurrence.resolve` is the single producer, and it
    emits both halves from one input.

    Pylint: ``too-many-instance-attributes`` (16/7) -- this value IS the row,
    mirroring the columns of ``budget.recurrence_rules`` plus the 0-or-1
    ``recurrence_month_anchors`` day, and it is read as a flat unit by its
    single consumer.  The one arguable sub-group -- closed-set columns vs
    two-axis columns -- is NOT nested on purpose: ``interval_n`` belongs to
    both readings, and the split dissolves entirely at plan step R9 when the
    closed-set half is dropped, so nesting would encode a transitional shape
    into every consumer for no invariant gained.  Mirrors the
    ``transfer_service.TransferSpec`` precedent.

    Attributes:
        user_id: The owning user.
        pattern_id: The closed-set pattern; still what the engine dispatches
            on until plan step R4.
        interval_n: The cadence count, on BOTH readings -- the pay-period
            interval for ``Every N Periods``, and the two-axis interval
            (3 for Quarterly, 6 for Semi-Annual, 1 elsewhere) otherwise.
        offset_periods: Phase within the ``Every N Periods`` cycle.
        day_of_month: Scheduling day for the calendar patterns.
        due_day_of_month: The real bill due day when it differs from the
            scheduling day.
        month_of_year: Cycle-start month for quarterly / semi-annual / annual.
        start_period_id: The form's "First paycheck" choice.
        start_date: The rule's opening validity bound.
        end_date: The rule's closing validity bound.
        unit_id: The two-axis cadence unit.
        anchor_date: The FIRST occurrence -- the rule's phase, day and opening
            bound in one value.
        placement_id: How an occurrence maps onto a pay period.
        shift_id: Weekend / holiday adjustment; always ``none`` until plan
            step R8.
        max_occurrences: The count-bounded end.
        nominal_day: The day the user meant, when the anchor month was too
            short to hold it -- the ``budget.recurrence_month_anchors`` row's
            value, or ``None`` when no such row belongs to this rule.  Presence
            is the discriminator (ruling R-R3).
    """

    user_id: int
    pattern_id: int
    interval_n: int
    offset_periods: int
    day_of_month: int | None
    due_day_of_month: int | None
    month_of_year: int | None
    start_period_id: int | None
    start_date: date | None
    end_date: date | None
    unit_id: int
    anchor_date: date
    placement_id: int
    shift_id: int
    max_occurrences: int | None
    nominal_day: int | None


class RecurrenceRule(  # pylint: disable=too-many-instance-attributes
    UserScopedMixin, CreatedAtMixin, db.Model,
):
    """A recurrence pattern attached to a transaction template.

    **Written only through :meth:`create` and :meth:`reauthor`**, each of
    which takes a complete :class:`ResolvedRecurrence`.  See that class for
    why a whole-value write is the invariant rather than a convention.

    Pylint: ``too-many-instance-attributes`` (16/7) -- the count is the
    table's own column count, assigned together in :meth:`reauthor` because a
    rule is written whole; suppressing per-column assignment behind a loop
    would hide the write from the reader without removing a single column.
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
    # payday.  For a ``period``-unit rule the anchor is the rule's own
    # effective BOUND (ruling R-R8) -- the greatest of its ``start_date``,
    # its start period's start, and the schedule's opening payday -- rather
    # than a period boundary, which keeps it derivable when the bound falls
    # past the materialised horizon.  ``Every N Periods`` is the one
    # exception: its phase is unrepresentable in a bare date, so its anchor
    # advances to the first period boundary that satisfies the phase.
    #
    # **The anchor is a function of the SCHEDULE as well as the rule**, so a
    # rebuilt schedule re-authors every rule the owner has
    # (``pay_period_admin._repoint_recurrence_rules``), not only the ones
    # whose start period the wipe nulled.
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

    @classmethod
    def create(cls, resolved: ResolvedRecurrence) -> "RecurrenceRule":
        """Build a new rule from a complete resolved value.

        Does NOT add the rule to the session: the caller owns the transaction
        boundary (``app.services.recurrence.author_rule`` adds and flushes).

        Args:
            resolved: Every column of the rule to build, from
                :func:`app.services.recurrence.resolve`.

        Returns:
            The unsaved :class:`RecurrenceRule`.
        """
        rule = cls()
        rule.reauthor(resolved)
        return rule

    def reauthor(self, resolved: ResolvedRecurrence) -> None:
        """Replace this rule's entire authored state.

        The ONLY way a rule's cadence changes, and it takes the whole value
        rather than a field: this table carries two vocabularies for one
        cadence until plan step R4 cuts the engine over, so a field-at-a-time
        edit is how the halves come to disagree.  Replacing them together
        means a stale half is not a state the row can reach.

        The ``budget.recurrence_month_anchors`` row moves with it -- created,
        updated, or DELETED -- because it too is derived: a day change that
        stops the anchor month clamping must take the row with it, or the rule
        keeps firing on a day the user no longer means.

        Args:
            resolved: Every column of the rule's new state, from
                :func:`app.services.recurrence.resolve`.
        """
        self.user_id = resolved.user_id
        self.pattern_id = resolved.pattern_id
        self.interval_n = resolved.interval_n
        self.offset_periods = resolved.offset_periods
        self.day_of_month = resolved.day_of_month
        self.due_day_of_month = resolved.due_day_of_month
        self.month_of_year = resolved.month_of_year
        self.start_period_id = resolved.start_period_id
        self.start_date = resolved.start_date
        self.end_date = resolved.end_date
        self.unit_id = resolved.unit_id
        self.anchor_date = resolved.anchor_date
        self.placement_id = resolved.placement_id
        self.shift_id = resolved.shift_id
        self.max_occurrences = resolved.max_occurrences
        self._apply_month_anchor(resolved.nominal_day)

    def _apply_month_anchor(self, nominal_day: int | None) -> None:
        """Create, update, or remove the 0-or-1 month-anchor row.

        Presence is the discriminator (ruling R-R3), so ``None`` must DELETE
        an existing row rather than leave it: a rule edited from day 31 to day
        15 no longer has a clamped day, and a surviving anchor would restore
        the 31st on the next read.  ``delete-orphan`` on the relationship is
        what turns the detach into a delete.

        Args:
            nominal_day: The day the anchor month clamped, or ``None`` when
                ``anchor_date`` holds the day the rule means.
        """
        if nominal_day is None:
            self.month_anchor = None
            return
        if self.month_anchor is None:
            self.month_anchor = RecurrenceMonthAnchor(nominal_day=nominal_day)
            return
        self.month_anchor.nominal_day = nominal_day

    def __repr__(self):
        return f"<RecurrenceRule id={self.id} pattern={self.pattern_id}>"
