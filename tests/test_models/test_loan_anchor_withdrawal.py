"""A loan statement's WITHDRAWAL: its keys and its refusals (plan step ``recurrence:R23``).

``budget.loan_anchor_withdrawals`` takes one loan statement out of the loan's
standing assertions without editing it (ruling **R-R98**).  Every rule the
relation states about itself is graded here with a control SHOWN to fire, and
the two disposal paths the append-only refusal must PERMIT -- a withdrawal
going with its statement, and with its account -- are graded as positive
controls, because a refusal that also refused the cascade would make a
statement's or an account's disposal impossible.

Every case that reaches the DEFERRED delete arm COMMITS: a deferred constraint
trigger fires at commit and not before, so a case that asserted on the flush
alone would grade nothing.  Figures are made up.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_anchor_withdrawal import (
    LoanAnchorWithdrawal,
    LoanAnchorWithdrawalImmutableError,
)
from tests._test_helpers import (
    append_only_guard_lifted,
    constraint_name_from,
    create_account_of_type,
    create_loan_account,
    insert_trueup_event,
    loan_params_for,
)


def _loan(db, owner, name="Withdrawal Loan"):
    """A committed loan for *owner*, originated well before its statements."""
    return create_loan_account(
        owner, db.session, name=name, principal=Decimal("24000.00"),
        term=60, origination_date=date(2025, 1, 15), payment_day=15,
    )


def _statement(db, loan, *, balance="18000.00", day=date(2026, 2, 20)):
    """Stage one ``user_trueup`` on *loan*; returns it flushed."""
    event = insert_trueup_event(
        loan_params_for(db.session, loan.id), Decimal(balance),
        anchor_date=day,
    )
    db.session.flush()
    return event


def _withdraw(db, statement, *, account_id=None):
    """Stage one withdrawal of *statement*; returns it flushed."""
    withdrawal = LoanAnchorWithdrawal(
        account_id=account_id or statement.account_id,
        anchor_event_id=statement.id,
    )
    db.session.add(withdrawal)
    db.session.flush()
    return withdrawal


def _refused_by(db, exc_info) -> str:
    """Return the constraint the flush named, rolling the session back."""
    db.session.rollback()
    return constraint_name_from(exc_info.value)


class TestAWithdrawal:
    """One withdrawal per statement, keyed to the statement's own account."""

    def test_a_statement_is_withdrawn_AT_MOST_ONCE(self, app, db, seed_user):
        """``uq_loan_anchor_withdrawals_event``."""
        statement = _statement(db, _loan(db, seed_user))
        _withdraw(db, statement)

        with pytest.raises(IntegrityError) as raised:
            _withdraw(db, statement)

        assert _refused_by(db, raised) == "uq_loan_anchor_withdrawals_event"

    def test_a_withdrawal_naming_ANOTHER_accounts_statement_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """``fk_loan_anchor_withdrawals_event_account``: the composite key.

        The withdrawal carries the FIRST owner's account and names the SECOND
        owner's statement; the account key alone would admit it, since both
        ids exist.
        """
        mine = _loan(db, seed_user, name="Mine")
        theirs = _statement(db, _loan(db, seed_second_user, name="Theirs"))

        with pytest.raises(IntegrityError) as raised:
            _withdraw(db, theirs, account_id=mine.id)

        assert (
            _refused_by(db, raised) == "fk_loan_anchor_withdrawals_event_account"
        )

    def test_the_superkey_it_targets_rejects_no_statement(
        self, app, db, seed_user,
    ):
        """``uq_loan_anchor_events_account_id`` is a key, not a content rule.

        Two statements identical in every value but ``id`` both stand -- the
        property ruling R-EQ protects, which a key over the row's values would
        have broken and a key over ``(account_id, id)`` cannot.
        """
        loan = _loan(db, seed_user)
        _statement(db, loan)
        _statement(db, loan)
        db.session.commit()

        assert db.session.query(LoanAnchorEvent).filter_by(
            account_id=loan.id, anchor_date=date(2026, 2, 20),
        ).count() == 2


class TestTheAppendOnlyRefusal:
    """What the shared trigger refuses on the relation and what it PERMITS."""

    def test_a_withdrawal_may_NOT_be_picked_off_while_its_statement_stands(
        self, app, db, seed_user,
    ):
        """The DELETE arm: an un-withdrawal would reinstate the statement."""
        withdrawal = _withdraw(db, _statement(db, _loan(db, seed_user)))
        db.session.commit()

        db.session.execute(text(
            "DELETE FROM budget.loan_anchor_withdrawals WHERE id = :id"
        ), {"id": withdrawal.id})
        with pytest.raises(InternalError, match="append-only; DELETE rejected"):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(LoanAnchorWithdrawal, withdrawal.id) is not None

    def test_a_raw_UPDATE_is_refused(self, app, db, seed_user):
        """The UPDATE arm, with no admitted transition on this relation.

        Asserts the row EXISTS first: a row trigger fires per row, so an
        UPDATE matching nothing would raise nothing and read as a pass.
        """
        loan = _loan(db, seed_user)
        withdrawal = _withdraw(db, _statement(db, loan))
        other = _statement(db, loan, balance="17500.00")
        db.session.commit()
        assert db.session.get(LoanAnchorWithdrawal, withdrawal.id) is not None

        with pytest.raises(InternalError, match="append-only; UPDATE rejected"):
            db.session.execute(text(
                "UPDATE budget.loan_anchor_withdrawals "
                "SET anchor_event_id = :other WHERE id = :id"
            ), {"other": other.id, "id": withdrawal.id})
        db.session.rollback()

    def test_TRUNCATE_is_refused(self, app, db, seed_user):
        """The statement arm, the one spelling no row trigger sees."""
        with pytest.raises(InternalError, match="TRUNCATE rejected"):
            db.session.execute(text("TRUNCATE budget.loan_anchor_withdrawals"))
        db.session.rollback()

    def test_the_ORM_names_the_refusal(self, app, db, seed_user):
        """The listener pair: a named Shekel exception at the call site."""
        loan = _loan(db, seed_user)
        withdrawal = _withdraw(db, _statement(db, loan))
        other = _statement(db, loan, balance="17500.00")
        db.session.commit()
        withdrawal_id, other_id = withdrawal.id, other.id

        withdrawal = db.session.get(LoanAnchorWithdrawal, withdrawal_id)
        withdrawal.anchor_event_id = other_id
        with pytest.raises(
            LoanAnchorWithdrawalImmutableError, match="UPDATE rejected",
        ):
            db.session.flush()
        db.session.rollback()

        withdrawal = db.session.get(LoanAnchorWithdrawal, withdrawal_id)
        db.session.delete(withdrawal)
        with pytest.raises(
            LoanAnchorWithdrawalImmutableError, match="DELETE rejected",
        ):
            db.session.flush()
        db.session.rollback()

    def test_a_withdrawal_goes_WITH_its_statement(self, app, db, seed_user):
        """Positive control for the relation's OWN owner arm.

        The account STANDS, so the account test that ends the delete arm would
        refuse this withdrawal's cascade; only the branch asking whether its
        statement is gone admits it.  The statement's own guard is lifted to
        reach it -- a statement is otherwise deleted only with its account --
        and the withdrawal's is not, so the withdrawal's arm is what is graded.
        """
        loan = _loan(db, seed_user)
        statement = _statement(db, loan)
        withdrawal = _withdraw(db, statement)
        db.session.commit()
        statement_id, withdrawal_id = statement.id, withdrawal.id

        with append_only_guard_lifted(db.session, "budget.loan_anchor_events"):
            db.session.execute(text(
                "DELETE FROM budget.loan_anchor_events WHERE id = :id"
            ), {"id": statement_id})
        db.session.commit()

        assert db.session.get(LoanAnchorWithdrawal, withdrawal_id) is None
        assert db.session.get(Account, loan.id) is not None

    def test_an_account_takes_its_statements_and_withdrawals_with_it(
        self, app, db, seed_user,
    ):
        """Positive control: the account disposal still passes.

        The account's cascade takes its statements and their withdrawals, and
        the withdrawal's deferred arm admits it at COMMIT.  Asserts the ROWS
        are gone rather than that nothing raised, since a cascade that
        silently did nothing passes the weaker claim.

        Built without the loan's posting ledger, as
        ``test_loan_anchor_event``'s cascade case is: a configured loan's
        chart row keys onto the account with ``RESTRICT``, which refuses a raw
        account delete for a reason unrelated to this relation.  The
        statement is staged directly for the same reason -- these rows are
        what the cascade is graded on, and no balance is read.
        """
        account = create_account_of_type(
            seed_user, db.session, "Auto Loan", "Disposable Loan",
            anchor_balance=Decimal("100.00"),
        )
        statement = LoanAnchorEvent(
            account_id=account.id,
            anchor_date=date(2026, 2, 20),
            anchor_balance=Decimal("18000.00"),
            source_id=ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            ),
        )
        db.session.add(statement)
        db.session.flush()
        withdrawal = _withdraw(db, statement)
        db.session.commit()
        ids = (account.id, statement.id, withdrawal.id)
        db.session.expunge_all()

        db.session.execute(text(
            "DELETE FROM budget.accounts WHERE id = :id"
        ), {"id": ids[0]})
        db.session.commit()

        assert db.session.get(Account, ids[0]) is None
        assert db.session.get(LoanAnchorEvent, ids[1]) is None
        assert db.session.get(LoanAnchorWithdrawal, ids[2]) is None

    def test_every_withdrawal_is_audited(self, app, db, seed_user):
        """``audit_loan_anchor_withdrawals`` records the INSERT, every column."""
        withdrawal = _withdraw(db, _statement(db, _loan(db, seed_user)))
        db.session.commit()

        rows = db.session.execute(text(
            "SELECT operation, new_data->>'anchor_event_id' "
            "FROM system.audit_log WHERE table_name = 'loan_anchor_withdrawals' "
            "AND row_id = :id"
        ), {"id": withdrawal.id}).all()

        assert rows == [("INSERT", str(withdrawal.anchor_event_id))]
