"""A settle writes the movement it records, and the movement follows the record.

Plan step **balance:X-bi-3a**, rulings **R-BAL39** and **R-BAL41**.  A bill
ticked Paid used to record what moved on the bill row alone; an envelope's
purchases were each a row of money that moved.  Since this leaf the MANUAL
branch's settlement is mirrored as ONE covering movement -- a
``transaction_entries`` row carrying the figure the settle booked, WHO wrote
it, the day and how the day is known, and the statement link -- written by the
status seam, the one writer of the settlement record.

**Every case here drives a DOOR** -- ``transaction_service.settle_transaction``
and ``status_seam.apply_status_change`` -- rather than assigning columns,
because the claim under test is what those doors do.  And every case is a
FIRING CONTROL (``docs/plans/verification.md`` standard 4): the balance
equality below is graded against the SAME fold with the movement removed, the
release cases assert the ledger's net moved back, and the gates assert that a
row of the excluded kind holds NO movement.

The shapes under test, and the real act each stands for:

* **the write** -- a bill's tick books its figure as a movement sourced
  ``resolved``, a typed correction as ``typed``, named after the plan, dated
  on the settle day, on the owner's account;
* **balance-neutral by construction** (ruling **R-FM**'s identity): the fold's
  per-day sums and the posted ledger's cash net are identical with and
  without the movement, because the parent's leg nets to zero and the
  movement carries the money;
* **the lifecycle mirrors the RECORD**: a revert deletes the movement (its
  postings reversed) while the row retains what moved; a re-settle rebuilds
  it from the retained record -- ``typed`` for a honoured correction,
  ``resolved`` for a re-priced derivation; an envelope closed empty and later
  given real purchases sums the purchases alone;
* **the mirror never lowers evidence**: an identity re-submit leaves a
  movement's bank-observed day and link standing; a settle-day correction
  moves the movement's day with the row's;
* **the 3b / 3c gates**: an income parent and a transfer shadow hold no
  movement yet, stated so those leaves have an arm to delete;
* **the purchase doors' source rule**: a hand-typed purchase is ``typed``, a
  bank-born one ``observed``, a human amount edit ``typed``, a bank
  confirmation ``observed``, a day-only edit unchanged.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, status_seam, transaction_service
from app.services.cash_ledger import settled_cash_facts, settled_cash_leg
from app.services.cash_ledger._amounts import _entry_aware_amount
from app.services.entry_service import EntryDetails
from app.services.settle_day import SettleDay
from app.services.status_seam._covering import covering_movements
from app.services.transaction_service._settle import settle_from_entries
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_expense_template,
    make_income_template,
    planted_basis,
)


def _source(member):
    """The stored id of a ``MovementFigureSourceEnum`` member."""
    return ref_cache.movement_figure_source_id(member)


def _bill(seed_user, period, amount="148.32", *, is_envelope=False):
    """One engine-generated expense row of a fresh definition."""
    template = make_expense_template(
        db.session, seed_user, amount=amount,
        name="Electric", category_key="Rent", is_envelope=is_envelope,
    )
    return generate_row_of(template, period)


def _settle(txn, *, submitted=None, settle_day=None):
    """Settle *txn* through the verb."""
    return transaction_service.settle_transaction(
        txn, submitted=submitted, settle_day=settle_day,
    )


def _revert(txn):
    """Put *txn* back to Projected through the ONE status door."""
    status_seam.apply_status_change(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _only_movement(txn):
    """Return the row's one covering movement, asserting there is exactly one."""
    movements = covering_movements(txn)
    assert len(movements) == 1, f"expected one covering movement, got {len(movements)}"
    return movements[0]


def _per_day(facts):
    """Sum the facts' deltas per settle day."""
    sums = defaultdict(Decimal)
    for fact in facts:
        sums[fact.settled_on] += fact.delta
    return dict(sums)


def _latest_anchor(account_id):
    """The account's latest balance assertion, which the fixtures seed."""
    anchor = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )
    assert anchor is not None, "the fixture account carries no anchor"
    return anchor


class TestASettleWritesTheMovementItRecords:
    """The write, column by column."""

    def test_a_ticked_bill_holds_one_resolved_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.32")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert movement.description == txn.name
            assert movement.purchased_on == txn.settled_on
            assert movement.settled_on == txn.settled_on
            assert movement.settled_day_basis_id == txn.settled_day_basis_id
            assert movement.reconciled_by_id is None
            assert movement.account_id == txn.account_id
            assert movement.user_id == txn.user_id
            assert movement.is_credit is False
            # The row's own record still stands through the interval.
            assert txn.settled_amount == Decimal("148.32")
            assert txn.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.DERIVED,
            )

    def test_a_typed_correction_is_a_typed_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=Decimal("150.00"))
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.amount == Decimal("150.00")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.TYPED,
            )

    def test_a_callers_settle_day_is_the_movements_day(
        self, app, seed_user, seed_periods,
    ):
        """The reconcile panel's shape: an asserted day, stated by the caller."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            day = SettleDay(
                day=seed_periods[0].start_date,
                basis=SettledDayBasisEnum.ASSERTED,
            )
            _settle(txn, settle_day=day)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.settled_on == seed_periods[0].start_date
            assert movement.purchased_on == seed_periods[0].start_date
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )


class TestTheMovementMovesNoBalance:
    """Ruling R-FM's identity, graded against the same fold without the row."""

    def test_the_folds_per_day_sums_are_identical_with_and_without(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id

            # The parent's own leg nets to zero and the movement carries it.
            assert settled_cash_leg(txn) == Decimal("0")
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("-148.32")

            # The CONTROL: delete the movement around the seam, and the fold
            # books the parent's whole leg on the same day instead.
            movement = _only_movement(txn)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            assert settled_cash_leg(txn) == Decimal("-148.32")
            without = _per_day(settled_cash_facts(account_id, scenario_id))
            assert without == with_movement

    def test_the_posted_ledgers_cash_net_moves_by_the_figure(
        self, app, seed_user, seed_periods,
    ):
        """The verb's ledger reconcile posts the family in one pass.

        Measured as a DELTA: the fixture's own opening correction already
        sits on the same cash ledger.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            after = ledger_net(db.session, cash.id, txn.scenario_id)
            assert after - before == Decimal("-148.32")


class TestARevertDeletesAndAReSettleRebuilds:
    """The movement mirrors the row's record: withdrawn with the band, rebuilt from it."""

    def test_a_revert_deletes_a_resolved_movement_and_reverses_its_legs(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            assert ledger_net(db.session, cash.id, txn.scenario_id) - before == Decimal("-148.32")

            _revert(txn)
            db.session.flush()

            assert covering_movements(txn) == []
            assert db.session.get(TransactionEntry, movement_id) is None
            # The movement's own legs were reversed BEFORE the row went; the
            # parent's zero leg had nothing to reverse.  Net: back to before.
            assert ledger_net(db.session, cash.id, txn.scenario_id) == before

    def test_a_revert_deletes_a_typed_movement_and_the_row_retains_the_figure(
        self, app, seed_user, seed_periods,
    ):
        """The movement goes; what moved stays where X-au-c3 keeps it."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=Decimal("150.00"))
            db.session.flush()
            movement_id = _only_movement(txn).id

            _revert(txn)
            db.session.flush()

            assert covering_movements(txn) == []
            assert db.session.get(TransactionEntry, movement_id) is None
            assert txn.settled_amount == Decimal("150.00")
            assert txn.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )

    def test_a_reverted_bill_is_worth_its_estimate_as_before(
        self, app, seed_user, seed_periods,
    ):
        """The projection reads the plan again: no movement, no reservation split."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=Decimal("150.00"))
            db.session.flush()
            _revert(txn)
            db.session.flush()
            db.session.expire(txn)
            assert _entry_aware_amount(txn, planted_basis(txn)) == Decimal("148.32")

    def test_a_re_settle_rebuilds_a_typed_movement_from_the_retained_record(
        self, app, seed_user, seed_periods,
    ):
        """Revert, re-settle with nothing typed: the honoured figure, mirrored again."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=Decimal("150.00"))
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()

            booked_a_human_figure = _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id != first_id
            assert movement.amount == Decimal("150.00")
            assert movement.figure_source_id == _source(MovementFigureSourceEnum.TYPED)
            assert movement.settled_on == txn.settled_on
            assert movement.settled_on is not None
            assert booked_a_human_figure is False

    def test_a_re_settle_after_a_resolved_revert_writes_a_fresh_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()

            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id != first_id
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )

    def test_an_envelope_closed_empty_then_given_purchases_sums_the_purchases(
        self, app, seed_user, seed_periods,
    ):
        """The stale-close control the delete arm exists for."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            close = _only_movement(envelope)
            assert close.amount == Decimal("100.00")
            assert close.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )

            _revert(envelope)
            db.session.flush()
            assert list(envelope.entries) == []

            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("60.00"), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            _settle(envelope)
            db.session.flush()

            assert envelope.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.PURCHASES,
            )
            assert sum(e.amount for e in envelope.entries) == Decimal("60.00")
            assert covering_movements(envelope) == []

    def test_an_envelope_with_purchases_holds_no_covering_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("60.00"), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            settle_from_entries(envelope)
            db.session.flush()
            assert len(envelope.entries) == 1
            assert covering_movements(envelope) == []


class TestTheMirrorNeverLowersEvidence:
    """An identity re-submit leaves a bank-observed movement standing."""

    def test_an_identity_re_submit_keeps_the_movements_observed_day_and_link(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            # The matcher's write: the bank observed the same day, and a
            # statement showed the MOVEMENT (the row's leg is zero, so the row
            # is never offered).
            movement.settled_day_basis_id = ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            movement.reconciled_by_id = _latest_anchor(txn.account_id).id
            db.session.flush()

            status_seam.apply_status_change(txn, txn.status_id)
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == txn.settled_on
            assert same.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert same.reconciled_by_id is not None

    def test_a_settle_day_correction_moves_the_movements_day_and_releases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            movement.reconciled_by_id = _latest_anchor(txn.account_id).id
            db.session.flush()
            moved_to = txn.settled_on - timedelta(days=1)

            status_seam.apply_status_change(
                txn, txn.status_id,
                settle_day=SettleDay(day=moved_to, basis=SettledDayBasisEnum.ENTERED),
            )
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == moved_to
            assert same.purchased_on == moved_to
            assert same.reconciled_by_id is None


class TestTheGatesHoldForTheLeavesToCome:
    """3b and 3c: an income parent and a transfer shadow hold no movement."""

    def test_an_income_row_settles_with_no_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            template = make_income_template(
                db.session, seed_user, amount="2572.78", name="Paycheck",
            )
            txn = generate_row_of(template, seed_periods[0])
            _settle(txn)
            db.session.flush()
            assert txn.status.is_settled
            assert list(txn.entries) == []

    def test_a_transfer_settles_with_no_movement_on_either_leg(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.flush()
            legs = (
                db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            )
            assert len(legs) == 2, "the transfer has no shadow pair"
            for leg in legs:
                assert leg.status.is_settled
                assert list(leg.entries) == []


class TestThePurchaseDoorsStateTheSource:
    """Who wrote the figure, by the door that wrote it."""

    def test_a_hand_typed_purchase_is_typed(self, app, seed_user, seed_periods):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("12.79"), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_purchase_born_from_a_bank_line_is_observed(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("12.79"), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)

    def test_a_human_amount_edit_makes_a_bank_figure_typed(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("12.79"), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id, amount=Decimal("12.97"),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_bank_confirmation_raises_a_typed_figure_to_observed(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("12.79"), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)

    def test_a_day_only_edit_on_the_owners_word_leaves_the_source_alone(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("12.79"), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date + timedelta(days=1),
                    basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)


class TestTheRecordIsMarkedAndTheSeamsAlone:
    """``covers_settlement``: found by the mark, held to one, closed to the doors."""

    def test_a_second_record_on_one_row_is_refused_by_the_index(
        self, app, seed_user, seed_periods,
    ):
        """The partial unique index, driven."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.covers_settlement is True
            db.session.add(TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=seed_user["user"].id, amount=Decimal("1.00"),
                description="second record", purchased_on=txn.settled_on,
                covers_settlement=True,
                figure_source_id=_source(MovementFigureSourceEnum.TYPED),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "uq_transaction_entries_one_settlement_record" in str(exc.value)
            db.session.rollback()

    def test_the_entry_doors_refuse_the_record(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            record = _only_movement(envelope)
            with pytest.raises(ValidationError, match="payment record"):
                entry_service.update_entry(
                    record.id, seed_user["user"].id,
                    settle_day=SettleDay(
                        day=envelope.settled_on - timedelta(days=1),
                        basis=SettledDayBasisEnum.ENTERED,
                    ),
                )
            with pytest.raises(ValidationError, match="payment record"):
                entry_service.delete_entry(record.id, seed_user["user"].id)
            assert db.session.get(TransactionEntry, record.id) is not None

    def test_a_stored_figure_row_holding_a_real_purchase_keeps_it(
        self, app, seed_user, seed_periods,
    ):
        """The door path that refutes 'a stored-figure row holds no purchases'.

        An envelope with a bank-born purchase is settled, its *Track
        individual purchases* is unticked on the settled row, and a figure is
        typed over it: a ``corrected`` record beside a real purchase.  The
        seam must write its OWN movement beside that purchase, never
        overwrite the purchase as its mirror (``test_release``'s
        container-beyond-the-door case is the same path, graded there for
        the undo's refusal).
        """
        with app.app_context():
            # An AD-HOC envelope, bare-built as the bank door still mints one
            # (``statement_match._container._create_envelope``, until
            # X-bi-7b-3): the flag is the row's own there, which is what the
            # popover's untick writes -- a definition's row reads its
            # definition's flag and offers no such control.
            envelope = Transaction(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Kayla's Spending Money",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                is_envelope=True,
            )
            db.session.add(envelope)
            db.session.flush()
            purchase = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    amount=Decimal("57.96"), description="Walmart",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            _settle(envelope)
            db.session.flush()
            assert covering_movements(envelope) == []
            envelope.is_envelope = False
            db.session.flush()
            transaction_service.apply_requested_status(
                envelope, envelope.status_id, submitted=Decimal("999.99"),
            )
            db.session.flush()

            assert envelope.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            record = _only_movement(envelope)
            assert record.id != purchase.id
            assert record.amount == Decimal("999.99")
            kept = db.session.get(TransactionEntry, purchase.id)
            assert kept.amount == Decimal("57.96")
            assert kept.description == "Walmart"
            assert kept.covers_settlement is False


class TestTheCatalogueIsSeededAndResolvable:
    """Three members, each a row, none absent."""

    @pytest.mark.parametrize("member", list(MovementFigureSourceEnum))
    def test_each_member_resolves(self, app, member):
        with app.app_context():
            assert isinstance(_source(member), int)

    def test_a_movement_stating_no_source_is_refused_at_flush(
        self, app, seed_user, seed_periods,
    ):
        """NOT NULL with no default: the constraint, driven."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            db.session.add(TransactionEntry(
                transaction_id=envelope.id, account_id=envelope.account_id,
                user_id=seed_user["user"].id, amount=Decimal("5.00"),
                description="bare", purchased_on=date(2026, 1, 5),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "figure_source_id" in str(exc.value)
            db.session.rollback()
