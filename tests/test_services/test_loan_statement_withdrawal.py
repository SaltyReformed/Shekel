"""A withdrawn loan statement is read by NOTHING (plan step ``recurrence:R23``).

Two questions read a loan's stored statements -- the walk (through
:func:`app.services.loan_loaders.load_loan_anchor_facts`) and the write door's
duplicate rule (``loan_anchor_service._governing_loan_anchor``, ruling
**R-EQ**) -- and since this step both read ONE producer,
:func:`app.services.loan_loaders.load_standing_loan_assertions`.  Each case
here grades one reader against a withdrawal, with the same case run with the
statement STANDING as its control, so a producer that ignored the withdrawal
fails and one that dropped the statement for another reason cannot pass for
the right one.

The door's order moved too: it spelled ``(anchor_date, created_at, id) DESC``
in SQL and now takes the LAST match of the producer's ascending order.  The tie
that key exists for -- two statements written in ONE transaction, sharing an
instant -- is graded at the door, since no earlier case put one there.

Figures are made up.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_anchor_withdrawal import LoanAnchorWithdrawal
from app.services import loan_loaders
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.loan_anchor_service import (
    apply_loan_anchor_true_up,
    record_loan_tracking_start,
)
from tests._test_helpers import (
    create_loan_account,
    insert_tracking_start_event,
    insert_trueup_event,
    loan_params_for,
)

_DAY = date(2026, 2, 20)
_LATER = date(2026, 3, 20)


def _loan(db, seed_user):
    """A committed loan originated well before every statement below."""
    return create_loan_account(
        seed_user, db.session, name="Withdrawn Statement Loan",
        principal=Decimal("24000.00"), term=60,
        origination_date=date(2025, 1, 15), payment_day=15,
    )


def _statement(db, loan, balance, day=_DAY):
    """Record one committed ``user_trueup``; returns its id."""
    event = insert_trueup_event(
        loan_params_for(db.session, loan.id), Decimal(balance),
        anchor_date=day,
    )
    db.session.commit()
    return event.id


def _withdraw(db, loan, statement_id):
    """Withdraw one statement and commit."""
    db.session.add(LoanAnchorWithdrawal(
        account_id=loan.id, anchor_event_id=statement_id,
    ))
    db.session.commit()


def _stored_ids(facts):
    """The stored statement ids among *facts* (the origination has id 0)."""
    return [fact.event_id for fact in facts if not fact.is_opening]


class TestTheWalkReadsOnlyStandingStatements:
    """``load_loan_anchor_facts`` and the producer beneath it."""

    def test_a_withdrawn_statement_is_not_a_fact(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Withdraw one of two statements: only the other remains, in order.

        The control is the same loan read BEFORE the withdrawal, which holds
        both -- so the case cannot pass on a loader that drops every stored
        row.
        """
        assert seed_periods_today
        loan = _loan(db, seed_user)
        kept = _statement(db, loan, "18000.00")
        withdrawn = _statement(db, loan, "19500.00", day=_LATER)
        params = loan_params_for(db.session, loan.id)

        assert _stored_ids(loan_loaders.load_loan_anchor_facts(params)) == [
            kept, withdrawn,
        ]

        _withdraw(db, loan, withdrawn)

        assert _stored_ids(loan_loaders.load_loan_anchor_facts(params)) == [
            kept,
        ]
        assert [
            fact.event_id
            for fact in loan_loaders.load_standing_loan_assertions(loan.id)
        ] == [kept]

    def test_the_facts_are_the_origination_plus_the_producer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One producer: the loader adds the origination and nothing else."""
        assert seed_periods_today
        loan = _loan(db, seed_user)
        _statement(db, loan, "18000.00")
        insert_tracking_start_event(
            loan_params_for(db.session, loan.id), Decimal("21000.00"),
            date(2025, 6, 1),
        )
        db.session.commit()
        params = loan_params_for(db.session, loan.id)

        facts = loan_loaders.load_loan_anchor_facts(params)

        assert facts[0] == loan_loaders.synthesize_origination_anchor(params)
        assert facts[1:] == loan_loaders.load_standing_loan_assertions(loan.id)
        assert [fact.is_tracking_start for fact in facts[1:]] == [True, False]


class TestTheDoorComparesOnlyAgainstStandingStatements:
    """Ruling R-EQ's duplicate rule, read through the same producer."""

    def test_a_statement_identical_to_a_WITHDRAWN_one_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The correction a withdrawal makes room for.

        Control first: while the statement stands, re-submitting it is
        ``UNCHANGED`` (nothing appended).  Once withdrawn, the same
        submission is ``COMMITTED`` and a new row exists -- the door no
        longer refuses a statement the walk no longer reads.
        """
        assert seed_periods_today
        loan = _loan(db, seed_user)
        statement = _statement(db, loan, "18000.00")

        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("18000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.UNCHANGED

        _withdraw(db, loan, statement)

        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("18000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.COMMITTED
        rows = (
            db.session.query(LoanAnchorEvent.id)
            .filter_by(account_id=loan.id, anchor_date=_DAY)
            .order_by(LoanAnchorEvent.id)
            .all()
        )
        assert len(rows) == 2 and rows[0].id == statement
        assert _stored_ids(loan_loaders.load_loan_anchor_facts(
            loan_params_for(db.session, loan.id),
        )) == [rows[1].id]

    def test_withdrawing_the_latest_makes_the_earlier_one_govern(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The ``anchor_date <=`` horizon, over standing statements only.

        Two statements, the later one withdrawn: a submission repeating the
        withdrawn one's figure for its day now differs from what governs (the
        earlier statement), so it is recorded; a submission repeating the
        earlier one on ITS day is still ``UNCHANGED``.
        """
        assert seed_periods_today
        loan = _loan(db, seed_user)
        _statement(db, loan, "18000.00")
        later = _statement(db, loan, "17500.00", day=_LATER)
        _withdraw(db, loan, later)

        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("18000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.UNCHANGED
        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("17500.00"),
            anchor_date=_LATER,
        ) is AnchorTrueUpOutcome.COMMITTED

    def test_a_same_instant_tie_governs_by_the_HIGHER_id(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two statements for one day written in ONE transaction.

        In production they share ``created_at`` (``now()`` is the
        transaction's start), so only the key's ``id`` term orders them, and
        the later INSERT governs -- the rule the walk applies.  The suite's
        clock TICKS between statements, so the shared instant is written
        explicitly rather than hoped for.  Re-submitting the later one's
        figure is ``UNCHANGED``; the earlier one's is a change and is recorded.
        """
        assert seed_periods_today
        loan = _loan(db, seed_user)
        instant = datetime(2026, 2, 20, 17, 0, tzinfo=timezone.utc)
        trueup = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        )
        first, second = (
            LoanAnchorEvent(
                account_id=loan.id, anchor_date=_DAY,
                anchor_balance=Decimal(balance), source_id=trueup,
                created_at=instant,
            )
            for balance in ("18000.00", "18100.00")
        )
        db.session.add(first)
        db.session.flush()
        db.session.add(second)
        db.session.commit()
        assert first.created_at == second.created_at and first.id < second.id

        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("18100.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.UNCHANGED
        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("18000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.COMMITTED

    def test_the_comparison_stays_within_its_source(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A standing ``tracking_start`` does not answer for a ``user_trueup``.

        The per-source scope the door now reads off ``is_tracking_start``:
        the same (date, balance) as a tracking-start is ``UNCHANGED``, as a
        true-up it is a distinct fact and is recorded.
        """
        assert seed_periods_today
        loan = _loan(db, seed_user)
        insert_tracking_start_event(
            loan_params_for(db.session, loan.id), Decimal("21000.00"), _DAY,
        )
        db.session.commit()

        assert record_loan_tracking_start(
            account=loan, anchor_balance=Decimal("21000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.UNCHANGED
        assert apply_loan_anchor_true_up(
            account=loan, anchor_balance=Decimal("21000.00"), anchor_date=_DAY,
        ) is AnchorTrueUpOutcome.COMMITTED
