"""
Shekel Budget App -- Transfer Template Model (budget schema)

A template defines a recurring transfer between accounts (e.g. "Monthly
savings contribution") along with its recurrence rule and default amount.
The transfer recurrence engine uses templates to auto-generate Transfer
rows into future pay periods.
"""

from app.extensions import db
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


class TransferTemplate(
    UserScopedMixin, IsActiveMixin, SortOrderMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """Blueprint for a recurring transfer between two accounts.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent transfer-template edits
    race for the bump; the loser raises ``StaleDataError`` and the
    route surfaces a flash + redirect.  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    __tablename__ = "transfer_templates"
    __table_args__ = (
        db.Index("idx_transfer_templates_user", "user_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfer_templates_different_accounts",
        ),
        db.CheckConstraint(
            "default_amount > 0",
            name="ck_transfer_templates_positive_amount",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transfer_templates_version_id_positive",
        ),
        db.UniqueConstraint("user_id", "name", name="uq_transfer_templates_user_name"),
        {"schema": "budget"},
    )

    # Pylint: ``duplicate-code`` -- Incidental id-PK + from/to-account FK
    # preamble, shared by structure (not by domain) with the transfer table.
    # A transfer is a generated instance and a transfer_template is its
    # blueprint; they are deliberately separate tables (the transfer carries
    # the shadow-transaction invariants), so extracting a base would couple
    # them wrongly (coding-standards rule 13).  One-sided disable.
    # pylint: disable=duplicate-code
    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recurrence_rule_id = db.Column(
        db.Integer, db.ForeignKey("budget.recurrence_rules.id", ondelete="SET NULL"),
    )
    name = db.Column(db.String(200), nullable=False)
    default_amount = db.Column(db.Numeric(12, 2), nullable=False)
    # is_active + sort_order: from IsActiveMixin / SortOrderMixin.
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    # When TRUE, this template's recurring transfers are a loan payment
    # whose cash amount (P&I + escrow/components) is derived LIVE from
    # the destination loan account on every balance render, so it tracks
    # the loan's monthly payment after an escrow or rate change rather
    # than staying frozen at ``default_amount`` (E-18 loan model).  Set by
    # ``app.routes.loan.payment_transfer.create_payment_transfer`` for new
    # loan-payment transfers; FALSE for every other template and pre-existing
    # rows, so the live-derive override is dormant unless enabled.
    derive_from_loan = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    recurrence_rule = db.relationship("RecurrenceRule", lazy="joined")
    category = db.relationship("Category", lazy="joined")
    transfers = db.relationship(
        "Transfer", back_populates="template", lazy="select"
    )

    def __repr__(self):
        return f"<TransferTemplate '{self.name}' ${self.default_amount}>"
