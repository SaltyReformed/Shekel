"""
Shekel Budget App -- Pay Schedule Model (budget schema)

One row per user holding the configuration a pay schedule cannot derive from
its own rows and that is the OWNER's rather than any one era's: the
continuous-rolling-window settings, and how far back the owner's paychecks
reach.

**The RHYTHM does not live here since plan step ``pay_calendar:C17-a``**
(ruling **R-PC58**).  Until that step this row held one ``cadence_days``, one
``shift_id`` and one ``nominal_anchor`` per owner, and every batch that
recorded a payday overwrote all three -- so "correct my cadence going forward"
silently re-described every PAST payday too (ledger row **N-492**).  A pay
schedule is a SEQUENCE OF ERAS now: :class:`~app.models.pay_era.PayEra`
holds one row per *how I have been paid since*, each carrying the cadence,
its kind, the phase and the convention, and this row is the owner-level
configuration those eras hang off through ``fk_pay_eras_schedule``.  The
:attr:`PaySchedule.eras` relationship is how a reader that holds this row
reaches them in one load.

**It still holds ``history_opens_on``** (plan step balance:X-bh-2, ruling
**balance:R-IA** amended 2026-08-31): how far back the owner's paychecks
reach, or ``NULL`` for an owner who has not said.  It is the OWNER's fact and
not an era's -- it bounds the EARLIEST era's backward rhythm, and only that
era has one -- so it stays here.  The app knows how often somebody is paid and
cannot know when the job began, which is why it is asked rather than
inferred.

**The row's own job is the same as it was**: a pay period stores only its
``start_date`` -- the payday -- and the extend / regenerate / rolling-top-up
paths have nothing to continue an existing schedule FROM unless the rhythm is
persisted somewhere.  What changed is that "somewhere" is one era row per
rhythm rather than three columns here.
"""

from app.config import BaseConfig
from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

# ``history_opens_on``'s window is NOT declared here, and that is the point of
# this comment.  It is the window this application HAS a calendar for --
# ``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX`` -- so the CHECK below, the
# Marshmallow field and the two ``<input type="date">`` hints all read THAT
# pair directly.  A ``HISTORY_OPENS_MIN`` alias stood here for one review pass
# and was deleted: the same fact already carries two domain-named aliases
# (``EFFECTIVE_DATE_*`` in the validation helpers, ``_STARTS_ON_*`` in the
# recurrence resolver), and a third would have been a third name for one
# number rather than a bound of this column's own.  The cadence bound, which
# IS one column's own rule, lives with that column on
# :mod:`app.models.pay_era` since plan step ``pay_calendar:C17-a``.


class PaySchedule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A user's owner-level pay-schedule configuration.

    Exactly one row per user, enforced by ``uq_pay_schedule_user``
    (UNIQUE on ``user_id``).  The row is created by
    ``pay_schedule_service.ensure_schedule_row`` the first time a batch
    records a payday for its owner, and never rewritten by a batch after
    that: the rhythm a batch states is an era's fact
    (:class:`~app.models.pay_era.PayEra`), and the two facts here are set by
    their own doors (``set_rolling``, ``set_history_opening``).

    **Since plan step C4-b-2 it is also a foreign-key TARGET**, and that is
    what ``uq_pay_schedule_user`` makes legal:
    ``budget.pay_periods.user_id`` references ``user_id`` here through
    ``fk_pay_periods_schedule``, ``ON DELETE RESTRICT``, and since plan step
    ``pay_calendar:C17-a`` ``budget.pay_eras.user_id`` does the same through
    ``fk_pay_eras_schedule``.  So a row cannot be deleted while its owner holds
    a payday or an era, and an owner cannot hold either without one.  A row
    WITHOUT paydays stays ordinary: ``pay_period_admin.reset_pay_periods``
    deletes every period and keeps this row, and that is the state it passes
    through.

    Columns:

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
      ``user_id`` -- from :class:`UserScopedMixin` (CASCADE FK to
                          ``auth.users.id``).
      ``created_at`` -- from :class:`CreatedAtMixin`.

    Relationships:

      ``eras`` -- the owner's :class:`~app.models.pay_era.PayEra` rows,
                          ``effective_from`` ascending.  Joined by OWNER rather
                          than by this row's primary key, because
                          ``fk_pay_eras_schedule`` targets ``user_id``; the
                          ``foreign()`` annotation says which side of that
                          join is the key, since the column carries a second
                          key to ``auth.users`` as well.
    """

    __tablename__ = "pay_schedule"
    __table_args__ = (
        # One schedule row per user.  Also the conflict target the
        # backfill migration's ``ON CONFLICT (user_id) DO NOTHING`` and
        # the service's row-creating insert rely on.
        db.UniqueConstraint("user_id", name="uq_pay_schedule_user"),
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
    # It is the OWNER's fact and not an era's (plan step pay_calendar:C17-a):
    # only the EARLIEST era runs backward below the record, so a floor per era
    # would be a column with one meaningful row.  It stays here, beside the
    # rolling configuration, as the second fact a schedule cannot derive.
    #
    # NOT NULL was not available.  Every existing row predates the column, and
    # there is no derivation to backfill one with: the first recorded payday is
    # a RECORD boundary, and writing it here would state as fact exactly the
    # guess ledger row N-390 measured at $14,103.84 against a true $31,733.64.
    history_opens_on = db.Column(db.Date, nullable=True)
    # The owner's eras, ascending.  ``primaryjoin`` spells the join on
    # ``user_id`` because the era table carries TWO keys on that column -- the
    # mixin's to ``auth.users`` and ``fk_pay_eras_schedule`` to this row --
    # and the ORM cannot pick the relationship's path between them unasked.
    # ``viewonly``: an era is written and retired by
    # ``pay_schedule_service`` alone, never through this collection.
    eras = db.relationship(
        "PayEra",
        primaryjoin="PaySchedule.user_id == foreign(PayEra.user_id)",
        order_by="PayEra.effective_from",
        viewonly=True,
        lazy="select",
    )
    # user_id (UserScopedMixin) and created_at (CreatedAtMixin) render
    # at the table tail; see the mixin docstrings for the DDL contract.

    def __repr__(self):
        return (
            f"<PaySchedule user={self.user_id} "
            f"rolling={self.rolling_enabled}>"
        )
