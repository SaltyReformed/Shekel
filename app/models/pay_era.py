"""
Shekel Budget App -- Pay Era Model (budget schema)

**A pay schedule is a SEQUENCE OF ERAS** (plan step ``pay_calendar:C17``,
ruling **R-PC58**): one row per *how I have been paid since*, carrying the
day the rhythm took effect, its KIND, its cadence and its payday convention.

Until this step ``budget.pay_schedule`` held ONE rhythm per owner and every
batch that recorded a payday overwrote it, so "correct my cadence going
forward" silently re-described every PAST payday too -- an owner who moved
from fortnightly to weekly held paydays 14 and 35 days apart under one stored
cadence of 7, and no producer could ask what cadence a past payday ran at
(ledger row **N-492**).  An era is the fact that column could not hold: the
rhythm a span of paydays actually ran on, kept when the next span starts on a
different one.

**Which days an era governs is DERIVED from the table, never stored.**  An
era governs every day from its ``effective_from`` up to the next era's; the
EARLIEST era additionally runs backward below the record, bounded by
``budget.pay_schedule.history_opens_on`` (the backward rhythm, whose floor is
a floor and never an anchor -- ruling **balance:R-IA**).  There is no
``effective_through`` column, because it would be the next row's
``effective_from`` minus one stored beside the fact it derives from.

**``effective_from`` IS the grid's phase** (developer ruling 2026-09-11, on
the shape ``C17`` presented).  Ruling **R-PC61** gave the schedule row a
``nominal_anchor`` -- a day the owner's NOMINAL grid passes through -- and
said ``C17`` absorbs it.  It is absorbed INTO this column rather than carried
beside it: an era's first nominal payday is by definition a day its grid
passes through, so a second column would have to satisfy *anchor equals effective_from
modulo the cadence*, which is one value in two homes held equal by a
maintenance contract (rule 14's tell).  Every producer that
stepped from ``nominal_anchor`` steps from this column.  *At ``C17-a`` those
producers are the extend door and the rolling top-up; the projection past the
horizon and the backward rhythm still anchor on RECORDED paydays and move to
this column at ``C17-b``, the leaf that MOVES MONEY.*

**The RECORD stays authoritative below the horizon.**  A recorded payday may
sit off its era's grid (ruling **R-PC47**: payroll occasionally pays off the
cadence, and such a payday is warned about, never rewritten).  The era says
where the grid runs; ``budget.pay_periods`` says where money arrived.  Two
facts, one home each.

**Why a table of its own beside ``budget.pay_schedule`` rather than that
table reshaped** (the same developer ruling).  The schedule row still holds
two facts that are the OWNER's and not an era's -- the rolling-window
configuration and ``history_opens_on`` -- and it is the target of
``fk_pay_periods_schedule``, which needs a unique owner column.  Reshaping it
into a multi-row table would have moved both facts and retargeted that key for
no structural gain; a second relation keyed to the same owner row leaves the
key, the rolling top-up and the history door untouched.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


#: Inclusive bounds on ``pay_eras.cadence_days``, declared ONCE and read by
#: the ``ck_pay_eras_cadence_range`` CHECK below, by every Marshmallow field
#: that accepts a cadence, and by
#: :func:`app.services.pay_schedule_service.reject_out_of_range_cadence`, which
#: the column's one writer asks.  They were six hand-copied literals until plan
#: step X-ad-a, which added a seventh door (registration) and made the copying
#: the defect: a bound stated in six places is six places to disagree, and the
#: one that would have disagreed silently was the service's -- a cadence the
#: schema never saw reaches the CHECK as a 500 rather than as a refusal the
#: form can render.  They lived on :mod:`app.models.pay_schedule` while that
#: table held the cadence; they moved here with the column at plan step
#: ``pay_calendar:C17-a``.
#:
#: **One further copy survives on purpose**: ``pay_calendar._derive`` states the
#: same pair as :data:`~app.services.pay_calendar.MIN_CADENCE_DAYS` /
#: :data:`~app.services.pay_calendar.MAX_CADENCE_DAYS`.  That package is PURE by
#: design -- no Flask symbol, no session, no clock -- which is what lets the
#: pay-calendar arc's harness drive the derivation over production's paydays
#: without a database, and importing this module would pull ``app.extensions``
#: in and close a cycle through ``pay_schedule_service``.  So the two copies are
#: deliberate, and they are held in step by a TEST rather than by memory:
#: ``tests/test_models/test_pay_schedule.py::TestTheCadenceBoundHasOneValue``.
CADENCE_DAYS_MIN = 1
CADENCE_DAYS_MAX = 365


class PayEra(UserScopedMixin, CreatedAtMixin, db.Model):
    """One span of an owner's pay history and the rhythm it ran on.

    Ordered per owner by ``effective_from``, which
    ``uq_pay_eras_user_effective_from`` makes unique: two eras cannot take
    effect on one day, so "the era covering this day" has exactly one answer.

    **Written by ONE door**, ``pay_schedule_service.mint_era``, which asks the
    cadence bound and the cadence-convention pairing before it writes; every
    batch that records a payday reaches it through
    ``pay_period_write.record_paydays``, which mints an era only when the batch
    states a rhythm the era covering its first payday does not already hold.
    Retired by ``pay_schedule_service.retire_eras`` on the same writer's terms:
    minting an era supersedes every era taking effect on or after it, and a
    batch that leaves no payday standing leaves no era either.

    Columns:

      ``effective_from`` -- the day the era takes effect, and the day its
                          grid is phased on: the era's first NOMINAL payday
                          for every era the writer mints, and a nominal grid
                          day at or below the record's opening for the one
                          era the ``C17-a`` migration backfills (under a
                          displacing convention that can be one cadence
                          below the true first payday; ``$0.00``, since every
                          producer reads the phase modulo the cadence).
                          Nominal rather than cash: under a displacing
                          convention the day money moves is this day
                          displaced (``C14-e-3``), and the grid is what
                          payroll INTENDS.
      ``kind_id`` -- what KIND of rhythm the era runs on, keyed to
                          ``ref.pay_cadence_kinds``
                          (:class:`~app.enums.PayCadenceKindEnum`).
                          ``fixed_days`` is the only member until the
                          day-of-month kinds land (``C17-d``); it is a column
                          now because ruling **R-PC58** puts the kind on the
                          era so those kinds arrive as rows, not as a
                          migration over this table.
      ``cadence_days`` -- days between consecutive paydays under the
                          ``fixed_days`` kind.  ``ck_pay_eras_cadence_range``
                          bounds it to :data:`CADENCE_DAYS_MIN` ..
                          :data:`CADENCE_DAYS_MAX`, the same two names the
                          Marshmallow cadence fields and the write door read.
                          ``NOT NULL`` while the only kind reads it; the leaf
                          that adds a kind which does not owns the shape that
                          kind needs.
      ``shift_id`` -- what payroll does when a payday lands on a day no
                          money moves on, keyed to ``ref.business_day_shifts``
                          (``none`` / ``prior`` / ``next``).  Carries NO CHECK
                          and NO server default, for the reasons the column
                          gave on the schedule row (ruling **R-PC59**): the
                          floor a displacing convention needs is DERIVED from a
                          holiday set that moves, and which integer means
                          ``none`` is seed data.  The pairing is refused at the
                          one write door.
      ``user_id`` -- from :class:`UserScopedMixin` (CASCADE FK to
                          ``auth.users.id``), and ALSO the target of
                          ``fk_pay_eras_schedule`` below.
      ``created_at`` -- from :class:`CreatedAtMixin`.
    """

    __tablename__ = "pay_eras"
    __table_args__ = (
        # Two eras cannot take effect on one day: "the era covering this
        # day" has exactly one answer.
        db.UniqueConstraint(
            "user_id", "effective_from", name="uq_pay_eras_user_effective_from",
        ),
        db.CheckConstraint(
            f"cadence_days BETWEEN {CADENCE_DAYS_MIN} AND {CADENCE_DAYS_MAX}",
            name="ck_pay_eras_cadence_range",
        ),
        # An era belongs to an owner who holds a schedule row -- the
        # configuration a schedule cannot derive from its own rows -- exactly
        # as ``fk_pay_periods_schedule`` holds a payday to one.  RESTRICT, so
        # the config row cannot go while an era stands on it.
        db.ForeignKeyConstraint(
            ["user_id"],
            ["budget.pay_schedule.user_id"],
            name="fk_pay_eras_schedule",
            ondelete="RESTRICT",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.Date, nullable=False)
    kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.pay_cadence_kinds.id", ondelete="RESTRICT",
            name="fk_pay_eras_kind_id",
        ),
        nullable=False,
    )
    cadence_days = db.Column(db.Integer, nullable=False)
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_pay_eras_shift_id",
        ),
        nullable=False,
    )
    # user_id (UserScopedMixin) and created_at (CreatedAtMixin) render
    # at the table tail; see the mixin docstrings for the DDL contract.

    def __repr__(self):
        return (
            f"<PayEra user={self.user_id} from={self.effective_from} "
            f"cadence={self.cadence_days}>"
        )
