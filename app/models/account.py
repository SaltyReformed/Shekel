"""
Shekel Budget App -- Account Models (budget schema)

Tracks checking and savings accounts with anchor balance history
for the true-up workflow.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, TimestampMixin


class Account(TimestampMixin, db.Model):
    """A financial account (checking or savings) owned by a user.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is automatically narrowed to ``WHERE id = ? AND
    version_id = ?`` and the stored value is incremented in the same
    statement.  When two concurrent requests both load the same row
    at version N, the first commit advances the row to N+1; the
    second commit's WHERE matches zero rows, SQLAlchemy raises
    :class:`sqlalchemy.orm.exc.StaleDataError`, and the calling
    route returns HTTP 409 Conflict.

    The column has ``server_default="1"`` so existing rows on the
    production database are populated automatically when the
    accompanying migration runs ALTER TABLE; new rows insert with
    version_id = 1 on either path (default or explicit).
    """

    __tablename__ = "accounts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_accounts_user_name"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_accounts_version_id_positive",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.account_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    current_anchor_balance = db.Column(db.Numeric(12, 2))
    current_anchor_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id", ondelete="SET NULL"),
    )
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    # Optimistic-locking version counter.  See the class docstring
    # for the contract.  NOT NULL with server_default="1" so existing
    # production rows are filled at ALTER TABLE time and new rows
    # always start at version 1.
    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    # Optimistic locking: SQLAlchemy will (a) issue
    # ``UPDATE ... WHERE id = ? AND version_id = ?`` for every flush
    # of a dirty Account, (b) atomically increment version_id in the
    # same statement, and (c) raise StaleDataError when rowcount = 0.
    # Routes that mutate Account MUST catch StaleDataError and
    # return 409 Conflict.  See app/routes/accounts.py.
    __mapper_args__ = {"version_id_col": version_id}

    # Relationships
    account_type = db.relationship("AccountType", lazy="joined")
    anchor_period = db.relationship("PayPeriod", foreign_keys=[current_anchor_period_id])
    anchor_history = db.relationship(
        "AccountAnchorHistory",
        back_populates="account",
        order_by="AccountAnchorHistory.created_at.desc()",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Account {self.name} ({self.id})>"


class AccountAnchorHistory(CreatedAtMixin, db.Model):
    """Audit trail of anchor balance true-ups for an account.

    Same-day duplicate prevention (F-103 / C-22): the partial unique
    expression index ``uq_anchor_history_account_period_balance_day``
    on ``(account_id, pay_period_id, anchor_balance,
    ((created_at AT TIME ZONE 'UTC')::date))`` rejects a second row
    with identical values inserted on the same calendar day.  This
    is the database-level backstop for ``true_up`` and
    ``inline_anchor_update`` double-submits: a network retry, a
    double-click on the Save button, or the back-and-resubmit
    pattern would otherwise create two consecutive history rows with
    the same anchor_balance, polluting the audit trail with entries
    that record nothing the prior row did not already record.

    The index intentionally includes ``anchor_balance`` so two
    legitimate true-ups on the same day -- the user noticed an
    arithmetic error and corrected the balance twice -- are still
    allowed; only literal duplicate rows (same balance, same
    period, same day, same account) are rejected.  Truncating
    ``created_at`` (a ``timestamptz``) to a civil date requires
    pinning the timezone via ``AT TIME ZONE 'UTC'`` -- PostgreSQL
    refuses to use the bare ``::date`` cast in an index because the
    cast depends on the session's TimeZone and is therefore not
    IMMUTABLE.  UTC is the application's storage timezone (every
    ``timestamptz`` in this database is stored in UTC by
    ``CreatedAtMixin``), so anchoring the truncation to UTC matches
    the row's logical day-of-record exactly.
    """

    __tablename__ = "account_anchor_history"
    __table_args__ = (
        db.Index(
            "idx_anchor_history_account",
            "account_id",
            "created_at",
        ),
        # Functional unique index on a UTC-day-truncated timestamp; the
        # raw text expression matches the migration's DDL exactly so
        # Alembic autogenerate produces no spurious diff against the
        # post-migration database state.  See class docstring for the
        # business rationale and the IMMUTABLE-cast requirement.
        db.Index(
            "uq_anchor_history_account_period_balance_day",
            "account_id", "pay_period_id", "anchor_balance",
            db.text("((created_at AT TIME ZONE 'UTC')::date)"),
            unique=True,
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_period_id = db.Column(
        db.Integer, db.ForeignKey("budget.pay_periods.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    notes = db.Column(db.Text)

    # Relationships
    account = db.relationship("Account", back_populates="anchor_history")
    pay_period = db.relationship("PayPeriod")

    def __repr__(self):
        return f"<AnchorHistory account={self.account_id} balance={self.anchor_balance}>"
