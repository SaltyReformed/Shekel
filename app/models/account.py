"""
Shekel Budget App -- Account Models (budget schema)

Tracks checking and savings accounts with anchor balance history
for the true-up workflow.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


class Account(
    UserScopedMixin, SortOrderMixin, IsActiveMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
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
        # Anchor balance presence (E-19, Commit 3).  Redundant with the
        # NOT NULL on the column itself, but named so a future schema
        # audit can match it to the Marshmallow contract by name.  The
        # canonical balance resolver (Commit 4) relies on this guarantee
        # to delete the four NULL-anchor forks documented in CRIT-01.
        db.CheckConstraint(
            "current_anchor_balance IS NOT NULL",
            name="ck_accounts_anchor_balance_present",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.account_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    # Anchor columns are the storage-tier half of E-19: the
    # canonical balance producer (Commit 4) assumes both are non-NULL
    # on every account row, so CRIT-01's four NULL-anchor forks
    # (blank/projection/omit) become unreachable.  See migration
    # cfb15e782f86 for the backfill rule and the rationale.
    #
    # FK action note: ``current_anchor_period_id`` is ``ON DELETE NO
    # ACTION DEFERRABLE INITIALLY IMMEDIATE`` (migration d410f6b9caa3,
    # pay-period CRUD Phase 0).  The column is ``NOT NULL``, so deleting
    # the referenced pay period is refused immediately -- the database
    # backstop behind the application-level anchor lock in
    # ``pay_period_admin``.  ``NO ACTION`` (not ``RESTRICT``) is chosen
    # because only ``NO ACTION`` can be deferred: the full-reset path
    # (``reset_pay_periods``, Phase 3) deletes the old anchor period and
    # re-points each account to a fresh one inside one transaction via
    # ``SET CONSTRAINTS ... DEFERRED``, so the FK validates at commit.
    # Every other path keeps the fail-fast immediate check.
    current_anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    current_anchor_period_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.pay_periods.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

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


class AccountAnchorHistory(AccountScopedMixin, CreatedAtMixin, db.Model):
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
