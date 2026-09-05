"""
Shekel Budget App -- Pay Period Model (budget schema)

**One row is ONE FACT: the day money arrived.**  A pay period's ordinal is its
position in its owner's payday order and its last covered day is the day before
the next payday, and since plan step ``pay_calendar:C4-c`` neither is stored --
:func:`app.services.pay_calendar.derive_periods` is the one place either is
answered.  Until that step the table carried both as columns beside the fact
they derive from, with nothing reconciling them, which is what made a gap, an
overlap and an ordinal out of date order EXPRESSIBLE states that five separate
runtime fences had to police (``docs/plans/implementation_plan_pay_calendar.md``
section 1).  None of the three is expressible now, so none of the fences has a
subject.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class PayPeriod(UserScopedMixin, CreatedAtMixin, db.Model):
    """One payday, which is the whole of what this table records."""

    __tablename__ = "pay_periods"
    __table_args__ = (
        # The payday model's exact key: one period per owner per opening day.
        # It is the ONLY uniqueness rule the table needs since plan step
        # ``pay_calendar:C4-c`` -- ``uq_pay_periods_user_index`` policed a
        # duplicate ORDINAL, which is now a position in a sort rather than a
        # value anything can repeat.
        db.UniqueConstraint("user_id", "start_date", name="uq_pay_periods_user_start"),
        # The SUPERKEY ``budget.transactions`` names to prove its own owner is
        # this period's (plan step ``pay_calendar:C13-a``, ruling **R-PC32**).
        # It constrains nothing -- ``id`` is already the primary key, so this
        # key can reject no row -- and exists only because PostgreSQL requires
        # a UNIQUE over exactly the referenced columns before a composite
        # foreign key may target them.  The same construction, for the same
        # reason, as ``uq_accounts_id_user`` one table over and
        # ``uq_transactions_id_account`` on the table that names this one.
        db.UniqueConstraint("id", "user_id", name="uq_pay_periods_id_user"),
        # An owner holding a payday HAS a recorded cadence, guaranteed rather
        # than remembered (plan step C4-b-2, closing findings **P8** and
        # **P35**).  ``pay_period_write._apply`` upserts the schedule row
        # before it writes a single period, so the rule was already true --
        # but only for as long as every future writer remembered it, and the
        # state it forbids is one ``resolve_schedule`` had to carry a CIRCULAR
        # guess for: infer the cadence from the last period's stored length,
        # which ``record_paydays`` derived FROM that same cadence.  With the
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
        # already leads with ``user_id``.
        db.ForeignKeyConstraint(
            ["user_id"],
            ["budget.pay_schedule.user_id"],
            name="fk_pay_periods_schedule",
            ondelete="RESTRICT",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    #: The payday -- the day money arrived, and the only fact in the row.
    #:
    #: Everything a consumer used to read off a neighbouring column is derived
    #: from this and its siblings by
    #: :func:`app.services.pay_calendar.derive_periods`, whose answer is a
    #: :class:`~app.services.pay_calendar.DerivedPeriod`.
    start_date = db.Column(db.Date, nullable=False)

    # Relationships -- transactions loaded via back_populates on Transaction
    # ``foreign_keys`` for the reason ``Transaction.pay_period`` states: this
    # table is reached from ``budget.transactions`` by two declared keys since
    # plan step ``pay_calendar:C13-a``, and the single-column one is the
    # declared join path for both directions of the pair.
    transactions = db.relationship(
        "Transaction", foreign_keys="Transaction.pay_period_id",
        back_populates="pay_period", lazy="select",
    )

    def __repr__(self):
        return f"<PayPeriod {self.start_date}>"
