"""
Shekel Budget App -- Asset Appreciation Parameters Model (budget schema)

Stores the annual appreciation rate for physical-asset account types
(Property / Real Estate, and future Vehicle / valuables) whose balance
is a market value that grows -- or depreciates -- over time rather than
being funded by transactions.
"""

from app.extensions import db
from app.models.mixins import AccountScopedUniqueMixin, TimestampMixin


class AssetAppreciationParams(AccountScopedUniqueMixin, TimestampMixin, db.Model):
    """Appreciation parameters linked one-to-one with an Account.

    Serves any account type that has ``has_appreciation=True`` on its
    :class:`~app.models.ref.AccountType` (currently Property).  Stores the
    single annual appreciation rate the net-worth and savings projections
    feed into :func:`app.services.growth_engine.project_balance` (with
    contributions zeroed) to carry the market value forward.  Unlike
    :class:`~app.models.investment_params.InvestmentParams`, there are no
    contributions, employer match, or contribution limits -- a home is
    valued, not funded.

    Uses :class:`~app.models.mixins.AccountScopedUniqueMixin` for the
    one-to-one ``account_id`` (NOT NULL, UNIQUE, ``ON DELETE CASCADE``) --
    the same shared declaration ``LoanParams`` and ``InvestmentParams`` use
    -- and adds a one-to-one eager backref so
    ``account.asset_appreciation_params`` is readable without a separate
    batch-loaded parameter map.
    """

    __tablename__ = "asset_appreciation_params"
    __table_args__ = (
        # Appreciation rate is a decimal fraction (e.g. 0.03500 for
        # 3.5%/yr).  The exclusive lower bound mirrors
        # ``ck_investment_params_valid_return``: a -100% rate (-1) makes
        # the per-period compounding non-invertible, and the upper bound
        # of 1 (100%/yr) is a generous typo guard.  A negative rate is
        # permitted so a future depreciating asset (e.g. Vehicle) reuses
        # this table unchanged.
        db.CheckConstraint(
            "annual_appreciation_rate > -1 AND annual_appreciation_rate <= 1",
            name="ck_asset_appreciation_params_valid_rate",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    annual_appreciation_rate = db.Column(db.Numeric(7, 5), nullable=False)

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref(
            "asset_appreciation_params", uselist=False, lazy="joined",
        ),
    )

    def __repr__(self):
        return (
            f"<AssetAppreciationParams account_id={self.account_id} "
            f"rate={self.annual_appreciation_rate}>"
        )
