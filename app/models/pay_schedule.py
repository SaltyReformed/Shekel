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


class PaySchedule(UserScopedMixin, CreatedAtMixin, db.Model):
    """A user's persisted pay-period cadence and rolling-window config.

    Exactly one row per user, enforced by ``uq_pay_schedule_user``
    (UNIQUE on ``user_id``).  The row is created or refreshed by
    ``pay_schedule_service.upsert_schedule`` whenever the schedule's
    cadence is established (first generation) or changed (regenerate).

    Columns:

      ``cadence_days`` -- days between consecutive paydays (e.g. 14 for
                          biweekly).  ``ck_pay_schedule_cadence_range``
                          bounds it to 1..365, matching the
                          ``generate_pay_periods`` cadence argument and
                          the generate/extend Marshmallow schemas.
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
            "cadence_days BETWEEN 1 AND 365",
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
