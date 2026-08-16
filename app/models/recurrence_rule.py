"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).

**This table states its recurrence in SIX columns and carries no second
statement of any of them.**  ``interval_n`` / ``unit_id`` / ``placement_id`` /
``shift_id`` / ``starts_on`` / ``nominal_day`` are what a caller AUTHORS and
what every reader takes; beside them sit only ``due_day_of_month`` -- the
servicer's date for a bill the cadence schedules elsewhere -- and the closing
bound's exclusive arc.

**Seven columns were DROPPED at plan step R7c-c** (migration
``d9f5c1a48b73``), each a derived encoding the write door maintained:
``pattern_id`` (a closed set of eight names for cadences the two axes state
directly), ``day_of_month`` and ``month_of_year`` (the cycle's day and its
residue class, both carried by the first occurrence), ``start_date`` (the
opening bound, which the first occurrence IS), ``start_period_id`` (the
paycheck a rule started in), ``offset_periods`` (the cycle phase, derived from
the first occurrence on every read), and ``interval_n``'s ENCODED value -- the
column held ``1`` for every pattern whose interval was baked into its name, so
a Quarterly rule read as monthly at face value until that migration re-pointed
it.

Plan step R2d (developer ruling, 2026-08-07) refused to store the two-axis
values while they were a DERIVATION over the closed-set columns plus the
owner's schedule, because a stored derivation is a cache and a cache drifts the
moment one writer moves one side alone.  That is not what they are: the form
collects ``starts_on`` and the loan sync writes it from a contract, so there is
no input left for it to lag.  A stored derivation is a cache; a stored authored
value is a fact.  (A day-less LOAN payment is the one shape still measured
against the schedule -- see ``recurrence._authoring._author`` and plan ledger
row **D39**, which plan step R5 owns.)

Ruling **R-R16** is what collapsed the four anchor columns: the first
occurrence is the earliest thing a cadence produces, its day is the cycle's day
and its month is the cycle's month.  Plan ledger row **D28** measured what
keeping them apart would have cost, at 18 of 24 live multi-month rules firing
in the wrong months forever.

``end_date`` and ``max_occurrences`` are ONE authored value above this table
(``app.services.recurrence.EndBound``, plan step R7b-3): the occurrence walk
has read both since plan step R3, and the write door splits the single bound
into this pair on the way in and rejoins it on the way out.  That is what makes
the exclusive arc below structural rather than maintained -- no value in the
application can state two closing bounds, so nothing has to check.

**Every write goes through one door** (:mod:`app.services.recurrence`).  A
caller states what it AUTHORS -- a ``RecurrenceSpec``, never a column -- and
the seam writes the whole spec.  Nothing in ``app/`` or ``scripts/`` constructs
or mutates this model outside that seam.
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
        # At most ONE closing bound -- the EXCLUSIVE ARC this pair of nullable
        # columns is.  A rule that both ends on a date and after N occurrences
        # has two answers to "when does this stop", and the engine would have
        # to pick one; the schema refuses the question.
        #
        # SQL has no sum type to write "one of three shapes" in, which is why
        # the arc is a pair plus a CHECK here and ONE value above the door
        # (``app.services.recurrence.EndBound``, plan step R7b-3).  This
        # constraint therefore guards writers that never see that value -- a
        # restore, a hand edit, a raw-SQL migration -- rather than the
        # application, which cannot express the violation.
        db.CheckConstraint(
            "end_date IS NULL OR max_occurrences IS NULL",
            name="ck_recurrence_rules_single_end_bound",
        ),
        db.CheckConstraint(
            "max_occurrences IS NULL OR max_occurrences > 0",
            name="ck_recurrence_rules_positive_max_occurrences",
        ),
        # ``nominal_day`` records a day the FIRST OCCURRENCE'S MONTH clamped,
        # and nothing else (ruling R-R3).  Three conjuncts and none is implied
        # by the others: the range keeps it a real month-end day; the
        # comparison keeps it from restating a day ``starts_on`` already
        # carries -- a ``nominal_day`` of 15 beside a 15th would be the second
        # representation the D28 ruling removes; and the CLAMP EQUALITY keeps
        # it from sitting beside a date that was never clamped at all.
        #
        # **The third conjunct landed at plan step R7c-b and it retired a
        # FENCE.**  Without it ``(starts_on = 2026-04-15, nominal_day = 30)``
        # passes -- 30 is in range and exceeds 15 -- and April HAS a 30th, so
        # the rule would fire on a day the date does not name.  Only a runtime
        # guard in ``recurrence._occurrence._require_generable`` caught that,
        # and a guard whose entire reachability condition is "the schema cannot
        # say it" is what this project removes rather than tests.  Presence now
        # IMPLIES the clamp happened, so the absence has ONE meaning.
        #
        # ``EXTRACT(day FROM <date>)`` is IMMUTABLE for a ``date`` argument
        # (it lowers to ``date_part(text, date)``; the STABLE spellings are the
        # ``timestamptz`` ones), and ``date_trunc`` is cast to ``::timestamp``
        # for the same reason -- which is what lets both appear in a CHECK at
        # all.  Verified against the live server rather than assumed.
        db.CheckConstraint(
            "nominal_day IS NULL OR ("
            "nominal_day BETWEEN 29 AND 31 "
            "AND nominal_day > EXTRACT(day FROM starts_on) "
            "AND EXTRACT(day FROM starts_on) = LEAST(nominal_day, "
            "EXTRACT(day FROM (date_trunc('month', starts_on::timestamp) "
            "+ INTERVAL '1 month - 1 day'))))",
            name="ck_recurrence_rules_nominal_day",
        ),
        # **There is deliberately NO ``end_date >= starts_on`` CHECK**, and the
        # absence is a ruling rather than an omission (developer ruling
        # 2026-08-15, plan step R7c-b).  These two columns hold two different
        # KINDS of fact: what a user AUTHORS about a repeating definition,
        # where a stop before the start is a mistake to report, and what the
        # app DERIVES for a recurring loan payment, where an EMPTY window is a
        # legitimate answer -- a loan paid off before its first contractual
        # installment owes nothing, and ``loan_recurrence_sync`` states that by
        # writing a payoff below the installment date.  A CHECK cannot tell the
        # two apart, so it would turn a correct derived state into an unhandled
        # ``CheckViolation`` out of a balance true-up.  The invariant is held at
        # the two AUTHORING doors instead
        # (``schemas/validation/_helpers.require_end_bound_after_start`` and
        # ``_recurrence_form_refusals.refuse_inverted_window``), and the CHECK
        # lands with the step that stops persisting the derived window, when
        # every row is user-authored.  See the R7c-b migration's own docstring
        # for the measured case.
        #
        # How far the application's calendar reaches, mirrored on the column
        # for a writer that never sees a schema -- the same job
        # ``ck_template_amount_versions_effective_date_range`` does for the
        # other user-authored date, and the same two dates
        # (``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX``).
        #
        # **It backs a measured 500 rather than a hypothetical one.**  Past the
        # saved horizon the pay calendar PROJECTS the covering paycheck by
        # adding ``cadence_days`` to a start; a ``starts_on`` near
        # ``date.max`` overflows that addition with an ``OverflowError`` from
        # outside the recurrence package's error hierarchy, so the recurrence
        # preview -- which reads the value from ``request.args``, where no
        # schema stands -- answered a stack trace to any signed-in user.
        # ``_resolution._require_authored_start_window`` is the door-side
        # mirror this backs.
        db.CheckConstraint(
            "starts_on BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'",
            name="ck_recurrence_rules_starts_on_range",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # ---- The cadence: the two axes, their interval and their anchor ------
    #
    # **The WHOLE of what a rule says about when it fires, since plan step
    # R7c-c.**  They landed NULLABLE at R7c-a beside the closed-set columns
    # they replace, were backfilled and dual-written there, became
    # authoritative and ``NOT NULL`` at R7c-b, and the encoding beside them was
    # dropped at R7c-c -- the expand / migrate / contract ruling R-R18 laid
    # out, with the destructive DDL last and no translation shim in any leaf.
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.recurrence_units.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_unit_id",
        ),
        nullable=False,
    )
    placement_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.period_placements.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_placement_id",
        ),
        nullable=False,
    )
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_shift_id",
        ),
        nullable=False,
    )
    # The rule's FIRST OCCURRENCE, and since the developer ruling of
    # 2026-08-14 (plan ledger row D28) that is its ONE meaning for every unit:
    # for a calendar cadence the first date the cadence fires on, for a
    # pay-period cadence the payday of the first paycheck it bills in.  Nothing
    # is generated before it, and its position in the cycle IS the rule's
    # phase -- which is why ``day_of_month`` and ``month_of_year`` have nothing
    # left to say once R7c-b's readers move here.
    #
    # The ruling REPLACED this arc's earlier specification, which made the
    # column the opening validity BOUND.  A bound is not in the cycle's residue
    # class, so it cannot carry the phase: measured on a 2026-08-14 production
    # clone, 18 of the 24 live multi-month rules would have fired in the wrong
    # months forever under that reading.
    #
    # Produced by ``recurrence._resolution.resolve``, which normalises a
    # pay-period cadence's authored date onto the payday hosting it and returns
    # every other unit's verbatim -- and that answer is also what the R7c-a
    # migration's SQL backfill is proven against.  ``NOT NULL`` from R7c-b,
    # which is the leaf that made every reader take it.
    starts_on = db.Column(db.Date, nullable=False)
    # The day the rule MEANS when ``starts_on``'s own month was too short to
    # hold it -- April has no 31st, so a day-31 rule first occurring there
    # carries ``starts_on = 2026-04-30`` and ``nominal_day = 31``.  NULL when
    # the date holds the day itself, which is every rule whose day is 1-28,
    # every rule that does not fire on a day of the month at all, and all 46
    # live rules as of 2026-08-14.  Presence is the discriminator (ruling
    # R-R3), and it is what stops a month-end rule decaying to the 30th
    # forever.
    #
    # **The domain is 29-31, not 1-31**, and the difference is the ruling: a
    # value at or below ``starts_on``'s own day would be a SECOND statement of
    # a day the date already holds, which is the two-representations defect
    # this step removes.  The CHECK says both halves -- the range, and that it
    # exceeds the day the date carries -- so the only rows it admits are the
    # ones where the date genuinely lost the intent.
    nominal_day = db.Column(db.SmallInteger, nullable=True)
    # How many ``unit_id``\\ s pass between occurrences.
    #
    # **It says what the cadence says from plan step R7c-c, and it did not
    # before.**  ``encode_cadence`` wrote ``1`` for every pattern whose interval
    # was baked into its NAME, so a Quarterly rule stored
    # ``(interval_n = 1, unit_id = month)`` -- MONTHLY at face value, 12
    # occurrences a year where 4 were owed -- and every reader had to recover
    # the ``3`` through ``pattern_id``.  That step's migration re-points the
    # four live rules the encoding touched (2 quarterly to 3, 2 semi-annual to
    # 6) and drops the pattern the value had to be read through.
    #
    # ``CHECK (interval_n > 0)`` is the BOTTOM of the domain, and the ``integer``
    # type is the top.  There is no upper CHECK because there is nothing for one
    # to say that the type does not: an interval whose stride carries the second
    # occurrence past the calendar this application reaches is honest -- such a
    # rule fires once, and ``_months.walk_months`` stops there rather than
    # walking off the end of ``date``.
    #
    # **The top half is stated at the SUBMISSION and it was not, until plan step
    # R7c-c** (``_recurrence._MAX_INTEGER_COLUMN``).  A value above ``int4``
    # reaches the flush as an unhandled ``NumericValueOutOfRange`` -- a 500 on a
    # door a crafted POST reaches -- and until that step the accident of the
    # closed pattern set covered three of the four units, because
    # ``is_authorable`` refused any MONTH or YEAR interval above 6.
    interval_n = db.Column(
        db.Integer, nullable=False, default=1, server_default=db.text("1"),
    )
    # The bill's real due day, when the servicer's date differs from the day
    # the cadence schedules it on.  NOT a coordinate of the cadence -- the rule
    # fires on its own day and the row carries this one -- which is why it
    # survived plan step R7c-c's contraction while ``day_of_month`` did not.
    # Plan step **R5** moves it onto the generated ROW as ``due_on``, where the
    # loan ledger already reads it.
    due_day_of_month = db.Column(
        db.Integer,
        db.CheckConstraint(
            "due_day_of_month IS NULL OR "
            "(due_day_of_month >= 1 AND due_day_of_month <= 31)",
            name="ck_recurrence_rules_due_dom",
        ),
    )
    # Optional end date -- recurrence stops generating after this date.
    # NULL means indefinite (no end).
    end_date = db.Column(db.Date, nullable=True)
    # Count-bounded end: stop after this many occurrences.  Genuinely
    # optional and mutually exclusive with ``end_date`` (see the
    # ``ck_recurrence_rules_single_end_bound`` CHECK above); NULL means "not
    # count-bounded", which is what all 46 live rules carry as of 2026-08-13.
    # Read by the occurrence walk since plan step R3
    # (``recurrence._occurrence._bounded``); its first WRITER is plan step
    # R7b-3's "Ends" form control, which took the count-bounded end over from
    # plan step R8's four add-ons.
    max_occurrences = db.Column(db.Integer, nullable=True)

    # Relationships
    #
    # **THREE have been deleted from this model and none replaced.**
    # ``pattern`` went at plan step R7a (ledger row **D17**): ``lazy="joined"``,
    # so every rule load eager-joined ``ref.recurrence_patterns`` for a single
    # reader, the ``recurrence_cell`` macro's fallback branch, which titled the
    # row's ``name`` for a pattern the application does not model.  The
    # Recurring surface words a recurrence from what it MEANS
    # (:func:`app.services.recurrence.describe`), so the branch, the join and
    # the last ``.name``-for-display coupling on this table left together --
    # and plan step R7c-c dropped the column behind it.
    # ``start_period`` went at plan step R7b-4 (row **D30**), the same defect
    # one line down: eager-joined ``budget.pay_periods`` for ZERO readers.
    # ``month_anchor`` went at plan step R7c-c with the table it pointed at
    # (ruling **R-R16**): ``budget.recurrence_month_anchors`` was to hold the
    # clamped day once the anchor became a column, and that day is
    # ``nominal_day`` above instead, under a CHECK tying its presence to
    # meaning.  The counter-argument it settled is worth keeping: a nullable
    # column is a wide sparse table one column at a time UNLESS its absence is
    # the discriminator and its presence is constrained, which
    # ``ck_recurrence_rules_nominal_day`` is what makes true here.
    #
    # ``weekday_anchor`` is the ONE 0-or-1 subtype left, it is EMPTY, and plan
    # step R8 is its first writer: two fields with their own domain, and "the
    # LAST Tuesday" (``nth_week = -1``) is not derivable from a date at all.
    # ``uselist=False`` because the UNIQUE constraint on its
    # ``recurrence_rule_id`` makes at most one row possible; ``lazy="select"``
    # because nothing reads it until R8, so an eager join would cost every rule
    # load for no reader; ``passive_deletes`` defers to the FK's ON DELETE
    # CASCADE rather than loading the child to delete it.  A rule fires on a
    # day-of-month OR an nth-weekday, never both, and with one table left that
    # is a CHECK against ``nominal_day`` rather than the cross-table invariant
    # it used to be; step R8 owns it.
    weekday_anchor = db.relationship(
        "RecurrenceWeekdayAnchor",
        uselist=False, lazy="select",
        cascade="all, delete-orphan", passive_deletes=True,
        back_populates="rule",
    )

    def __repr__(self):
        return (
            f"<RecurrenceRule id={self.id} "
            f"every {self.interval_n} unit={self.unit_id} "
            f"from {self.starts_on}>"
        )
