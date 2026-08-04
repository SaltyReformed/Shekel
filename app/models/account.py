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
        # Collateral self-link guard (home-equity mini-sprint): an
        # account may not secure itself.  Belt-and-suspenders with the
        # route validator's no-self-link check.
        db.CheckConstraint(
            "collateral_account_id IS NULL OR collateral_account_id != id",
            name="ck_accounts_collateral_not_self",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.account_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    # An account carries NO anchor columns, and that is ruling R-EH (plan step
    # X-f1c3c).  ``current_anchor_balance`` / ``current_anchor_period_id`` were
    # a denormalized copy of the newest ``AccountAnchorHistory`` row --
    # ``cash_ledger/_facts`` said so in those words, and when they disagreed the
    # history row already won while the copy was logged and left wrong.  Twelve
    # surfaces read the copy instead of the fact.  What the account has been
    # asserted to hold is now asked of
    # :func:`app.services.cash_ledger.resolve_anchor`, the one resolver, and the
    # divergence this pair could express is not detected-and-logged, it is
    # inexpressible.  Measured before the drop: the copy agreed with the latest
    # assertion on 9 of 9 production accounts, so nothing moved.
    #
    # Their FK to ``pay_periods`` went with them, and it took real machinery:
    # ``ON DELETE NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` existed so
    # ``reset_pay_periods`` could delete the old anchor period and re-point
    # every account inside one transaction via ``SET CONSTRAINTS ... DEFERRED``.
    # With no column to re-point there is nothing to defer.
    # Collateral link (home-equity mini-sprint): a secured liability
    # (mortgage / HELOC / auto loan) points at the Asset account it is
    # secured by, so a Property and its loans can be grouped and equity
    # rendered.  Nullable -- NULL means the loan is not secured by a
    # tracked asset.  ``ON DELETE SET NULL`` (not CASCADE) keeps the loan
    # -- real money -- alive when the asset row is deleted; the link is
    # presentation only and the net-worth math never reads it.  A
    # self-link is rejected by ``ck_accounts_collateral_not_self`` and the
    # route validator.
    collateral_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.accounts.id",
            name="fk_accounts_collateral_account",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    account_type = db.relationship("AccountType", lazy="joined")
    anchor_history = db.relationship(
        "AccountAnchorHistory",
        back_populates="account",
        order_by="AccountAnchorHistory.created_at.desc()",
        cascade="all, delete-orphan",
    )
    # Self-referential collateral link.  ``remote_side`` marks the
    # referenced (asset) side; ``foreign_keys`` disambiguates from the
    # account's other FKs.  The ``secured_loans`` backref lists the
    # liabilities secured by this account (a Property can secure a
    # mortgage AND a HELOC).  ``passive_deletes`` lets the database-level
    # ``ON DELETE SET NULL`` null the link on asset deletion without the
    # ORM eagerly loading and rewriting each secured loan first.
    collateral_account = db.relationship(
        "Account",
        remote_side="Account.id",
        foreign_keys="Account.collateral_account_id",
        backref=db.backref("secured_loans", passive_deletes=True),
    )

    def __repr__(self):
        return f"<Account {self.name} ({self.id})>"


class AccountAnchorHistory(AccountScopedMixin, CreatedAtMixin, db.Model):
    """Audit trail of anchor balance true-ups for an account.

    **Two clocks, and only one of them dates anything.**  ``observed_on`` is
    the BUSINESS date -- the civil day the asserted balance was TRUE -- and it
    is what the balance engine partitions settled movements against (ruling
    R-DH).  ``created_at`` is the RECORDING instant, and its only remaining job
    is to order two assertions that share an ``observed_on`` so the last one
    recorded is that day's closing balance.  The loan side has carried the same
    split since Commit 16 (``LoanAnchorEvent.anchor_date`` beside its
    ``created_at``); the cash side is the half that never got it, and until
    2026-07-31 it derived the business date from the recording instant.  That
    derivation is what let an ordinary bookkeeping session subtract
    ``$4,001.42`` of already-cleared payments a second time (finding N-130).

    **It carries no pay period, and that is ruling R-EO** (plan step X-f1c3b).
    An assertion is a fact about a BANK -- "on day D, account A held $B" -- and
    it is true whatever the user's paychecks are scheduled to do.  A
    ``pay_period_id`` filed it under a BUDGETING artifact, on an
    ``ON DELETE CASCADE`` FK, so a pay-period operation could destroy the
    record of what the bank said: a schedule reset wiped all 78 of the
    developer's production assertions and wrote 9 fabricated replacements.  The
    column was also a CACHE of a derivation rather than a fact -- both posting
    reconciles derive a correction's period from ``observed_on`` and
    ``account_posting_service._anchors`` refuses this column BY NAME -- and it
    was already WRONG on 2 of those 78 rows, whose stored period does not
    contain their own ``observed_on`` (finding N-168).  A reader wanting the
    period an assertion books in derives it from the day, which is ruling
    R-EA verbatim.

    Same-day duplicate prevention (F-103 / C-22): the unique index
    ``uq_anchor_history_account_period_balance_day`` on ``(account_id,
    anchor_balance, observed_on)`` rejects a second row with identical values
    asserting the same business day.  This
    is the database-level backstop for ``true_up`` double-submits:
    a network retry, a double-click on the Save button, or the
    back-and-resubmit pattern would otherwise create two consecutive
    history rows with the same anchor_balance, polluting the audit
    trail with entries that record nothing the prior row did not
    already record.

    **Its key lost ``pay_period_id`` with the column, and the guard got
    STRICTLY TIGHTER rather than looser** (measured: 0 of the 78 production
    rows are rejected by the narrower key).  The period was derived from the
    day, so two rows sharing a day shared a period -- except across a schedule
    rebuild, which is the one case the narrower key now also catches.  The
    index NAME is deliberately unchanged: renaming it would touch
    ``anchor_service.ANCHOR_HISTORY_UNIQUE_INDEX`` and every migration that
    references it, for a word.

    The index intentionally includes ``anchor_balance`` so two
    legitimate true-ups on the same day -- the user noticed an
    arithmetic error and corrected the balance twice -- are still
    allowed; only literal duplicate rows (same balance, same
    day, same account) are rejected.

    **Its last column was ``((created_at AT TIME ZONE 'UTC')::date)`` until
    ``observed_on`` existed** (finding N-133 / F12).  That keyed the guard to a
    UTC day while the ruling's day is the user's, so two assertions of one
    balance on two different Eastern days that happened to share a UTC day
    (23:00 EDT one evening, 01:00 EDT the next) were rejected as a same-day
    duplicate.  Keying on the stored business date fixes that and still catches
    every double-submit, because a double-click asserts one ``observed_on``.
    It also retires the functional-index machinery: the ``AT TIME ZONE`` pin
    existed only because PostgreSQL refuses a bare ``::date`` cast in an index
    (it depends on the session TimeZone and is therefore not IMMUTABLE), and a
    plain ``DATE`` column needs no pin at all.
    """

    __tablename__ = "account_anchor_history"
    __table_args__ = (
        db.Index(
            "idx_anchor_history_account",
            "account_id",
            "created_at",
        ),
        db.Index(
            "uq_anchor_history_account_period_balance_day",
            "account_id", "anchor_balance", "observed_on",
            unique=True,
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    # The civil day the asserted balance was TRUE, in the user's timezone --
    # the business date the whole anchor/settle partition turns on (ruling
    # R-DH).  NOT NULL because there is no honest assertion without one: a
    # balance that was true on no particular day cannot be compared against the
    # movements it does or does not already contain.  Backfilled from
    # ``(created_at AT TIME ZONE 'America/New_York')::date``, the derivation it
    # replaces, so no rendered figure moved on the day the column shipped.
    observed_on = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)

    # Relationships
    account = db.relationship("Account", back_populates="anchor_history")

    def __repr__(self):
        return f"<AnchorHistory account={self.account_id} balance={self.anchor_balance}>"
