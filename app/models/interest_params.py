"""
Shekel Budget App -- Interest Parameters Model (budget schema)

Stores interest configuration for interest-bearing account types
(HYSA, Money Market, CD, HSA, etc.): APY and compounding frequency.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class InterestParams(TimestampMixin, db.Model):
    """Interest parameters linked one-to-one with an Account.

    Serves any account type that has ``has_interest=True`` on its
    :class:`AccountType`.  Stores the annual percentage yield and
    compounding frequency used by the interest projection engine.
    """

    __tablename__ = "interest_params"
    __table_args__ = (
        db.CheckConstraint(
            "compounding_frequency IN ('daily', 'monthly', 'quarterly')",
            name="ck_interest_params_frequency",
        ),
        # F-077 / C-24: ``apy`` is persisted as a decimal fraction
        # (e.g. ``0.04500`` for 4.5%) by ``app/routes/accounts.py``
        # which divides the user-entered percent by 100 before
        # INSERT.  CHECK pins storage to ``[0, 1]`` so a future
        # writer that forgets the conversion is rejected at the
        # database tier; the column itself is ``Numeric(7, 5)`` and
        # could otherwise hold up to 999.99 (=99,999% APY).
        db.CheckConstraint(
            "apy >= 0 AND apy <= 1",
            name="ck_interest_params_valid_apy",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        # F-072 / F-138 / C-42: explicit FK name follows the project's
        # ``fk_*`` convention documented in ``docs/coding-standards.md``.
        # Earlier the constraint carried the Alembic-default
        # ``interest_params_account_id_fkey`` name (renamed by
        # 44893a9dbcc3 from the deeper-legacy
        # ``hysa_params_account_id_fkey``); this declaration keeps
        # ``db.create_all()`` aligned with the post-C-42 migrated
        # state so the test-template path and the production
        # migration chain converge on the same name.
        db.ForeignKey(
            "budget.accounts.id",
            name="fk_interest_params_account",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )
    apy = db.Column(db.Numeric(7, 5), nullable=False, server_default="0.04500")
    compounding_frequency = db.Column(
        db.String(10), nullable=False, server_default="daily"
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("interest_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return f"<InterestParams account_id={self.account_id} apy={self.apy}>"
