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
* **the lifecycle mirrors the RECORD** (plan step X-bi-3e-2, ruling
  **R-BAL61**): a revert UN-DATES the movement and keeps it -- its day pair
  and link released with the row's, the door's family reconcile reversing
  its legs -- while both homes retain what moved; a re-settle re-dates the
  SAME row from the retained record -- ``typed`` for a honoured correction,
  the new price for a re-priced derivation; an envelope closed empty,
  reverted and later given real purchases sums the purchases alone, and the
  ``purchases`` record withdraws the survivor;
* **a kept movement is not a purchase** (ruling **R-BAL68**): every
  purchase-meaning reader -- the fold's reservation, the reconcile panel's
  offer and stamp, carry-forward's leftover, the grid's sums and list, the
  dashboard's progress, the settle's own offer and booking, the statement
  bound -- reads ``Transaction.purchases`` and sees nothing of a reverted
  manual close; the family readers keep ``entries``;
* **the mirror never lowers evidence**: an identity re-submit leaves a
  movement's bank-observed day and link standing; a settle-day correction
  moves the movement's day with the row's;
* **the income arm** (plan step X-bi-3b): a paycheck is covered in its own
  direction -- the fact, the ledger and the family all read ``+figure`` --
  which is the control the plan names for that leaf;
* **the transfer arm** (plan step X-bi-3c): both legs of a settled transfer
  are covered through ``transfer_service``, each in its own direction; the
  ledger books the pair whole and the movements nowhere (ruling **R-BAL45**),
  the reconcile tick's link reaches the leg's movement, a revert un-dates
  both, and an endpoint move carries them, settled or not (rulings
  **R-BAL46**, **R-BAL72**); a loan payment's loan-side movement moves no
  loan figure;
* **the purchase doors' source rule**: a hand-typed purchase is ``typed``, a
  bank-born one ``observed``, a human amount edit ``typed``, a bank
  confirmation ``observed``, a day-only edit unchanged.
"""

from collections import defaultdict
from dataclasses import replace
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
    carry_forward_service,
    cash_ledger,
    entry_service,
    posting_service,
    reconcile_service,
    row_valuation,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import settled_cash_facts
from app.services.cash_ledger._amounts import (
    _entry_aware_amount,
    _entry_checking_impact,
)
from app.services.cash_ledger._amount_source import resolve_transaction_amount
from app.services.dashboard_service._bills import _entry_progress_fields
from app.services.dashboard_service._pulse import _row_still_due
from app.services.entry_service import EntryDetails
from app.services.entry_service._sums import (
    build_entry_lists_dict,
    build_entry_sums_dict,
)
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.reconcile_service._rows import wholly_spent_by
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.transaction_service._row_rules import settles_from_entries
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
    observed,
    one_off_row_of,
    planted_basis,
    posted_loan_balance_at,
    state_template_price,
    transit_ledger_account,
    typed,
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
    """Put *txn* back to Projected through the door production reverts by.

    ``transaction_service.apply_requested_status`` is what every revert of a
    transaction reaches (the popover's PATCH and the cancel route; the
    matcher never reverts) -- the seam plus the family reconcile that
    reverses the survivor's legs.  The seam alone is driven where the claim
    is the seam's (:class:`TestTheBareSeamUnDatesAndKeeps`).
    """
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _only_movement(txn):
    """Return the row's one covering movement, asserting there is exactly one."""
    movements = txn.covering_movements
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
            # The movement IS the row's record (plan step X-bi-4b-2).
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("148.32"), MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_typed_correction_is_a_typed_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
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


class TestTheMovementIsTheBalance:
    """The fold reads the movement and nothing of the row (ruling R-BAL80).

    Through plan step ``X-bi-3e`` this class graded ruling R-FM's identity --
    the fold's per-day sums were IDENTICAL with and without the movement,
    because the row's own leg booked whatever the movement did not.  Since
    ``balance:X-bi-4a`` the row's leg is not a fact, and under ruling
    **R-BAL81** it is not a price either: the row is WORTH what its covering
    movement moves (``status_seam.covered_cash_leg``, the matcher's one
    valuation of a settled row).  Delete the movement around the seam and
    the fold reads NOTHING for the row and the matcher prices it at
    nothing -- the same answer from both readers, stated by the movement.
    The inverse of the identity, and the control that would fail if a
    row-leg fact came back.  (Through X-bi-4a's first cut the control
    asserted the deleted ``settled_cash_leg`` still priced the whole bill
    without its movement -- the row-leg reader R-BAL81 deleted.)
    """

    def test_the_fold_reads_the_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id

            # The row is worth its covering movement (R-BAL81) and the fold
            # reads that movement.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-148.32")
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("-148.32")

            # The CONTROL: delete the movement around the seam.  The row is
            # worth nothing to the matcher and the fold reads none of it.
            movement = _only_movement(txn)
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).
            db.session.delete(movement)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0")
            without = _per_day(settled_cash_facts(account_id, scenario_id))
            assert txn.settled_on not in without
            assert without == {}

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


class TestARevertUnDatesAndAReSettleReDates:
    """The movement mirrors the row's record: un-dated with the band, re-dated from it.

    Plan step **X-bi-3e-2**, ruling **R-BAL61**: a revert releases the row's
    assertion and KEEPS what moved, and the movement -- the record's mirror
    and the only home of who wrote the figure -- follows the record rather
    than the band.  Every revert here goes through the door production uses
    (``transaction_service.apply_requested_status``), which runs the family
    reconcile after the seam; the seam's own half is graded alone in
    :class:`TestTheBareSeamUnDatesAndKeeps`.
    """

    def test_a_revert_un_dates_a_resolved_movement_and_the_door_reverses_its_legs(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            movement_id = movement.id
            assert movement.reconciled_by_id is None
            assert ledger_net(db.session, cash.id, txn.scenario_id) - before == Decimal("-148.32")

            _revert(txn)
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert db.session.get(TransactionEntry, movement_id) is survivor
            assert survivor.settled_on is None
            assert survivor.settled_day_basis_id is None
            assert survivor.reconciled_by_id is None
            assert survivor.amount == Decimal("148.32")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.RESOLVED)
            assert txn.purchases == []
            # The door's family reconcile reversed the un-dated movement's
            # legs (it posts nothing without a day); the parent's zero leg had
            # nothing to reverse.  Net: back to before.
            assert ledger_net(db.session, cash.id, txn.scenario_id) == before

    def test_a_revert_un_dates_a_typed_movement_and_both_homes_retain_the_figure(
        self, app, seed_user, seed_periods,
    ):
        """The movement stays; what moved stays where X-au-c3 keeps it, on both."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            movement_id = _only_movement(txn).id

            _revert(txn)
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.amount == Decimal("150.00")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.TYPED)
            # The retained read takes the figure and the source off the survivor.
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("150.00")
            assert retained.source is MovementFigureSourceEnum.TYPED

    def test_a_revert_releases_the_movements_clearing_link_with_the_rows(
        self, app, seed_user, seed_periods,
    ):
        """A link says a statement showed money that moved; un-dated, it cannot stand."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            anchor = _latest_anchor(txn.account_id)
            status_seam.record_clearing(txn, anchor.id)
            db.session.flush()
            assert _only_movement(txn).reconciled_by_id == anchor.id

            _revert(txn)
            db.session.flush()

            assert txn.reconciled_by_id is None
            assert _only_movement(txn).reconciled_by_id is None

    def test_a_reverted_bill_is_worth_its_estimate_as_before(
        self, app, seed_user, seed_periods,
    ):
        """The projection reads the plan again: the survivor is no purchase, no split."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            _revert(txn)
            db.session.flush()
            db.session.expire(txn)
            assert len(txn.entries) == 1, "the survivor is on the row"
            assert _entry_aware_amount(txn, planted_basis(txn)) == Decimal("148.32")

    def test_a_re_settle_re_dates_the_same_typed_movement_from_the_retained_record(
        self, app, seed_user, seed_periods,
    ):
        """Revert, re-settle with nothing typed: the honoured figure, on the same row."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()

            booked_a_human_figure = _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("150.00")
            assert movement.figure_source_id == _source(MovementFigureSourceEnum.TYPED)
            assert movement.settled_on == txn.settled_on
            assert movement.settled_on is not None
            assert movement.purchased_on == txn.settled_on
            assert movement.settled_day_basis_id == txn.settled_day_basis_id
            assert booked_a_human_figure is False

    def test_a_re_settle_keeps_the_kept_movement_where_the_money_moved(
        self, app, seed_user, seed_periods,
    ):
        """Revert, move the row's account, re-settle: the record stays where it was.

        Plan step ``credit_card:CC-5-3``, ruling **R-CC42** (developer
        2026-09-21), which AMENDS ruling **R-CC36**'s re-point clause -- and
        this test, which pinned that clause as
        ``test_a_re_settle_re_points_the_kept_movement_onto_the_rows_account``
        under CC-5-2, is RE-EXPRESSED under CLAUDE.md rule 5 with the
        developer's confirmation, his option text as picked: *"Retained across
        a revert (Recommended)": "From scratch: where the money moved is part
        of the record and survives a revert exactly as a stated figure does.
        A re-settle that names no account keeps the kept payment's account;
        naming one re-points it; every door reads the record: the popover's
        'Paid from' shows the kept account selected, and the one-click
        checkmark and mobile Mark Paid pass nothing, so the seam keeps it.
        One default, spelled once in the seam. AMENDS R-CC36's re-point
        clause: a Projected row whose definition moved keeps its kept record
        where the money moved, and only a named tender moves it (that test is
        re-expressed to say so)."*

        The maintain pass no longer retains a row holding a kept payment
        record when its definition's account moves (R-CC36's clause that
        STANDS), so the row can sit on Second Checking while its un-dated
        record still names Checking.  Under R-CC42 the same columns cannot
        tell that row from a bill the owner charged to the card and reverted
        to edit, and the one-click re-settle of the latter must not move its
        money back onto checking -- so a re-settle naming no tender keeps the
        SAME movement (its id survives, as ``X-bi-3e-2`` pinned) on Checking,
        and the fold reads `-148.32` there and nothing on Second Checking;
        a re-settle NAMING Second Checking is what moves it.  The row's
        account is moved directly here: the maintain pass is the door that
        moves it in production, and its own suite pins that it now does.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            first_id = _only_movement(txn).id
            old_account_id, scenario_id = txn.account_id, txn.scenario_id
            _revert(txn)
            db.session.flush()
            kept = _only_movement(txn)
            assert kept.settled_on is None
            assert kept.account_id == old_account_id

            moved_to = create_savings_account(
                seed_user, db.session, "Second Checking", Decimal("0.00"),
            )
            db.session.flush()
            txn.account_id = moved_to.id
            db.session.flush()

            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.account_id == old_account_id
            assert movement.settled_on == txn.settled_on
            on_the_new = _per_day(settled_cash_facts(moved_to.id, scenario_id))
            on_the_old = _per_day(settled_cash_facts(old_account_id, scenario_id))
            assert on_the_old[txn.settled_on] == Decimal("-148.32")
            assert txn.settled_on not in on_the_new

            # Only a NAMED tender moves it: revert and re-settle naming the
            # row's new account, and the same movement books there.
            _revert(txn)
            db.session.flush()
            transaction_service.settle_transaction(
                txn, tender_account_id=moved_to.id,
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.account_id == moved_to.id
            on_the_new = _per_day(settled_cash_facts(moved_to.id, scenario_id))
            on_the_old = _per_day(settled_cash_facts(old_account_id, scenario_id))
            assert on_the_new[txn.settled_on] == Decimal("-148.32")
            assert txn.settled_on not in on_the_old

    def test_a_re_settle_after_a_resolved_revert_re_prices_the_same_movement(
        self, app, seed_user, seed_periods,
    ):
        """The plan moved meanwhile: the survivor takes the new price, keeps its id."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()
            # The definition is re-priced; a derived row is worth its plan.
            state_template_price(txn.template, Decimal("160.00"))
            db.session.flush()

            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("160.00")
            assert row_valuation.settled_figure(txn) == Decimal("160.00")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert movement.settled_on == txn.settled_on

    def test_a_re_settle_on_a_later_day_moves_the_survivors_purchase_day(
        self, app, seed_user, seed_periods,
    ):
        """A covering movement's purchase day IS its settle day (R-BAL39), re-dated too."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            first_day = seed_periods[0].start_date
            _settle(txn, settle_day=SettleDay(
                day=first_day, basis=SettledDayBasisEnum.ENTERED,
            ))
            db.session.flush()
            assert _only_movement(txn).purchased_on == first_day
            _revert(txn)
            db.session.flush()
            assert _only_movement(txn).purchased_on == first_day, (
                "un-dated, the survivor keeps the day of the close it recorded"
            )

            later = first_day + timedelta(days=3)
            _settle(txn, settle_day=SettleDay(
                day=later, basis=SettledDayBasisEnum.ENTERED,
            ))
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.purchased_on == later
            assert movement.settled_on == later

    def test_an_envelope_closed_empty_then_given_purchases_sums_the_purchases(
        self, app, seed_user, seed_periods,
    ):
        """The stale-close control: the survivor is no purchase, and the purchases record withdraws it."""
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
            assert [entry.id for entry in envelope.entries] == [close.id]
            assert envelope.purchases == []
            assert settles_from_entries(envelope) is False

            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            assert [entry.amount for entry in envelope.purchases] == [Decimal("60.00")]
            assert settles_from_entries(envelope) is True
            _settle(envelope)
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert sum(e.amount for e in envelope.entries) == Decimal("60.00")
            assert envelope.covering_movements == []
            assert db.session.get(TransactionEntry, close.id) is None

    def test_an_envelope_with_purchases_holds_no_covering_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            settle_from_entries(envelope)
            db.session.flush()
            assert len(envelope.entries) == 1
            assert envelope.covering_movements == []


class TestTheBareSeamUnDatesAndKeeps:
    """What the SEAM alone guarantees on a revert, apart from any door.

    ``status_seam.apply_status_change`` un-dates the movement and keeps it;
    the ledger is the door's (``_covering``'s module docstring), so this
    class asserts nothing about postings -- ``test_integrity_check``'s DC-10
    case is where the state a caller of the bare seam would leave is graded.
    """

    def test_the_seam_alone_un_dates_the_movement_and_keeps_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            movement_id = _only_movement(txn).id

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.settled_day_basis_id is None
            assert survivor.reconciled_by_id is None
            assert survivor.amount == Decimal("150.00")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.TYPED)


class TestARecordThatMovesMarksItsRow:
    """``_record_moved``: the row's counter follows its record, and only a change.

    Plan step ``balance:X-bi-4b-2``.  The settled figure lives on the
    covering movement alone, so a figure correction writes this table and
    nothing of the row; the row's optimistic-lock counter is what a stale
    popover is caught by (``version_id``), and through ``X-bi-4b-1`` the
    seam's write of the row's own figure columns moved it for free.  The
    covering writer now marks the ROW modified whenever its record nets a
    change -- and not otherwise, because a mark that moved for an identical
    re-record would turn every second tab into a spurious 409.  Graded at
    the seam, on the session's own dirty state, with the SECOND identical
    write as its own case: a producer right the first time can be wrong on
    the repeat.  The route-tier controls are the two
    ``test_a_stale_tab_cannot_overwrite_a_figure_correction`` cases
    (``test_full_edit_settle_door``, ``test_transfers``).
    """

    @staticmethod
    def _re_record(txn, amount):
        """Hand the seam an identity re-settle stating *amount* as typed."""
        status_seam.apply_status_change(
            txn, txn.status_id,
            settlement=status_seam.Settlement(
                Decimal(amount), MovementFigureSourceEnum.TYPED,
            ),
        )

    def test_a_re_record_that_changes_the_figure_marks_the_row(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "150.00")

            assert db.session.is_modified(txn), (
                "the record moved and the row stayed clean"
            )
            db.session.flush()
            assert txn.version_id == version + 1
            assert row_valuation.settled_figure(txn) == Decimal("150.00")

    def test_an_identical_second_re_record_marks_nothing(
        self, app, seed_user, seed_periods,
    ):
        """The second time: the same figure and source again nets no change."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            self._re_record(txn, "150.00")
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "150.00")

            assert not db.session.is_modified(txn), (
                "an identical re-record marked the row"
            )
            db.session.flush()
            assert txn.version_id == version

    def test_a_withdrawal_marks_the_row(self, app, seed_user, seed_periods):
        """A typed ``$0.00`` over a covered bill deletes the movement: a change."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "0.00")

            assert db.session.is_modified(txn)
            db.session.flush()
            assert txn.version_id == version + 1
            assert txn.covering_movements == []


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
            assert row_valuation.settled_figure(txn) == Decimal("0.00")
            assert txn.covering_movements == []

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
                    Decimal("148.32"), typed(Decimal("0.00")),
                    status_seam.recorded_settlement(txn),
                ),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert db.session.get(TransactionEntry, movement_id) is None


class TestTheSourceFollowsWhoStatedTheFigure:
    """A corrected figure is labelled by WHO STATED it, never by the day.

    Ruling **R-BAL61** (plan step X-bi-3e-1): the seam used to infer the
    writer from the day's basis beside the figure -- an ``observed`` day made
    the figure the bank's -- and the premise was measured false on every row
    kind.  The writer states it now, as one value with the figure
    (:class:`~app.services.stated_figure.StatedFigure`, ruling **R-BAL69**).
    """

    def test_a_bank_repriced_bill_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The matcher's shape: the line's figure, stated as the bank's.

        The seam mapped ``corrected`` to ``typed`` regardless of the day, so
        a bill the matcher repriced from a bank line carried a figure stamped
        as a person's (adversarial review, 2026-09-16); then it inferred
        ``observed`` from the day.  The matcher STATES it now.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=observed(Decimal("148.40")),
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

    def test_a_figure_typed_over_a_bank_observed_day_is_typed(
        self, app, seed_user, seed_periods,
    ):
        """BAL-508's class on a bill: the person's figure beside the bank's day.

        The reconcile-then-correct shape ruling R-BAL61 measured (5 settled
        rows on the 2026-09-18 restore): the matcher confirmed the DAY, and
        a person later typed the figure through the popover.  The old
        inference (``_source_of`` over the day's basis) labelled that figure
        the bank's; delete this leaf's change and this fails.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            assert _only_movement(txn).figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            transaction_service.apply_requested_status(
                txn, txn.status_id, submitted=typed(Decimal("148.40")),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            ), "the bank's day still stands; only the figure's writer changed"
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.TYPED,
            )

    def test_the_matchers_transaction_arm_reprice_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """Through the ONE door the matcher's transaction arm reaches.

        ``statement_match._moving._apply_day`` re-prices a settled bill by an
        identity transition through ``apply_requested_status`` with the
        bank's figure stated ``observed``; the record it makes is the
        bank's on the movement and ``corrected`` on the row.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
                submitted=observed(Decimal("148.40")),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.OBSERVED
            ), "the retained read takes the source off the movement"


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
            # The movement carries the money IN: the fact says income, and
            # says +figure; the row is worth that movement (R-BAL81).
            facts = [
                fact for fact in settled_cash_facts(txn.account_id, txn.scenario_id)
                if fact.entry_id == movement.id
            ]
            assert len(facts) == 1
            assert facts[0].is_income is True
            assert facts[0].delta == Decimal("2572.78")
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("2572.78")

    def test_the_fold_reads_the_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        """The movement IS the fold for income exactly as for a bill (R-BAL80)."""
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("2572.78")
            movement = _only_movement(txn)
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).
            db.session.delete(movement)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            # Worth nothing without its movement (R-BAL81), as the fold reads.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0")
            assert _per_day(settled_cash_facts(account_id, scenario_id)) == {}

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

    def test_a_revert_un_dates_it_and_the_ledger_moves_back(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            assert ledger_net(db.session, cash.id, txn.scenario_id) - before == Decimal("2572.78")
            _revert(txn)
            db.session.flush()
            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.amount == Decimal("2572.78")
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
    each leg's movement and nothing of the leg (ruling **R-BAL80**), and
    each leg is worth its movement (ruling **R-BAL81**), so the fold reads
    nothing for a leg without one -- the control below.  Worked on
    ``$500.00`` Checking -> Savings.
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
                # The leg is worth what its movement moves (R-BAL81).
                assert status_seam.covered_cash_leg(leg, leg.account_id) == (
                    cash_ledger.movement_cash_leg(leg, movement)
                )
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

    def test_the_fold_reads_each_legs_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        """The movement IS the fold on the from-account and the to-account (R-BAL80)."""
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            expected = {expense: Decimal("-500.00"), income: Decimal("500.00")}
            for leg, figure in expected.items():
                account_id, scenario_id = leg.account_id, leg.scenario_id
                with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
                assert with_movement[leg.settled_on] == figure
                movement = _only_movement(leg)
                # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
                # rule-5 re-expression, developer-confirmed 2026-09-23).
                db.session.delete(movement)
                leg.entries.remove(movement)
                db.session.flush()
                db.session.expire(leg)
                assert status_seam.covered_cash_leg(leg, leg.account_id) == Decimal("0")
                assert _per_day(settled_cash_facts(account_id, scenario_id)) == {}

    def test_the_ledger_books_each_movement_against_transit_and_nothing_whole(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL45's ENDPOINT as the ledger shows it, graded by the oracle.

        Re-expressed from ``test_the_ledger_books_the_pair_whole_and_the_
        movements_nowhere`` at plan step ``balance:X-bi-6-3`` (ruling
        **R-BAL101**), which pinned the interval that step closes.  The cash
        nets move by exactly the figure on each account, each MOVEMENT links
        its own entry (two, against the owner's Transfers-in-transit
        account), NO entry links the transfer whole, the oracle's per-account
        identity holds on both, transit nets to zero, and the deploy resync --
        which walks every settled source -- finds nothing to post.  A
        movement posted beside a whole-pair entry would read ``$1,000.00``
        against the oracle's ``$500.00`` on that account.
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
            # ONE entry per MOVEMENT, and none linked by the transfer whole.
            pair_entries = (
                db.session.query(JournalEntry).filter_by(transfer_id=xfer.id).all()
            )
            assert pair_entries == []
            assert len(_movement_entries(movement_ids)) == 2
            transit = transit_ledger_account(db.session, seed_user["user"].id)
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), 0)
            ).filter(Posting.ledger_account_id == transit.id).scalar() == 0
            for account in accounts:
                opening = Decimal(str(cash_ledger.resolve_anchor(account).balance))
                assert posting_service.account_posting_total(account.id, scenario_id) == (
                    opening
                    + posting_service.settled_transfer_effect(account.id, scenario_id)
                    + posting_service.posted_purchase_effect(account.id, scenario_id)
                )
            db.session.commit()
            assert posting_service.resync_all_cash_postings() == (0, 0)
            assert len(_movement_entries(movement_ids)) == 2

    def test_a_revert_un_dates_both_movements_and_the_ledger_reverses_the_pair(
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
            for leg, movement_id in zip((expense, income), movement_ids):
                assert not leg.status.is_settled
                survivor = _only_movement(leg)
                assert survivor.id == movement_id
                assert survivor.settled_on is None
                assert survivor.reconciled_by_id is None
                assert survivor.amount == Decimal("500.00")
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
            assert status_seam.covered_cash_leg(expense, expense.account_id) == Decimal("-500.00")
            assert status_seam.covered_cash_leg(income, income.account_id) == Decimal("500.00")


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

    def test_a_reverted_then_soft_deleted_then_restored_transfer_keeps_both_survivors(
        self, app, seed_user, seed_periods,
    ):
        """The restore's identity pass moves nothing: same ids, un-dated, same labels."""
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            user_id = seed_user["user"].id
            transfer_service.update_transfer(
                xfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            movement_ids = {leg.id: _only_movement(leg).id for leg in (expense, income)}
            transfer_service.delete_transfer(xfer.id, user_id, soft=True)
            db.session.flush()
            transfer_service.restore_transfer(xfer.id, user_id)
            db.session.flush()
            for leg in (expense, income):
                assert not leg.status.is_settled
                survivor = _only_movement(leg)
                assert survivor.id == movement_ids[leg.id]
                assert survivor.settled_on is None
                assert survivor.reconciled_by_id is None
                assert survivor.amount == Decimal("500.00")
                assert survivor.figure_source_id == _source(
                    MovementFigureSourceEnum.RESOLVED,
                )

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
        """Ruling R-BAL46: a leg's movement moves with its leg, by the applier.

        Since plan step ``credit_card:CC-5-1`` the applier's own assignment is
        the ONE writer of the move (``_endpoints._apply_endpoint_move``): the
        composite key whose ``ON UPDATE CASCADE`` used to move the row beneath
        it, ``fk_transaction_entries_parent_account``, is dropped (ruling
        R-BAL76), so a movement's account is its own and only the applier
        re-points a leg's.  Both the session and the database are read here:
        the in-session object before any expire, the row after, and the walks
        of the vacated and the new account.  Delete the assignment and the
        movement stays on the vacated account in all three.
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


class TestALoanPaymentsLoanSideMovementIsItsRecord:
    """The loan replay prices a payment by its RECORD, and the record is the movement.

    A payment's income leg sits on the LOAN account and carries a covering
    movement there; every loan reader values the leg through
    ``row_valuation.settled_contribution``, which since plan step
    ``balance:X-bi-4b-1`` sums the leg's entries (ruling **R-BAL80**) -- so
    the movement is the whole of what the loan side reads of the payment.
    Through ``X-bi-4a`` the reader took the leg's own ``settled_amount`` and
    this class pinned the movement as INERT to it ("the seam's balance and
    the posted balance read identically with and without the movement");
    that was the interval ruling R-BAL40 accepted, and the pin is inverted
    with it: taking the movement away (a state no door writes) now reads the
    payment as nothing moved, and moves the loan's position with it.  The
    posted ledger's loan entry is still R-BAL45's, untouched by either.
    """

    def test_the_loan_side_reads_the_movement_as_the_payment(
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

            assert row_valuation.settled_contribution(income) == Decimal("1910.95")
            with_movement = _read()
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).
            db.session.delete(movement)
            income.entries.remove(movement)
            db.session.commit()
            db.session.expire_all()
            assert xfer.status.is_settled
            # The record IS the movement: without it the leg records $0.00
            # (R-BAL82's close of nothing), the loan's position moves, and the
            # posted ledger -- R-BAL45's one transfer entry -- is untouched.
            assert row_valuation.settled_contribution(income) == Decimal("0")
            without_movement = _read()
            assert without_movement[0] != with_movement[0]
            assert without_movement[1] == with_movement[1]


class TestThePurchaseDoorsStateTheSource:
    """Who wrote the figure, by the door that wrote it."""

    def test_a_hand_typed_purchase_is_typed(self, app, seed_user, seed_periods):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_purchase_born_from_a_bank_line_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The born-purchase builder STATES the bank wrote it (R-BAL61)."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
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
                    figure=observed(Decimal("12.79")), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("12.97")),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_bank_confirmation_of_the_day_alone_leaves_a_typed_figure_typed(
        self, app, seed_user, seed_periods,
    ):
        """INVERTED at plan step X-bi-3e-1 (ruling R-BAL61).

        This door RAISED a typed figure to ``observed`` whenever a submission
        carried an ``observed`` day, figure written or not -- and every
        bank-observed day ever written onto a settled row on production was
        exactly this, a day-only confirmation.  A match that confirms the day
        writes no figure, so nothing about who wrote it changed.
        """
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
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
            assert entry.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            ), "the day IS confirmed; the figure's writer is not the question"
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_figure_typed_over_a_standing_observed_day_is_typed(
        self, app, seed_user, seed_periods,
    ):
        """BAL-508's class on a purchase, through the PATCH's own shape.

        The entry PATCH re-submits the whole form, so a person re-pricing a
        bank-confirmed purchase sends the recorded ``observed`` day back
        unchanged beside the new figure (``submitted_settle_day``'s echo).
        Under ``figure_source_of`` that echo made the person's figure the
        bank's; the figure says ``typed`` itself now.
        """
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
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
                figure=typed(Decimal("13.00")),
                settle_day=recorded_settle_day(entry),
            )
            assert entry.amount == Decimal("13.00")
            assert entry.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_the_matchers_reprice_of_a_purchase_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The matcher's purchase arm: the bank's figure, stated as such."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                figure=observed(Decimal("12.97")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            assert entry.amount == Decimal("12.97")
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)

    def test_a_day_only_edit_on_the_owners_word_leaves_the_source_alone(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
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


def _reverted_manual_close(seed_user, period, *, budget="100.00", close="120.00",
                           settle_day=None):
    """R-BAL68's worked row: a purchase-tracked envelope, no purchases, closed at
    a typed figure and reverted -- so its kept movement is the only entry.

    Returns ``(envelope, survivor)``.
    """
    envelope = _bill(seed_user, period, budget, is_envelope=True)
    _settle(envelope, submitted=typed(Decimal(close)), settle_day=settle_day)
    db.session.flush()
    _revert(envelope)
    db.session.flush()
    survivor = _only_movement(envelope)
    assert survivor.settled_on is None
    assert survivor.amount == Decimal(close)
    assert [entry.id for entry in envelope.entries] == [survivor.id], (
        "the survivor is the row's only entry"
    )
    return envelope, survivor


def _statement_on(seed_user, observed_on):
    """The owner's Checking statement presented for *observed_on* (the
    account's real assertion, the test's day -- ``test_reconcile_service``'s
    own shape)."""
    account_id = seed_user["account"].id
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account_id,
        replace(cash_ledger.governing_anchor(account_id), observed_on=observed_on),
    )


class TestAKeptMovementIsNotAPurchase:
    """Ruling **R-BAL68**: every purchase-meaning reader reads ``purchases``.

    One control per reader class, each on the same worked row -- a `$100.00`
    envelope with no purchases, closed at a typed `$120.00` and reverted, so
    the movement the revert kept is its only entry -- and each a FIRING
    control: the family reading is asserted beside the purchases reading, so
    a reader that slid back onto ``entries`` fails by the worked figure
    rather than passing over a row where the two agree.
    """

    def test_the_fold_holds_the_plan_not_the_withdrawn_close(
        self, app, seed_user, seed_periods,
    ):
        """A reverted row is worth its PLAN (developer, 2026-08-17)."""
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            basis = planted_basis(envelope)
            assert _entry_aware_amount(envelope, basis) == Decimal("100.00")
            # Over the family the survivor would SPEND the budget -- the close
            # the owner withdrew read as a purchase -- and nothing of the plan
            # would be held back (the unspent-budget reservation, ruling
            # R-BAL77; through X-bi-3e the three-bucket floor read $120.00).
            assert _entry_checking_impact(envelope.entries, Decimal("100.00")) == Decimal("0")

    def test_the_reconcile_panel_neither_offers_nor_stamps_the_survivor(
        self, app, seed_user, seed_periods,
    ):
        """The panel's purchase arm: no phantom `$120.00` offer, no forged tick."""
        with app.app_context():
            close_day = seed_periods[0].start_date + timedelta(days=2)
            envelope, survivor = _reverted_manual_close(
                seed_user, seed_periods[0],
                settle_day=SettleDay(day=close_day, basis=SettledDayBasisEnum.ENTERED),
            )
            statement = _statement_on(seed_user, close_day + timedelta(days=3))
            # Every other clause of the outstanding scope admits it.
            assert survivor.settled_on is None
            assert survivor.is_credit is False
            assert survivor.purchased_on <= statement.observed_on
            offered = {
                group.transaction_id: group.purchases
                for group in reconcile_service.outstanding_set(statement).groups
            }
            assert offered.get(envelope.id, ()) == (), (
                "the reverted envelope is offered as a ROW at most, never its close"
            )
            assert reconcile_service.record_settled_days(statement, {survivor.id}) == 0
            db.session.flush()
            assert _only_movement(envelope).settled_on is None
            assert _only_movement(envelope).reconciled_by_id is None

    def test_the_statement_bound_ignores_the_survivors_day(
        self, app, seed_user, seed_periods,
    ):
        """A withdrawn close dated after the statement is no reason to hold the row back."""
        with app.app_context():
            close_day = seed_periods[0].start_date + timedelta(days=5)
            envelope, survivor = _reverted_manual_close(
                seed_user, seed_periods[0],
                settle_day=SettleDay(day=close_day, basis=SettledDayBasisEnum.ENTERED),
            )
            statement = _statement_on(seed_user, close_day - timedelta(days=3))
            assert survivor.purchased_on > statement.observed_on, (
                "over the family the bound would refuse the row"
            )
            assert wholly_spent_by(statement, envelope) is True

    def test_carry_forward_rolls_the_whole_budget(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plan = next(p for p in preview.plans if p.transaction.id == envelope.id)
            assert plan.kind == carry_forward_service.PLAN_KIND_ENVELOPE
            assert plan.entries_sum == Decimal("0.00")
            assert plan.leftover == Decimal("100.00")

    def test_carry_forward_executes_the_whole_budget_and_the_close_withdraws(
        self, app, seed_user, seed_periods,
    ):
        """The execute arm reads the same purchases the preview did."""
        with app.app_context():
            envelope, survivor = _reverted_manual_close(seed_user, seed_periods[0])
            target = generate_row_of(envelope.template, seed_periods[1])
            db.session.flush()
            basis = planted_basis(envelope, target)
            assert resolve_transaction_amount(target, basis) == Decimal("100.00")

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 1
            assert resolve_transaction_amount(
                target, planted_basis(envelope, target),
            ) == Decimal("200.00"), "the whole `$100.00` rolled"
            assert envelope.status.is_settled
            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert envelope.covering_movements == []
            assert db.session.get(TransactionEntry, survivor.id) is None

    def test_the_dashboards_still_due_reads_the_whole_budget(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            assert _row_still_due(
                envelope, Decimal("100.00"), Decimal("100.00"),
            ) == Decimal("100.00")

    def test_the_grids_sums_list_and_refresh_read_no_purchases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            budgets = {envelope.id: Decimal("100.00")}
            assert build_entry_sums_dict([envelope], budgets) == {}
            periods = {
                envelope.pay_period_id: calendar_for(
                    seed_user["user"].id,
                ).require_period(FiledRow.for_row(envelope)),
            }
            listed = build_entry_lists_dict(
                [envelope], budgets, periods,
                resolve_owner_cash_flow_set(envelope.user_id),
            )
            assert listed[envelope.id]["entries"] == []
            assert entry_service.get_entries_for_transaction(
                envelope.id, seed_user["user"].id,
            ) == []

    def test_the_dashboards_progress_reads_no_purchases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            progress = _entry_progress_fields(envelope, Decimal("100.00"))
            assert progress["is_tracked"] is True
            assert progress["entry_count"] == 0
            assert progress["entry_total"] is None

    def test_a_re_settle_after_one_real_purchase_offers_and_books_the_purchase(
        self, app, seed_user, seed_periods,
    ):
        """The offer equals the booking: `$30.00`, not `$150.00` and `$30.00`."""
        with app.app_context():
            envelope, survivor = _reverted_manual_close(seed_user, seed_periods[0])
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            assert sum(e.amount for e in envelope.entries) == Decimal("150.00"), (
                "the family still holds the withdrawn close"
            )
            basis = planted_basis(envelope)
            assert transaction_service.settle_amount(envelope, basis) == Decimal("30.00")

            _settle(envelope)
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            # The purchase carries the `$30.00`; the row, closed from its
            # purchases, has no covering movement and is worth nothing of its
            # own (ruling R-BAL81).
            assert envelope.covering_movements == []
            assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0")
            assert [
                cash_ledger.movement_cash_leg(envelope, entry)
                for entry in envelope.entries
            ] == [Decimal("-30.00")]
            assert db.session.get(TransactionEntry, survivor.id) is None

    def test_a_settled_manual_close_envelope_lists_no_purchase(
        self, app, seed_user, seed_periods,
    ):
        """The live class: 8 production envelopes (`$794.79`) read their own close as a purchase."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            close = _only_movement(envelope)
            assert close.amount == Decimal("100.00")
            budgets = {envelope.id: Decimal("100.00")}
            assert build_entry_sums_dict([envelope], budgets) == {}
            assert _entry_progress_fields(envelope, Decimal("100.00"))["entry_count"] == 0
            assert entry_service.get_entries_for_transaction(
                envelope.id, seed_user["user"].id,
            ) == []

    def test_an_endpoint_move_carries_a_projected_shadows_survivor(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL72: the movement's account is assigned on every shadow."""
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
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            survivor = _only_movement(income)
            assert not income.status.is_settled
            assert survivor.settled_on is None
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, to_account_id=other.id,
            )
            db.session.flush()
            assert income.account_id == other.id
            assert survivor.account_id == other.id, "in-session, before any expire"
            db.session.expire_all()
            assert db.session.get(TransactionEntry, survivor.id).account_id == other.id
            assert _only_movement(expense).account_id == seed_user["account"].id


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
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
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

    def test_a_figure_typed_over_a_real_purchase_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """The door path that once produced a stored figure beside a purchase.

        An envelope with a bank-born purchase is settled, its *Track
        individual purchases* is unticked on the settled row, and a figure is
        typed over it.  Through plan step ``X-bi-3e`` that wrote a
        ``corrected`` record beside the real purchase, and this case graded
        that the seam wrote its OWN movement rather than overwriting the
        purchase as its mirror.  Ruling **R-BAL78** (plan step
        ``balance:X-bi-4a``) makes the state UNREPRESENTABLE: the purchases
        ARE the figure, a stated figure over them would be counted beside
        them by a fold that reads movements alone, so the seam refuses the
        record and the row stands exactly as it was -- settled on its
        purchases, the purchase untouched, no second movement.
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
                    figure=typed(Decimal("57.96")), description="Walmart",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            _settle(envelope)
            db.session.flush()
            assert envelope.covering_movements == []
            envelope.template.is_envelope = False
            db.session.flush()
            assert envelope.tracks_purchases is False
            # The DOOR refuses first: a row holding purchases takes its figure
            # from them whatever the flag says (``settles_from_entries``).
            with pytest.raises(ValidationError, match="takes its figure from the purchases"):
                transaction_service.apply_requested_status(
                    envelope, envelope.status_id, submitted=typed(Decimal("999.99")),
                )
            # The SEAM refuses on its own, for a caller around the door.
            with pytest.raises(ValidationError, match="records its money as purchases"):
                status_seam.apply_status_change(
                    envelope, envelope.status_id,
                    settlement=status_seam.Settlement(
                        amount=Decimal("999.99"),
                        source=MovementFigureSourceEnum.TYPED,
                    ),
                )
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert envelope.covering_movements == []
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
                transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
                user_id=seed_user["user"].id, amount=Decimal("5.00"),
                description="bare", purchased_on=date(2026, 1, 5),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "figure_source_id" in str(exc.value)
            db.session.rollback()


class TestTheRetainedReadTakesTheSourceOffTheMovement:
    """``recorded_settlement`` reads the figure AND who wrote it off the
    covering movement, and a settled row with none records its entries.

    Plan step X-bi-3e-1 (ruling **R-BAL61**): the row stores no writer, so
    the retained record's source is the movement's.  Plan step
    ``balance:X-bi-4b-1`` (rulings **R-BAL80**, **R-BAL82**): the FIGURE is
    the movement's too, and a settled row holding no movement -- a ``$0.00``
    figure, whose movement ``ck_transaction_entries_positive_amount`` admits
    no row for -- is a close of nothing, read as a record stating no figure
    (``Settlement(None, None)``) exactly as an envelope's ``purchases``
    record is; reverted, it retains NOTHING.  Ruling **R-BAL70**'s cutover
    mapping (``derived`` -> ``resolved``, ``corrected`` -> ``typed`` off the
    row's basis column) answered that row through ``X-bi-4a`` and retired
    with the column read; the two ``$0.00`` cases below pinned it and pin
    R-BAL82 now.
    """

    def test_a_typed_correction_reads_typed_off_its_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("150.00")
            assert retained.source is MovementFigureSourceEnum.TYPED
            assert retained.stated

    def test_a_typed_zero_record_is_a_close_of_nothing_and_retains_nothing(
        self, app, seed_user, seed_periods,
    ):
        """A ``$0.00`` typed figure holds no movement: a close of nothing.

        ``ck_transaction_entries_positive_amount`` admits no movement of
        nothing (``TestAZeroSettlementWritesNoMovement``), so the row records
        its entries -- none -- and the typed figure's writer is stored nowhere
        (R-BAL70 accepted that; R-BAL82 follows it through).  Worked as the
        ruling was put: 'Water' budgeted `$148.32` here, closed at a typed
        `$0.00`, then reverted -- the panel used to OFFER and re-book
        `$0.00` (the retained ``corrected`` column), and offers the plan now
        unless the owner re-types `$0.00`.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, txn.status_id, submitted=typed(Decimal("0.00")),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert row_valuation.settled_figure(txn) == Decimal("0")
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount is None
            assert retained.source is None
            assert status_seam.honoured_correction(txn) is None

            _revert(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) is None
            assert transaction_service.retained_settle_amounts_by_id([txn]) == {
                txn.id: None,
            }
            basis = cash_ledger.derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert transaction_service.settle_amount(txn, basis) == Decimal(
                "148.32"
            )

    def test_a_derived_zero_record_is_a_close_of_nothing(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            db.session.flush()
            assert txn.covering_movements == []
            assert row_valuation.settled_figure(txn) == Decimal("0")
            retained = status_seam.recorded_settlement(txn)
            assert retained == status_seam.Settlement(None, None)
            assert not retained.stated

    def test_a_purchases_record_reads_no_source(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            _settle(envelope)
            db.session.flush()
            retained = status_seam.recorded_settlement(envelope)
            assert retained == status_seam.Settlement(None, None)
            assert not retained.stated

    def test_both_legs_of_a_corrected_transfer_read_typed_off_their_movements(
        self, app, seed_user, seed_periods,
    ):
        """The pair repair's read (``apply_status_to_all_three``) off a leg."""
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("480.00")),
            )
            db.session.flush()
            for leg in (expense, income):
                movement = _only_movement(leg)
                assert movement.amount == Decimal("480.00")
                assert movement.figure_source_id == _source(
                    MovementFigureSourceEnum.TYPED,
                )
                retained = status_seam.recorded_settlement(leg)
                assert retained.amount == Decimal("480.00")
                assert retained.source is MovementFigureSourceEnum.TYPED


class TestTheRetainedReadSurvivesARevert:
    """The 3e-1 interval pin, INVERTED at leaf X-bi-3e-2 (ruling R-BAL61).

    Through 3e-1 a revert deleted the covering movement and the retained
    record read by ruling R-BAL70's mapping alone, so a figure the BANK
    stated, reverted and re-settled, read ``typed``.  The movement now
    survives a revert un-dated, and the retained read takes the source off
    it: the bank's figure re-settles ``observed`` on the same row.
    """

    def test_a_reverted_bank_figure_re_settles_observed_off_the_surviving_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=observed(Decimal("148.40")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            first = _only_movement(txn)
            first_id = first.id
            assert first.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )
            _revert(txn)
            db.session.flush()
            survivor = _only_movement(txn)
            assert survivor.id == first_id
            assert survivor.settled_on is None
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("148.40")
            assert retained.source is MovementFigureSourceEnum.OBSERVED
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("148.40"), "the figure is honoured"
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            ), "who wrote a figure does not change because the row was reverted"
            assert movement.settled_on == txn.settled_on
