"""
Shekel Budget App -- Pay Schedule Model (budget schema)

One row per user holding the persisted pay-period cadence plus the
continuous-rolling-window configuration.

A pay period stores only ``(start_date, end_date, period_index)`` --
its cadence is never recorded on the period itself (it is a
generation-time argument to ``pay_period_service.generate_pay_periods``).
That means the extend / regenerate / rolling-top-up paths have nothing
to continue an existing schedule FROM unless the cadence is persisted
somewhere.  This table is that storage: the genuinely non-derivable
configuration a user's schedule needs to grow itself forward.

The anchor start date is deliberately NOT stored here -- it equals
``min(pay_periods.start_date)`` and has no consumer, so persisting it
would only invite drift.
"""

from app.config import BaseConfig
from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


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


class PaySchedule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A user's persisted pay-period cadence and rolling-window config.

    Exactly one row per user, enforced by ``uq_pay_schedule_user``
    (UNIQUE on ``user_id``).  The row is created or refreshed by
    ``pay_schedule_service.upsert_schedule`` whenever the schedule's
    cadence is established (first generation) or changed (regenerate).

    Columns:

      ``cadence_days`` -- days between consecutive paydays (e.g. 14 for
                          biweekly).  ``ck_pay_schedule_cadence_range``
                          bounds it to
                          :data:`CADENCE_DAYS_MIN`..:data:`CADENCE_DAYS_MAX`,
                          the same two names the Marshmallow cadence
                          fields and ``pay_period_service.establish_schedule``
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
    # user_id (UserScopedMixin) and created_at (CreatedAtMixin) render
    # at the table tail; see the mixin docstrings for the DDL contract.

    def __repr__(self):
        return (
            f"<PaySchedule user={self.user_id} cadence={self.cadence_days} "
            f"rolling={self.rolling_enabled}>"
        )
