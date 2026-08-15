"""
Shekel Budget App -- Recurrence Rule Model (budget schema)

Defines the pattern by which transactions are auto-generated into
future pay periods (every_period, monthly, annual, etc.).

**This table is mid-CUTOVER, and it carries two statements of one recurrence.**
Plan step R2d (developer ruling, 2026-08-07) settled that the redesign's
two-axis values -- the cadence unit, the first occurrence, the placement, the
business-day shift -- must not be STORED while they are a derivation over the
closed-set columns plus the owner's schedule, because a stored derivation is a
cache and a cache drifts the moment one writer moves one side alone.

Plan step **R7c-a** adds them anyway, and the ruling is not being broken: the
three leaves of R7c are an expand / migrate / contract, and until R7c-b moves
the readers across **nothing reads the new columns at all**, so no consumer can
take a stale one.  From R7c-b they are AUTHORED rather than derived for every
form-authored rule -- ``starts_on`` is what the form collects -- so there is no
input left for them to lag.  A day-less LOAN payment stays partly
schedule-derived even then; see ``_authoring._author`` and plan ledger
row D6.  R7c-c drops the closed-set columns and the table states its
recurrence once.  Both sides are written from ONE ``resolve`` call in ONE function
(``recurrence._authoring._author``) for the duration, so no write leaves them
disagreeing -- though a SCHEDULE rebuilt with no rule written does move the
derivation past the stored value, which is why R7c-b re-runs the backfill
before it switches the readers.

**``interval_n`` is NOT one of the two sets, and it belongs to the closed one
until R7c-c.**  ``encode_cadence`` writes ``1`` for every pattern whose
interval is baked into its name, so a Quarterly rule stores
``(interval_n = 1, unit_id = month)`` -- which reads as MONTHLY to anything
that takes the pair at face value.  Nothing does: every reader goes through
``decode_pattern``, which answers ``3`` from the pattern and consults the
column only for ``Every N Periods``.  R7c-c re-points it in the migration that
drops ``pattern_id``, which is where the interval stops being the encoding's.

What READS this table today: ``app.services.recurrence.rule_occurrences``,
which since plan step R4a reads the row WHOLE -- it builds a ``RecurrenceSpec`` from every
authored column and hands it to ``app.services.recurrence.resolve``.  There is
no per-pattern dispatch and no branch: ``interval_n`` is read (and refused when
below 1) for every pattern, and ``day_of_month`` / ``due_day_of_month`` /
``month_of_year`` are refused outside their CHECK domains for all of them.

``start_period_id`` and ``offset_periods`` are no longer read at all (plan
step R7b-4).  They were one fact between them -- the paycheck a rule started
on, and the cycle phase derived from it -- and a rule has ONE opening bound,
``start_date``.  The phase is now a function of that bound
(``recurrence._resolution._derive_offset_periods``), written to its column and
read back by nobody; the FK is NULL on every row.  Plan step R7c drops both.

``end_date`` and ``max_occurrences`` are ONE authored value above this table
(``app.services.recurrence.EndBound``, plan step R7b-3): the occurrence walk
has read both since plan step R3, and the write door splits the single bound
into this pair on the way in and rejoins it on the way out.  That is what makes
the exclusive arc below structural rather than maintained -- no value in the
application can state two closing bounds, so nothing has to check.

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
        # ``nominal_day`` records a day the ANCHOR MONTH clamped, and nothing
        # else (plan step R7c-a; ruling R-R3).  Both halves are needed and
        # neither implies the other: the range keeps it a real month-end day,
        # and the comparison keeps it from restating a day ``starts_on``
        # already carries -- a ``nominal_day`` of 15 beside a 15th would be the
        # second representation the D28 ruling removes, and one of 15 beside a
        # 20th would name a day the rule does not fire on.
        #
        # ``EXTRACT(day FROM <date>)`` is IMMUTABLE for a ``date`` argument
        # (it lowers to ``date_part(text, date)``; the STABLE spellings are the
        # ``timestamptz`` ones), which is what lets it appear in a CHECK at
        # all.  Verified against the live server rather than assumed.
        db.CheckConstraint(
            "nominal_day IS NULL OR ("
            "starts_on IS NOT NULL "
            "AND nominal_day BETWEEN 29 AND 31 "
            "AND nominal_day > EXTRACT(day FROM starts_on))",
            name="ck_recurrence_rules_nominal_day",
        ),
        # **There is deliberately NO ``end_date >= anchor_date`` CHECK**, and
        # there is no ``anchor_date`` column for one to name: the anchor is
        # computed, not stored (plan step R2d).  The constraint lands with the
        # column at step R7c, together with the Marshmallow validator that can
        # refuse the pair at the door -- because ``end_date`` is user-authored
        # and live, and a CHECK against a value the form does not yet collect
        # would surface as an unhandled CheckViolation out of
        # ``update_template``'s autoflush, leaving the user unable to stop a
        # recurring bill.  ``starts_on`` exists from R7c-a; the CHECK naming
        # it lands at R7c-b, the leaf where the FORM authors that column and
        # the pair becomes a two-field comparison a schema can refuse.
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(
        db.Integer, db.ForeignKey("ref.recurrence_patterns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # ---- The two-axis columns (plan step R7c-a) --------------------------
    #
    # **Written and read by NOBODY but the write door, until plan step
    # R7c-b.**  They are the EXPAND half of an expand / migrate / contract:
    # this leaf adds them, backfills them and has ``_authoring._author`` keep
    # them in step on every write, while the closed-set columns above stay
    # authoritative; R7c-b moves every reader onto them; R7c-c drops what they
    # replace.  Writing both sides for one leaf is what makes each of the three
    # independently revertible without a translation shim in any of them.
    #
    # **NULLABLE for now, and R7c-b tightens them.**  That is the documented
    # three-step (``.claude/rules/database.md``: add nullable, backfill,
    # tighten), and the third step belongs with the leaf that makes the columns
    # matter: nothing reads them here, so a NULL is invisible, while R7c-b
    # moves the readers across and a NULL becomes a wrong answer.
    #
    # What ``NOT NULL`` would reach in THIS leaf is the ~40 test modules that
    # build a ``RecurrenceRule`` directly rather than through
    # :func:`app.services.recurrence.author_rule` -- and some of those are
    # transient values exercising pure functions, which must stay transient.
    # Sorting them is R7c-b's, with the commit that gives it a reason.  The
    # backfill's totality is proven by the migration's own refusal query, not
    # by a constraint.
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.recurrence_units.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_unit_id",
        ),
        nullable=True,
    )
    placement_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.period_placements.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_placement_id",
        ),
        nullable=True,
    )
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_recurrence_rules_shift_id",
        ),
        nullable=True,
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
    # Produced by ``recurrence._occurrence.first_occurrence``, which is also
    # what the R7c-a migration's SQL backfill is proven against.  Nullable
    # until R7c-b for the reason the three above are.
    starts_on = db.Column(db.Date, nullable=True)
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
    # **RETIRED at plan step R7b-4, and NULL on every row.**  It was the
    # form's "First paycheck" affordance -- a WEAK bound that seeded
    # ``effective_from`` only when the caller passed none, so a caller
    # supplying its own silently bypassed it (defect D2).  That step's
    # migration folded every value into ``start_date`` below, which is the
    # bound and cannot be bypassed, and NOTHING reads or writes this column
    # now: not the write door (``recurrence._authoring._author``), not the
    # resolver, not the lock classifier.  It survives only because dropping a
    # column belongs with the four others plan step R7c drops in one
    # transaction.
    start_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The rule's OPENING BOUND, and since plan step R7b-4 the only thing a
    # rule says about when it begins: recurrence generates nothing whose
    # occurrence falls before this date.  NULL means unbounded below, and the
    # owner's first payday is then the floor.
    #
    # The SYMMETRIC partner of ``end_date`` below, and unbypassable the same
    # way: since plan step R4a both bounds bind the OCCURRENCE rather than the
    # candidate period -- this one through the anchor
    # ``app.services.recurrence.resolve`` derives (the GREATEST of the
    # schedule's opening and this date), that one through the occurrence
    # engine's stopping bound.  Neither is expressible through a caller's
    # ``effective_from``, so no caller can bypass them.  Together the two
    # columns are the rule's validity window.
    #
    # TWO writers.  The recurrence form authors it as "Starts on" (plan step
    # R7b-4).  For a LOAN PAYMENT the app derives it instead, and the form
    # renders it read-only:
    # ``loan_recurrence_sync.sync_recurring_payment_bounds`` writes the loan's
    # FIRST CONTRACTUAL INSTALLMENT (plan step C9a), because a loan payment
    # cannot precede the loan.  A payment generated before origination is
    # erased by the fold (it splits against a zero balance and the origination
    # anchor then resets over it), so it debits cash for a loan that does not
    # exist yet -- measured at $3,220.92 on a mortgage closing one month out.
    start_date = db.Column(db.Date, nullable=True)
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
    # **``pattern`` is GONE** (plan step R7a, ledger row D17).  It was
    # ``lazy="joined"``, so every rule load eager-joined
    # ``ref.recurrence_patterns`` for a single reader: the ``recurrence_cell``
    # macro's fallback branch, which titled the row's ``name`` for a pattern
    # the application does not model.  The Recurring surface now words a
    # recurrence from what it MEANS
    # (:func:`app.services.recurrence.describe`), so the branch, the join and
    # the last ``.name``-for-display coupling on this table left together.
    # ``pattern_id`` stays -- it is what a form still authors, until step R7c
    # replaces it with ``unit_id`` / ``interval_n`` -- and is resolved to its
    # enum member through ``ref_cache``, never through a relationship.
    #
    # **``start_period`` is GONE TOO** (plan step R7b-4, ledger row D30), and
    # it was D17's defect one line down: ``lazy="joined"``, so every rule load
    # eager-joined ``budget.pay_periods`` for ZERO readers.  The only mention
    # of it anywhere was a comment in ``_recurrence_form_helpers`` claiming
    # ``recurrence_engine`` dereferenced ``rule.start_period.start_date``,
    # which was false -- the resolver looked the period up through the
    # calendar.  It was left out of R7a-1 deliberately (CLAUDE.md rule 6: D17
    # named the ``pattern`` relationship, not this one) and goes here, with
    # the affordance that gave it its name.
    # The two 0-or-1 subtypes.  ``uselist=False`` because the UNIQUE
    # constraint on each child's ``recurrence_rule_id`` makes at most one
    # row possible, and ``lazy="select"`` (not ``joined`` like the two
    # above) because nothing reads them until steps R7c/R8 -- an eager join
    # here would cost every rule load for no reader.  ``passive_deletes``
    # defers to the FK's ON DELETE CASCADE rather than loading the child to
    # delete it.
    #
    # BOTH ARE EMPTY, and only ONE will ever be written.
    #
    # ``month_anchor`` was to take the clamped day once the anchor became a
    # column.  Ruling **R-R16** (2026-08-14) put that day on the RULE instead
    # -- ``nominal_day`` above -- so the satellite has no writer and never
    # will: plan step **R7c-c** drops it unwritten.  The design argument in
    # ``recurrence_anchors`` is what changed, and the counter-argument is
    # worth keeping: a nullable column is a wide sparse table one column at a
    # time UNLESS its absence is the discriminator and its presence is
    # constrained, which ``ck_recurrence_rules_nominal_day`` is what makes
    # true here.
    #
    # ``weekday_anchor`` is a REAL subtype and plan step R8 is its first
    # writer: two fields with their own domain, and "the LAST Tuesday"
    # (``nth_week = -1``) is not derivable from a date at all.  A rule carries
    # at most one of the two -- day-of-month OR nth-weekday -- and with one
    # table left that is a CHECK against ``nominal_day`` rather than the
    # cross-table invariant it used to be; step R8 owns it.
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
