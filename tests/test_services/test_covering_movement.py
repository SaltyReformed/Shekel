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
* **the income arm** (plan step X-bi-3b): a paycheck is covered in its own
  direction -- the fact, the ledger and the family all read ``+figure`` --
  which is the control the plan names for that leaf;
* **the transfer arm** (plan step X-bi-3c): both legs of a settled transfer
  are covered through ``transfer_service``, each in its own direction; the
  ledger books the pair whole and the movements nowhere (ruling **R-BAL45**),
  the reconcile tick's link reaches the leg's movement, a revert withdraws
  both, and an endpoint move carries them (ruling **R-BAL46**); a loan
  payment's loan-side movement moves no loan figure;
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
    LedgerAccountClassEnum,
    MovementFigureSourceEnum,
    PostingKindEnum,
    SettledDayBasisEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.amount_ownership import AmountOwnership
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    balance_at,
    cash_ledger,
    entry_service,
    posting_service,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.cash_ledger import settled_cash_facts, settled_cash_leg
from app.services.cash_ledger._amounts import _entry_aware_amount
from app.services.entry_service import EntryDetails
from app.services.settle_day import SettleDay
from app.services.status_seam._covering import covering_movements
from app.services.transaction_service._settle import settle_from_entries
from tests._test_helpers import (
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_expense_template,
    make_income_template,
    one_off_row_of,
    planted_basis,
    posted_loan_balance_at,
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


class TestAZeroSettlementWritesNoMovement:
    """Zero movements is a legal count; a movement of nothing is not one."""

    def test_a_bill_budgeted_at_zero_settles_with_no_movement(
        self, app, seed_user, seed_periods,
    ):
        """The reachable case: Mark Paid on a `$0.00` bill.

        ``ck_transaction_entries_positive_amount`` is ``<> 0``, so a mirror
        written at zero was an IntegrityError where yesterday it was a 200
        (adversarial review, 2026-09-16).  R-BAL40's cutover arm, applied to
        the go-forward writer.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            db.session.flush()
            assert txn.status.is_settled
            assert txn.settled_amount == Decimal("0.00")
            assert covering_movements(txn) == []

    def test_a_typed_zero_over_a_covered_bill_withdraws_the_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            status_seam.apply_status_change(
                txn, txn.status_id,
                settlement=status_seam.Settlement.from_settle(
                    Decimal("148.32"), Decimal("0.00"),
                    status_seam.recorded_settlement(txn),
                ),
            )
            db.session.flush()
            assert covering_movements(txn) == []
            assert db.session.get(TransactionEntry, movement_id) is None


class TestTheSourceFollowsWhoStatedTheFigure:
    """A corrected figure on a bank-observed day is the BANK's, not a person's."""

    def test_a_bank_repriced_bill_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The matcher's shape: the line's figure with the line's day.

        The seam mapped ``corrected`` to ``typed`` regardless of the day, so
        a bill the matcher repriced from a bank line carried a figure stamped
        as a person's while the migration's backfill and the purchase doors
        call the same fact ``observed`` (adversarial review, 2026-09-16).
        One rule now: ``settle_day.figure_source_of``.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=Decimal("148.40"),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )


class TestTheMirrorNeverLowersEvidence:
    """An identity re-submit leaves a bank-observed movement standing."""

    def test_a_bank_confirmation_on_the_same_day_raises_the_movement_too(
        self, app, seed_user, seed_periods,
    ):
        """Finding N-332's fix, carried down to the record.

        The panel ticked the bill on an asserted day; a bank line then
        confirms that very day and the row's basis rises to observed.  The
        mirror rose with it only where the DAY moved, so the two homes
        diverged on the reachable path (adversarial review, 2026-09-16).
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            day = seed_periods[0].start_date
            _settle(txn, settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.ASSERTED))
            db.session.flush()
            status_seam.record_clearing(txn, _latest_anchor(txn.account_id).id)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )
            link_before = movement.reconciled_by_id
            assert link_before is not None

            status_seam.apply_status_change(
                txn, txn.status_id,
                settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED),
            )
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == day
            assert same.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert same.reconciled_by_id == link_before

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


def _paycheck(seed_user, period, amount="2572.78"):
    """One engine-generated income row of a fresh definition."""
    template = make_income_template(
        db.session, seed_user, amount=amount, name="Paycheck",
    )
    return generate_row_of(template, period)


class TestAPaycheckIsCoveredInItsOwnDirection:
    """Plan step X-bi-3b: the INCOME arm, and the control that names it.

    A settled paycheck is covered exactly as a bill is, and every reader of
    its movement reads ``+figure``: the walk's fact (``is_income`` and the
    delta both the parent's), the posted ledger's cash net, the ledger legs'
    kind and class, and the family valuation.  The plan's own control is the
    ``+figure`` assertion below: with the seam's income half deleted and the
    direction still spelled ``-amount`` anywhere, the fold reads
    ``-2572.78`` for a ``$2,572.78`` paycheck -- wrong by twice the figure.
    """

    def test_a_paycheck_settles_with_a_movement_reading_PLUS_figure(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            assert txn.status.is_settled
            movement = _only_movement(txn)
            assert movement.amount == Decimal("2572.78")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            # The parent's own leg nets to zero and the movement carries the
            # money IN: the fact says income, and says +figure.
            assert settled_cash_leg(txn) == Decimal("0")
            facts = [
                fact for fact in settled_cash_facts(txn.account_id, txn.scenario_id)
                if fact.entry_id == movement.id
            ]
            assert len(facts) == 1
            assert facts[0].is_income is True
            assert facts[0].delta == Decimal("2572.78")
            assert status_seam.settled_family_leg(txn) == Decimal("2572.78")

    def test_the_folds_per_day_sums_are_identical_with_and_without(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-FM's identity holds for income exactly as for a bill."""
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("2572.78")
            movement = _only_movement(txn)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            assert settled_cash_leg(txn) == Decimal("2572.78")
            assert _per_day(settled_cash_facts(account_id, scenario_id)) == with_movement

    def test_the_posted_ledger_books_the_family_as_INCOME(
        self, app, seed_user, seed_periods,
    ):
        """The cash net rises by the figure; the movement's legs are income.

        The counter leg lands in an INCOME-class category account and both
        legs carry the ``income`` kind -- the parent's, read through the one
        mapping the transaction writer uses (``_posting_write.ledger_class_of``
        / ``posting_kind_of``).  Measured as a DELTA: the fixture's own
        opening correction already sits on the cash ledger.
        """
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            after = ledger_net(db.session, cash.id, txn.scenario_id)
            assert after - before == Decimal("2572.78")
            movement = _only_movement(txn)
            legs = (
                db.session.query(Posting, LedgerAccount)
                .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
                .join(LedgerAccount, LedgerAccount.id == Posting.ledger_account_id)
                .filter(JournalEntry.transaction_entry_id == movement.id)
                .all()
            )
            assert {leg.ledger_account_id: leg.amount for leg, _ in legs} == {
                cash.id: Decimal("2572.78"),
                next(
                    account.id for _, account in legs if account.id != cash.id
                ): Decimal("-2572.78"),
            }
            income_kind = ref_cache.posting_kind_id(PostingKindEnum.INCOME)
            assert {leg.posting_kind_id for leg, _ in legs} == {income_kind}
            counter = next(account for _, account in legs if account.id != cash.id)
            assert counter.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.INCOME,
            )

    def test_a_revert_withdraws_it_and_the_ledger_moves_back(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()
            assert covering_movements(txn) == []
            assert db.session.get(TransactionEntry, movement_id) is None
            assert ledger_net(db.session, cash.id, txn.scenario_id) == before


def _legs_of(xfer):
    """Return a transfer's ``(expense leg, income leg)``, by TYPE."""
    legs = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
    assert len(legs) == 2, "the transfer has no shadow pair"
    expense = next(leg for leg in legs if leg.is_expense)
    income = next(leg for leg in legs if leg is not expense)
    return expense, income


def _settled_pair(seed_user, period, *, to_account=None, amount="500.00"):
    """A settled ``Checking -> to_account`` transfer with its two covered legs.

    *to_account* defaults to a fresh Savings account opened at ``$0.00``.
    Returns ``(transfer, expense leg, income leg)``.
    """
    if to_account is None:
        to_account = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], to_account,
        period, amount=Decimal(amount),
    )
    db.session.flush()
    return (xfer, *_legs_of(xfer))


def _movement_entries(movement_ids):
    """Return the journal entries linked to any of *movement_ids*."""
    return (
        db.session.query(JournalEntry)
        .filter(JournalEntry.transaction_entry_id.in_(movement_ids))
        .all()
    )


class TestATransferIsCoveredOnBothLegs:
    """Plan step X-bi-3c: each leg of a settled transfer holds its movement.

    The transfer settle reaches the seam once per shadow through
    ``transfer_service`` (Invariant 4), so both legs are covered by the ONE
    writer a bill and a paycheck are (ruling **R-BAL41**); each movement moves
    in its own leg's direction.  The posted ledger keeps booking the pair as
    ONE transfer entry and a shadow's movement posts NOWHERE through the
    interval (ruling **R-BAL45**: the ruled endpoint, one entry per movement
    against a transfers-in-transit account, is ``X-bi-6``'s); the walk reads
    each leg as ``0 + movement``, so the fold is identical with and without,
    which is the control below.  Worked on ``$500.00`` Checking -> Savings.
    """

    def test_each_leg_holds_one_movement_in_its_own_direction(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            for leg in (expense, income):
                assert leg.status.is_settled
                movement = _only_movement(leg)
                assert movement.amount == Decimal("500.00")
                assert movement.figure_source_id == _source(
                    MovementFigureSourceEnum.RESOLVED,
                )
                assert movement.description == leg.name
                assert movement.purchased_on == leg.settled_on
                assert movement.settled_on == leg.settled_on
                assert movement.account_id == leg.account_id
                # The leg's own leg nets to zero; the movement carries the money.
                assert settled_cash_leg(leg) == Decimal("0")
            facts = {
                leg.id: [
                    fact for fact in settled_cash_facts(leg.account_id, leg.scenario_id)
                    if fact.entry_id == _only_movement(leg).id
                ]
                for leg in (expense, income)
            }
            assert [fact.delta for fact in facts[expense.id]] == [Decimal("-500.00")]
            assert [fact.is_income for fact in facts[expense.id]] == [False]
            assert [fact.delta for fact in facts[income.id]] == [Decimal("500.00")]
            assert [fact.is_income for fact in facts[income.id]] == [True]

    def test_the_folds_per_day_sums_are_identical_with_and_without_on_both_accounts(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-FM's identity, on the from-account and the to-account."""
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            expected = {expense: Decimal("-500.00"), income: Decimal("500.00")}
            for leg, figure in expected.items():
                account_id, scenario_id = leg.account_id, leg.scenario_id
                with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
                assert with_movement[leg.settled_on] == figure
                movement = _only_movement(leg)
                leg.entries.remove(movement)
                db.session.flush()
                db.session.expire(leg)
                assert settled_cash_leg(leg) == figure
                assert _per_day(settled_cash_facts(account_id, scenario_id)) == with_movement

    def test_the_ledger_books_the_pair_whole_and_the_movements_nowhere(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL45 as the ledger shows it, graded by the oracle.

        The cash nets move by exactly the figure on each account (the ONE
        transfer entry), no journal entry links either movement, the oracle's
        per-account identity holds on both, and the deploy resync -- which
        walks every settled source -- finds nothing to post.  A movement
        posted anywhere would read ``$1,000.00`` against the oracle's
        ``$500.00`` on that account.
        """
        with app.app_context():
            # Opened BEFORE the settle day, so the pair rides on top of the
            # opening instead of being absorbed by an assertion on the same
            # day (the lifecycle suite's stated precondition for this identity).
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
                observed_on=seed_periods[0].start_date,
            )
            accounts = (seed_user["account"], savings)
            scenario_id = seed_user["scenario"].id
            before = {
                account.id: posting_service.account_posting_total(account.id, scenario_id)
                for account in accounts
            }
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            after = {
                account.id: posting_service.account_posting_total(account.id, scenario_id)
                for account in accounts
            }
            assert after[accounts[0].id] - before[accounts[0].id] == Decimal("-500.00")
            assert after[savings.id] - before[savings.id] == Decimal("500.00")
            # ONE entry for the pair, linked by the transfer, and none by a movement.
            pair_entries = (
                db.session.query(JournalEntry).filter_by(transfer_id=xfer.id).all()
            )
            assert len(pair_entries) == 1
            assert _movement_entries(movement_ids) == []
            for account in accounts:
                opening = Decimal(str(cash_ledger.resolve_anchor(account).balance))
                assert posting_service.account_posting_total(account.id, scenario_id) == (
                    opening
                    + posting_service.settled_transfer_effect(account.id, scenario_id)
                    + posting_service.settled_transaction_effect(account.id, scenario_id)
                    + posting_service.posted_purchase_effect(account.id, scenario_id)
                )
            db.session.commit()
            assert posting_service.resync_all_cash_postings() == (0, 0)
            assert _movement_entries(movement_ids) == []

    def test_a_revert_withdraws_both_movements_and_the_ledger_reverses_the_pair(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
                observed_on=seed_periods[0].start_date,
            )
            account_ids = (seed_user["account"].id, savings.id)
            scenario_id = seed_user["scenario"].id
            before = {
                account_id: posting_service.account_posting_total(account_id, scenario_id)
                for account_id in account_ids
            }
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            assert posting_service.account_posting_total(savings.id, scenario_id) != before[savings.id]
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            for leg in (expense, income):
                assert not leg.status.is_settled
                assert covering_movements(leg) == []
            for movement_id in movement_ids:
                assert db.session.get(TransactionEntry, movement_id) is None
            assert {
                account_id: posting_service.account_posting_total(account_id, scenario_id)
                for account_id in account_ids
            } == before

    def test_the_reconcile_ticks_link_reaches_the_leg_and_its_movement_alone(
        self, app, seed_user, seed_periods,
    ):
        """``transfer_service.record_clearing`` links the leg AND its mirror.

        The door delegates to ``status_seam.record_clearing`` since X-bi-3c;
        with the one-column write it had, the movement's fact walked unlinked
        (the gap 3a closed for bills).  Per leg, still: the sibling on the
        other account and its movement take nothing.
        """
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            anchor = _latest_anchor(expense.account_id)
            transfer_service.record_clearing(expense, anchor.id)
            db.session.flush()
            assert expense.reconciled_by_id == anchor.id
            assert _only_movement(expense).reconciled_by_id == anchor.id
            assert income.reconciled_by_id is None
            assert _only_movement(income).reconciled_by_id is None

    def test_a_settle_day_correction_moves_both_movements(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            corrected = expense.settled_on - timedelta(days=3)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                settle_day=SettleDay(day=corrected, basis=SettledDayBasisEnum.ENTERED),
            )
            db.session.flush()
            for leg in (expense, income):
                assert leg.settled_on == corrected
                movement = _only_movement(leg)
                assert movement.settled_on == corrected
                assert movement.purchased_on == corrected

    def test_a_born_settled_transfer_is_covered_on_both_legs(
        self, app, seed_user, seed_periods,
    ):
        """``create_transfer``'s born-settled arm reaches the seam with a record."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                    status_id=ref_cache.status_id(StatusEnum.DONE),
                    category_id=None,
                ),
            )
            db.session.flush()
            expense, income = _legs_of(xfer)
            for leg in (expense, income):
                assert leg.status.is_settled
                movement = _only_movement(leg)
                assert movement.amount == Decimal("500.00")
                assert movement.settled_on == leg.settled_on
            assert settled_cash_leg(expense) == Decimal("0")
            assert status_seam.settled_family_leg(expense) == Decimal("-500.00")
            assert status_seam.settled_family_leg(income) == Decimal("500.00")


class TestATransfersMovementsFollowItsLifecycle:
    """Soft delete, restore, hard delete and the endpoint move (R-BAL46)."""

    def test_a_soft_deleted_transfer_keeps_its_movements_worth_nothing_and_restore_revives_them(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            movement_ids = {leg.id: _only_movement(leg).id for leg in (expense, income)}
            user_id = seed_user["user"].id
            transfer_service.delete_transfer(xfer.id, user_id, soft=True)
            db.session.flush()
            for leg in (expense, income):
                movement = db.session.get(TransactionEntry, movement_ids[leg.id])
                assert movement is not None
                assert cash_ledger.movement_cash_leg(leg, movement) == Decimal("0.00")
                assert settled_cash_facts(leg.account_id, leg.scenario_id) == []
            transfer_service.restore_transfer(xfer.id, user_id)
            db.session.flush()
            for leg, figure in ((expense, Decimal("-500.00")), (income, Decimal("500.00"))):
                assert _only_movement(leg).id == movement_ids[leg.id]
                assert _per_day(settled_cash_facts(leg.account_id, leg.scenario_id)) == {
                    leg.settled_on: figure,
                }

    def test_a_hard_deleted_transfer_takes_its_movements_with_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            db.session.commit()
            transfer_service.delete_transfer(xfer.id, seed_user["user"].id, soft=False)
            db.session.commit()
            for movement_id in movement_ids:
                assert db.session.get(TransactionEntry, movement_id) is None
            assert _movement_entries(movement_ids) == []

    def test_an_endpoint_move_carries_the_movements_to_the_new_account(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL46: the movement's account IS its parent's, on a move too.

        Without migration ``c4e8a2d7f1b3``'s ``ON UPDATE CASCADE`` the move is
        refused by ``fk_transaction_entries_parent_account`` (measured: five
        endpoint-move cases); without the applier's own assignment the
        session's movement still says the old account after the flush.
        Both are read here: the in-session object before any expire, and the
        walks of the vacated and the new account after.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            other = create_savings_account(
                seed_user, db.session, "Other Savings", Decimal("0.00"),
            )
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement = _only_movement(income)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, to_account_id=other.id,
            )
            db.session.flush()
            assert income.account_id == other.id
            assert movement.account_id == other.id
            db.session.expire_all()
            assert db.session.get(TransactionEntry, movement.id).account_id == other.id
            assert _per_day(settled_cash_facts(other.id, income.scenario_id)) == {
                income.settled_on: Decimal("500.00"),
            }
            assert settled_cash_facts(savings.id, income.scenario_id) == []
            assert _only_movement(expense).account_id == seed_user["account"].id


class TestALoanPaymentsLoanSideMovementMovesNoLoanFigure:
    """The loan replay prices a payment by its RECORD, so the movement is inert.

    A payment's income leg sits on the LOAN account and now carries a covering
    movement there; every loan reader values the leg through
    ``row_valuation.settled_contribution`` and never through the family, and
    the ledger's loan entry is unchanged by R-BAL45 -- so the seam's balance
    and the posted balance read identically with and without the movement.
    """

    def test_the_seam_and_the_posted_ledger_read_the_same_with_and_without(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            db.session.flush()
            xfer, _, income = _settled_pair(
                seed_user, seed_periods[0], to_account=loan, amount="1910.95",
            )
            db.session.commit()
            user_id, scenario_id = seed_user["user"].id, seed_user["scenario"].id
            as_of = income.settled_on
            movement = _only_movement(income)
            assert movement.account_id == loan.id

            def _read():
                ctx = balance_at.BalanceContext.build(user_id, as_of=as_of)
                return (
                    balance_at.positions(loan, ctx, [as_of])[as_of],
                    posted_loan_balance_at(loan.id, scenario_id, as_of),
                )

            with_movement = _read()
            income.entries.remove(movement)
            db.session.commit()
            db.session.expire_all()
            assert _read() == with_movement
            assert xfer.status.is_settled


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
            # A bank-born envelope as the bank door mints one since X-bi-7b-3
            # (``statement_match._container._create_envelope`` on the
            # producer): a rule-less definition's placed row, whose flag is
            # the DEFINITION's (ruling R-BAL36) -- which is where the
            # popover's untick lands, below.
            envelope = one_off_row_of(
                seed_periods[0],
                name="Kayla's Spending Money",
                amount=Decimal("100.00"),
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
                is_envelope=True,
            )
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
            envelope.template.is_envelope = False
            db.session.flush()
            assert envelope.tracks_purchases is False
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
