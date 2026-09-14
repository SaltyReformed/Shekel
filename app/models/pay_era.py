"""
Shekel Budget App -- Pay Era Model (budget schema)

**A pay schedule is a SEQUENCE OF ERAS** (plan step ``pay_calendar:C17``,
ruling **R-PC58**): one row per *how I have been paid since*, carrying the
day the rhythm took effect, its cadence and its payday convention -- and
the cadence's KIND as which parameter columns the row carries (plan step
``C17-d-2``, ruling **R-PC80**).

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
from app.utils.dates import SHORTEST_MONTH_DAYS


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

#: The day-of-month bounds (plan step ``pay_calendar:C17-d-2``, ruling
#: **R-PC79**), declared once beside the cadence bounds and for the same
#: reason: the two CHECKs below, the write door's
#: :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence` and
#: the pure package's mirror (:data:`app.services.pay_calendar._eras.MIN_DAY_OF_MONTH`
#: and its two siblings, held equal by the same test) all read these names.
#: A day is 1..31, with 29..31 meaning "or the last day of a shorter month".
#: The two February-derived bounds are DERIVED from
#: :data:`app.utils.dates.SHORTEST_MONTH_DAYS` rather than spelled: a
#: semi-monthly pair's LOWER day is at most one less than February's length,
#: because at that length or more both days clamp onto one day in February;
#: and ``nominal_day``'s domain -- the days a month can fail to hold -- starts
#: one above it.
DAY_OF_MONTH_MIN = 1
DAY_OF_MONTH_MAX = 31
SEMI_MONTHLY_LOWER_DAY_MAX = SHORTEST_MONTH_DAYS - 1
NOMINAL_DAY_MIN = SHORTEST_MONTH_DAYS + 1

#: The CHECK texts, built from the constants above and read by the tests
#: that hold model and DDL to one statement (the migration that installed
#: them states each text frozen, as a revision records what it did).  Each
#: is written so that every storable row is a LEGAL era (ruling
#: **R-PC80**): the kind is readable off which columns are present and no
#: reader needs a fence.
#:
#: ``ck_pay_eras_one_kind`` -- a fixed-days era carries neither month
#: column, so ``cadence_days`` present means the other two are absent.
ONE_KIND_CHECK = "cadence_days IS NULL OR (nominal_day IS NULL AND other_day IS NULL)"
#: ``ck_pay_eras_nominal_day`` -- ``recurrence:R-R3``'s three conjuncts on
#: this table's anchor: the domain (only 29..31 can be lost), a value
#: strictly above the day the date carries (else it restates the date), and
#: the CLAMP EQUALITY (the date's day is exactly what clamping the meant day
#: into its month gives), so presence IMPLIES the first month was too short.
#: ``EXTRACT(day FROM <date>)`` is IMMUTABLE for a ``date`` argument and
#: ``date_trunc`` is cast to ``::timestamp`` for the same reason; verified
#: against the live server when the recurrence CHECK landed.
NOMINAL_DAY_CHECK = (
    f"nominal_day IS NULL OR ("
    f"nominal_day BETWEEN {NOMINAL_DAY_MIN} AND {DAY_OF_MONTH_MAX} "
    f"AND nominal_day > EXTRACT(day FROM effective_from) "
    f"AND EXTRACT(day FROM effective_from) = LEAST(nominal_day, "
    f"EXTRACT(day FROM (date_trunc('month', effective_from::timestamp) "
    f"+ INTERVAL '1 month - 1 day'))))"
)
#: ``ck_pay_eras_other_day`` -- a semi-monthly era's second day is a day of
#: the month, DIFFERENT from the day the anchor means (``nominal_day`` when
#: the first month clamped it, else ``effective_from``'s own day), and the
#: lower of the two is at most 27.
OTHER_DAY_CHECK = (
    f"other_day IS NULL OR ("
    f"other_day BETWEEN {DAY_OF_MONTH_MIN} AND {DAY_OF_MONTH_MAX} "
    f"AND other_day <> COALESCE(nominal_day, EXTRACT(day FROM effective_from)) "
    f"AND LEAST(other_day, COALESCE(nominal_day, "
    f"EXTRACT(day FROM effective_from))) <= {SEMI_MONTHLY_LOWER_DAY_MAX})"
)


class PayEra(UserScopedMixin, CreatedAtMixin, db.Model):
    """One span of an owner's pay history and the rhythm it ran on.

    Ordered per owner by ``effective_from``, which
    ``uq_pay_eras_user_effective_from`` makes unique: two eras cannot take
    effect on one day, so "the era covering this day" has exactly one answer.

    **Written by ONE door**, ``pay_era_write.mint_era``, which asks the
    cadence bound and the cadence-convention pairing before it writes; every
    batch that records a payday reaches it through
    ``pay_period_write.record_paydays``, which mints an era only when the batch
    states a rhythm the era covering its first payday does not already hold.
    Retired by ``pay_schedule_service.retire_eras`` on the same writer's terms:
    minting an era supersedes every era taking effect on or after it, and a
    batch that leaves no payday standing leaves no era either.

    **The KIND is which parameter columns the row carries** (plan step
    ``C17-d-2``, ruling **R-PC80**, revising **R-PC58**'s letter).  The
    three kinds have DISJOINT stored shapes: every-N-days is
    ``cadence_days`` present; semi-monthly is ``other_day`` present;
    monthly is neither.  A ``kind_id`` beside them would be a derived value
    stored next to its source (rule 14, ``balance:R-IY``) that no CHECK
    could tie to the columns without pinning a seed id, and a row saying
    ``fixed_days`` with no cadence would be storable and need a reader-side
    refusal.  Under the CHECKs below every storable row is a legal era and
    ``pay_schedule_service._era_of`` reads the value's type off the row.
    *``C17-a`` landed the column with one seeded member, ``C17-d-1`` mapped
    the value's type onto it at the writer, and ``C17-d-2``'s migration
    dropped it with ``ref.pay_cadence_kinds``.*

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
                          payroll INTENDS.  **For a day-of-month era it also
                          carries the DAY the era pays on**, the month
                          coordinate and the day coordinate in one date --
                          the day the era MEANS unless ``nominal_day``
                          records that this month could not hold it.
      ``cadence_days`` -- days between consecutive paydays under the
                          fixed-days kind, and NULL for a day-of-month
                          era.  ``ck_pay_eras_cadence_range`` bounds it to
                          :data:`CADENCE_DAYS_MIN` .. :data:`CADENCE_DAYS_MAX`
                          (NULL passes ``BETWEEN``), the same two names the
                          Marshmallow cadence fields and the write door
                          read; ``ck_pay_eras_one_kind`` keeps both month
                          columns NULL beside it.
      ``nominal_day`` -- the day a day-of-month era MEANS when
                          ``effective_from``'s month was too short to hold
                          it -- 29, 30 or 31 -- and NULL otherwise, so the
                          meant day never decays: an era opening 2026-02-28
                          meaning the 31st projects 03-31, 04-30, 05-31.
                          The shape ``budget.recurrence_rules.nominal_day``
                          gives a rule (ruling ``recurrence:R-R3``), under
                          the same three-conjunct CHECK
                          (``ck_pay_eras_nominal_day``): presence IMPLIES the
                          clamp happened, so absence has ONE meaning.
      ``other_day`` -- a semi-monthly era's SECOND day: the member of the
                          pair ``effective_from`` does not stand for, 1..31,
                          distinct from the day the anchor means, with the
                          lower of the two at most 27
                          (``ck_pay_eras_other_day``); NULL for the other
                          two kinds.
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
        # The three kinds' disjoint shapes, and each month column's own
        # domain (plan step C17-d-2, ruling R-PC80): the texts are the module
        # constants the migration installs, so model and DDL are one
        # statement.
        db.CheckConstraint(ONE_KIND_CHECK, name="ck_pay_eras_one_kind"),
        db.CheckConstraint(NOMINAL_DAY_CHECK, name="ck_pay_eras_nominal_day"),
        db.CheckConstraint(OTHER_DAY_CHECK, name="ck_pay_eras_other_day"),
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
    cadence_days = db.Column(db.Integer, nullable=True)
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_pay_eras_shift_id",
        ),
        nullable=False,
    )
    # The two day-of-month columns sit after the mixin columns in the
    # table, where the C17-d-2 migration added them.
    nominal_day = db.Column(db.SmallInteger, nullable=True)
    other_day = db.Column(db.SmallInteger, nullable=True)
    # user_id (UserScopedMixin) and created_at (CreatedAtMixin) render
    # at the table tail; see the mixin docstrings for the DDL contract.

    def __repr__(self):
        return (
            f"<PayEra user={self.user_id} from={self.effective_from} "
            f"cadence_days={self.cadence_days} nominal_day={self.nominal_day} "
            f"other_day={self.other_day}>"
        )
