"""
Shekel Budget App -- Pay Period Model (budget schema)

Auto-generated biweekly date ranges that anchor every transaction
to a specific paycheck.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin
from app.utils.dates import pay_period_label


#: The shortest cadence the CURRENT writer can materialise into this table.
#:
#: **Not a claim that a one-day pay cycle is illegitimate -- it is legal, and
#: pay-calendar step C4 legalises it.**  ``ck_pay_periods_date_order`` below
#: requires ``start_date < end_date``, while
#: :func:`app.services.pay_period_write.record_paydays` STORES a last period
#: whose end is ``start_date + (cadence_days - 1)``; at a cadence of 1 those
#: two are the same day and the INSERT dies as a ``CheckViolation`` -- an
#: unhandled 500 on both the settings form and, since plan step X-ad-a, the
#: public registration form.  So this bound is a property of the
#: REPRESENTATION, not of the schedule: a STORED ``end_date`` cannot hold a
#: one-day period.  ``budget.pay_schedule.cadence_days`` still accepts 1 and
#: :data:`app.services.pay_calendar.MIN_CADENCE_DAYS` still derives one
#: correctly, because neither of those has a column to write it to.
#:
#: *Every other* end became ``next payday - 1`` at plan step C3-b, which is why
#: the bound now reads off the last period alone: two paydays a day apart in
#: the MIDDLE of a schedule derive a one-day period the same way, and the same
#: CHECK refuses it, which is what ``_reject_backward_payday`` keeps out.
#:
#: **C4 deletes this constant with the column and the CHECK it mirrors**
#: (pay-calendar finding **P9**): once ``end_date`` is derived rather than
#: stored, two paydays a day apart define a one-day period and nothing has to
#: refuse anything.  Until then the writer states what it cannot express, so
#: the refusal is a form error rather than a stack trace.
MIN_MATERIALISABLE_CADENCE_DAYS = 2


class PayPeriod(UserScopedMixin, CreatedAtMixin, db.Model):
    """A single pay period defined by start_date (payday) and end_date."""

    __tablename__ = "pay_periods"
    __table_args__ = (
        db.UniqueConstraint("user_id", "start_date", name="uq_pay_periods_user_start"),
        # ``period_index`` is unique per user: the balance resolver walks a
        # user's periods ordered by ``period_index`` and trusts that order
        # to be chronological, so a duplicate index would silently drop a
        # period from as-of balances.  Enforced in the schema (migration
        # f75485db6757) so every period-appending path -- extend,
        # regenerate, rolling top-up -- is protected, not just one.  The
        # backing index also serves the ``(user_id, period_index)`` lookups
        # the old non-unique ``idx_pay_periods_user_index`` covered.
        db.UniqueConstraint(
            "user_id", "period_index", name="uq_pay_periods_user_index"
        ),
        db.CheckConstraint("start_date < end_date", name="ck_pay_periods_date_order"),
        db.CheckConstraint("period_index >= 0", name="ck_pay_periods_positive_index"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    period_index = db.Column(db.Integer, nullable=False)

    # Relationships -- transactions loaded via back_populates on Transaction
    transactions = db.relationship(
        "Transaction", back_populates="pay_period", lazy="select"
    )

    @property
    def label(self):
        """Human-readable label, e.g. '02/21 - 03/06' or '12/26/26 - 01/08/27'.

        **The rule itself is :func:`app.utils.dates.pay_period_label`**, shared
        with :attr:`app.services.pay_calendar.DerivedPeriod.label` since plan
        step C2-f: two types answer "which paycheck" in this application and a
        user comparing two screens is looking at the same period, so the format
        is stated once.  Plan step **C4** deletes this accessor along with the
        ``end_date`` column it reads; the shared function and the derived
        value's property are what survive.
        """
        return pay_period_label(self.start_date, self.end_date)

    def __repr__(self):
        return f"<PayPeriod {self.start_date} idx={self.period_index}>"
