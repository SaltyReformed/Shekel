"""
Shekel Budget App -- Loan Anchor Event Model (budget schema)

Append-only log of dated balance assertions for a loan account.  The
loan resolver (Commit 13 / E-18) replays confirmed payments forward
from the latest event in this log to derive the loan's current
principal, monthly payment, and full amortisation schedule on read.

Two row provenances are recorded via the ``source_id`` FK into
``ref.loan_anchor_sources``:

* ``origination`` -- materialised once per loan from the immutable
  :class:`LoanParams` fields (``origination_date``,
  ``original_principal``) by the Commit 12 backfill migration.  Every
  loan carries exactly one origination event.
* ``user_trueup`` -- appended by the loan dashboard's balance-edit
  flow (Commit 16, decision D-C) whenever the operator asserts a new
  dated balance.  Mirrors the checking-account ``AccountAnchorHistory``
  UX so the mental model is consistent across account types.

This table is **structurally append-only**.  Application code never
updates or deletes a row, mirroring the
:class:`AccountAnchorHistory` design and matching the project's
forensic-immutability stance for financial state changes.  The
in-process guard fires on any ORM-mediated UPDATE or DELETE
(:func:`sqlalchemy.event` listeners below); database-level CASCADE
deletes from ``budget.accounts`` still cascade through the FK action,
since those run outside the ORM and are the documented disposal path
for an entire account's history.

Same-day duplicate prevention follows the
:class:`AccountAnchorHistory` precedent: a functional unique
expression index ``uq_loan_anchor_events_acct_date_bal_day`` covers
``(account_id, anchor_date, anchor_balance,
((created_at AT TIME ZONE 'UTC')::date))`` so that a network retry
or double-click on the Save button cannot create two literal-duplicate
rows on the same calendar day while still permitting two legitimate
trueups on the same day with different balances (the user corrected
their initial typo and re-saved).
"""

from sqlalchemy import event

from app.extensions import db
from app.models.mixins import CreatedAtMixin


class LoanAnchorEvent(CreatedAtMixin, db.Model):
    """Append-only dated balance assertion for a loan account.

    Read by the loan resolver (Commit 13) which selects the most
    recent event per account, treats ``(anchor_date, anchor_balance)``
    as the snap-to point, and replays confirmed shadow-income
    transactions forward from that anchor to produce a current
    balance, monthly payment, and full amortisation schedule.

    The ``source_id`` FK is RESTRICT-on-delete because the seed rows
    in ``ref.loan_anchor_sources`` are non-removable application
    invariants: a successful DELETE would orphan every event tagged
    with that source.

    Storage tier guarantees:

    * ``anchor_balance >= 0`` -- a negative loan balance is
      meaningless; the engine treats overpayment as zero principal,
      and a positive-only domain matches every monetary CHECK in
      the rest of the schema.
    * ``account_id`` CASCADE-on-delete -- deleting a loan account
      removes its anchor history with it.  No orphan-event rows.
    * Unique functional index prevents same-day duplicate inserts
      (see the module docstring for the exact expression).
    """

    __tablename__ = "loan_anchor_events"
    __table_args__ = (
        db.CheckConstraint(
            "anchor_balance >= 0",
            name="ck_loan_anchor_events_balance_nonneg",
        ),
        # Forward-scan index for the resolver's "latest anchor per
        # account" lookup; the unique index below already covers
        # ``(account_id, anchor_date, ...)`` but its postgres-text
        # expression term keeps it from doubling as a clean range
        # scan over (account_id, anchor_date).  This secondary
        # ascending index serves the ORDER BY pattern.
        db.Index(
            "idx_loan_anchor_events_account",
            "account_id", "anchor_date",
        ),
        # Same-day duplicate prevention; mirrors
        # ``uq_anchor_history_account_period_balance_day`` on
        # :class:`AccountAnchorHistory`.  Truncating ``created_at``
        # (timestamptz) to a civil date requires pinning the timezone
        # via ``AT TIME ZONE 'UTC'`` because PostgreSQL refuses to use
        # the bare ``::date`` cast in an index -- the cast depends on
        # the session's TimeZone and is not IMMUTABLE.  UTC matches
        # ``CreatedAtMixin``'s storage convention exactly.
        db.Index(
            "uq_loan_anchor_events_acct_date_bal_day",
            "account_id", "anchor_date", "anchor_balance",
            db.text("((created_at AT TIME ZONE 'UTC')::date)"),
            unique=True,
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_date = db.Column(db.Date, nullable=False)
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.loan_anchor_sources.id",
            name="fk_loan_anchor_events_source_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # Relationships -- read-only consumers (resolver, dashboard).
    account = db.relationship(
        "Account",
        backref=db.backref(
            "loan_anchor_events",
            order_by="LoanAnchorEvent.anchor_date.desc(), "
                    "LoanAnchorEvent.created_at.desc()",
            cascade="all, delete-orphan",
            passive_deletes=True,
            lazy="select",
        ),
    )
    source = db.relationship("LoanAnchorSource", lazy="joined")

    def __repr__(self):
        return (
            f"<LoanAnchorEvent account_id={self.account_id} "
            f"date={self.anchor_date} balance={self.anchor_balance}>"
        )


class LoanAnchorEventImmutableError(RuntimeError):
    """Raised when ORM code attempts to UPDATE or DELETE a LoanAnchorEvent.

    The table is structurally append-only (decision D-A): a
    correction is expressed as a NEW row, never as an edit of an
    existing one.  Mirrors the audit philosophy applied to
    ``system.audit_log`` and matches the forensic invariant that
    backfilled origination rows must be reconstructible at any point
    in the future from the same immutable LoanParams source.

    Database-level CASCADE deletes from ``budget.accounts`` are NOT
    intercepted -- they happen outside the SQLAlchemy ORM session
    and are the documented disposal path for an entire account's
    history.  Direct SQL UPDATE/DELETE statements are similarly
    unaffected by this guard; the in-process guard exists to catch
    programmer errors at the call site, not to enforce the invariant
    against malicious database actors (the audit-log trigger
    captures the row anyway).
    """


@event.listens_for(LoanAnchorEvent, "before_update")
def _block_update(_mapper, _connection, target):
    """Refuse every ORM-mediated UPDATE on a LoanAnchorEvent.

    Fires before SQLAlchemy emits the UPDATE so the offending session
    rolls back cleanly with a named exception the test suite can
    assert against.  Any field correction must be expressed as a new
    row inserted via the trueup flow (Commit 16); this guard is the
    programmatic enforcement of that contract.
    """
    raise LoanAnchorEventImmutableError(
        f"LoanAnchorEvent is append-only; UPDATE rejected for id={target.id!r}."
    )


@event.listens_for(LoanAnchorEvent, "before_delete")
def _block_delete(_mapper, _connection, target):
    """Refuse every ORM-mediated DELETE on a LoanAnchorEvent.

    Same rationale as :func:`_block_update`: the table is
    structurally append-only.  CASCADE deletes from
    ``budget.accounts`` flow through the database FK action and do
    NOT load each event into the ORM session, so this guard does
    not interfere with the normal account-deletion path.
    """
    raise LoanAnchorEventImmutableError(
        f"LoanAnchorEvent is append-only; DELETE rejected for id={target.id!r}."
    )
