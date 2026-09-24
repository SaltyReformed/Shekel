"""
Shekel Budget App -- Loan Anchor Withdrawal Model (budget schema)

A WITHDRAWAL takes one loan statement out of the loan's standing assertions
without editing it (plan step ``recurrence:R23``, ruling **R-R98**).

**What it is for.**  A loan statement
(:class:`~app.models.loan_anchor_event.LoanAnchorEvent`, a ``user_trueup`` or a
``tracking_start``) says what the loan owed at the close of one day, and the
walk RESETS the running balance there.  A statement recorded under a day it
does not belong to is therefore wrong for every payment between its true day
and the day it names: each one sits inside a statement that did not see it.
Migration ``d3d25212504b`` wrote exactly that row -- it copied the balance an
owner had stated at SETUP into a ``user_trueup`` dated the day the migration
RAN -- and plan step ``recurrence:R23`` records the balance under its setup
day and withdraws the copy (revision ``cddb15ffba5f``).

**Why it is its own relation and not a row in the statement table.**  The
statement table is append-only at the database tier (ruling **R-HY**), so a
wrong statement cannot be edited or deleted, and an append-only log of
OBSERVATIONS cannot un-observe: a later statement only resets the balance from
ITS day on, never before it.  A withdrawal is a different kind of fact, so it
gets a relation of its own -- the shape the bank level's
:class:`~app.models.anchor_release.AnchorRelease` already has (rulings
**R-IS**, **R-JN**) -- and the statement table keeps every row an observation
with an amount.

**A statement STANDS when no withdrawal names it.**
``uq_loan_anchor_withdrawals_event`` makes "withdrawn at most once" structural,
the composite key onto ``uq_loan_anchor_events_account_id`` keeps a withdrawal
from naming another account's statement, and every reader of a loan's
statements reads the ONE producer that applies the rule,
:func:`app.services.loan_loaders.load_standing_loan_assertions` -- the walk and
the write door's duplicate rule alike, so a withdrawn statement neither resets
a balance nor refuses a new identical one as a duplicate.

**No cause column, because nothing reads one.**  The release carries its cause
because the statements page's badge names it; no surface names why a loan
statement was withdrawn.  ``system.audit_log`` records the INSERT, and the
revision that writes the withdrawals states its cause.

**Append-only.**  The shared trigger ``budget.refuse_append_only_change``
(:mod:`app.append_only_infrastructure`) refuses every UPDATE, refuses DELETE
while the withdrawn statement still stands (a withdrawal goes only with its
statement, as a statement goes only with its account), and refuses TRUNCATE
outright; the ORM listener pair
:func:`~app.models.append_only.install_append_only_guards` installs is the
named half of the same refusal.  ``system.audit_log`` records every row.
"""

from app.extensions import db
from app.models.append_only import (
    AppendOnlyViolation,
    install_append_only_guards,
)
from app.models.mixins import AccountScopedMixin, CreatedAtMixin


class LoanAnchorWithdrawalImmutableError(AppendOnlyViolation):
    """Raised when ORM code tries to UPDATE or DELETE a loan statement's withdrawal.

    The fifth member of the append-only family
    (:class:`~app.models.anchor_release.AnchorReleaseImmutableError` is its
    bank twin).  A withdrawal is the record that a statement no longer
    stands; editing or deleting one would let the statement silently reset
    the loan's balance again.
    """


class LoanAnchorWithdrawal(AccountScopedMixin, CreatedAtMixin, db.Model):
    """One withdrawal of one loan statement.

    See the module docstring for what a withdrawal is and why it is its own
    relation.

    Attributes:
        anchor_event_id: The statement withdrawn -- a
            ``budget.loan_anchor_events`` row.  Composite with ``account_id``
            onto ``uq_loan_anchor_events_account_id``, so a withdrawal cannot
            name another account's statement.  CASCADE: a withdrawal goes with
            its statement.
    """

    __tablename__ = "loan_anchor_withdrawals"
    __table_args__ = (
        # Withdrawn at most once.  The readers ask whether ANY withdrawal names
        # a statement, so a second one would change nothing they answer; the
        # key makes it a state no writer can produce either.
        db.UniqueConstraint(
            "anchor_event_id", name="uq_loan_anchor_withdrawals_event",
        ),
        db.ForeignKeyConstraint(
            ["account_id", "anchor_event_id"],
            ["budget.loan_anchor_events.account_id",
             "budget.loan_anchor_events.id"],
            name="fk_loan_anchor_withdrawals_event_account",
            ondelete="CASCADE",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    anchor_event_id = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return (
            f"<LoanAnchorWithdrawal account_id={self.account_id} "
            f"anchor_event={self.anchor_event_id}>"
        )


install_append_only_guards(
    LoanAnchorWithdrawal, LoanAnchorWithdrawalImmutableError,
)
