"""The cutover writes every settled row's covering movement by a total rule.

Plan step **balance:X-bi-3d**, rulings **R-BAL40**, **R-BAL61** and
**R-BAL62**.  Migration ``ad573b07bede`` writes, for every row in the settled
band on a ``derived`` / ``corrected`` basis, the covering movement the status
seam would have written had it existed when the row settled -- and refuses,
with the count, any row it cannot mirror without a judgment.

**The migration's arms are DRIVEN, not described** (the pattern
``test_movement_figure_source`` set for 3a's backfill): each case settles a
row through the production door, DELETES the seam's mirror by SQL -- the row
tables' state for every production row settled before X-bi-3a; the ledger's
is staged only where a case reads it -- and runs the migration's module-level
callables against the test connection.
Every refusal is a control SHOWN TO FIRE (``docs/plans/verification.md``
standard 4), and the balance equality is graded against the same fold in all
three states: the seam's mirror, no mirror, the cutover's mirror.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    PostingSourceEnum,
    SettledDayBasisEnum,
    SettlementBasisEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    entry_service,
    posting_service,
    status_seam,
    transaction_service,
)
from app.services._posting_write import emit_typed_source_deltas, ledger_class_of
from app.services.cash_ledger import settled_cash_facts, settled_cash_leg
from app.services.posting_reads import _ledger_account_for
from app.services.entry_service import EntryDetails
from app.services.settle_day import SettleDay
from tests._test_helpers import (
    typed,
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    linked_ledger_account,
    load_migration_module,
    make_expense_template,
    make_income_template,
)

_MIGRATION = load_migration_module(
    "ad573b07bede_every_settled_row_is_covered.py"
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


def _paycheck(seed_user, period, amount="2572.78"):
    """One engine-generated income row of a fresh definition."""
    template = make_income_template(
        db.session, seed_user, amount=amount, name="Paycheck",
    )
    return generate_row_of(template, period)


def _settle(txn, *, submitted=None, settle_day=None):
    """Settle *txn* through the verb, so the seam writes its mirror."""
    transaction_service.settle_transaction(
        txn, submitted=submitted, settle_day=settle_day,
    )
    db.session.flush()


def _uncover(*rows):
    """Put *rows*' ROW TABLES in the pre-cutover shape: delete the seam's mirror.

    The row's own record stands and no movement mirrors it, which is every
    production row settled before X-bi-3a.  By SQL rather than through the
    seam, because no door produces this state: the seam keeps a mirror across
    a revert (plan step X-bi-3e-2) and withdraws one only for a ``$0.00`` or
    a ``purchases`` record, neither of which leaves a stored figure to
    cover.  **It does not stage the
    LEDGER**: the deleted mirror's purchase-sourced journal entry is orphaned
    (``ON DELETE SET NULL``) rather than reversed, and the parent's
    transaction-sourced entry stays at zero, where production's carries the
    whole figure.  Every case here reads the fold and the rows, never the
    ledger; the one case that grades the ledger transition
    (:class:`TestTheDeploysResyncMovesNoLedgerNet`) stages it itself.
    """
    ids = [row.id for row in rows]
    db.session.execute(sqlalchemy.text(
        "DELETE FROM budget.transaction_entries "
        "WHERE covers_settlement AND transaction_id = ANY(:ids)"
    ), {"ids": ids})
    for row in rows:
        db.session.expire(row)
        assert row.covering_movements == []


def _stage_legacy_row_leg(txn, cash_leg):
    """Post the TRANSACTION-sourced entry production's ledger held for *txn*.

    The row's own leg, ``{cash: cash_leg, category: -cash_leg}`` on the row's
    period and settle day -- what ``posting_service._settled_target`` booked
    through plan step ``X-bi-3e`` and nothing books since ``balance:X-bi-4a``
    (ruling **R-BAL80**).  Written by the balanced-write leaf the writer
    itself used, so the staged entry is shaped exactly as the legacy one.
    """
    # pylint: disable=import-outside-toplevel  -- the lazy-app-import convention.
    from app.services import ledger_account_service

    cash = _ledger_account_for(txn.account_id)
    category = ledger_account_service.get_or_create_category_ledger_account(
        txn.user_id, txn.category_id, ledger_class_of(txn),
    )
    emit_typed_source_deltas(
        txn,
        targets={
            (txn.pay_period_id, txn.settled_on): {
                cash.id: cash_leg, category.id: -cash_leg,
            },
        },
        source=PostingSourceEnum.TRANSACTION,
        description=txn.name,
        log_label=f"legacy row leg {txn.id}",
        transaction_id=txn.id,
    )


def _cover():
    """Run the migration's write against the test connection; return the count."""
    written = _MIGRATION.cover_settled_rows(db.session.connection())
    db.session.expire_all()
    return written


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


def _nets_by_account_day():
    """The posted ledger's net per ``(ledger account, entry_date)``, non-zero keys."""
    rows = (
        db.session.query(
            Posting.ledger_account_id, JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
        .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
        .all()
    )
    return {(account, day): net for account, day, net in rows if net != 0}


def _net_by_source(txn_id, source):
    """The cash net the ledger holds for one row under one posting source."""
    kind = ref_cache.posting_source_id(source)
    link = (
        JournalEntry.transaction_id == txn_id
        if source is PostingSourceEnum.TRANSACTION
        else JournalEntry.transaction_entry_id.in_(
            db.session.query(TransactionEntry.id).filter(
                TransactionEntry.transaction_id == txn_id,
            )
        )
    )
    cash = db.session.query(Transaction.account_id).filter_by(id=txn_id).scalar()
    ledger = linked_ledger_account(db.session, cash)
    return (
        db.session.query(db.func.coalesce(db.func.sum(Posting.amount), Decimal("0")))
        .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
        .filter(
            JournalEntry.source_kind_id == kind, link,
            Posting.ledger_account_id == ledger.id,
        )
        .scalar()
    )


def _assert_mirrors(txn, movement, source):
    """Every column the total rule states, read back off the written row."""
    assert movement.amount == txn.settled_amount
    assert movement.figure_source_id == _source(source)
    assert movement.description == txn.name
    assert movement.purchased_on == txn.settled_on
    assert movement.settled_on == txn.settled_on
    assert movement.settled_day_basis_id == txn.settled_day_basis_id
    assert movement.reconciled_by_id == txn.reconciled_by_id
    assert movement.account_id == txn.account_id
    assert movement.user_id == txn.user_id
    assert movement.is_credit is False
    assert movement.covers_settlement is True


class TestTheCutoverMirrorsTheRowsOwnRecord:
    """One movement per settled row, every column a function of the row."""

    def test_a_derived_bill_is_covered_resolved(self, app, seed_user, seed_periods):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            assert _cover() == 1
            _assert_mirrors(
                txn, _only_movement(txn), MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_corrected_bill_is_covered_typed(self, app, seed_user, seed_periods):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            assert txn.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            _uncover(txn)
            assert _cover() == 1
            movement = _only_movement(txn)
            _assert_mirrors(txn, movement, MovementFigureSourceEnum.TYPED)
            assert movement.amount == Decimal("150.00")

    def test_a_corrected_figure_on_a_bank_observed_day_is_TYPED(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL61's firing control: the writer, never the day.

        The cutover classifies by the record's basis alone, because on
        production every ``corrected`` figure was typed by a person and the
        day beside it says only that the bank confirmed the day.  Under
        R-BAL40's old arm this case read ``observed`` and this assertion
        fails.  The seam labelled this shape ``observed`` too when this test
        was written (its re-record inferred the source from the day); leaf
        X-bi-3e-1 retired that, so the seam's own label and the cutover's
        now agree, which the first assertion pins.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=typed(Decimal("148.40")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            assert _only_movement(txn).figure_source_id == _source(
                MovementFigureSourceEnum.TYPED,
            ), "the seam labels the person's figure a person's (X-bi-3e-1)"
            _uncover(txn)
            assert _cover() == 1
            movement = _only_movement(txn)
            _assert_mirrors(txn, movement, MovementFigureSourceEnum.TYPED)
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )

    def test_a_paycheck_is_covered_and_its_fact_reads_PLUS_figure(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            assert _cover() == 1
            movement = _only_movement(txn)
            _assert_mirrors(txn, movement, MovementFigureSourceEnum.RESOLVED)
            facts = [
                fact for fact in settled_cash_facts(txn.account_id, txn.scenario_id)
                if fact.entry_id == movement.id
            ]
            assert [fact.delta for fact in facts] == [Decimal("2572.78")]

    def test_both_legs_of_a_settled_transfer_are_covered(
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
            legs = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            assert len(legs) == 2
            _uncover(*legs)
            assert _cover() == 2
            for leg in legs:
                _assert_mirrors(
                    leg, _only_movement(leg), MovementFigureSourceEnum.RESOLVED,
                )

    def test_a_statement_linked_row_carries_its_link_onto_the_movement(
        self, app, seed_user, seed_periods,
    ):
        """The clearing link is mirrored, through the composite key over the account."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            anchor = _latest_anchor(txn.account_id)
            status_seam.record_clearing(txn, anchor.id)
            db.session.flush()
            assert txn.reconciled_by_id == anchor.id
            _uncover(txn)
            assert _cover() == 1
            movement = _only_movement(txn)
            _assert_mirrors(txn, movement, MovementFigureSourceEnum.RESOLVED)
            assert movement.reconciled_by_id == anchor.id
            _MIGRATION.refuse_unless_total(db.session.connection())

    def test_a_soft_deleted_settled_row_is_covered_too(
        self, app, seed_user, seed_periods,
    ):
        """The population is the settled BAND, deleted rows included.

        The seam leaves a mirror standing through a soft delete (worth
        nothing under a non-contributing parent), and a restore that skips
        the seam would otherwise revive an uncovered settled row.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            transaction_service.delete_transaction(txn, seed_user["user"].id)
            db.session.flush()
            assert txn.is_deleted is True
            assert txn.status.is_settled
            _uncover(txn)
            assert _cover() == 1
            movement = _only_movement(txn)
            _assert_mirrors(txn, movement, MovementFigureSourceEnum.RESOLVED)
            # Worth nothing to the fold, as the row's own leg is.
            assert settled_cash_leg(txn) == Decimal("0.00")
            assert not any(
                fact.entry_id == movement.id
                for fact in settled_cash_facts(txn.account_id, txn.scenario_id)
            )


class TestTheCutoverWritesNothingItShouldNot:
    """Zero movements is the right count for three shapes, and once is enough."""

    def test_a_zero_record_writes_no_movement(self, app, seed_user, seed_periods):
        """R-BAL40's arm: a movement of nothing is not one."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            assert txn.settled_amount == Decimal("0.00")
            assert txn.covering_movements == []
            assert _cover() == 0
            assert txn.covering_movements == []
            _MIGRATION.refuse_unless_total(db.session.connection())

    def test_a_purchases_basis_envelope_is_not_covered(
        self, app, seed_user, seed_periods,
    ):
        """Its purchases ARE the record; a mirror would state the money twice."""
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
            _settle(envelope)
            assert envelope.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.PURCHASES,
            )
            assert _cover() == 0
            assert len(envelope.entries) == 1
            assert envelope.covering_movements == []

    def test_a_row_already_covered_is_left_alone(self, app, seed_user, seed_periods):
        """Idempotent: a database that ran the seam before the cutover."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            before = _only_movement(txn)
            before_id, before_source = before.id, before.figure_source_id
            assert _cover() == 0
            after = _only_movement(txn)
            assert (after.id, after.figure_source_id) == (before_id, before_source)

    def test_a_reverted_row_is_not_covered(self, app, seed_user, seed_periods):
        """Out of the band the retained record is not money; the cutover writes nothing.

        Since plan step ``X-bi-3e-2`` (ruling **R-BAL61**) the revert KEEPS
        the mirror, un-dated -- so a reverted row already holds one, and the
        cutover, which writes for settled rows alone, still writes none.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "100.00")
            _settle(txn, submitted=typed(Decimal("90.00")))
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            assert txn.settled_amount == Decimal("90.00")
            (survivor,) = txn.covering_movements
            assert survivor.settled_on is None
            assert _cover() == 0
            assert [m.id for m in txn.covering_movements] == [survivor.id]
            assert survivor.settled_on is None


class TestTheCutoverWritesWhatTheFoldReads:
    """The cutover's movement is the fold's fact, graded across the three states of one row.

    Through plan step ``X-bi-3e`` this class graded ruling R-FM's identity:
    the fold's per-day sums were IDENTICAL across the cutover, because the
    row's own leg carried the figure until the movement did.  On the tree
    the migration shipped in that was its balance-neutrality claim, and its
    docstring still states the measurement.  Since ``balance:X-bi-4a`` the
    fold reads movements alone (ruling **R-BAL80**), so on THIS tree an
    uncovered settled row is worth NOTHING to the fold and the cutover's
    write is what makes its money visible -- which is what a database still
    behind ``ad573b07bede`` meets when this image upgrades it.  The row leg
    the matcher still prices (``settled_cash_leg``) reads the whole figure
    uncovered and zero covered, exactly as before.
    """

    def test_the_fold_reads_nothing_uncovered_and_the_movement_covered(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            account_id, scenario_id = txn.account_id, txn.scenario_id
            with_seams = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_seams[txn.settled_on] == Decimal("-148.32")

            _uncover(txn)
            assert settled_cash_leg(txn) == Decimal("-148.32")
            assert _per_day(settled_cash_facts(account_id, scenario_id)) == {}

            assert _cover() == 1
            assert settled_cash_leg(txn) == Decimal("0")
            assert _per_day(settled_cash_facts(account_id, scenario_id)) == with_seams


class TestTheCutoverRefusesWhatItCannotAnswer:
    """R-BAL40: no judgment.  Each refusal names its count; each is shown to fire."""

    def test_a_dateless_settled_row_refuses(self, app, seed_user, seed_periods):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transactions "
                "SET settled_on = NULL, settled_day_basis_id = NULL WHERE id = :id"
            ), {"id": txn.id})
            with pytest.raises(RuntimeError, match=r"^1 settled .* no settle day"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())

    def test_a_figureless_record_refuses(self, app, seed_user, seed_periods):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transactions SET settled_amount = NULL WHERE id = :id"
            ), {"id": txn.id})
            with pytest.raises(RuntimeError, match=r"^1 settled .* store no figure"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())

    def test_a_settled_row_with_no_record_at_all_refuses(
        self, app, seed_user, seed_periods,
    ):
        """Outside the population, so it must be refused rather than skipped.

        The pairing CHECKs make such a row dateless too; the figureless
        refusal is asked FIRST so the sentence names the whole repair.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transactions SET settled_amount = NULL, "
                "settled_basis_id = NULL, settled_on = NULL, "
                "settled_day_basis_id = NULL WHERE id = :id"
            ), {"id": txn.id})
            with pytest.raises(RuntimeError, match=r"^1 settled .* store no figure"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())
            assert _cover() == 0

    def test_a_recordless_leg_of_a_settled_pair_refuses(
        self, app, seed_user, seed_periods,
    ):
        """The pair check reads a NULL beside a figure as agreement; this fires first."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.flush()
            legs = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            _uncover(*legs)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transactions SET settled_amount = NULL, "
                "settled_basis_id = NULL, settled_on = NULL, "
                "settled_day_basis_id = NULL WHERE id = :leg"
            ), {"leg": legs[0].id})
            with pytest.raises(RuntimeError, match=r"^1 settled .* store no figure"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())

    @pytest.mark.parametrize(
        "drift",
        [
            pytest.param(
                "UPDATE budget.transactions SET status_id = :projected "
                "WHERE id = :leg",
                id="a-shadow-out-of-its-parents-status",
            ),
            pytest.param(
                "UPDATE budget.transactions SET settled_amount = settled_amount + 1 "
                "WHERE id = :leg",
                id="the-legs-disagree-on-the-figure",
            ),
            pytest.param(
                "UPDATE budget.transactions SET settled_on = settled_on + 1 "
                "WHERE id = :leg",
                id="the-legs-disagree-on-the-day",
            ),
        ],
    )
    def test_a_broken_transfer_pair_refuses(
        self, app, seed_user, seed_periods, drift,
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
            legs = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            _uncover(*legs)
            _MIGRATION.refuse_unanswerable_rows(db.session.connection())
            db.session.execute(sqlalchemy.text(drift), {
                "leg": legs[0].id,
                "projected": ref_cache.status_id(StatusEnum.PROJECTED),
            })
            with pytest.raises(RuntimeError, match=r"^1 budget.transfers .* broken"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())

    def test_a_missing_shadow_refuses(self, app, seed_user, seed_periods):
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.flush()
            legs = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
            _uncover(*legs)
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.transactions WHERE id = :leg"
            ), {"leg": legs[0].id})
            with pytest.raises(RuntimeError, match=r"^1 budget.transfers .* broken"):
                _MIGRATION.refuse_unanswerable_rows(db.session.connection())

    def test_a_clean_database_passes_every_refusal(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            _MIGRATION.refuse_unanswerable_rows(db.session.connection())


class TestTheProofIsGradedBothWays:
    """The total rule after the write, and it fires on either direction's failure."""

    def test_a_mirror_disagreeing_on_the_figure_refuses(
        self, app, seed_user, seed_periods,
    ):
        """The cent the fold cannot see (R-FM's identity absorbs it)."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _MIGRATION.refuse_unless_total(db.session.connection())
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transaction_entries SET amount = amount + 0.01 "
                "WHERE covers_settlement AND transaction_id = :id"
            ), {"id": txn.id})
            with pytest.raises(RuntimeError, match=r"^1 settled .* exactly one covering"):
                _MIGRATION.refuse_unless_total(db.session.connection())

    def test_a_mirror_on_another_day_refuses(self, app, seed_user, seed_periods):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            moved = txn.settled_on + timedelta(days=1)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transaction_entries "
                "SET settled_on = :day, purchased_on = :day "
                "WHERE covers_settlement AND transaction_id = :id"
            ), {"id": txn.id, "day": moved})
            with pytest.raises(RuntimeError, match=r"^1 settled .* exactly one covering"):
                _MIGRATION.refuse_unless_total(db.session.connection())

    def test_a_mirror_under_a_zero_record_refuses(self, app, seed_user, seed_periods):
        """A $0.00 record holds NONE, so the zero arm counts every mirror."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            _MIGRATION.refuse_unless_total(db.session.connection())
            db.session.execute(sqlalchemy.text(
                "INSERT INTO budget.transaction_entries ("
                "  transaction_id, account_id, user_id, amount, description,"
                "  purchased_on, settled_on, settled_day_basis_id, is_credit,"
                "  covers_settlement, figure_source_id"
                ") SELECT id, account_id, user_id, 1.00, name, settled_on,"
                "  settled_on, settled_day_basis_id, false, true, :source "
                "FROM budget.transactions WHERE id = :id"
            ), {"id": txn.id, "source": _source(MovementFigureSourceEnum.RESOLVED)})
            with pytest.raises(RuntimeError, match=r"^1 settled .* exactly one covering"):
                _MIGRATION.refuse_unless_total(db.session.connection())

    def test_a_stray_mirror_under_an_unsettled_row_refuses(
        self, app, seed_user, seed_periods,
    ):
        """The other direction: a movement the seam would have withdrawn."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.execute(sqlalchemy.text(
                "UPDATE budget.transactions SET status_id = :projected, "
                "settled_on = NULL, settled_day_basis_id = NULL WHERE id = :id"
            ), {"id": txn.id, "projected": ref_cache.status_id(StatusEnum.PROJECTED)})
            db.session.expire(txn)
            assert len(txn.covering_movements) == 1
            with pytest.raises(RuntimeError, match=r"^1 covering movement"):
                _MIGRATION.refuse_unless_total(db.session.connection())

    def test_the_upgrade_runs_end_to_end_on_a_staged_row(
        self, app, seed_user, seed_periods,
    ):
        """The three callables in the upgrade's own order, over one row."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            _uncover(txn)
            bind = db.session.connection()
            _MIGRATION.refuse_unanswerable_rows(bind)
            assert _MIGRATION.cover_settled_rows(bind) == 1
            _MIGRATION.refuse_unless_total(bind)
            db.session.expire_all()
            _assert_mirrors(
                txn, _only_movement(txn), MovementFigureSourceEnum.RESOLVED,
            )


class TestTheDeploysResyncMovesNoLedgerNet:
    """The half the deploy performs after the migration, staged as production holds it.

    Production's ledger carried each settled row's whole figure on its
    TRANSACTION-sourced entry and held no purchase-sourced entry for it.  The
    deploy's ``resync_all_cash_postings`` then reverses that entry to zero and
    posts the new movement's purchase-sourced one on the same day and period,
    against the same category account.  The net per ``(ledger account, day)``
    must not move by a cent; measured on the production clone, and graded here
    on a bill and a paycheck.

    **The legacy leg is staged through the balanced-write leaf** since plan
    step ``balance:X-bi-4a``: the writer posts nothing for a row any more
    (ruling **R-BAL80**), so the pre-3a ledger can no longer be produced by
    asking it -- :func:`_stage_legacy_row_leg` writes the one entry
    production held, by the same primitive the writer once used.
    """

    @pytest.mark.parametrize(
        ("build", "figure"),
        [
            pytest.param(_bill, Decimal("-148.32"), id="a-bill"),
            pytest.param(_paycheck, Decimal("2572.78"), id="a-paycheck"),
        ],
    )
    def test_the_nets_per_account_day_are_identical_and_the_source_moves(
        self, app, seed_user, seed_periods, build, figure,
    ):
        with app.app_context():
            txn = build(seed_user, seed_periods[0])
            _settle(txn)
            # Stage the PRE-3a ledger: reverse the mirror's legs through the
            # door, delete the mirror, and lay the parent's own leg carrying
            # the whole figure, as production's ledger held it.
            posting_service.reverse_purchase_postings_before_delete(_only_movement(txn))
            _uncover(txn)
            _stage_legacy_row_leg(txn, figure)
            db.session.flush()
            assert _net_by_source(txn.id, PostingSourceEnum.TRANSACTION) == figure
            assert _net_by_source(txn.id, PostingSourceEnum.PURCHASE) == Decimal("0")
            before = _nets_by_account_day()
            assert before, "the staged ledger holds nothing to compare"

            assert _cover() == 1
            changed = posting_service.resync_all_cash_postings()
            db.session.flush()
            assert changed == (1, 0)
            assert _nets_by_account_day() == before
            assert _net_by_source(txn.id, PostingSourceEnum.TRANSACTION) == Decimal("0")
            assert _net_by_source(txn.id, PostingSourceEnum.PURCHASE) == figure
            assert posting_service.resync_all_cash_postings() == (0, 0)
