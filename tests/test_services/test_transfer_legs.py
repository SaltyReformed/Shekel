"""A projected transfer's legs are DERIVED from the parent, and the readers say so.

Plan step **balance:X-bi-6a**, ruling **R-BAL13** (developer ruling
**R-BAL38**).  Until this step every balance reader loaded a still-projected
transfer's two SHADOW rows and priced each through amount rule 5 (*a shadow is
worth its parent*).  Now the readers derive one
:class:`~app.services.transfer_legs.TransferLeg` per side from the parent
row in ``budget.transfers`` and no reader folding a projection reads a
projected shadow at all.

**What these cases grade, and why the byte-identical ones are not enough.**
Wherever Transfer Invariants 1-3 hold, the fold's answer is the same whether it
reads the shadow or the parent -- 0 of 350 shadow rows drifted on the
2026-09-15 production snapshot -- so a before/after diff of every figure the seam
answers proves the re-point moved nothing and CANNOT show which relation is
read.  The firing controls below write past Transfer Invariant 4 -- a shadow
soft-deleted alone, a shadow's due date moved alone -- and assert the fold
follows the PARENT.  Before this step each of those writes moved the fold;
after it, each is invisible to the plan, which is ruling **R-JA**'s direction
(the parent is the value's one home) and the property the step exists to buy.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services.balance_at._asset_contributions import (
    _recorded_contributions,
)
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.services.loan_ledger import payment_installments
from app.services.cash_ledger import (
    planned_cash_rows,
    planned_leg_contribution,
    sum_projected,
)
from app.services.recorded_contributions import (
    load_shadow_income_contributions_for_account,
)
from app.exceptions import AmountUnresolvable
from app.services.cash_flow_set import (
    CashFlowSet,
    PlanItems,
    leg_accounts_shown,
    own_rows_clause,
    set_transfer_legs,
    set_transfer_legs_in_periods,
    touched_transfers_clause,
)
from app.services.cash_ledger import (
    leg_amounts_by_key,
    leg_contribution_of,
    leg_contributions_by_key,
    leg_settled_amounts_by_key,
)
from app.services.row_valuation import (
    leg_fixed_contribution,
    leg_settled_contribution,
    leg_settled_figure,
)
from app.services.transaction_service import leg_retained_amounts_by_key
from app.services.transfer_legs import (
    TransferLeg,
    cell_key,
    covering_movements_by_leg,
    grid_transfer_leg,
    grid_transfer_legs,
    leg_label,
    leg_of,
    planned_transfer_legs,
)
from app.services.transfer_service import (
    TransferSpec,
    create_transfer as create_transfer_from_spec,
    delete_transfer,
    update_transfer,
)
from app.utils.balance_predicates import is_projected_clause
from app.services.account_resolver import resolve_cash_flow_set
from tests._test_helpers import (
    cover_bare_settled_row,
    basis_for,
    capture_sql_statements,
    create_account_of_type,
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    make_investment_account,
    read_pass,
    settle_day_columns,
)

#: The transfer every DB case below moves: checking -> savings.
_AMOUNT = Decimal("250.00")
#: A read pinned BEFORE every seeded period (the schedule opens 2026-01-02),
#: so ruling R-G's clamp -- a plan lands no earlier than ``as_of + 1`` --
#: never moves a landing day and a leg lands on its nominal day.
_AS_OF = date(2026, 1, 1)


def _shadow_on(transfer, account):
    """Return *transfer*'s live shadow row on *account*."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer.id,
            Transaction.account_id == account.id,
            Transaction.is_deleted.is_(False),
        )
        .one()
    )


def _savings(seed_user, name="Savings"):
    """A savings account of the seeded owner, anchored at zero."""
    return create_savings_account(
        seed_user, db.session, name, Decimal("0.00"),
    )


def _cancelled_transfer(seed_user, from_account, to_account, period):
    """Create a CANCELLED transfer through the door (parent and both shadows)."""
    return create_transfer_from_spec(TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(_AMOUNT),
        status_id=ref_cache.status_id(StatusEnum.CANCELLED),
        category_id=seed_user["categories"]["Rent"].id,
    ))


def _balance_on(account, scenario, day):
    """Fold *account* through a FRESH read pass and sample it on *day*."""
    ctx = read_pass(account, scenario, _AS_OF)
    return balances_at(assembled_fold(account, ctx), [day])[day]


class TestLegOf:
    """Which side of a transfer an account is on decides the leg's direction."""

    def test_the_to_side_is_income_and_the_from_side_is_expense(self):
        transfer = Transfer(id=7, from_account_id=1, to_account_id=2)

        income = leg_of(transfer, 2)
        expense = leg_of(transfer, 1)

        assert income == TransferLeg(
            transfer=transfer, account_id=2, is_income=True,
        )
        assert income.is_expense is False
        assert expense == TransferLeg(
            transfer=transfer, account_id=1, is_income=False,
        )
        assert expense.is_expense is True

    def test_an_account_on_neither_side_is_refused(self):
        transfer = Transfer(id=7, from_account_id=1, to_account_id=2)

        with pytest.raises(ValueError, match="neither side of transfer 7"):
            leg_of(transfer, 3)

    def test_a_leg_reads_its_period_and_due_date_off_the_parent(self):
        """The leg stores nothing: every column is the parent's."""
        transfer = Transfer(
            id=7, from_account_id=1, to_account_id=2, pay_period_id=11,
            due_date=date(2026, 3, 4),
        )

        leg = leg_of(transfer, 1)

        assert leg.pay_period_id == 11
        assert leg.due_date == date(2026, 3, 4)


class TestTransferLegs:
    """The loader: which parents, on which side, and none that has settled."""

    def test_each_account_gets_its_own_side(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            on_checking = planned_transfer_legs(
                checking.id, scenario_id, options=(),
            )
            on_savings = planned_transfer_legs(
                savings.id, scenario_id, options=(),
            )

            assert [(leg.transfer.id, leg.is_income) for leg in on_checking] == [
                (transfer.id, False),
            ]
            assert [(leg.transfer.id, leg.is_income) for leg in on_savings] == [
                (transfer.id, True),
            ]

    def test_a_settled_a_cancelled_and_a_deleted_parent_yield_no_leg(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The plan half admits live still-Projected parents and nothing else.

        A settled transfer is the RECORD half's (its shadow row, until
        ``X-bi-4``); a Cancelled one is not money the account owes; a deleted
        one is gone.  Each is built through the door so the state is one the
        app produces.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[1],
                amount=_AMOUNT,
            )
            _cancelled_transfer(seed_user, checking, savings, seed_periods[2])
            deleted = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            live = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[4],
                amount=_AMOUNT,
            )
            db.session.commit()
            # Through the transfer service, so the parent and both shadows
            # go together (Transfer Invariants 3 and 4).
            delete_transfer(deleted.id, seed_user["user"].id, soft=True)
            db.session.commit()

            legs = planned_transfer_legs(
                checking.id, seed_user["scenario"].id, options=(),
            )

            assert [leg.transfer.id for leg in legs] == [live.id]


class TestTheCashFoldReadsTheParent:
    """The general case: every account's plan derives its transfer legs."""

    def test_the_plan_holds_a_leg_and_no_shadow_row(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``planned_cash_rows`` carries the parent's leg where the shadow was.

        The row half excludes shadows -- the ONE ``transfer_id`` narrowing this
        step adds, which ``X-bi-6`` deletes with the rows -- and the leg half
        derives them, so a projected transfer appears exactly once.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()

            plan = planned_cash_rows(checking.id, seed_user["scenario"].id)

            legs = [item for item in plan if isinstance(item, TransferLeg)]
            rows = [item for item in plan if isinstance(item, Transaction)]
            assert [(leg.transfer.id, leg.is_income) for leg in legs] == [
                (transfer.id, False),
            ]
            assert not [row for row in rows if row.transfer_id is not None], (
                "a projected shadow row is still in the plan beside its leg: "
                "the transfer would be counted twice"
            )

    def test_both_sides_fold_the_parent_to_the_cent(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Worked: $250.00 checking -> savings lands -$250.00 and +$250.00."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            before = period.start_date - timedelta(days=1)
            checking_before = _balance_on(checking, scenario, before)
            savings_before = _balance_on(savings, scenario, before)
            create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()

            landed = period.start_date
            assert (
                _balance_on(checking, scenario, landed) - checking_before
                == -_AMOUNT
            )
            assert (
                _balance_on(savings, scenario, landed) - savings_before
                == _AMOUNT
            )

    def test_a_shadow_deleted_around_the_service_moves_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL: the parent decides, a drifted shadow does not.

        The checking-side shadow is soft-deleted DIRECTLY -- a write past
        Transfer Invariant 4, the pair-drift ``transfer_service._restore``
        repairs by hand -- and the fold on checking still counts the parent's
        leg.  Before this step the fold read the shadow and the $250.00
        vanished from the projection; spliced back to reading shadows, this
        case fails at the last assertion.  The parent is the value's one home
        (ruling **R-JA**), so a shadow that has parted from it moves no
        balance.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            with_leg = _balance_on(checking, scenario, period.start_date)

            shadow = _shadow_on(transfer, checking)
            shadow.is_deleted = True
            db.session.commit()
            db.session.expire_all()

            # The bypass landed: the row is gone and the parent is not.
            assert db.session.get(Transaction, shadow.id).is_deleted is True
            assert db.session.get(Transfer, transfer.id).is_deleted is False
            assert (
                _balance_on(checking, scenario, period.start_date) == with_leg
            )

    def test_a_shadow_dated_around_the_service_lands_on_the_parents_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL: the landing day is the PARENT's due date.

        The parent is created due on the 3rd day of its period and the
        checking-side shadow's due date is then moved to the 8th, directly.
        The leg lands on the 3rd: the day before it reads the pre-transfer
        balance and the 3rd reads the transfer.  Before this step the fold
        dated the shadow, so the money would have landed on the 8th.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            parents_day = period.start_date + timedelta(days=2)
            shadows_day = period.start_date + timedelta(days=7)
            opening = _balance_on(checking, scenario, parents_day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT, due_date=parents_day,
            )
            db.session.commit()
            shadow = _shadow_on(transfer, checking)
            shadow.due_date = shadows_day
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).due_date == parents_day

            day_before = parents_day - timedelta(days=1)
            assert _balance_on(checking, scenario, day_before) == opening
            assert _balance_on(checking, scenario, parents_day) == (
                opening - _AMOUNT
            )

    def test_the_fold_reads_the_plan_relation_once(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """ONE statement against ``budget.transfers`` for a whole account's plan.

        The leg loader is a set load, not a per-row walk: three projected
        transfers cost one read of the plan relation.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            for period in seed_periods[2:5]:
                create_transfer(
                    seed_user, db.session, checking, savings, period,
                    amount=_AMOUNT,
                )
            db.session.commit()
            db.session.expire_all()
            ctx = read_pass(checking, seed_user["scenario"], _AS_OF)

            folded, statements = capture_sql_statements(
                lambda: assembled_fold(checking, ctx),
            )

            assert len([
                item for item in folded.plan.rows
                if isinstance(item, TransferLeg)
            ]) == 3, "the plan came back short -- a count over nothing"
            reads = [
                statement for statement, _ in statements
                if "FROM budget.transfers" in statement
            ]
            assert len(reads) == 1, reads


class TestSumProjectedOverLegs:
    """The ONE reduction values a leg on the side its account is on."""

    def test_an_income_and_an_expense_leg_reduce_on_their_own_sides(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            basis = basis_for(checking, seed_user["scenario"])

            income, expense = sum_projected(
                [leg_of(transfer, savings.id), leg_of(transfer, checking.id)],
                basis,
            )

            assert (income, expense) == (_AMOUNT, _AMOUNT)
            assert planned_leg_contribution(
                leg_of(transfer, savings.id), basis,
            ) == _AMOUNT

    def test_a_leg_of_a_parent_that_is_not_projected_is_skipped(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The reduction re-checks the PARENT's status, as it does a row's.

        The loader admits only still-Projected parents; the reduction does not
        trust it, exactly as it re-applies ``is_projected`` to every row it is
        handed, so the two cannot disagree about which legs are in the plan.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[1],
                amount=_AMOUNT,
            )
            db.session.commit()

            assert sum_projected(
                [leg_of(settled, checking.id)],
                basis_for(checking, seed_user["scenario"]),
            ) == (Decimal("0.00"), Decimal("0.00"))


class TestTheContributionFeedsReadTheParent:
    """Sites 3 and 4: the investment feeds' projected half is the parent's."""

    def test_a_projected_contribution_is_the_parents_amount_unconfirmed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            create_transfer(
                seed_user, db.session, seed_user["account"], account, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_shadow_income_contributions_for_account(
                basis, account.id, [period.id],
            )
            recorded = _recorded_contributions(basis, account.id)

            assert [
                (r.account_id, r.payday, r.amount, r.is_confirmed)
                for r in feed.records
            ] == [(account.id, period.start_date, _AMOUNT, False)]
            assert feed.linked_account_ids == frozenset({account.id})
            assert recorded == {period.id: _AMOUNT}

    def test_a_cancelled_contribution_links_the_account_and_counts_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The link set is read off the parents in EVERY status."""
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            _cancelled_transfer(seed_user, seed_user["account"], account, period)
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_shadow_income_contributions_for_account(
                basis, account.id, [period.id],
            )

            assert feed.records == []
            assert feed.linked_account_ids == frozenset({account.id})
            assert _recorded_contributions(basis, account.id) == {}

    def test_a_settled_contribution_is_its_record_confirmed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The RECORD half is still the shadow row, at what it recorded."""
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account, period,
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
            )
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_shadow_income_contributions_for_account(
                basis, account.id, [period.id],
            )

            assert [(r.amount, r.is_confirmed) for r in feed.records] == [
                (Decimal("240.00"), True),
            ]
            assert _recorded_contributions(basis, account.id) == {
                period.id: Decimal("240.00"),
            }


def _settle_shadow_around_the_service(shadow, day, amount):
    """Write a settle day and a settled status onto ONE shadow.

    A STATUS DRIFT: the parent and the sibling stay Projected.  Transfer
    Invariants 3 and 4 forbid it and no door writes it (every status change
    goes through ``apply_status_to_all_three``); it is written here directly
    because the class has occurred (``transfer_service._restore`` carries a
    corrector for it) and the fold's answer under it is what the controls
    below pin.  The day pair, not the status alone: its CHECK welds the day
    to its basis.  The RECORD is the covering movement, which each caller
    lays through ``cover_bare_settled_row`` after the flush, or leaves off
    to stage a ``$0.00`` close.

    Args:
        shadow: The leg to drift.
        day: Its settle day.
        amount: Unused since plan step ``balance:X-bi-4b-2`` deleted the
            row's own figure columns; kept so every caller still names what
            the leg settled at beside the cover it writes.
    """
    del amount
    for column, value in settle_day_columns(day).items():
        setattr(shadow, column, value)
    shadow.status_id = ref_cache.status_id(StatusEnum.DONE)


class TestAStatusDriftIsCountedOnce:
    """A leg is planned exactly while its own dated movement does not exist (R-BAL79).

    **The instrument before the measurement** (the X-g2b-0 idiom), inverted
    at plan step ``balance:X-bi-4a``.  Since plan step balance:X-bi-6a the
    record half keyed settled-ness on the SHADOW's status and the plan half
    on the PARENT's, so a status drift -- forbidden by Transfer Invariants 3
    and 4, written by no door, 0 of 350 shadow rows on the 2026-09-15
    snapshot -- was read by both halves (``-$500.00`` on a `$250.00`
    transfer, ledger row **BAL-500**) or by neither.  Since X-bi-4a the
    record half is the shadow's DATED covering movement and the plan half
    emits a leg only for a side whose dated movement does not exist (ruling
    **R-BAL79**), so the movement, not the status, decides which relation a
    leg is in and no leg is counted by both; drift B still reads by neither.
    The structural end is X-bi-6, after which a transfer's status lives in
    one row and the state cannot be written at all.
    """

    def test_a_settled_shadow_under_a_projected_parent_is_counted_once(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift A: $250.00 folds checking at -$250.00 and savings at +$250.00.

        Checking's shadow carries a DATED covering movement (-$250.00 on its
        settle day, the record half) and the parent is still a PLAN, whose
        from-side leg the plan half now SKIPS because that side's movement
        exists (ruling **R-BAL79**).  Savings is right either way: its shadow
        holds no movement, so its leg lands once.  Through ``X-bi-3e`` the
        first figure read -$500.00.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            day = period.start_date
            checking_before = _balance_on(checking, scenario, day)
            savings_before = _balance_on(savings, scenario, day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            shadow = _shadow_on(transfer, checking)
            _settle_shadow_around_the_service(shadow, day, _AMOUNT)
            db.session.flush()
            cover_bare_settled_row(db.session, shadow, _AMOUNT)
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the drift did not land: the parent must stay Projected"

            assert _balance_on(checking, scenario, day) - checking_before == (
                -_AMOUNT
            )
            assert _balance_on(savings, scenario, day) - savings_before == (
                _AMOUNT
            )

    def test_a_dated_movement_under_a_DELETED_shadow_is_not_a_record_and_the_plan_counts_once(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fold's one predicate CHANGE at leaf X-bi-6-1, declared and pinned.

        The plan's ``dated_leg`` EXISTS now rides
        ``transfer_legs._covering_movements_query``, which requires the shadow
        the movement hangs off to be LIVE -- the term the pre-leaf predicate
        did not carry.  On any door-written state the two agree (no door
        soft-deletes one shadow alone).  On the double drift built here -- a
        settled leg's dated movement under a shadow soft-deleted around the
        service, the parent still Projected -- the old predicate saw a dated
        movement and emitted no plan leg, while the settled half already
        excluded the deleted shadow (``balance_contributing_clause``): the
        leg vanished from BOTH halves.  Now the plan emits it and the balance
        moves by the parent's amount exactly once, which is R-JA's direction
        (the parent decides) and the settled half's own rule applied to the
        plan.  An adversarial review found the change shipped under a
        "no fold read changed" claim; this is the pin the claim owed.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            day = period.start_date
            checking_before = _balance_on(checking, scenario, day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            shadow = _shadow_on(transfer, checking)
            _settle_shadow_around_the_service(shadow, day, _AMOUNT)
            db.session.flush()
            cover_bare_settled_row(db.session, shadow, _AMOUNT)
            shadow.is_deleted = True
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the drift did not land: the parent must stay Projected"

            legs = planned_transfer_legs(
                checking.id, scenario.id, options=(),
            )
            assert [leg.transfer.id for leg in legs] == [transfer.id], (
                "the plan must emit the leg whose only dated movement hangs "
                "off a DELETED shadow: that movement is no record"
            )
            assert covering_movements_by_leg([transfer.id]) == {}
            assert _balance_on(checking, scenario, day) - checking_before == (
                -_AMOUNT
            ), "counted once, by the plan"

    def test_a_settled_shadow_with_no_movement_is_counted_once_by_the_plan(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift A without the movement: the plan leg alone carries the -$250.00.

        A shadow settled bare with no covering movement -- the ``$0.00``
        record since plan step ``balance:X-bi-4b-2`` (ruling R-BAL82); the
        pre-``X-bi-3d`` shape before it -- is worth nothing to the record
        half, and its side's plan leg is emitted because no dated movement
        exists.  Once, by the other relation; never twice.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            day = period.start_date
            checking_before = _balance_on(checking, scenario, day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            _settle_shadow_around_the_service(
                _shadow_on(transfer, checking), day, _AMOUNT,
            )
            db.session.commit()
            db.session.expire_all()

            assert _balance_on(checking, scenario, day) - checking_before == (
                -_AMOUNT
            )

    def test_a_projected_shadow_under_a_settled_parent_is_counted_by_neither(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift B: the parent settled alone, and the $250.00 vanishes.

        No leg (the parent is not Projected), no plan row (the shadow is
        excluded by ``transfer_id``), no settled fact (the shadow is not
        settled).  Written by no door either; pinned for the same reason.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            day = period.start_date
            checking_before = _balance_on(checking, scenario, day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            db.session.get(Transfer, transfer.id).status_id = (
                ref_cache.status_id(StatusEnum.DONE)
            )
            db.session.commit()
            db.session.expire_all()

            assert _balance_on(checking, scenario, day) == checking_before

    def test_the_loan_feed_lists_a_drifted_payment_in_both_halves(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift A on a loan: one payment, two installments in the feed.

        The settled shadow arrives in the record half with its settle day and
        the still-Projected parent in the plan half as a leg, so the
        amortization feed sees the payment twice; the replay drops the
        unsettled one (``has_settled_by``), the collision slotting does not.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Drift Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2024, 1, 1),
            )
            db.session.commit()
            period = seed_periods[2]
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], loan, period,
                amount=Decimal("1500.00"),
            )
            db.session.commit()
            _settle_shadow_around_the_service(
                _shadow_on(transfer, loan), period.start_date,
                Decimal("1500.00"),
            )
            db.session.commit()
            db.session.expire_all()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, 1,
                options=(), leg_options=(),
            )

            assert [
                (isinstance(i.source, TransferLeg), i.dates.settled_on)
                for i in installments
            ] == [(False, period.start_date), (True, None)]


# ── The grid's legs (plan step balance:X-bi-6-1, ruling R-BAL87) ────────
#
# The leaf that widened this module's value from the fold's plan half to the
# grid's whole view of a transfer: a leg carries its RECORD (the covering
# movement, reached through the one join), the grid loader draws one leg per
# side the cash-flow set shows, and three producers price a leg beside the
# rows' maps.  Each case below is the state the byte-identity harness could
# not distinguish on the production restore.


class TestALegsRecordIsItsCoveringMovement:
    """``covering_movements_by_leg`` and the leg it hangs off."""

    def test_a_settled_transfers_legs_carry_their_movements(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Each side's record is the covering movement on ITS account, dated."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            projected = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            db.session.commit()

            records = covering_movements_by_leg([settled.id, projected.id])

            assert set(records) == {
                (settled.id, checking.id), (settled.id, savings.id),
            }
            for account in (checking, savings):
                movement = records[(settled.id, account.id)]
                assert movement.account_id == account.id
                assert movement.covers_settlement is True
                assert movement.amount == Decimal("240.00")
                assert movement.settled_on == date(2026, 2, 3)
            assert covering_movements_by_leg([]) == {}

    def test_a_deleted_shadows_movement_is_not_a_legs_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The join reads the LIVE pair: a soft-deleted shadow's movement is no record."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            assert len(covering_movements_by_leg([settled.id])) == 2

            delete_transfer(settled.id, seed_user["user"].id, soft=True)
            db.session.commit()

            assert covering_movements_by_leg([settled.id]) == {}

    def test_grid_transfer_leg_loads_one_leg_with_its_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()

            leg = grid_transfer_leg(settled, savings.id)

            assert leg == TransferLeg(
                transfer=settled, account_id=savings.id, is_income=True,
                record=leg.record,
            )
            assert leg.record is not None and leg.record.account_id == savings.id
            assert leg.record.settled_on == date(2026, 2, 3)
            assert leg.cell_key == (settled.id, savings.id)


class TestTheGridLoaderDrawsTheSetsSide:
    """``grid_transfer_legs`` under ``leg_accounts_shown`` (ruling R-CC23)."""

    def test_a_transfer_with_one_endpoint_in_the_set_shows_from_that_side(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            checking_set = CashFlowSet.single(checking)
            savings_set = CashFlowSet.single(savings)

            assert leg_accounts_shown(checking_set, transfer) == (checking.id,)
            assert leg_accounts_shown(savings_set, transfer) == (savings.id,)
            on_checking = grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(checking_set, t),
            )
            assert [(leg.account_id, leg.is_income) for leg in on_checking] == [
                (checking.id, False),
            ]

    def test_a_transfer_between_two_members_shows_once_from_the_balance_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both endpoints in the set: the balance line's leg alone (the far leg is not drawn)."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            elsewhere = _savings(seed_user, name="Elsewhere")
            db.session.commit()
            both = CashFlowSet(balance=savings, members=(checking, savings))

            assert leg_accounts_shown(both, transfer) == (savings.id,)
            legs = grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(both, t),
            )
            assert [(leg.account_id, leg.is_income) for leg in legs] == [
                (savings.id, True),
            ]
            # A transfer touching no member draws nowhere.
            assert leg_accounts_shown(CashFlowSet.single(elsewhere), transfer) == ()
            assert grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(CashFlowSet.single(elsewhere), t),
            ) == []

    def test_both_endpoints_in_the_set_and_the_balance_line_on_neither_draws_nowhere(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The rule's fourth arm, reproduced: a checking -> B payment seen from A.

        Members ``(checking, A, B)`` with the balance line on A: both
        endpoints are members and neither is the balance account.  The row
        rule this replaced (``far_leg_clause``, deleted at leaf X-bi-6-1b)
        dropped both shadows there, so the payment was drawn NOWHERE on A's
        grid; the leg rule answers the same.  The first
        cut answered ``(A,)`` and ``leg_of`` refused it -- a 500 on the grid
        for every paycheck holding a payment to the other card (found by the
        leaf's adversarial review).  Whether "nowhere" is what R-CC23 means
        here is an open question for the developer, recorded at X-bi-6-1.
        """
        with app.app_context():
            checking = seed_user["account"]
            card_a = _savings(seed_user, name="Card A")
            card_b = _savings(seed_user, name="Card B")
            payment = create_transfer(
                seed_user, db.session, checking, card_b, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            seen_from_a = CashFlowSet(
                balance=card_a, members=(checking, card_a, card_b),
            )

            assert leg_accounts_shown(seen_from_a, payment) == ()
            assert grid_transfer_legs(
                [payment], lambda t: leg_accounts_shown(seen_from_a, t),
            ) == []

    def test_the_own_rows_clause_keeps_no_shadow(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``own_rows_clause``: every member row, no shadow (the near leg is the LEG's)."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            checking_set = CashFlowSet.single(checking)

            own = (
                db.session.query(Transaction.id)
                .filter(
                    Transaction.pay_period_id == seed_periods[2].id,
                    own_rows_clause(checking_set),
                )
                .all()
            )
            shadow_id = _shadow_on(transfer, checking).id

            assert (shadow_id,) not in own
            assert not any(
                db.session.get(Transaction, row_id).transfer_id is not None
                for (row_id,) in own
            )


class TestTheLegMapProducers:
    """The three leg-keyed maps beside their row twins, over the three states a leg can be in."""

    def test_a_projected_leg_is_priced_and_records_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            leg = grid_transfer_leg(transfer, checking.id)
            key = leg.cell_key

            assert leg_amounts_by_key([leg], basis_for(checking, seed_user["scenario"])) == {key: _AMOUNT}
            assert leg_settled_amounts_by_key([leg]) == {key: None}
            assert leg_retained_amounts_by_key([leg]) == {key: None}

    def test_a_settled_leg_records_its_movements_figure(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The recorded figure is the movement's, not the plan's, on both sides."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            legs = [
                grid_transfer_leg(settled, checking.id),
                grid_transfer_leg(settled, savings.id),
            ]

            assert leg_settled_amounts_by_key(legs) == {
                (settled.id, checking.id): Decimal("240.00"),
                (settled.id, savings.id): Decimal("240.00"),
            }
            # What the amount IS is the PLAN, unconditionally -- the budget
            # map's rule (``amounts_by_id``, ruling E-21) -- so the cell can
            # strike the estimate through beside the record.
            assert leg_amounts_by_key(legs, basis_for(checking, seed_user["scenario"])) == {
                (settled.id, checking.id): _AMOUNT,
                (settled.id, savings.id): _AMOUNT,
            }
            assert leg_retained_amounts_by_key(legs) == {
                (settled.id, checking.id): None, (settled.id, savings.id): None,
            }

    def test_a_settled_leg_with_no_movement_is_the_zero_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling R-BAL82's state on a leg: settled, no covering movement, ``$0``."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            leg = TransferLeg(
                transfer=settled, account_id=checking.id, is_income=False,
                record=None,
            )
            assert leg_settled_amounts_by_key([leg]) == {leg.cell_key: Decimal("0")}

    def test_a_reverted_leg_retains_a_stated_figure_and_not_a_resolved_one(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A typed correction outlives the revert; the plan's own price does not.

        ``honoured_figure`` over ``movement_settlement``: only a STATED record
        is honoured (ruling R-BAL61), which is the rule the row twin applies
        through ``honoured_correction``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            owner_id = seed_user["user"].id
            typed = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            resolved = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            for transfer in (typed, resolved):
                update_transfer(
                    transfer.id, owner_id,
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                )
            db.session.commit()

            typed_leg = grid_transfer_leg(typed, checking.id)
            resolved_leg = grid_transfer_leg(resolved, checking.id)

            assert typed_leg.record is not None and typed_leg.record.settled_on is None
            assert leg_retained_amounts_by_key([typed_leg, resolved_leg]) == {
                typed_leg.cell_key: Decimal("240.00"),
                resolved_leg.cell_key: None,
            }
            assert leg_settled_amounts_by_key([typed_leg, resolved_leg]) == {
                typed_leg.cell_key: None, resolved_leg.cell_key: None,
            }


class TestTheLegContributionTwins:
    """The per-leg valuation twins leaf X-bi-6-1b gave the display readers.

    ``leg_settled_figure`` / ``leg_fixed_contribution`` / ``leg_settled_contribution``
    beside ``settled_figure`` / ``fixed_contribution`` / ``settled_contribution``,
    and ``leg_contribution_of`` / ``leg_contributions_by_key`` beside
    ``contribution_of`` / ``contributions_by_id``, over the four states a
    display leg can be in: planned, settled with a movement, settled with none
    (the $0.00 record), and excluded.
    """

    def test_a_planned_leg_is_worth_its_parents_plan_and_records_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            leg = grid_transfer_leg(transfer, checking.id)
            basis = basis_for(checking, seed_user["scenario"])

            assert leg_settled_figure(leg) is None
            assert leg_fixed_contribution(leg) is None
            assert leg_contribution_of(leg, basis) == _AMOUNT
            assert leg_contributions_by_key([leg], basis) == {leg.cell_key: _AMOUNT}
            with pytest.raises(AmountUnresolvable):
                leg_settled_contribution(leg)

    def test_a_settled_leg_is_worth_its_record_and_not_its_plan(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The contribution is the movement's $240.00 where the plan says $250.00."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            leg = grid_transfer_leg(settled, checking.id)
            basis = basis_for(checking, seed_user["scenario"])

            assert leg_settled_figure(leg) == Decimal("240.00")
            assert leg_fixed_contribution(leg) == Decimal("240.00")
            assert leg_settled_contribution(leg) == Decimal("240.00")
            assert leg_contribution_of(leg, basis) == Decimal("240.00")
            assert leg_amounts_by_key([leg], basis) == {leg.cell_key: _AMOUNT}

    def test_a_settled_leg_with_no_movement_is_the_zero_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            leg = TransferLeg(
                transfer=settled, account_id=checking.id, is_income=False,
                record=None,
            )
            assert leg_settled_figure(leg) == Decimal("0")
            assert leg_settled_contribution(leg) == Decimal("0")
            assert leg_contribution_of(
                leg, basis_for(checking, seed_user["scenario"]),
            ) == Decimal("0")

    def test_an_excluded_leg_is_worth_zero_whatever_it_remembers(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A Cancelled parent's leg contributes 0 and is not refused."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            cancelled = _cancelled_transfer(
                seed_user, checking, savings, seed_periods[2],
            )
            db.session.commit()
            leg = grid_transfer_leg(cancelled, checking.id)

            assert leg_settled_figure(leg) is None
            assert leg_fixed_contribution(leg) == Decimal("0")
            assert leg_settled_contribution(leg) == Decimal("0")
            assert leg_contribution_of(
                leg, basis_for(checking, seed_user["scenario"]),
            ) == Decimal("0")


class TestALegsSettleDayAndTimeliness:
    """``settled_on`` and ``days_paid_before_due`` read the leg's MOVEMENT."""

    def test_a_settled_leg_reads_its_movements_day_and_agrees_with_the_row(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The leg's day is the movement's, and the arithmetic is the row's.

        Parity with the shadow row is a real check rather than one producer
        read twice: the row's ``settled_on`` is its own column and the leg's
        is the covering movement's, two stored days the seam writes together.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
                due_date=date(2026, 2, 6),
            )
            db.session.commit()
            leg = grid_transfer_leg(settled, checking.id)
            shadow = _shadow_on(settled, checking)

            assert leg.settled_on == date(2026, 2, 3)
            assert leg.days_paid_before_due == 3
            assert leg.days_paid_before_due == shadow.days_paid_before_due
            assert leg.tracks_purchases is False

    def test_a_planned_and_an_undated_leg_answer_none(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            planned = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, due_date=date(2026, 2, 6),
            )
            undated = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            planned_leg = grid_transfer_leg(planned, checking.id)
            undated_leg = grid_transfer_leg(undated, checking.id)

            assert planned_leg.settled_on is None
            assert planned_leg.days_paid_before_due is None
            assert undated_leg.settled_on == date(2026, 2, 17)
            assert undated_leg.days_paid_before_due is None


class TestTheSetsTransferHalf:
    """``touched_transfers_clause``, ``set_transfer_legs`` and ``PlanItems``."""

    def test_the_clause_selects_a_transfer_touching_any_member_and_no_other(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            other = _savings(seed_user, name="Other")
            touching = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            elsewhere = create_transfer(
                seed_user, db.session, savings, other, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            selected = (
                db.session.query(Transfer.id)
                .filter(touched_transfers_clause(cash_flow)).all()
            )
            assert {row.id for row in selected} == {touching.id}
            assert elsewhere.id not in {row.id for row in selected}

    def test_set_transfer_legs_draws_the_sets_side_and_plan_items_derives_items(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            legs = set_transfer_legs(cash_flow, [transfer])
            assert [(leg.transfer.id, leg.account_id, leg.is_expense) for leg in legs] == [
                (transfer.id, checking.id, True),
            ]
            rows = db.session.query(Transaction).filter(
                own_rows_clause(cash_flow), Transaction.is_deleted.is_(False),
            ).all()
            items = PlanItems.of(rows, legs)
            assert items.items == [*rows, *legs]
            assert [cell_key(item) for item in items.items] == [
                *[row.id for row in rows], (transfer.id, checking.id),
            ]


    def test_both_endpoints_members_draws_the_balance_lines_leg_from_either_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-CC23 from BOTH balance lines: the from-side's expense leg on
        checking's line, the to-side's income leg on the card's (the two
        cases ``TestPaycheckRowsClause`` graded as rows until leaf X-bi-6-1b)."""
        with app.app_context():
            checking = seed_user["account"]
            card = _savings(seed_user, name="Card")
            payment = create_transfer(
                seed_user, db.session, checking, card, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            from_checking = CashFlowSet(balance=checking, members=(checking, card))
            from_card = CashFlowSet(balance=card, members=(checking, card))

            assert [(leg.account_id, leg.is_expense) for leg in set_transfer_legs(
                from_checking, [payment],
            )] == [(checking.id, True)]
            assert [(leg.account_id, leg.is_income) for leg in set_transfer_legs(
                from_card, [payment],
            )] == [(card.id, True)]

    def test_one_endpoint_in_the_set_shows_from_that_endpoint_even_off_the_balance_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-CC23's second arm under a set of TWO, through the resolver.

        A savings -> card transfer is drawn on the CARD, a member that is not
        the balance line; a checking -> savings one on checking.  Sets of
        one cannot grade this arm (the one member IS the line), and the
        rejected option 3 -- hide every transfer leg on a non-balance member
        -- passes them; the deleted row case graded it, and this is its leg
        twin (the second review of leaf X-bi-6-1b).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            savings = _savings(seed_user)
            into_card = create_transfer(
                seed_user, db.session, savings, card, seed_periods[2],
                amount=Decimal("300.00"),
            )
            into_savings = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = resolve_cash_flow_set(seed_user["user"].id, None)
            assert cash_flow.member_ids == (checking.id, card.id)
            assert cash_flow.balance.id == checking.id

            assert leg_accounts_shown(cash_flow, into_card) == (card.id,)
            assert leg_accounts_shown(cash_flow, into_savings) == (checking.id,)
            assert [
                (leg.account_id, leg.is_income)
                for leg in set_transfer_legs(cash_flow, [into_card, into_savings])
            ] == [(card.id, True), (checking.id, False)]

    def test_the_period_loader_windows_by_membership_and_takes_the_readers_filter(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            in_window = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            create_transfer(
                seed_user, db.session, checking, savings, seed_periods[4],
                amount=_AMOUNT,
            )
            # A second endpoint: ``uq_transfers_adhoc_dedupe`` refuses two
            # identical ad-hoc transfers in one period.
            cancelled = _cancelled_transfer(
                seed_user, checking, _savings(seed_user, name="Other"),
                seed_periods[2],
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)
            scenario_id = seed_user["scenario"].id
            period_ids = [seed_periods[2].id]

            every = set_transfer_legs_in_periods(cash_flow, scenario_id, period_ids)
            assert [leg.transfer.id for leg in every] == sorted(
                [in_window.id, cancelled.id],
            )
            projected = set_transfer_legs_in_periods(
                cash_flow, scenario_id, period_ids, is_projected_clause(Transfer),
            )
            assert [leg.transfer.id for leg in projected] == [in_window.id]
            assert set_transfer_legs_in_periods(cash_flow, scenario_id, []) == []


class TestALegsDisplayProjections:
    """The parent's columns read through the leg, and the label composition."""

    def test_the_label_is_composed_from_the_endpoints_current_names(self):
        savings = Account(name="Savings")
        checking = Account(name="Checking")
        transfer = Transfer(from_account=checking, to_account=savings)
        transfer.from_account_id, transfer.to_account_id = 1, 2
        expense = TransferLeg(transfer=transfer, account_id=1, is_income=False)
        income = TransferLeg(transfer=transfer, account_id=2, is_income=True)

        assert expense.name == "Transfer to Savings"
        assert income.name == "Transfer from Checking"
        assert leg_label(checking, savings) == (
            "Transfer to Savings", "Transfer from Checking",
        )
        assert expense.account is checking and income.account is savings

        savings.name = "Emergency Fund"
        assert expense.name == "Transfer to Emergency Fund"
        assert expense.template_id is None and expense.recurs is False
        assert expense.record is None
