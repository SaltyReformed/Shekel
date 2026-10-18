"""The settled half: a paid leg's money through one loader, and the ledger writer's answer to what
a movement's parent is.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text as sa_text

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.cash_ledger import settled_cash_facts
from app.services.transfer_legs import (
    TransferLeg,
    covering_movements_by_leg,
    dated_leg_exists_clause,
    movement_parent,
    recorded_transfer_legs,
    transfer_family_movements,
    transfer_movement_rows,
)
from app.services.settle_day import record_settle_day
from tests._test_helpers import (
    an_entered_day,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    figure_source_columns,
)

from ._helpers import _AMOUNT, _shadow_on, _savings


class TestTheSettledHalfReadsTheLegOffItsTransfer:
    """Leaf ``balance:X-bi-6-4a`` (ruling **R-BAL106**): a paid leg's money through ONE loader."""

    def test_recorded_legs_carry_their_transfer_side_and_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One leg per covering movement: the side is the link's, the account its endpoint."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            db.session.commit()

            legs = recorded_transfer_legs(Transfer.from_account_id == checking.id)

            assert [
                (leg.transfer.id, leg.account_id, leg.is_income,
                 leg.record.account_id, leg.record.amount, leg.record.settled_on)
                for leg in legs
            ] == [
                (settled.id, checking.id, False, checking.id,
                 Decimal("240.00"), date(2026, 2, 3)),
                (settled.id, savings.id, True, savings.id,
                 Decimal("240.00"), date(2026, 2, 3)),
            ]
            assert all(
                leg.record.covers_settlement and leg.pay_period_id == settled.pay_period_id
                for leg in legs
            )

    def test_a_legs_fact_names_its_transfer_and_a_rows_names_none(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fold's two arms: a leg's fact carries ``transfer_id``, signed by its side."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [out_fact] = settled_cash_facts(checking.id, scenario_id)
            [in_fact] = settled_cash_facts(savings.id, scenario_id)

            assert (out_fact.transfer_id, out_fact.is_income, out_fact.delta) == (
                settled.id, False, -_AMOUNT,
            )
            assert (in_fact.transfer_id, in_fact.is_income, in_fact.delta) == (
                settled.id, True, _AMOUNT,
            )
            assert out_fact.pay_period_id == settled.pay_period_id

    def test_a_legs_budget_column_is_its_transfers_not_its_shadows(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-JA, the parent decides: a shadow moved to another period around the service moves no fact.

        The shadow's ``pay_period_id`` is rewritten by SQL (Transfer Invariant
        3 drift, which no door writes); the fold files the leg's money under
        the TRANSFER's period.  Through ``X-bi-6-3`` it read the shadow's, so
        this is red on the tree before the leaf.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            shadow = _shadow_on(settled, checking)
            db.session.execute(
                sa_text(
                    "UPDATE budget.transactions SET pay_period_id = :p "
                    "WHERE id = :id"
                ),
                {"p": seed_periods[4].id, "id": shadow.id},
            )
            db.session.commit()
            db.session.expire_all()

            [fact] = settled_cash_facts(checking.id, seed_user["scenario"].id)

            assert fact.pay_period_id == seed_periods[2].id
            assert fact.transfer_id == settled.id


class TestTheWritersParentIsTheLeg:
    """Leaf ``balance:X-bi-6-4a``'s second half: the ledger writer's parent resolution.

    The writer asks ONE function what a movement's parent is
    (``movement_parent``) and walks a transfer's family through ONE loader
    (``transfer_family_movements``).  Through the interval the answer reads
    the shadow the movement hangs off -- the Python twin of the join's
    expressions -- so these cases pin the twin to the join, live and dead
    shadows alike, and the one dated-leg clause the plan half and the deploy
    resync now share.
    """

    def test_a_rows_movement_answers_its_row_and_a_transfers_its_leg(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A plan row's movement is booked under the row; a transfer's under its LEG."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[2], Decimal("30.00"),
                account=checking, name="a row",
            )
            db.session.commit()
            [row_movement] = row.entries
            out_movement = covering_movements_by_leg([settled.id])[
                (settled.id, checking.id)
            ]

            assert movement_parent(row_movement) is row
            leg = movement_parent(out_movement)
            assert leg == TransferLeg(
                transfer=settled, account_id=checking.id, is_income=False,
                record=out_movement,
            )
            assert leg.record is out_movement
            assert leg.user_id == seed_user["user"].id

    def test_the_python_twin_agrees_with_the_join_live_and_dead_shadows_alike(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``movement_parent`` against ``transfer_movement_rows`` over a whole family.

        Two settled ``$250.00`` Checking -> Savings transfers; the second's
        CHECKING shadow is soft-deleted by SQL (Transfer Invariant 4 drift).
        The family loader returns all four movements, and the four it gives a
        record are exactly the four the ONE join returns, each on the side
        the join says.  Until plan step ``balance:X-bi-6-4d-2`` the dead
        shadow's movement was recordless on the from-side (the twin and the
        join each carried a live-shadow term); since then each side's record
        hangs off the TRANSFER (ruling **R-BAL88**), both terms are deleted,
        and the hidden twin is read by neither -- its side's record is still
        its leg's (ruling **R-BAL167** class 3).  MUTATION: give either a
        live-twin term back and the two disagree on that movement.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            first = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            second = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            dead_shadow_id = _shadow_on(second, checking).id
            db.session.execute(
                sa_text(
                    "UPDATE budget.transactions SET is_deleted = TRUE "
                    "WHERE id = :id"
                ),
                {"id": dead_shadow_id},
            )
            db.session.commit()
            db.session.expire_all()
            transfers = [db.session.get(Transfer, t.id) for t in (first, second)]

            joined = {
                movement.id: is_income
                for movement, _transfer, is_income in transfer_movement_rows(
                    Transfer.id.in_([first.id, second.id]),
                )
            }
            family = [
                pair for transfer in transfers
                for pair in transfer_family_movements(transfer)
            ]

            assert (len(family), len(joined)) == (4, 4)
            assert {
                movement.id for movement, leg in family
                if leg.record is movement
            } == set(joined)
            assert all(
                leg.is_income == joined[movement.id]
                for movement, leg in family if movement.id in joined
            )
            [(dead_movement, dead_leg)] = [
                (movement, leg) for movement, leg in family
                if leg.transfer.id == second.id and not leg.is_income
            ]
            assert dead_movement.transaction_id is None
            assert db.session.get(Transaction, dead_shadow_id).is_deleted
            assert (dead_leg.record, dead_leg.is_income, dead_leg.account_id) == (
                dead_movement, False, checking.id,
            )
            for transfer in transfers:
                ids = [m.id for m, _leg in transfer_family_movements(transfer)]
                assert ids == sorted(ids), "a family is walked in movement order"

    def test_a_non_covering_entry_under_a_live_shadow_is_no_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A non-covering entry under a live shadow is no record of its transfer.

        A settled ``$250.00`` Checking -> Savings transfer, and a ``$5.00``
        purchase-shaped entry written under its LIVE Savings shadow (no door
        writes one: ``entry_service`` refuses a shadow).  Until plan step
        ``balance:X-bi-6-4d-2`` ``movement_parent`` booked it under the
        Savings leg with NO record, by the twin's ``covers_settlement`` term.
        Since then a side's record hangs off the TRANSFER by a side link that
        is a payment record by ``ck_transaction_entries_side_link_is_a_record``
        (ruling **R-BAL88**; the term is deleted, finding **BAL-551**), so an
        entry under a twin is filed under the row it hangs off and is read by
        no transfer reader: neither the join nor the pair door's family
        returns it, so the writer posts nothing for it under the transfer
        (ruling **R-BAL167** class 3).
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            shadow = _shadow_on(settled, savings)
            stray = TransactionEntry(
                **figure_source_columns(),
                transaction_id=shadow.id, account_id=savings.id,
                owner_id=shadow.user_id, user_id=seed_user["user"].id,
                amount=Decimal("5.00"), description="stray",
                purchased_on=date(2026, 2, 3), is_credit=False,
            )
            db.session.add(stray)
            db.session.flush()

            assert movement_parent(stray) is shadow
            assert stray.id not in {
                movement.id for movement, _transfer, _is_income
                in transfer_movement_rows(Transfer.id == settled.id)
            }
            assert stray.id not in {
                movement.id for movement, _leg in transfer_family_movements(settled)
            }

    def test_the_dated_leg_clause_reads_records_alone(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One clause, two readers: the resync asks it bare, the plan half per account.

        Three ``$250.00`` Checking -> Savings transfers: settled (both sides
        dated), projected (no covering movement, but a DATED ``$5.00`` entry
        that covers no settlement written under its live Savings shadow --
        no door writes one), and settled with its CHECKING shadow
        soft-deleted by SQL.  Bare, the clause holds for the first and the
        third -- the third's Savings side is still a dated record -- and not
        for the projected one: the stray is no record.  The resync's clause
        before leaf ``X-bi-6-4a`` read every dated entry under a live shadow
        and held for it.  Narrowed to Checking it holds for the first and the
        third: since plan step ``balance:X-bi-6-4d-2`` the third's Checking
        record hangs off the TRANSFER (ruling **R-BAL88**), so its hidden
        shadow is read by no reader (ruling **R-BAL167** class 3); until then
        that movement hung off the dead shadow and was no record, and the
        clause held for the first alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            projected = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            drifted = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[4],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            db.session.execute(
                sa_text(
                    "UPDATE budget.transactions SET is_deleted = TRUE "
                    "WHERE id = :id"
                ),
                {"id": _shadow_on(drifted, checking).id},
            )
            shadow = _shadow_on(projected, savings)
            stray = TransactionEntry(
                **figure_source_columns(),
                transaction_id=shadow.id, account_id=savings.id,
                owner_id=shadow.user_id, user_id=seed_user["user"].id,
                amount=Decimal("5.00"), description="stray",
                purchased_on=date(2026, 2, 17), is_credit=False,
            )
            record_settle_day(stray, an_entered_day(date(2026, 2, 17)))
            db.session.add(stray)
            db.session.commit()

            def holding(*filters):
                return {
                    row.id for row in db.session.query(Transfer.id)
                    .filter(dated_leg_exists_clause(*filters))
                }

            assert holding() == {settled.id, drifted.id}
            assert holding(TransactionEntry.account_id == checking.id) == {
                settled.id, drifted.id,
            }
