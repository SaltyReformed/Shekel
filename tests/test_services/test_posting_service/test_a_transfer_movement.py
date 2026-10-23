"""A transfer's movements post through the one movement writer, booked under the transfer's leg."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.utils.dates import display_today
from tests._test_helpers import (
    a_twin_of,
    assert_a_twin_is_refused,
    constraint_name_from,
    create_settled_transfer,
    refused_by_database_rule,
)

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _covering_movement_of_side,
    _scenario_id,
    _ledger_total,
)


class TestTransactionShadowFamily:
    """A transfer's money posts through its own door; no shadow is left for the row door.

    Through plan step ``balance:X-bi-6-4d-2``'s re-parenting a shadow's
    family was empty -- each side's record hangs off the transfer -- so the
    row door on a shadow posted nothing (ruling R-BAL167 class 3).  The same
    step made a transfer one row (ruling **R-BAL258**), so the shadow the
    door was handed cannot be stored, and its case asserts that refusal
    (class 2).
    """

    def test_a_shadow_reconciles_its_movement_against_transit(
        self, app, db, seed_user, savings,
    ):
        """No shadow can be stored for the row door: the transfer's teardown is its own.

        Re-expressed from ``test_transfer_shadow_is_noop`` at plan step
        ``balance:X-bi-6-3`` (ruling **R-BAL101**): the guard that returned
        ``[]`` for a shadow went with ruling R-BAL45's interval, and the
        shadow's family -- its covering movement -- re-posted its side
        against transit, {Savings +100, transit -100}, from a ledger the
        teardown had brought to zero.

        **Re-expressed again at plan step ``balance:X-bi-6-4d-2``** (ruling
        **R-BAL167** class 3, developer 2026-10-09: "Keep (1) and (2) as
        guards that a copy row posts and reverses nothing"): each side's
        record hung off the TRANSFER and the shadow held none, so the row
        door on the income shadow reconciled an empty family and wrote
        nothing, before the teardown and after it.

        **Class 2 since the same step made a transfer one row** (ruling
        **R-BAL258**): ``ck_transactions_names_no_transfer`` refuses the
        income shadow the door was handed, so it is staged as
        :func:`~tests._test_helpers.assert_a_twin_is_refused` builds it and
        that refusal asserted.  The guard the 10-09 answer kept is that CHECK
        now, pinned by ``test_a_transfer_is_one_row.py``'s
        ``test_the_database_refuses_a_twin``; no row door is reached here.  The
        money read is the transfer's own teardown: Savings back on its
        ``$100.00`` opening, transit at ``0.00`` and Checking on its
        ``$1,000.00`` opening.  Those are also the figures of a settle that
        posted nothing, so they grade only a teardown that leaves a side
        posted.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # The row the door was handed, the income shadow, cannot be stored.
            assert_a_twin_is_refused(_db.session, transfer, is_income=True)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()

            assert _ledger_total(savings_ledger) == Decimal("100.00")  # opening
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                checking.id, _scenario_id(seed_user),
            ) == Decimal("1000.00")  # opening

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


class TestTheWriterBooksATransferMovementUnderItsLeg:
    """Leaf ``balance:X-bi-6-4a``: a transfer movement's parent is its LEG, never its shadow.

    Every door hands the writer the parent
    ``transfer_legs.movement_parent`` resolves, so a transfer movement's
    period, contributing gate and owner are its TRANSFER's -- the ones the
    cash fold has read since the leaf's first half (ruling **R-BAL106**).
    Each case wrote a drift around the service and asked the pair's door.
    Since plan step ``balance:X-bi-6-4d-2`` the transfer drifts are refused
    at COMMIT and asked inside the save (ruling **R-BAL167** class 2), and a
    transfer is one row (ruling **R-BAL258**), so the two shadow cases stage
    a shadow the database refuses.  The period case, a shadow moved to
    another period, went with the shadows (ruling **R-BAL380**); the period
    read it kept is graded by ``test_the_oracle_and_attribution.py``'s
    revert-and-move case.
    """

    def test_a_transfer_deleted_around_the_service_holds_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """A paid transfer hidden around the service is REFUSED at commit.

        ``$100.00`` Checking -> Savings settled; the TRANSFER row is flagged
        deleted by SQL while both its sides hold their records (Transfer
        Invariant 4 drift; until plan step ``balance:X-bi-6-4d-2`` its two
        shadows stayed live beside it).
        Until plan step ``balance:X-bi-6-4d-2`` the state committed
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
        """No shadow can be stored for the row teardown: the transfer's money stays counted once.

        ``$100.00`` Checking -> Savings settled; ``reverse_postings_before_delete``
        was handed the SAVINGS shadow.  Until plan step ``balance:X-bi-6-4d-2``
        the shadow held the Savings side's movement, booked under the Savings
        leg, so the reversal was written under the ``transfer_movement``
        source: one entry, {Savings -100.00, transit +100.00}, Savings back
        on its ``$100.00`` opening (the promise ``posting_service`` made at
        plan step ``balance:X-bi-6-3``, "a transfer shadow reaching here is
        reversed like any row").

        **Re-expressed at that step** (ruling **R-BAL167** class 3, developer
        2026-10-09: "Keep (1) and (2) as guards that a copy row posts and
        reverses nothing"): each side's record hung off the TRANSFER and the
        shadow held none, so the teardown on the shadow wrote no entry.

        **Class 2 since the same step made a transfer one row** (ruling
        **R-BAL258**): the Savings shadow the teardown was handed is refused
        (:func:`~tests._test_helpers.assert_a_twin_is_refused`), the SAME
        planted shape as the income shadow of
        ``TestTransactionShadowFamily``'s case.  The guard the 10-09 answer
        kept is that CHECK now; no row door is reached here.  The money read
        is the settle's: both sides posted by the transfer's own records --
        Savings ``200.00`` (``$100.00`` opening + ``100.00``), Checking
        ``900.00`` -- which the settle's own cases grade too.
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
            # The row the teardown was handed, the Savings shadow, cannot be
            # stored.
            assert_a_twin_is_refused(_db.session, transfer, is_income=True)

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("900.00")

    def test_a_movement_under_a_dead_shadow_posts_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """No hidden shadow can be stored to hold a movement: the transfer's sides stay posted.

        ``$100.00`` Checking -> Savings settled; the CHECKING shadow alone was
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

        **Staged inside the save at plan step ``balance:X-bi-6-4d-2``**
        (ruling **R-BAL167** class 2): a shadow held a movement only when it
        was planted there -- both side records re-parented under their twins
        in one statement -- and the deleted-row rule's row arm refused a twin
        hidden holding one at COMMIT; inside the save the pair door wrote
        nothing, because a transfer's family is its side records alone
        (``transfer_legs.transfer_family_movements``).

        **The hidden shadow itself is refused since the same step made a
        transfer one row** (ruling **R-BAL258**): the Checking shadow is
        staged as :func:`~tests._test_helpers.a_twin_of` builds it, hidden,
        and ``ck_transactions_names_no_transfer`` refuses it at the write, so
        no record can be re-parented under it.  A HIDDEN twin is staged in no
        other case, so this refusal is the one shape of its own here.  The
        case then reads the pair's door at target (``[]``) and Savings at
        ``200.00``, which the pair door's and the settle's own cases grade
        too.
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
            dead_shadow = a_twin_of(transfer, is_income=False)
            dead_shadow.is_deleted = True
            _db.session.add(dead_shadow)
            # The constraint IS the claim: a bare ``IntegrityError`` would be
            # satisfied by a foreign key or a NOT NULL just as well.
            with pytest.raises(IntegrityError) as caught:
                _db.session.flush()
            _db.session.rollback()
            assert constraint_name_from(caught.value) == (
                "ck_transactions_names_no_transfer"
            ), f"the hidden shadow was refused by another rule: {caught.value}"

            assert posting_service.sync_transfer_postings(transfer) == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")

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
