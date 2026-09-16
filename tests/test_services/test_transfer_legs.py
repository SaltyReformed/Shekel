"""A projected transfer's legs are DERIVED from the parent, and the readers say so.

Plan step **balance:X-bi-6a**, ruling **R-BAL13** (developer ruling
**R-BAL38**).  Until this step every balance reader loaded a still-projected
transfer's two SHADOW rows and priced each through amount rule 5 (*a shadow is
worth its parent*).  Now the readers derive one
:class:`~app.services.transfer_legs.PlannedTransferLeg` per side from the parent
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
from app.services.transfer_legs import (
    PlannedTransferLeg,
    leg_of,
    planned_transfer_legs,
)
from app.services.transfer_service import (
    TransferSpec,
    create_transfer as create_transfer_from_spec,
    delete_transfer,
)
from tests._test_helpers import (
    basis_for,
    capture_sql_statements,
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    make_investment_account,
    read_pass,
    settle_day_columns,
    settlement_columns,
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

        assert income == PlannedTransferLeg(
            transfer=transfer, account_id=2, is_income=True,
        )
        assert income.is_expense is False
        assert expense == PlannedTransferLeg(
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


class TestPlannedTransferLegs:
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

            legs = [item for item in plan if isinstance(item, PlannedTransferLeg)]
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
                if isinstance(item, PlannedTransferLeg)
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
    """Write a whole settlement record and a settled status onto ONE shadow.

    A STATUS DRIFT: the parent and the sibling stay Projected.  Transfer
    Invariants 3 and 4 forbid it and no door writes it (every status change
    goes through ``apply_status_to_all_three``); it is written here directly
    because the class has occurred (``transfer_service._restore`` carries a
    corrector for it) and the fold's answer under it is what the controls
    below pin.  The whole record, not the status alone: three CHECKs weld the
    settle day to its settlement record.
    """
    for column, value in {
        **settle_day_columns(day), **settlement_columns(day, amount),
    }.items():
        setattr(shadow, column, value)
    shadow.status_id = ref_cache.status_id(StatusEnum.DONE)


class TestAStatusDriftIsCountedByBothHalvesUntilXBi4:
    """PINNED, NOT ENDORSED: what the two relations answer when their statuses part.

    **The instrument before the measurement** (the X-g2b-0 idiom).  Since plan
    step balance:X-bi-6a the record half keys settled-ness on the SHADOW's
    status and the plan half on the PARENT's, so a status drift -- forbidden by
    Transfer Invariants 3 and 4, written by no door, 0 of 350 shadow rows on
    the 2026-09-15 snapshot -- is read by both halves or by neither, where the
    shadow-reading fold counted it exactly once.  These cases pin both
    directions so that ``balance:X-bi-4``, which re-keys the record half onto
    movements, moves a NUMBER here rather than an argument; the structural end
    is X-bi-4 + X-bi-6, after which a transfer's status lives in one row and
    the state cannot be written at all.  Developer ruling 2026-09-15 on
    X-bi-6a's review: disclose and pin, build nothing to tear down.
    """

    def test_a_settled_shadow_under_a_projected_parent_is_counted_twice(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift A: $250.00 folds checking at -$500.00 and savings at +$250.00.

        Checking's shadow is a settled RECORD (-$250.00 on its settle day) and
        the parent is still a PLAN (its from-side leg, -$250.00 on the same
        day).  Savings is right: its shadow is still Projected, so it is
        excluded from the plan, and its leg lands once.  X-bi-4 is expected to
        move the first figure to -$250.00.
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
            _settle_shadow_around_the_service(
                _shadow_on(transfer, checking), day, _AMOUNT,
            )
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the drift did not land: the parent must stay Projected"

            assert _balance_on(checking, scenario, day) - checking_before == (
                -_AMOUNT * 2
            )
            assert _balance_on(savings, scenario, day) - savings_before == (
                _AMOUNT
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
                (isinstance(i.source, PlannedTransferLeg), i.dates.settled_on)
                for i in installments
            ] == [(False, period.start_date), (True, None)]
