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
        # #38: ``compounding_frequency`` was a free-string column with
        # an ``IN (...)`` CHECK; it is now the ref-table FK
        # ``compounding_frequency_id`` (validity enforced by the FK +
        # RESTRICT, not a CHECK).
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
    # HIGH-06 / Commit 24: ``apy`` is NOT NULL but has no
    # ``server_default``.  Pre-fix the column carried
    # ``server_default="0.04500"`` so any INSERT that omitted
    # ``apy`` -- specifically the auto-create paths in
    # ``app/routes/accounts.py`` at account-creation and at
    # interest-detail rendering -- silently materialised a 4.5%
    # rate the user never configured.  ``calculate_interest``
    # treats only ``apy <= 0`` as "no interest"
    # (``interest_projection.py``), so a missing-value default in
    # the dangerous non-zero direction (Q-24 #2 / E-12 "zero is a
    # value, not missing") shipped ghost interest projections to
    # every silently-created row.  The fix is twofold: the
    # ``server_default`` is removed here, and the two auto-create
    # sites in ``accounts.py`` now pass an explicit
    # ``apy=Decimal("0")`` sentinel so the row is created in the
    # safe "no interest configured" state until the user enters a
    # real APY via the interest-detail form.
    apy = db.Column(db.Numeric(7, 5), nullable=False)
    # #38: ref-table FK (was a free-string ``compounding_frequency``
    # column).  RESTRICT mirrors the other ref FKs; the seeded DAILY
    # row is the auto-create default, set explicitly at each
    # construction site rather than a server_default (an FK id is not
    # a static literal).
    compounding_frequency_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.compounding_frequencies.id",
            name="fk_interest_params_compounding_frequency",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("interest_params", uselist=False, lazy="joined"),
    )
    compounding_frequency = db.relationship(
        "CompoundingFrequency", lazy="joined",
    )

    def __repr__(self):
        return f"<InterestParams account_id={self.account_id} apy={self.apy}>"
