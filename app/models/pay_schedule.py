"""
Shekel Budget App -- Pay Schedule Model (budget schema)

One row per user holding the persisted pay-period cadence plus the
continuous-rolling-window configuration.

A pay period stores only its ``start_date`` -- the payday, and since plan
step ``pay_calendar:C4-c`` the whole of what that row records.  Its cadence
is not on the period either (it is an argument to
``pay_period_write.record_paydays``, which spaces the batch's paydays by it
and then persists it here).  That means the extend / regenerate /
rolling-top-up paths have nothing to continue an existing schedule FROM
unless the cadence is persisted somewhere.  This table is that storage:
the genuinely non-derivable configuration a user's schedule needs to grow
itself forward.

**It is also the INPUT to the last period's PROJECTED end**, the one value
in a derived calendar that does not come from a payday -- every other end
is the day before the next one.  So a write to this column moves the
schedule's horizon, which is why only a batch that RECORDS a payday may
make one (the cadence rule; findings **P12** and **P29**).  *It was the
input to a stored ``end_date`` between plan steps C3-b and C4-c; the column
is gone and the derivation reads this one directly, so the value's job did
not change but the thing it feeds did.*

**Since plan step balance:X-bh-2 it holds a SECOND non-derivable fact**
(ruling **balance:R-IA**, amended 2026-08-31): ``history_opens_on``, how far
back the owner's paychecks reach, or ``NULL`` for an owner who has not
said.  The table's subject is unchanged -- it is still the
configuration a schedule cannot derive from its own rows -- and the two
facts are the two ENDS of one rhythm: ``cadence_days`` says how far apart
the paydays are, and this says where counting them backward stops.  The
app knows the cadence and cannot know when the job began, which is why
the second one is asked rather than inferred.

The anchor start date is deliberately NOT stored here -- it equals
``min(pay_periods.start_date)`` and has no consumer, so persisting it
would only invite drift.  ``history_opens_on`` is NOT that value under
another name: the first RECORDED payday is where the app's record opens,
and this is where the owner's pay history opens, which is the whole
distinction ledger row **N-390** measured at ``$14,103.84`` against a
true ``$31,733.64``.
"""

from app.config import BaseConfig
from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN


#: Inclusive bounds on ``pay_schedule.cadence_days``, declared ONCE and read by
#: the ``ck_pay_schedule_cadence_range`` CHECK below, by every Marshmallow field
#: that accepts a cadence, and by
#: :func:`app.services.pay_schedule_service.reject_out_of_range_cadence`, which
#: the column's one writer asks.  They were six hand-copied literals until plan
#: step X-ad-a, which added a seventh door (registration) and made the copying
#: the defect: a bound stated in six places is six places to disagree, and the
#: one that would have disagreed silently was the service's -- a cadence the
#: schema never saw reaches the CHECK as a 500 rather than as a refusal the
#: form can render.
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

# ``history_opens_on``'s window is NOT declared here, and that is the point of
# this comment.  It is the window this application HAS a calendar for --
# ``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX`` -- so the CHECK below, the
# Marshmallow field and the two ``<input type="date">`` hints all read THAT
# pair directly.  A ``HISTORY_OPENS_MIN`` alias stood here for one review pass
# and was deleted: the same fact already carries two domain-named aliases
# (``EFFECTIVE_DATE_*`` in the validation helpers, ``_STARTS_ON_*`` in the
# recurrence resolver), and a third would have been a third name for one
# number rather than a bound of this column's own.  ``cadence_days`` keeps its
# constants above because 1..365 IS that column's own rule and nothing else's.


class PaySchedule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A user's persisted pay-period cadence and rolling-window config.

    Exactly one row per user, enforced by ``uq_pay_schedule_user``
    (UNIQUE on ``user_id``).  The row is created or refreshed by
    ``pay_schedule_service.upsert_schedule`` whenever the schedule's
    cadence is established (first generation) or changed (regenerate).

    **Since plan step C4-b-2 it is also a foreign-key TARGET**, and that is
    what ``uq_pay_schedule_user`` makes legal:
    ``budget.pay_periods.user_id`` references ``user_id`` here through
    ``fk_pay_periods_schedule``, ``ON DELETE RESTRICT``.  So a row cannot be
    deleted while its owner holds a payday, and an owner cannot hold a payday
    without one -- the invariant ledger rows **P8** and **P35** existed for,
    moved out of ``resolve_schedule``'s inferring arm and into the schema.
    A row WITHOUT paydays stays ordinary: ``pay_period_admin.reset_pay_periods``
    deletes every period and keeps this row, and that is the state it passes
    through.

    Columns:

      ``cadence_days`` -- days between consecutive paydays (e.g. 14 for
                          biweekly).  ``ck_pay_schedule_cadence_range``
                          bounds it to
                          :data:`CADENCE_DAYS_MIN`..:data:`CADENCE_DAYS_MAX`,
                          the same two names the Marshmallow cadence
                          fields and ``pay_schedule_service.upsert_schedule``
                          read -- so the CHECK and every door in front of
                          it state one bound rather than four.
      ``rolling_enabled`` -- continuous-rolling-window switch.  When
                          true, the on-request top-up keeps a target
                          number of periods generated ahead of today.
                          False for every backfilled and newly created
                          row; the top-up logic and its toggle UI ship
                          in a later phase.
      ``rolling_target_periods`` -- how many current-and-future periods
                          the rolling window keeps generated ahead.
                          ``ck_pay_schedule_positive_target`` requires
                          it to be > 0; the default mirrors the app's
                          ~2-year horizon (``DEFAULT_PAY_PERIOD_HORIZON``).
      ``history_opens_on`` -- how far back this owner's paychecks reach,
                          NULLABLE.  See the column comment below for the
                          whole rule; in one sentence, it is the FLOOR on
                          the backward payday rhythm and ``NULL`` means NOT
                          STATED, which counts only the recorded paydays.
      ``shift_id`` -- what payroll does when a payday lands on a day no
                          money moves on, keyed to ``ref.business_day_shifts``
                          (``none`` / ``prior`` / ``next``).  ``NOT NULL``,
                          and every row starts at ``none``, so the behaviour
                          is off until an owner answers.  See the column
                          comment below for why it carries no CHECK and no
                          default.
      ``user_id`` -- from :class:`UserScopedMixin` (CASCADE FK to
                          ``auth.users.id``).
      ``created_at`` -- from :class:`CreatedAtMixin`.
    """

    __tablename__ = "pay_schedule"
    __table_args__ = (
        # One schedule row per user.  Also the conflict target the
        # backfill migration's ``ON CONFLICT (user_id) DO NOTHING`` and
        # the service's upsert rely on.
        db.UniqueConstraint("user_id", name="uq_pay_schedule_user"),
        db.CheckConstraint(
            f"cadence_days BETWEEN {CADENCE_DAYS_MIN} AND {CADENCE_DAYS_MAX}",
            name="ck_pay_schedule_cadence_range",
        ),
        db.CheckConstraint(
            "rolling_target_periods > 0",
            name="ck_pay_schedule_positive_target",
        ),
        # A stated opening must fall inside the window this application has a
        # calendar for.  NULL passes: a CHECK is satisfied by an unknown, and
        # NULL is this column's ordinary value rather than a gap (see the
        # column).  The bound backs the same typo the two sibling date CHECKs
        # were added for -- an HTML date input accepts a five-digit year, and
        # ``0202`` or ``9999`` here would name a floor no rhythm can reach.
        db.CheckConstraint(
            f"history_opens_on BETWEEN DATE '{CALENDAR_DATE_MIN.isoformat()}' "
            f"AND DATE '{CALENDAR_DATE_MAX.isoformat()}'",
            name="ck_pay_schedule_history_opens_range",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    cadence_days = db.Column(db.Integer, nullable=False)
    rolling_enabled = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    rolling_target_periods = db.Column(
        db.Integer,
        nullable=False,
        default=BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
        server_default=db.text(str(BaseConfig.DEFAULT_PAY_PERIOD_HORIZON)),
    )
    # NULLABLE, and the null means NOT STATED -- an owner nobody has asked,
    # which is a different fact from an owner who has said "I have always been
    # paid this way" (plan step balance:X-bh-2, ruling balance:R-IA as amended
    # 2026-08-31).  The paycheck engine counts a payday's position in its month
    # and the wages already paid this calendar year over the owner's RHYTHM --
    # the recorded paydays, plus the cadence continued past the horizon and,
    # since that step, below the opening payday.  The backward continuation
    # needs a floor and the app cannot derive one: it knows how often somebody
    # is paid and not when the job began.  So the floor is stored, and where it
    # is absent the backward half answers NOTHING.
    #
    # The first form of the ruling had NULL mean "run back to
    # app.utils.dates.CALENDAR_DATE_MIN", as the mirror of the
    # CALENDAR_DATE_MAX bounding the forward projection.  Three adversarial
    # reviews of the step converged on why that is wrong and the developer
    # amended it: the two ends are not symmetric in NEED -- the backward
    # rhythm's only readers ask over one calendar month or one calendar year,
    # so it never reaches twelve months down -- and, worse, every existing row
    # and every skipped form field holds NULL, so the absence of a question
    # stood in for an answer.  The error also pointed the wrong way: an
    # over-counted year-to-date retires the FICA wage base early and exhausts
    # an annual_cap early, both of which OVERSTATE net pay.  A budgeting app
    # that must guess should guess poor.  Priced in review at $1,437.91 of
    # Social Security tax not withheld on a $200,000 salary whose record opens
    # mid-year.
    #
    # It is a FLOOR, never an anchor.  The rhythm is stepped backward from the
    # first RECORDED payday at the stored cadence and days below this one are
    # dropped; the value itself is not treated as a payday, because a date the
    # owner remembers need not land on the recorded rhythm and re-anchoring on
    # it would put a short gap at the seam.  A value at or after the opening
    # payday is therefore legal and means "no backward rhythm" -- which is the
    # honest answer for an owner whose first payday has not happened yet.
    #
    # NOT NULL was not available.  Every existing row predates the column, and
    # there is no derivation to backfill one with: the first recorded payday is
    # a RECORD boundary, and writing it here would state as fact exactly the
    # guess ledger row N-390 measured at $14,103.84 against a true $31,733.64.
    history_opens_on = db.Column(db.Date, nullable=True)
    # What payroll does when a payday lands on a weekend or a federal holiday
    # (plan step pay_calendar:C14-b, rulings R-PC47 and R-PC56).  The
    # vocabulary is the EXISTING ref.business_day_shifts seeded at
    # recurrence:R2, which budget.recurrence_rules.shift_id already keys to,
    # so a bill's cash date and a payday ask one question of one table.
    #
    # It carries NO server_default, and the reason is the same one that keeps
    # every other ref comparison out of the schema: which integer means
    # ``none`` is SEED DATA, not a schema constant, so a default written into
    # the DDL would be a literal nobody can re-derive -- and the failure would
    # be silent in the money-moving direction, a row defaulting to ``prior``
    # displacing paydays its owner never asked to move.  The rule "a new
    # schedule displaces nothing" is a business rule, so it lives at the one
    # door that creates a row (``pay_schedule_service.upsert_schedule``),
    # which resolves the id through ``ref_cache`` exactly as
    # ``recurrence._authoring`` does for the same table.  A writer that
    # forgets therefore gets a NOT NULL violation rather than a wrong
    # convention.
    #
    # It carries NO CHECK either, and that is a DELIBERATE absence the
    # developer ruled on 2026-09-05 rather than an omission (**R-PC59**).
    # A displacing
    # convention needs a cadence longer than the longest run of consecutive
    # closed days, or two nominal paydays displace onto one day and
    # ``pay_calendar._derive.derive_periods`` refuses the whole calendar.
    # That floor is DERIVED from the holiday set -- see
    # :func:`app.utils.business_days.shortest_collision_free_cadence`, which
    # proves it is the longest closed run plus one -- and the holiday set is
    # not fixed (``business_days.JUNETEENTH_FIRST_YEAR`` records it changing
    # once inside this application's own calendar).  A CHECK expression must
    # be IMMUTABLE, so a constraint could only freeze the number where nothing
    # can recompute it.  The refusal is therefore
    # ``pay_schedule_service.reject_shift_on_short_cadence``, asked by
    # ``upsert_schedule`` -- the column's ONE writer, which writes the cadence
    # and the convention in a single statement so the pair is judged against
    # the state the operation leaves behind.
    #
    # A CHECK could not have been the primary refusal in any case, because it
    # cannot name a FIELD: a constraint violation arrives as an IntegrityError
    # with a constraint name, where a form needs the message attached to the
    # control the owner chose.  That is what
    # ``schemas.validation.pay_periods.validate_derivable_rhythm`` does, and it
    # is the same reason plan step X-ad-a moved the cadence BOUND out from
    # behind ``ck_pay_schedule_cadence_range`` and into the write door.
    #
    # **What a CHECK would still leave undone, stated because an earlier draft
    # of this comment claimed the opposite about PostgreSQL and was wrong.**
    # ``ADD CONSTRAINT`` without ``NOT VALID`` DOES scan every existing row, so
    # a migration that re-adds the constraint at a new floor fails loudly on a
    # row that has become illegal.  What is not re-evaluated is an IN-PLACE
    # constraint over rows nobody updates.  The honest statement of the gap is
    # therefore narrower and it applies to the DOOR as much as to a CHECK: a
    # refusal asked only on write cannot see a stored row that a LATER holiday
    # change made illegal, and nothing reconciles ``budget.pay_schedule``
    # today.  That is a finding this step reports rather than a property it
    # claims (adversarial design review, 2026-09-05).
    shift_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.business_day_shifts.id", ondelete="RESTRICT",
            name="fk_pay_schedule_shift_id",
        ),
        nullable=False,
    )
    # user_id (UserScopedMixin) and created_at (CreatedAtMixin) render
    # at the table tail; see the mixin docstrings for the DDL contract.

    def __repr__(self):
        return (
            f"<PaySchedule user={self.user_id} cadence={self.cadence_days} "
            f"rolling={self.rolling_enabled}>"
        )
