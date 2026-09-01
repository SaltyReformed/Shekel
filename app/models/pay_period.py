"""
Shekel Budget App -- Pay Period Model (budget schema)

Auto-generated biweekly date ranges that anchor every transaction
to a specific paycheck.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


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
        # An owner holding a payday HAS a recorded cadence, guaranteed rather
        # than remembered (plan step C4-b-2, closing findings **P8** and
        # **P35**).  ``pay_period_write._apply`` upserts the schedule row
        # before it writes a single period, so the rule was already true --
        # but only for as long as every future writer remembered it, and the
        # state it forbids is one ``resolve_schedule`` had to carry a CIRCULAR
        # guess for: infer the cadence from the last period's stored length,
        # which ``record_paydays`` derives FROM that same cadence.  With the
        # key there is nothing left to infer, so the guess is deleted rather
        # than left unreachable.
        #
        # It targets ``budget.pay_schedule.user_id`` -- legal because
        # ``uq_pay_schedule_user`` makes that column a superkey -- so it needs
        # no new column and stores no second pointer to keep in step.
        #
        # **It is NOT the ``fk_statement_matches_owner`` construction, and a
        # first draft of this comment said it was** (adversarial design review,
        # 2026-09-01).  That key is COMPOSITE, ``(account_id, user_id) ->
        # accounts (id, user_id)``, and its job is to hold a denormalised COPY
        # equal to its source.  This one is a single-column EXISTENCE key:
        # there is no copy and nothing is being held equal.  Two different
        # constraint kinds for two different problems, and citing one for the
        # other is how a design skips the question it should ask.
        #
        # The column's OTHER key, to ``auth.users`` from
        # :class:`~app.models.mixins.UserScopedMixin`, stays: it carries the
        # ``ON DELETE CASCADE`` that lets a user delete clear this table, and
        # without it this key would REFUSE that delete.  Two keys on one
        # column with two different actions, which is what makes them two
        # facts rather than one repeated.
        #
        # RESTRICT is the developer's ruling of 2026-09-01 (**R-PC41**), taken
        # against CASCADE, NO ACTION and DEFERRABLE.  Nothing in ``app/``
        # deletes a ``budget.pay_schedule`` row, so the event has no live
        # source and can only be reached by a bug, a hand-run statement, or a
        # future door whose author has not thought about it -- and every one
        # of those wants a loud refusal.  Measured on a clone of production's
        # shape before the ruling: under CASCADE, deleting one schedule row
        # took 63 pay periods, 1,057 transactions and every journal entry.
        # The cadence is a SETTING and the paydays are the RECORD; a record is
        # never destroyed because a setting went away.
        #
        # No index is added.  A referencing-side index is what makes the
        # parent's delete check cheap, and ``uq_pay_periods_user_start``
        # already leads with ``user_id`` -- it survives plan step C4-c, which
        # drops ``uq_pay_periods_user_index``.
        db.ForeignKeyConstraint(
            ["user_id"],
            ["budget.pay_schedule.user_id"],
            name="fk_pay_periods_schedule",
            ondelete="RESTRICT",
        ),
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

    def __repr__(self):
        return f"<PayPeriod {self.start_date} idx={self.period_index}>"
