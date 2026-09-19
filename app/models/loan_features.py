"""
Shekel Budget App -- Loan Account Feature Models (budget schema)

Rate change history, keyed to ``account_id`` and not to any params table --
which is what lets a revolving credit account (a Credit Card) ride the same
table for its APR since plan step credit_card:CC-3 (design
``docs/design/credit_card_from_scratch.md`` 3.4): a loan's rows are its rate
periods, read through :class:`~app.models.loan_params.LoanParams` by the loan
loaders; a card's rows are its APR series, read by
:mod:`app.services.card_apr` and written by the card's own doors.  Escrow
moved to the supersession model in :mod:`app.models.escrow_line` (an
:class:`~app.models.escrow_line.EscrowLine` and its effective-dated
versions), so this module no longer hosts escrow.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
)

#: The one-rate-per-effective-date key, named ONCE where the table declares
#: it: the loan's door reads the name to translate a collision into a refusal
#: and the card's door names it as the ``ON CONFLICT`` target of its set-by-date
#: write (plan step credit_card:CC-3), so neither spells the string itself.
RATE_HISTORY_UNIQUE_CONSTRAINT = "uq_rate_history_account_effective_date"


class RateHistory(AccountScopedMixin, CreatedAtMixin, db.Model):
    """Historical record of an account's rate changes, one per effective date.

    A variable-rate loan's rate periods (``monthly_pi`` is the recast P&I a
    loan may record), or a credit card's APR series (plan step
    credit_card:CC-3; a card records no P&I and reads the rows through
    :mod:`app.services.card_apr`).

    Duplicate prevention (F-104 / C-22): the composite unique
    constraint ``uq_rate_history_account_effective_date`` on
    ``(account_id, effective_date)`` rejects a second rate-change
    row with the same effective date.  Without it a double-submit
    of the loan rate form -- network retry, double-click, browser
    back-and-resubmit -- would create two history rows the
    amortisation engine cannot disambiguate (which rate applies on
    that date?), and the UI's "Most recent rate" rendering would
    flip non-deterministically depending on insertion order.  Each
    rate change has exactly one effective date by definition, so
    the constraint matches the domain model.  What each side's
    doors then make TRUE (developer ruling R-CC27, 2026-09-18):
    the card's set door writes the row BY date, creating or
    rewriting it, so a same-date submit corrects the rate, and its
    remove door deletes a row, so a mistyped date is correctable;
    the loan's door only appends and translates a same-date
    collision into a refusal -- no loan door edits or removes a
    row, so a loan's correction path does not exist yet.
    """

    __tablename__ = "rate_history"
    __table_args__ = (
        db.UniqueConstraint(
            "account_id", "effective_date",
            name=RATE_HISTORY_UNIQUE_CONSTRAINT,
        ),
        # F-077 / C-24 (HIGH-06 / Commit 24 reconciliation):
        # ``interest_rate`` is persisted as a decimal fraction (e.g.
        # ``0.04500`` for 4.5%).  The rate-change route's schema
        # (``RateChangeSchema``) divides the user-facing percent by
        # 100 in its ``@pre_load`` (E-28), so the route stores the
        # already-converted fraction directly.  The CHECK pins
        # storage to the closed unit interval so a future writer
        # that forgets the conversion is rejected at the database
        # tier rather than silently storing 4.5 as "450%".
        db.CheckConstraint(
            "interest_rate >= 0 AND interest_rate <= 1",
            name="ck_rate_history_valid_interest_rate",
        ),
        # The recorded recast P&I is a strictly-positive monetary
        # amount when present; NULL means "derive" (see the column
        # comment).  ``IS NULL OR ...`` preserves the nullable
        # demotion exactly as ``loan_params.interest_rate_upper`` does:
        # PostgreSQL treats NULL as unknown under the predicate, so the
        # CHECK permits NULL and rejects any non-NULL non-positive
        # amount a raw-SQL writer might attempt.
        db.CheckConstraint(
            "monthly_pi IS NULL OR monthly_pi > 0",
            name="ck_rate_history_monthly_pi_positive",
        ),
        # F-139 / C-42: composite index on
        # ``(account_id, effective_date DESC)`` matches the
        # predominant query in ``app/routes/loan/escrow_rates.py``:
        # ``RateHistory.query.filter_by(account_id=X)
        #     .order_by(RateHistory.effective_date.desc())``.
        # DESC ordering on the second column lets PostgreSQL satisfy
        # both the WHERE and the ORDER BY from a forward index scan;
        # an ascending second column would still serve correctness
        # (B-tree indexes scan backward) but would obscure the
        # canonical query shape from anyone reading the index.  The
        # uq_rate_history_account_effective_date unique index covers
        # ``(account_id, effective_date)`` without DESC; this
        # secondary non-unique index encodes the sort direction.
        db.Index(
            "idx_rate_history_account",
            "account_id", db.text("effective_date DESC"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    effective_date = db.Column(db.Date, nullable=False)
    interest_rate = db.Column(db.Numeric(7, 5), nullable=False)
    # Recast P&I (principal + interest, no escrow) that took effect on
    # ``effective_date`` -- the level payment the lender fixed for the
    # rate period this row begins.  NULL means "derive": the
    # rate-period engine amortizes the period-start balance over the
    # remaining term, which is exact only for the origination period or
    # a loan whose full payment history is present.  A mid-life ARM
    # whose period-start balance predates the app's recorded history
    # MUST record this value (it is printed on every statement) so the
    # period's monthly payment is held constant at the lender's figure
    # instead of being re-derived from a balance that may have drifted.
    # Consumed by ``app/services/rate_period_engine.py``.
    monthly_pi = db.Column(db.Numeric(12, 2), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("rate_history", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<RateHistory account_id={self.account_id} "
            f"date={self.effective_date} rate={self.interest_rate}>"
        )
