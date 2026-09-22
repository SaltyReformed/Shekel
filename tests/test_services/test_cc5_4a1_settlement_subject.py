"""Plan step ``credit_card:CC-5-4a-1``: the payment MOVEMENT is the matcher's subject.

Ruling **R-CC43** (developer 2026-09-21): *the MOVEMENT is the subject of every
settled match on every screen; a row is a candidate only while Projected.*
A settled row's money is its covering movement (ruling **R-BAL80**), on
whichever account the money moved through -- the card, for a checking bill
paid FROM the card (``CC-5-3``) -- so the movement is what a statement shows,
what the screen offers (a SETTLEMENT, on the row's terms) and what an accepted
act records; a Projected row is offered as itself, and its kept payment (a
reverted row's, ruling **R-CC42**) where the row is not.  Ruling **R-CC46**
(the same day): a payment re-pointed onto another account leaves the acts
naming it, withdrawn and disclosed as a delete's are.

The worked example throughout is the developer's own: GROCERIES / a hotel bill
budgeted on Checking, paid with the card, shown on the card's feed.  Every
figure is asserted from the state re-read through the app's own readers --
the offer set, the accept door, the register, the fold, the ledger, the
claims -- never from the row the test wrote.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.services import (
    bank_agreement,
    match_withdrawal,
    pay_calendar,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import (
    derived_amount_basis,
    settled_cash_facts,
)
from app.services.settle_day import SettleDay
# Pylint: ``shekel-private-module-import`` -- the producers' contracts
# (``_valuation``'s constructor and pricer, ``_candidates``' claims reader)
# are graded DIRECTLY here, where the offer set's own clauses would otherwise
# mask them (measured by mutation, the class ``CC-5-3`` named); and the
# statement-match builders are the one way a test stages a bank line and a
# scope as the app does.  The convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from app.services.statement_match import (
    RowKind,
    _valuation,
    accept_match,
    matched_subjects,
    release_match,
)
from app.services.statement_match._candidates import unmatched_rows
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_expense_template,
    payback_row_of,
    state_template_price,
    typed,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_import,
)

_HOTEL = Decimal("120.00")


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account for *seed_user*.

    Its origination assertion sits on the bootstrap period's first day, as the
    checking account's does, so a payment dated after it is the ledger's own
    posting rather than money the anchor true-up absorbs (see
    :func:`_first_day`).
    """
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )


def _hotel_bill(seed_user, period, *, account=None):
    """One engine-generated `$120.00` Hotel bill on checking (or *account*)."""
    template = make_expense_template(
        db.session, seed_user, amount=str(_HOTEL),
        name="Hotel", category_key="Rent", account=account,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _movement(txn):
    """Return the row's one covering movement, re-read from the database."""
    db.session.expire(txn)
    (movement,) = txn.covering_movements
    return movement


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _correct_tender(txn, account_id):
    """The full-edit popover's identity Save naming another 'Paid from' account."""
    transaction_service.apply_requested_status(
        txn, txn.status_id, tender_account_id=account_id,
    )
    db.session.commit()


def _entered(day):
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


def _first_day(seed_user):
    """The first day a settle on the fixture accounts is NOT absorbed.

    Every fixture account's origination assertion sits on the bootstrap
    period's first day, and the anchor true-up posts a correction against any
    movement dated on or before an assertion's day -- so a settle ON that day
    reads on the fold but nets to nothing on the posted ledger.  One day
    later is the first day the ledger carries it as its own posting.
    """
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _offered_for(seed_user, account, txn):
    """Return what *account*'s screen offers FOR *txn*, under either subject."""
    return [
        row for row in a_scope(seed_user, account).candidates.rows
        if row.transaction_id == txn.id
    ]


def _per_day(account_id, scenario_id):
    """Sum the settled cash facts on *account_id* per settle day."""
    sums = {}
    for fact in settled_cash_facts(account_id, scenario_id):
        sums[fact.settled_on] = sums.get(fact.settled_on, Decimal("0")) + fact.delta
    return sums


def _accept(seed_user, account, lines, transactions):
    """Accept a match on *account*'s screen naming *transactions* as offered."""
    scope = a_scope(seed_user, account)
    accepted = accept_match(
        a_submission(scope, lines=lines, transactions=transactions), scope,
    )
    db.session.commit()
    return accepted


def _members_of(match_id):
    return (
        db.session.query(StatementMatchMember)
        .filter(StatementMatchMember.match_id == match_id)
        .all()
    )


class TestThePaymentIsTheCardScreensCandidate:
    """R-CC40's second half, under R-CC43: the card's screen offers the payment."""

    def test_the_card_screen_offers_the_bill_as_its_payment_and_checking_does_not(
        self, app, seed_user,
    ):
        """One subject for the bill, on the account its money moved through."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            movement = _movement(txn)

            assert _offered_for(seed_user, checking, txn) == []
            (offered,) = _offered_for(seed_user, card, txn)
            assert offered.kind is RowKind.SETTLEMENT
            assert offered.row_id == movement.id
            assert offered.parent_id == txn.id
            assert offered.cash_amount == -_HOTEL
            assert offered.is_settled is True
            assert offered.settled_on == txn.settled_on == movement.settled_on
            assert offered.settle_day_basis is SettledDayBasisEnum.ENTERED
            assert offered.label == f"Hotel (budgeted on {checking.name})"
            # Both homes' counters (the review's L2: a reverted row can move
            # paychecks or be renamed without its kept movement changing).
            assert offered.version_id == movement.version_id + txn.version_id
            assert offered.purchased_on is None
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert offered.period == calendar.period_by_id(txn.pay_period_id)
            # The window is the ROW's: a point at its settle day, as the row's
            # own candidate carried through ``CC-5-3``.
            assert offered.expected_window == (txn.settled_on, txn.settled_on)

    def test_accepting_it_on_the_card_dates_the_bill_by_the_cards_line(
        self, app, seed_user,
    ):
        """The developer's worked case: accept dates the bill; nothing counted twice."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            scenario_id = seed_user["scenario"].id
            card_ledger = linked_ledger_account(db.session, card.id)
            checking_ledger = linked_ledger_account(db.session, checking.id)
            # The two ledgers BEFORE the bill exists: each holds its opening.
            card_before = ledger_net(db.session, card_ledger.id, scenario_id)
            checking_before = ledger_net(db.session, checking_ledger.id, scenario_id)
            settled_day = _first_day(seed_user)
            bank_day = settled_day + timedelta(days=2)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(settled_day), tender_account_id=card.id,
            )
            db.session.commit()
            movement_id = _movement(txn).id
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()

            accepted = _accept(seed_user, card, [line], [txn])

            db.session.expire_all()
            txn = db.session.get(type(txn), txn.id)
            movement = _movement(txn)
            assert accepted.corrected_count == 1 and accepted.settled_count == 0
            assert txn.settled_on == bank_day
            assert txn.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            # The movement FOLLOWED the row: same id, the bank's day, still on
            # the card, no clearing link written by the matcher (ruling R-FV).
            assert movement.id == movement_id
            assert movement.settled_on == bank_day
            assert movement.account_id == card.id
            assert movement.reconciled_by_id is None and txn.reconciled_by_id is None
            # The card's actual equals the bank; checking never moved.
            assert status_seam.covered_cash_leg(txn, card.id) == -_HOTEL
            assert status_seam.covered_cash_leg(txn, checking.id) == Decimal("0")
            assert _per_day(card.id, scenario_id).get(bank_day) == -_HOTEL
            assert settled_day not in _per_day(card.id, scenario_id)
            assert _per_day(checking.id, scenario_id) == {}
            # The posted ledger: the card carries the payment ONCE, checking
            # nothing -- the accept re-dated the one posting, it added none.
            assert ledger_net(
                db.session, card_ledger.id, scenario_id,
            ) == card_before - _HOTEL
            assert ledger_net(
                db.session, checking_ledger.id, scenario_id,
            ) == checking_before

    def test_the_door_settles_the_payments_row_and_not_the_row_sharing_its_id(
        self, app, seed_user,
    ):
        """A SETTLEMENT's ``row_id`` is a MOVEMENT id; the door reads the row through it.

        **The ids are made to diverge first**, because on a fresh fixture the
        first movement and the first row are both ``1`` and a door that
        looked a row up by the movement's id would settle the right row by
        coincidence -- measured: that mutation survived every case in this
        module until this one.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            decoys = [
                a_transaction(seed_user, name=f"Decoy {n}", amount="10.00")
                for n in range(3)
            ]
            db.session.commit()
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            movement = _movement(txn)
            assert movement.id != txn.id
            sharing = db.session.get(type(txn), movement.id)
            assert sharing is not None and sharing.id != txn.id
            assert sharing.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day + timedelta(days=1),
            )
            db.session.commit()

            _accept(seed_user, card, [line], [txn])

            db.session.expire_all()
            assert db.session.get(type(txn), txn.id).settled_on == bank_day + timedelta(days=1)
            sharing = db.session.get(type(txn), movement.id)
            assert sharing.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert sharing.settled_on is None
            assert all(
                db.session.get(type(txn), decoy.id).settled_on is None
                for decoy in decoys
            )
            assert _offered_for(seed_user, checking, txn) == []

    def test_the_member_names_the_movement_on_the_cards_account(
        self, app, seed_user,
    ):
        """What the act records: the movement, held to the card by its key."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=_first_day(seed_user),
            )
            db.session.commit()

            accepted = _accept(seed_user, card, [line], [txn])

            members = _members_of(accepted.match_id)
            assert {m.account_id for m in members} == {card.id}
            assert {m.transaction_id for m in members} == {None}
            assert {m.transaction_entry_id for m in members} == {
                None, _movement(txn).id,
            }
            assert {m.bank_statement_line_id for m in members} == {None, line.id}
            claims = matched_subjects(card.id)
            assert claims.entries == {_movement(txn).id}
            assert claims.transactions == {txn.id}, (
                "a row is claimed through its payment (the claims' two homes)"
            )
            assert claims.lines == {line.id}

    def test_the_register_reads_it_as_the_bill_and_stops_holding_on_a_revert(
        self, app, seed_user,
    ):
        """The accepted list on the card: the bill's name, its figure; a revert reads 0."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            bank_day = _first_day(seed_user)
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            _accept(seed_user, card, [line], [txn])

            (group,) = accepted_acts(seed_user, card)
            (row,) = group.rows
            assert row.label == "Hotel"
            assert row.cash_amount == -_HOTEL
            assert row.settled_on == bank_day
            assert group.agrees is True
            assert group.created_every_row is False

            _revert(txn)

            (group,) = accepted_acts(seed_user, card)
            (row,) = group.rows
            assert row.cash_amount == Decimal("0")
            assert row.settled_on is None
            assert group.agrees is False


class TestAProjectedRowIsOfferedOnceAndItsKeptPaymentWhereTheRowIsNot:
    """The two arms partition on one predicate; the reverted card bill is the case."""

    def test_the_reverted_card_bill_is_offered_on_both_screens_as_two_subjects(
        self, app, seed_user,
    ):
        """Checking offers the Projected row; the card offers its kept payment."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            movement = _movement(txn)
            assert movement.settled_on is None and movement.account_id == card.id

            (on_checking,) = _offered_for(seed_user, checking, txn)
            assert on_checking.kind is RowKind.TRANSACTION
            assert on_checking.row_id == txn.id
            assert on_checking.cash_amount == -_HOTEL
            assert on_checking.is_settled is False

            (on_card,) = _offered_for(seed_user, card, txn)
            assert on_card.kind is RowKind.SETTLEMENT
            assert on_card.row_id == movement.id
            assert on_card.cash_amount == -_HOTEL
            assert on_card.is_settled is False
            assert on_card.settled_on is None
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            period = calendar.period_by_id(txn.pay_period_id)
            assert on_card.expected_window == (period.start_date, period.end_date)

    def test_whichever_screen_accepts_first_wins_and_the_other_is_refused(
        self, app, seed_user,
    ):
        """Two screens, two stale forms: the second act re-prices to nothing."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            card_line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            checking_line = a_bank_line(
                seed_user, an_import(seed_user, checking), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            # Both screens rendered while the row was Projected.
            card_scope = a_scope(seed_user, card)
            checking_scope = a_scope(seed_user, checking)
            on_card = a_submission(card_scope, lines=[card_line], transactions=[txn])
            on_checking = a_submission(
                checking_scope, lines=[checking_line], transactions=[txn],
            )

            accepted = accept_match(on_card, card_scope)
            db.session.commit()

            assert accepted.settled_count == 1
            assert _movement(txn).account_id == card.id
            assert txn.status_id == transaction_service.settled_status_id(txn)
            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(on_checking, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 1
            assert _offered_for(seed_user, checking, txn) == []

    def test_the_reverse_order_settles_it_on_checking_and_refuses_the_card(
        self, app, seed_user,
    ):
        """Checking first: the tender NAMED re-points the kept payment (R-CC15)."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            movement_id = _movement(txn).id
            card_line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            checking_line = a_bank_line(
                seed_user, an_import(seed_user, checking), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            card_scope = a_scope(seed_user, card)
            on_card = a_submission(card_scope, lines=[card_line], transactions=[txn])

            _accept(seed_user, checking, [checking_line], [txn])

            movement = _movement(txn)
            assert movement.id == movement_id and movement.account_id == checking.id
            (member,) = [
                m for m in _members_of(
                    db.session.query(StatementMatch).one().id,
                ) if m.transaction_entry_id is not None
            ]
            assert member.transaction_entry_id == movement_id
            assert member.account_id == checking.id
            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(on_card, a_scope(seed_user, card))
            db.session.rollback()
            assert _offered_for(seed_user, card, txn) == []

    def test_a_kept_payment_is_priced_at_what_its_re_settle_books(
        self, app, seed_user,
    ):
        """The un-dated arm reads the ROW's price, not the movement's stale column."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            movement = _movement(txn)
            assert movement.amount == _HOTEL
            # The definition is re-priced meanwhile; a resolved record
            # re-prices at its re-settle (``test_covering_movement``'s case).
            state_template_price(txn.template, Decimal("160.00"))
            db.session.commit()

            (on_card,) = _offered_for(seed_user, card, txn)

            assert on_card.cash_amount == Decimal("-160.00")
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            assert _valuation.settlement_price(movement, basis) == Decimal("-160.00")
            assert movement.amount == _HOTEL, "the column is the stale figure"

    def test_a_kept_payments_form_is_stale_once_its_row_is_renamed(
        self, app, seed_user,
    ):
        """The row's counter rides the settlement's revision (the review's L2)."""
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, card)
            stale = a_submission(scope, lines=[line], transactions=[txn])
            assert {row.kind for row in stale.rows} == {RowKind.SETTLEMENT}
            movement_version = _movement(txn).version_id

            txn.name = "Hotel (renamed)"
            db.session.commit()

            assert _movement(txn).version_id == movement_version, (
                "the rename touched the row alone; the movement did not move"
            )
            with pytest.raises(ValidationError, match="reviewed against different"):
                accept_match(stale, a_scope(seed_user, card))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0

    def test_a_kept_STATED_payment_is_priced_at_the_honoured_figure(
        self, app, seed_user,
    ):
        """A typed figure survives the revert (R-BAL61) and the offer honours it."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, submitted=typed(Decimal("130.00")),
                settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            state_template_price(txn.template, Decimal("160.00"))
            db.session.commit()

            (on_card,) = _offered_for(seed_user, card, txn)

            assert on_card.cash_amount == Decimal("-130.00")

    def test_a_settled_row_on_its_own_screen_is_offered_as_its_payment_only(
        self, app, seed_user,
    ):
        """The universal subject: on checking too, a settled bill is its payment."""
        with app.app_context():
            checking = seed_user["account"]
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()

            (offered,) = _offered_for(seed_user, checking, txn)

            assert offered.kind is RowKind.SETTLEMENT
            assert offered.row_id == _movement(txn).id
            assert offered.label == "Hotel"
            assert offered.cash_amount == -_HOTEL

    def test_a_projected_row_with_a_kept_payment_on_its_own_screen_is_offered_as_itself_only(
        self, app, seed_user,
    ):
        """The partition's other half: never the row AND its kept payment."""
        with app.app_context():
            checking = seed_user["account"]
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()
            _revert(txn)
            assert _movement(txn).settled_on is None

            (offered,) = _offered_for(seed_user, checking, txn)

            assert offered.kind is RowKind.TRANSACTION
            assert offered.row_id == txn.id
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert _valuation.settlement_candidate(
                _movement(txn), calendar, -_HOTEL, checking.id,
            ) is None, "the constructor declines the kept payment here too"


class TestTheRowsRefusalsApplyToItsPayment:
    """A SETTLEMENT is the row's record, so the row's refusals are its own."""

    def test_a_settled_paybacks_payment_refuses_the_banks_differing_figure(
        self, app, seed_user,
    ):
        """Finding N-252's class on the third kind: no figure of its own to state."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            envelope = a_transaction(
                seed_user, name="Card Spend", amount="300.00", is_envelope=True,
            )
            a_later_period(seed_user)
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("60.00"), bank_day,
            )
            settle_transaction(payback, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-55.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            submission = a_submission(scope, lines=[line], transactions=[payback])
            assert {row.kind for row in submission.rows} == {RowKind.SETTLEMENT}

            with pytest.raises(ValidationError, match="no figure of its own"):
                accept_match(submission, scope)
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0

    def test_a_settled_shadow_legs_payment_refuses_a_correction(
        self, app, seed_user,
    ):
        """Transfer invariant 3: the correction is to the TRANSFER, not this door."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id, settle_day=_entered(bank_day),
            )
            db.session.commit()
            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == checking.id,
                )
                .one()
            )
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-95.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            submission = a_submission(scope, lines=[line], transactions=[shadow])
            (reviewed,) = submission.rows
            assert reviewed.kind is RowKind.SETTLEMENT

            with pytest.raises(ValidationError, match="one half of a transfer"):
                accept_match(submission, scope)
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0


class TestTheClaimsSeeARowThroughItsPayment:
    """What an act already names, read through either home."""

    def test_a_reverted_row_whose_payment_an_act_names_is_not_offered_again(
        self, app, seed_user,
    ):
        """The act shows on the register as no longer holding; the row waits on its Undo."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00", posted_on=bank_day,
            )
            db.session.commit()
            _accept(seed_user, checking, [line], [txn])
            _revert(txn)

            scope = a_scope(seed_user, checking)
            claims = matched_subjects(checking.id)
            assert claims.transactions == {txn.id}
            assert [
                row for row in unmatched_rows(scope.candidates, claims)
                if row.transaction_id == txn.id
            ] == []
            assert [
                row for row in scope.candidates.rows
                if row.transaction_id == txn.id
            ] != [], "the scope still holds it; the claims are what narrow"

    def test_a_row_whose_payment_the_CARDS_act_names_is_claimed_on_checking_too(
        self, app, seed_user,
    ):
        """The claim is the OWNER's, on every screen (the review's M1).

        A reverted card-paid bill whose payment the card's act still names is
        not offered on Checking: read on one account alone, Checking offered
        it, and accepting it there re-pointed the payment and withdrew the
        card's act through a door that discloses nothing.  The card's
        register carries the act as no longer holding, with its Undo; the
        undo is what frees the row for either screen.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            card_line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            checking_line = a_bank_line(
                seed_user, an_import(seed_user, checking), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            accepted = _accept(seed_user, card, [card_line], [txn])
            _revert(txn)

            claims = matched_subjects(checking.id)
            assert txn.id in claims.transactions
            scope = a_scope(seed_user, checking)
            assert [
                row for row in scope.candidates.rows
                if row.transaction_id == txn.id
            ] != [], "the scope holds the Projected row; the claims narrow"
            assert [
                row for row in unmatched_rows(scope.candidates, claims)
                if row.transaction_id == txn.id
            ] == []
            stale = a_submission(scope, lines=[checking_line], transactions=[txn])
            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 1
            assert _movement(txn).account_id == card.id
            (group,) = accepted_acts(seed_user, card)
            assert group.agrees is False

            release_match(accepted.match_id, seed_user["user"].id, card.id)
            db.session.commit()

            claims = matched_subjects(checking.id)
            assert txn.id not in claims.transactions
            scope = a_scope(seed_user, checking)
            (offered,) = [
                row for row in unmatched_rows(scope.candidates, claims)
                if row.transaction_id == txn.id
            ]
            assert offered.kind is RowKind.TRANSACTION

    def test_a_payment_re_pointed_since_the_screen_offered_it_is_refused_not_written(
        self, app, seed_user,
    ):
        """A stale screen cannot name money that moved elsewhere.

        Two guards, and the first is what answers here: the re-price re-asks
        the account (``settlement_candidate`` declines a movement not on the
        screen's account, so the row is "no longer available"), and behind
        it the movement's revision -- bumped by the re-point -- would refuse
        the form as moved.  The constructor's account test is graded
        DIRECTLY in ``test_cc5_3_settle_tender`` because on this path the
        second guard masks it (measured by mutation).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            stale = a_submission(scope, lines=[line], transactions=[txn])
            assert {row.kind for row in stale.rows} == {RowKind.SETTLEMENT}

            _correct_tender(txn, card.id)

            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatchMember).count() == 0


class TestAnActRecordedBeforeThisStepStillClaimsItsRow:
    """The interval's other home: an act naming the ROW, until CC-5-4a-2 re-keys it.

    The app writes no such member any more, so the case writes one as the
    past did.  Measured on the production clone of 2026-09-21 before the
    two-home claim existed: 103 settled rows named by row reappeared on
    Checking's unmatched panel as movements nothing claimed.
    """

    @staticmethod
    def _an_old_shape_act(seed_user, line, txn):
        """Record a match naming *txn* BY ROW, the member shape before R-CC43."""
        match = StatementMatch(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            applied_by_rule=False,
        )
        db.session.add(match)
        db.session.flush()
        db.session.add(StatementMatchMember(
            match_id=match.id, account_id=seed_user["account"].id,
            bank_statement_line_id=line.id,
        ))
        db.session.add(StatementMatchMember(
            match_id=match.id, account_id=seed_user["account"].id,
            transaction_id=txn.id,
        ))
        db.session.commit()
        return match

    def test_its_settlement_is_not_offered_and_the_register_holds(
        self, app, seed_user,
    ):
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00", posted_on=bank_day,
            )
            db.session.commit()
            self._an_old_shape_act(seed_user, line, txn)

            scope = a_scope(seed_user, checking)
            claims = matched_subjects(checking.id)
            (candidate,) = [
                row for row in scope.candidates.rows
                if row.transaction_id == txn.id
            ]
            assert candidate.kind is RowKind.SETTLEMENT
            assert candidate.row_id not in claims.entries
            assert txn.id in claims.transactions
            assert [
                row for row in unmatched_rows(scope.candidates, claims)
                if row.transaction_id == txn.id
            ] == []
            (group,) = accepted_acts(seed_user, checking)
            (row,) = group.rows
            assert row.label == "Hotel" and row.cash_amount == -_HOTEL
            assert group.agrees is True

    def test_a_stale_form_naming_its_settlement_is_refused(
        self, app, seed_user,
    ):
        """The screen rendered before the old act was recorded; the act refuses."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-120.00", posted_on=bank_day,
            )
            other_line = a_bank_line(
                seed_user, statement, amount="-120.00",
                posted_on=bank_day, sequence_in_group=1,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            stale = a_submission(scope, lines=[other_line], transactions=[txn])
            self._an_old_shape_act(seed_user, line, txn)

            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 1


class TestARePointWithdrawsTheMatchNamingThePayment:
    """Ruling **R-CC46**: the correction goes through; the match it falsifies goes."""

    def test_a_paid_from_correction_withdraws_the_match_and_frees_the_line(
        self, app, seed_user,
    ):
        """The disclosure before, the withdrawal at the seam, the line free after."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()
            _accept(seed_user, checking, [line], [txn])
            movement = _movement(txn)

            pending = match_withdrawal.pending_for_moved_movement(movement)
            assert pending.matches == 1
            assert [freed.line_id for freed in pending.lines] == [line.id]
            assert pending.lines[0].description == "GROCERIES"
            assert pending.lines[0].amount == Decimal("-120.00")
            assert db.session.query(StatementMatch).count() == 1

            _correct_tender(txn, card.id)

            assert _movement(txn).account_id == card.id
            assert db.session.query(StatementMatch).count() == 0
            assert db.session.query(StatementMatchMember).count() == 0
            assert matched_subjects(checking.id).lines == set()
            # ...and the payment is the CARD screen's candidate now.
            (on_card,) = _offered_for(seed_user, card, txn)
            assert on_card.kind is RowKind.SETTLEMENT
            assert _offered_for(seed_user, checking, txn) == []

    def test_a_group_act_keeps_standing_without_the_moved_member(
        self, app, seed_user,
    ):
        """The narrowest condition: an act keeping another row stays, flagged."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            hotel = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            other = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="80.00",
                    name="Parking", category_key="Rent",
                ),
                seed_user["bootstrap_period"],
            )
            db.session.commit()
            for row in (hotel, other):
                settle_transaction(row, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-200.00", posted_on=bank_day,
            )
            db.session.commit()
            accepted = _accept(seed_user, checking, [line], [hotel, other])
            assert len(_members_of(accepted.match_id)) == 3

            pending = match_withdrawal.pending_for_moved_movement(_movement(hotel))
            assert pending.matches == 0 and pending.lines == ()

            _correct_tender(hotel, card.id)

            assert db.session.query(StatementMatch).count() == 1
            members = _members_of(accepted.match_id)
            assert {m.transaction_entry_id for m in members} == {
                None, _movement(other).id,
            }
            (group,) = accepted_acts(seed_user, checking)
            assert group.agrees is False
            assert matched_subjects(checking.id).lines == {line.id}

    def test_an_unmatched_payments_re_point_withdraws_nothing(
        self, app, seed_user,
    ):
        """The ordinary case, so the door's cost is a query and a no-op."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()

            pending = match_withdrawal.pending_for_moved_movement(_movement(txn))
            assert pending.matches == 0 and not pending.frees_a_line
            _correct_tender(txn, card.id)

            assert _movement(txn).account_id == card.id


class TestADeletedBillWithdrawsTheActNamingItsPaymentOnAnotherAccount:
    """The withdrawal's scope is the going SUBJECTS, not the going rows' account."""

    def test_deleting_a_checking_bill_matched_on_the_card_withdraws_the_cards_act(
        self, app, seed_user,
    ):
        """An AD-HOC bill (a hard delete; a soft one withdraws nothing by design)."""
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()
            _accept(seed_user, card, [line], [txn])

            pending = match_withdrawal.pending_for_rows([txn])
            assert pending.matches == 1
            assert [freed.line_id for freed in pending.lines] == [line.id]

            outcome = transaction_service.delete_transaction(
                txn, seed_user["user"].id,
            )
            db.session.commit()

            assert outcome.soft is False
            assert outcome.withdrawn.matches == 1
            assert db.session.query(StatementMatch).count() == 0
            assert matched_subjects(card.id).lines == set()


class TestTheBankAgreementReadsAPaymentsClaimThroughTheMovement:
    """The day detail's match state for a covering fact, under both member homes."""

    def test_a_matched_payment_reads_as_matched_on_the_cards_day(
        self, app, seed_user,
    ):
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            before = bank_agreement.day_detail(card, ctx, bank_day)
            assert [(row.amount, row.matched) for row in before.rows] == [
                (-_HOTEL, False),
            ]

            _accept(seed_user, card, [line], [txn])

            after = bank_agreement.day_detail(
                card, BalanceContext.build(seed_user["user"].id), bank_day,
            )
            assert [(row.amount, row.matched) for row in after.rows] == [
                (-_HOTEL, True),
            ]
            assert [ln.matched for ln in after.lines] == [True]
