"""A transfer's movements post through the one movement writer, booked under the transfer's leg."""

from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.utils.dates import display_today
from tests._test_helpers import create_settled_transfer, refused_by_database_rule

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _covering_movement_of_side,
    _scenario_id,
    _period_nets_for_transfer,
    _ledger_total,
)


class TestTransactionShadowFamily:
    """A transfer shadow's family posts through the ONE movement writer.

    Since plan step ``balance:X-bi-6-4d-2`` a shadow's family is empty --
    each side's record hangs off the transfer -- so the row door on a shadow
    posts nothing (ruling R-BAL167 class 3).
    """

    def test_a_shadow_reconciles_its_movement_against_transit(
        self, app, db, seed_user, savings,
    ):
        """``sync_transaction_postings`` on a shadow posts nothing: a copy row holds no money.

        Re-expressed from ``test_transfer_shadow_is_noop`` at plan step
        ``balance:X-bi-6-3`` (ruling **R-BAL101**): the guard that returned
        ``[]`` for a shadow went with ruling R-BAL45's interval, and the
        shadow's family -- its covering movement -- re-posted its side
        against transit, {Savings +100, transit -100}, from a ledger the
        teardown had brought to zero.

        **Re-expressed again at plan step ``balance:X-bi-6-4d-2``** (ruling
        **R-BAL167** class 3, developer 2026-10-09: "Keep (1) and (2) as
        guards that a copy row posts and reverses nothing"): each side's
        record hangs off the TRANSFER since that step and the shadow holds
        none, so the row door on the income shadow reconciles an empty family
        and writes nothing, before the teardown and after it.  From the
        teardown's zero the call leaves Savings on its ``$100.00`` opening and
        transit at ``0.00`` -- no entry added, none sourced from the shadow --
        so the transfer's money is posted only by its own door.  The entry
        this case asserted (the side's record, ``transfer_movement``, the
        legs above) is not written.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            income_shadow = (
                _db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, account_id=savings.id)
                .one()
            )
            assert income_shadow.transfer_id is not None
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # At target after the settle: the door writes nothing.
            assert posting_service.sync_transaction_postings(income_shadow) == []

            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()
            assert _ledger_total(savings_ledger) == Decimal("100.00")  # opening
            entries_before = len(_entries_for_transfer(transfer.id))

            assert posting_service.sync_transaction_postings(income_shadow) == []
            _db.session.commit()

            # Unchanged by the call: Savings on its opening, transit at zero,
            # no entry added to the family.
            assert _ledger_total(savings_ledger) == Decimal("100.00")
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert len(_entries_for_transfer(transfer.id)) == entries_before
            # No transaction-sourced entry exists for the shadow (its own
            # target is empty), and the expense side stayed reversed.
            assert _db.session.query(JournalEntry).filter_by(
                transaction_id=income_shadow.id,
            ).count() == 0
            assert posting_service.account_posting_total(
                checking.id, _scenario_id(seed_user),
            ) == Decimal("1000.00")

    def test_a_movement_reconcile_reads_only_its_own_source_kind(
        self, app, db, seed_user, savings,
    ):
        """An entry linking the same movement under ANOTHER source is untouched.

        The hazard rulings **R-BAL100** and **R-BAL101** arm together: a loan
        payment's SPLIT correction links the loan-side movement's
        ``transaction_entry_id`` under the ``loan_payment`` source, beside the
        movement's own cash leg under ``transfer_movement``.  A reconcile that
        read the posted side by LINK alone would sum the split's legs into the
        cash leg's, compute a delta against the cash target, and REVERSE the
        split.  Every typed reconcile filters on its source kind as well as its
        link (``_posting_write.emit_typed_source_deltas``), so it must not.

        Planted by hand -- a balanced ``loan_payment`` entry of $30.00 linked
        to the to-side's record (off the transfer by its side link since plan
        step balance:X-bi-6-4d-2, the income shadow's covering movement until
        then; ruling R-BAL167 class 4), between the transit row and the
        Savings row -- then the pair re-synced: the sync writes nothing
        (the cash leg is at target and the split is not its concern) and the
        family holds exactly the three entries planted.  Mutation, observed
        to FIRE on 2026-09-21: with the kind term dropped from the filter the
        sync emitted a fourth entry reversing the $30.00 (``== []`` failed).
        The planted legs themselves are append-only and so never asserted --
        a claim the ledger cannot falsify grades nothing.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            movement = _covering_movement_of_side(transfer.id, savings.id)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            planted = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=movement.settled_on,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.LOAN_PAYMENT,
                ),
                transaction_entry_id=movement.id,
                description="planted split beside the cash leg",
            )
            _emit_balanced_entry(planted, [
                _PostingLeg(
                    savings_ledger, Decimal("-30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                _PostingLeg(
                    transit_ledger, Decimal("30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
            ])
            _db.session.commit()

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # The movement's own family: the cash leg, the planted entry, and
            # nothing the sync added.
            assert len(_entries_for_transfer(transfer.id)) == 3


def _live_shadow_id(transfer_id, account_id):
    """Return the id of the transfer's LIVE shadow row on *account_id*."""
    return (
        _db.session.query(Transaction.id)
        .filter(
            Transaction.transfer_id == transfer_id,
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        )
        .scalar()
    )


def _drift(sql, **params):
    """Write one row AROUND the service by raw SQL, commit, and expire the session.

    Each case below writes a state no door writes (Transfer Invariants 3 and
    4) the way it has occurred -- past the services -- so the writer is
    asked about exactly the rows the database holds.
    """
    _db.session.execute(_db.text(sql), params)
    _db.session.commit()
    _db.session.expire_all()


class TestTheWriterBooksATransferMovementUnderItsLeg:
    """Leaf ``balance:X-bi-6-4a``: a transfer movement's parent is its LEG, never its shadow.

    Every door hands the writer the parent
    ``transfer_legs.movement_parent`` resolves, so a transfer movement's
    period, contributing gate and owner are its TRANSFER's -- the ones the
    cash fold has read since the leaf's first half (ruling **R-BAL106**).
    Each case writes a drift around the service and asks the pair's door.
    The period, gate and oracle cases are red on the tree before the leaf,
    where the writer and the oracle read the shadow row or the status; the
    dead-shadow case pins the one term the leaf ADDED (a leg posts its
    record alone), whose answer the old tree gave through the dead shadow's
    own gate.
    """

    def test_a_legs_entries_file_under_its_transfers_period(
        self, app, db, seed_user, seed_periods, savings,
    ):  # pylint: disable=unused-argument
        """R-JA, the parent decides: a shadow moved to another period moves no entry.

        ``$100.00`` Checking -> Savings settled in P; the CHECKING shadow's
        period is rewritten to F by SQL.  Tearing the pair down and posting
        it again through its door files both sides' entries under P, the
        transfer's: over the family Checking nets ``-100.00`` in P and F
        holds nothing.  Before the leaf the re-post read the shadow's period,
        so Checking's ``-100.00`` landed in F and P netted ``0.00``.
        """
        with app.app_context():
            period, drifted = seed_periods[0], seed_periods[5]
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings, period,
                amount=Decimal("100.00"),
            )
            _db.session.commit()
            _drift(
                "UPDATE budget.transactions SET pay_period_id = :p WHERE id = :id",
                p=drifted.id, id=_live_shadow_id(transfer.id, checking.id),
            )

            posting_service.reverse_transfer_postings_before_delete(transfer)
            reposted = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert [entry.pay_period_id for entry in reposted] == [
                period.id, period.id,
            ]
            per_period = _period_nets_for_transfer(transfer.id)
            assert drifted.id not in per_period
            assert per_period[period.id][_ledger_id(checking)] == (
                Decimal("-100.00")
            )
            assert per_period[period.id][_ledger_id(savings)] == (
                Decimal("100.00")
            )

    def test_a_transfer_deleted_around_the_service_holds_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """A paid transfer hidden around the service is REFUSED at commit.

        ``$100.00`` Checking -> Savings settled; the TRANSFER row alone is
        flagged deleted by SQL, its two shadows left live (Transfer Invariant
        4 drift).  Until plan step ``balance:X-bi-6-4d-2`` the state committed
        and this asked the writer about it: the pair's door reversed both
        legs (Checking back on its ``$1,000.00`` opening, Savings on its
        ``$100.00``) and the cash fold named no fact, both gating on the
        transfer.  Since that step the payments hang off the transfer and the
        deleted-row rule's transfer arm refuses a transfer hidden holding one,
        so the drift cannot be stored (ruling **R-BAL167** class 2; the
        readers' Transfer-level live filters went with it, finding
        **BAL-534**).  Nothing is hidden and both payments stay.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :id"
                ),
                {"id": transfer.id},
            )

            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()

            assert f"transfer {transfer.id} " in str(caught.value)
            _db.session.expire_all()
            assert transfer.is_deleted is False
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
            ).count() == 2

    def test_a_row_teardown_on_a_shadow_reverses_its_side_under_the_leg(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """The row teardown reaching a shadow reverses nothing: a copy row holds no money.

        ``$100.00`` Checking -> Savings settled; ``reverse_postings_before_delete``
        is handed the SAVINGS shadow.  Until plan step ``balance:X-bi-6-4d-2``
        the shadow held the Savings side's movement, booked under the Savings
        leg, so the reversal was written under the ``transfer_movement``
        source: one entry, {Savings -100.00, transit +100.00}, Savings back
        on its ``$100.00`` opening (the promise ``posting_service`` made at
        plan step ``balance:X-bi-6-3``, "a transfer shadow reaching here is
        reversed like any row").

        **Re-expressed at that step** (ruling **R-BAL167** class 3, developer
        2026-10-09: "Keep (1) and (2) as guards that a copy row posts and
        reverses nothing"): each side's record hangs off the TRANSFER and the
        shadow holds none, so the teardown on the shadow writes no entry and
        both sides stay posted -- Savings ``200.00`` (``$100.00`` opening +
        ``100.00``), Checking ``900.00`` -- the transfer's money counted once,
        by its own record.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            income_shadow = _db.session.get(
                Transaction, _live_shadow_id(transfer.id, savings.id),
            )
            entries_before = len(_entries_for_transfer(transfer.id))

            posting_service.reverse_postings_before_delete(income_shadow)
            _db.session.commit()

            assert _entries_for_transfer(transfer.id)[entries_before:] == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("900.00")

    def test_a_movement_under_a_dead_shadow_posts_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """A leg posts its RECORD alone: the live side stays, whatever a dead shadow holds.

        ``$100.00`` Checking -> Savings settled; the CHECKING shadow alone is
        soft-deleted by SQL, the transfer left live and settled (Transfer
        Invariant 4 drift).  Until plan step ``balance:X-bi-6-4d-2``: its
        movement was no leg's record
        (``movement_parent`` gives its leg none: the join's live-shadow test
        over one loaded movement), so the pair's door reversed Checking's leg
        -- Checking back on its ``$1,000.00`` opening -- and kept Savings'
        (``$100.00`` opening + ``100.00`` = ``200.00``); the row door on the
        dead shadow then found its family at target.  MUTATION (then): drop
        ``purchase_posts``' record term and the dead shadow's movement was
        booked under the LIVE transfer's leg and stayed posted (Checking
        ``900.00``).

        **The state is refused at COMMIT since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): a shadow
        holds a movement only when it is planted there -- both side records
        re-parented under their twins in one statement, the shape the band
        rule admits as a ``$0.00`` close -- and the deleted-row rule's row arm
        refuses a twin hidden holding one.  So it is STAGED: planted, the
        Checking shadow hidden, both doors asked inside the save, and then
        the commit refused.  Kept: Savings ``200.00`` and the row door's
        ``[]``.  DROPPED: the pair door's one reversal ({Checking +100.00,
        transit -100.00}) and Checking ``1000.00``.  Inside the save the pair
        door writes nothing and Checking reads ``900.00`` (measured), because
        a transfer's family is its side records alone since that step
        (``transfer_legs.transfer_family_movements``).
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            dead_shadow_id = _live_shadow_id(transfer.id, checking.id)
            _db.session.execute(_db.text(
                "UPDATE budget.transaction_entries e SET transaction_id = t.id, "
                "expense_transfer_id = NULL, income_transfer_id = NULL "
                "FROM budget.transactions t WHERE t.transfer_id = :t "
                "  AND (e.expense_transfer_id = :t OR e.income_transfer_id = :t) "
                "  AND e.account_id = t.account_id"
            ), {"t": transfer.id})
            _db.session.execute(_db.text(
                "UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :id"
            ), {"id": dead_shadow_id})
            _db.session.expire_all()

            posting_service.sync_transfer_postings(transfer)

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.sync_transaction_postings(
                _db.session.get(Transaction, dead_shadow_id),
            ) == []

            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment or purchase",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()
            assert f"transaction {dead_shadow_id} " in str(caught.value)

    def test_the_oracle_counts_a_dated_leg_whatever_the_transfers_status(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """The oracle states the writer's rule: a dated leg of a contributing transfer.

        ``$100.00`` Checking -> Savings settled; the TRANSFER's status alone
        is rewritten to Projected by SQL (a status drift: both movements stay
        dated).  The writer keeps both legs posted -- a movement posts iff it
        is dated under a contributing parent (ruling **R-BAL101**) -- and
        ``settled_transfer_effect`` agrees on both accounts: Checking
        ``900.00`` = ``1,000.00`` + ``-100.00``, Savings ``200.00`` =
        ``100.00`` + ``100.00``.  Before the leaf the oracle filtered on the
        settled STATUS and answered ``0.00`` on both, grading a correct
        ledger as ``$100.00`` off.

        **The drift is STAGED inside the save since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2, the shape of
        :meth:`test_a_transfer_deleted_around_the_service_holds_nothing`): a
        record is dated only while its transfer is settled (the band rule),
        so the status rewrite is refused at COMMIT.  The writer and the
        oracle are asked inside the save, the commit is then refused, and
        the transfer stays Paid with both records dated.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transfers SET status_id = :s WHERE id = :id"
                ),
                {"s": ref_cache.status_id(StatusEnum.PROJECTED), "id": transfer.id},
            )
            _db.session.expire_all()

            assert posting_service.sync_transfer_postings(transfer) == []

            for account, opening, effect in (
                (checking, Decimal("1000.00"), Decimal("-100.00")),
                (savings, Decimal("100.00"), Decimal("100.00")),
            ):
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == effect
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == opening + effect

            with refused_by_database_rule(
                "is not settled but 2 of its payment records are dated",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()

            assert f"transfer {transfer.id} " in str(caught.value)
            _db.session.expire_all()
            assert transfer.status.is_settled
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
                TransactionEntry.settled_on.isnot(None),
            ).count() == 2
