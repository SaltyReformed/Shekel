"""
Shekel Budget App -- Loan Account Feature Models (budget schema)

Account-level features that extend loan accounts: escrow components
for impound accounts and rate change history for variable-rate loans.
Both FK to account_id, not to any params table.
"""

from datetime import date

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    TimestampMixin,
)


class RateHistory(AccountScopedMixin, CreatedAtMixin, db.Model):
    """Historical record of rate changes for a variable-rate loan account.

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
    the constraint matches the domain model: a same-day correction
    is expressed by editing the existing row rather than appending
    a duplicate.
    """

    __tablename__ = "rate_history"
    __table_args__ = (
        db.UniqueConstraint(
            "account_id", "effective_date",
            name="uq_rate_history_account_effective_date",
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


def _default_effective_date() -> date:
    """Today, resolved from this module's ``date`` name at INSERT time.

    The :class:`EscrowComponent.effective_date` ORM default.  A bare
    ``default=date.today`` would capture the real ``date.today`` bound method at
    class-definition time, so the test suite's ``freeze_today`` -- which rebinds
    this module's ``date`` name, not the captured method -- could not reach it,
    leaving an ORM INSERT's ``effective_date`` on the wall clock while a
    same-transaction ``end_date`` (set from the frozen ``date.today()``) uses the
    frozen clock, tripping ``ck_escrow_components_date_range``.  Looking ``date``
    up at CALL time keeps the two clocks identical (real in production, frozen
    under ``freeze_today``).
    """
    return date.today()


class EscrowComponent(AccountScopedMixin, TimestampMixin, db.Model):
    """An effective-dated escrow line item (property tax, insurance, etc.) for a loan.

    **Temporal (effective-dated) model.**  A component is not a single mutable
    row but a series of versions, each valid over a half-open date range
    ``[effective_date, end_date)`` -- the same effective-dating shape
    :class:`RateHistory` uses for rates.  This lets the loan-payment posting
    split read the escrow that was in effect *on each payment's date*
    (immutable for a past date, so a posted split never silently moves when the
    user later changes escrow), and it retires the previous ``is_active``
    boolean: "currently active" is now exactly ``end_date IS NULL``.

    * **Active on a date D** iff ``effective_date <= D AND (end_date IS NULL OR
      D < end_date)``.  ``end_date`` is exclusive so a component removed on D is
      not counted on D.
    * **Add** a component -> insert a row with ``end_date = NULL``.
    * **Remove** a component -> stamp ``end_date`` on the active row (replaces
      the old ``is_active = False``); the row survives as history.
    * **Change** an amount -> close the current version (stamp ``end_date``) and
      insert a new one; the existing add/delete flow already expresses this as
      delete + add.

    The monthly figure at a date is summed by
    :func:`app.services.escrow_calculator.calculate_monthly_escrow` over the
    components active on that date (loaded by
    :func:`app.services.loan_payment_service.escrow_components_as_of` /
    :func:`app.services.loan_payment_service.load_active_escrow_components`).
    """

    __tablename__ = "escrow_components"
    __table_args__ = (
        # At most one ACTIVE (``end_date IS NULL``) version per name per
        # account.  Partial so a removed version and its re-added successor may
        # share a name across time; replaces the former total
        # ``uq_escrow_account_name`` (a total unique would forbid ever re-adding
        # a removed line item under the same name).
        db.Index(
            "uq_escrow_components_account_name_active",
            "account_id", "name", unique=True,
            postgresql_where=db.text("end_date IS NULL"),
        ),
        # A version's active range is well-formed: an open range (still in
        # effect) or a closed one that does not end before it begins.  ``>=``
        # (not ``>``) admits a zero-length range -- a component added and
        # removed on the same day -- which is a legitimate "never active"
        # version (``active_on(D)`` is ``effective_date <= D < end_date``, empty
        # when the two are equal), and which a strict ``>`` would wrongly reject
        # on a same-day add-then-delete.
        db.CheckConstraint(
            "end_date IS NULL OR end_date >= effective_date",
            name="ck_escrow_components_date_range",
        ),
        # Serves the as-of lookup (WHERE account_id = ? AND effective_date <= ?
        # AND (end_date IS NULL OR ? < end_date)) the split walks per payment.
        db.Index(
            "ix_escrow_components_account_effective",
            "account_id", "effective_date", "end_date",
        ),
        # F-077 / C-24: Annual escrow amount must be non-negative.
        # Column is ``Numeric(12, 2)`` and the route validates a
        # positive Range at the schema layer; the CHECK is the
        # storage-tier counterpart for raw-SQL writers.
        db.CheckConstraint(
            "annual_amount >= 0",
            name="ck_escrow_components_nonneg_annual_amount",
        ),
        # F-077 / C-24 (HIGH-06 / Commit 24 reconciliation):
        # ``inflation_rate`` is nullable (NULL = no escalation) and
        # persisted as a decimal fraction.  ``EscrowComponentSchema``'s
        # ``@pre_load`` converts the form percent to fraction (E-28)
        # so the route stores the converted value directly.  CHECK
        # pins storage to ``[0, 1]`` when present.
        db.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_escrow_components_valid_inflation_rate",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    annual_amount = db.Column(db.Numeric(12, 2), nullable=False)
    inflation_rate = db.Column(db.Numeric(5, 4), nullable=True)
    # First date this version of the component is in effect.  Python
    # ``default=_default_effective_date`` (a CALL-TIME ``date.today()``; see that
    # helper for why a bare ``default=date.today`` is wrong) so an ORM INSERT
    # that omits it (the add route, the tests) stamps the APP's today -- the SAME
    # clock the delete route stamps ``end_date`` with, so a same-day
    # add-then-delete yields
    # ``effective_date == end_date`` (a valid zero-length range) rather than a
    # DB-clock ``effective_date`` that could sit AFTER an app-clock ``end_date``
    # under a frozen test clock.  ``server_default`` CURRENT_DATE is the
    # storage-tier fallback for a raw-SQL writer that omits it.  The temporal
    # migration backfills existing rows to the loan's origination date so every
    # historical payment sees today's escrow exactly as it did pre-migration.
    effective_date = db.Column(
        db.Date, nullable=False,
        default=_default_effective_date, server_default=db.func.current_date(),
    )
    # Exclusive end of this version's active range.  NULL = still in effect
    # (the "currently active" set); a non-NULL value means the component was
    # removed (or superseded by a new version) on that date.
    end_date = db.Column(db.Date, nullable=True)

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("escrow_components", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<EscrowComponent account_id={self.account_id} "
            f"name={self.name!r} annual={self.annual_amount}>"
        )
