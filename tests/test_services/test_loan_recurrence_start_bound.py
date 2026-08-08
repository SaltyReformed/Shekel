"""C9a: a recurring loan payment cannot start before the loan does.

``RecurrenceRule.start_date`` is the opening half of a recurrence's validity
window, derived by ``loan_recurrence_sync.sync_recurring_payment_bounds`` from
the loan's FIRST CONTRACTUAL INSTALLMENT.

**What it prevents, measured.** Before this bound, ``create_payment_transfer``
built its rule with no start at all and generated across every materialized pay
period, so a mortgage closing 2026-04-15 got payments due 2026-02-01,
2026-03-01 and 2026-04-01 -- three installments for a loan that did not exist.
Each debited the cash projection immediately ($3,220.92 total on the fixture's
10-period window; production materializes ~52), and each became an FU-5 money
erasure the moment it settled: the fold splits a pre-origination payment against
a ZERO balance, so it books $0.00 principal and routes the whole payment to a
Refund Receivable, then the origination anchor resets the balance over it.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AcctTypeEnum, RecurrencePatternEnum
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.services import (
    balance_at,
    loan_loaders,
    loan_recurrence_sync,
    rate_period_engine,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    make_transfer_template,
)


# The suite's frozen today is 2026-03-20, so a loan originating 2026-04-15 is
# configured BEFORE it closes -- the shape the bound exists for.
ORIGINATION = date(2026, 4, 15)
PRINCIPAL = Decimal("200000.00")


def _upcoming_mortgage(seed_user, db_session, periods, payment_day=1):
    """A mortgage that has not closed yet (origination after the frozen today)."""
    return create_loan_account(
        seed_user, db_session, name="Closing In April",
        principal=PRINCIPAL, rate=Decimal("0.05000"),
        term=360, origination_date=ORIGINATION, payment_day=payment_day,
        account_type=AcctTypeEnum.MORTGAGE,
    )


class TestFirstInstallmentDate:
    """The derivation itself: the loan engine's convention, not a calendar guess."""

    @pytest.mark.parametrize("origination, payment_day, expected", [
        # One month after origination, ON payment_day -- NOT the next
        # payment_day, which would be 2026-04-20 for the second case.
        (date(2026, 4, 15), 1, date(2026, 5, 1)),
        (date(2026, 4, 15), 20, date(2026, 5, 20)),
        (date(2026, 4, 15), 15, date(2026, 5, 15)),
        (date(2026, 4, 1), 1, date(2026, 5, 1)),
        # Year rollover.
        (date(2026, 12, 10), 5, date(2027, 1, 5)),
        # Day clamped to a short month (31 -> Feb 28 in a non-leap year).
        (date(2026, 1, 20), 31, date(2026, 2, 28)),
        (date(2028, 1, 20), 31, date(2028, 2, 29)),
    ])
    def test_the_convention(self, origination, payment_day, expected):
        """The first installment is the payment_day of the month AFTER origination."""
        assert rate_period_engine.first_installment_date(
            origination, payment_day,
        ) == expected

    def test_it_is_not_the_next_payment_day(self):
        """The distinction from ``monthly_due_date``, which answers a DIFFERENT question.

        ``monthly_due_date`` returns the first ``payment_day`` ON OR AFTER a date
        (the installment a pay period contains).  For a loan originating
        2026-04-15 with payment_day 20 that is 2026-04-20 -- five days after
        closing, and NOT what the loan bills.  Sourcing the bound from the wrong
        one of these two would admit an installment the engine never schedules.
        """
        assert rate_period_engine.monthly_due_date(
            date(2026, 4, 15), 20,
        ) == date(2026, 4, 20)
        assert rate_period_engine.first_installment_date(
            date(2026, 4, 15), 20,
        ) == date(2026, 5, 20)

    @pytest.mark.parametrize("origination, payment_day", [
        (date(2026, 4, 15), 1),
        (date(2026, 4, 15), 20),
        (date(2018, 12, 1), 1),
        (date(2023, 2, 14), 22),
        (date(2026, 12, 10), 5),
        (date(2026, 1, 20), 31),
    ])
    def test_it_equals_the_engine_schedule_row_zero(
        self, app, db, seed_user, seed_periods, origination, payment_day,
    ):
        """The DRY proof: the cheap derivation == the engine's own first row.

        ``first_installment_date`` exists so the bound does not have to build a
        360-row schedule (and load a rate feed) to learn one date.  That is only
        legitimate while the two agree, so this pins them against each other
        across shapes rather than asserting the claim in a docstring.  If the
        resolver ever changes its first-payment convention, this goes red.
        """
        with app.app_context():
            acct = create_loan_account(
                seed_user, db.session, name=f"L{origination}{payment_day}",
                principal=PRINCIPAL, rate=Decimal("0.05000"), term=360,
                origination_date=origination, payment_day=payment_day,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            params = loan_loaders.load_loan_params(acct.id)
            rows = contractual_schedule_from_origination(
                params, loan_loaders.load_rate_changes(acct.id),
            )
            assert rows[0].payment_date == rate_period_engine.first_installment_date(
                origination, payment_day,
            )


class TestStartBoundIsSynced:
    """``sync_recurring_payment_bounds`` writes the start bound from the loan."""

    def test_creating_the_payment_bounds_the_rule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The create-transfer route leaves the rule bounded at both ends."""
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302

        xfer = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        )
        rule = xfer.template.recurrence_rule
        # One month after the 2026-04-15 closing, on payment day 1.
        assert rule.start_date == date(2026, 5, 1)
        assert rule.end_date is not None

    def test_a_payment_day_edit_moves_the_bound_and_the_billing_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A ``payment_day`` edit must move BOTH derived values, together.

        ``start_date`` and ``rule.day_of_month`` both derive from
        ``params.payment_day``, and moving one without the other is worse than
        leaving both stale.  Measured on the real rule shape when only
        ``start_date`` moved (payment_day 1 -> 20): the bound advanced to the
        20th while the rule still matched the 1st, no surviving period contained
        a matching day, and regeneration produced **ZERO** installments -- the
        loan's entire recurring payment silently disappeared.

        This must use the loan route's real MONTHLY rule: an EVERY_PERIOD
        template carries no ``day_of_month``, so it cannot observe the
        divergence at all.

        NEGATIVE CONTROL: drop the ``day_of_month`` assignment from
        ``_sync_loan_cadence`` and the regenerated due-date list goes empty.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        template = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        ).template
        rule = template.recurrence_rule
        assert rule.start_date == date(2026, 5, 1)
        assert rule.day_of_month == 1

        params = loan_loaders.load_loan_params(acct.id)
        params.payment_day = 20
        db.session.commit()

        with auth_client.application.app_context():
            loan_recurrence_sync.sync_recurring_payment_bounds(acct.id)
            db.session.commit()

        db.session.refresh(rule)
        assert rule.start_date == date(2026, 5, 20)
        assert rule.day_of_month == 20, (
            "day_of_month must follow payment_day, or the rule bills a day the "
            "bound excludes"
        )

        # And the pair still generates: the loan now bills the 20th, so its
        # first installment after the 2026-04-15 closing is 2026-05-20.
        with auth_client.application.app_context():
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
                effective_from=seed_periods[0].start_date,
            )
            db.session.commit()
        dues = [
            x.due_date for x in db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
        ]
        assert dues == [date(2026, 5, 20)]

    def test_a_day_less_rule_keeps_its_pay_period_cadence(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck loan payment is bounded but NOT re-dayed.

        A rule with no ``day_of_month`` schedules by pay period, so it has no
        contractual due day to keep in step; writing one would re-date every
        generated instance from the period start onto a monthly day. The start
        bound still applies -- it is about the loan's life, not its cadence.
        """
        with app.app_context():
            acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
            template = make_transfer_template(
                db.session, seed_user, to_account=acct,
            )
            db.session.commit()
            assert template.recurrence_rule.day_of_month is None

            loan_recurrence_sync.sync_recurring_payment_bounds(acct.id)

            assert template.recurrence_rule.start_date == date(2026, 5, 1)
            assert template.recurrence_rule.day_of_month is None

    def test_the_start_bound_survives_a_missing_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """C8e's lesson: the start bound is not scenario-scoped, so it must not
        sit behind the scenario guard.

        The END bound is a fold over the forward plan and genuinely needs a
        baseline scenario; the START bound is ``origination_date`` +
        ``payment_day`` and needs nothing.  Deriving them in one pass makes it
        easy to strand the start behind the end's guard -- which would leave a
        loan configured while the baseline was gone (finding G1: a real,
        repairable state) generating payments from the beginning of time.

        NEGATIVE CONTROL: move the ``_sync_start_date`` call below the
        ``ctx.scenario is None`` return in ``sync_recurring_payment_bounds`` and
        this goes red.
        """
        with app.app_context():
            acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
            template = make_transfer_template(
                db.session, seed_user, to_account=acct,
            )
            db.session.commit()

            # Delete the baseline scenario the loan resolves against.
            db.session.query(Scenario).filter(
                Scenario.id == seed_user["scenario"].id,
            ).delete()
            db.session.commit()

            loan_recurrence_sync.sync_recurring_payment_bounds(acct.id)
            assert template.recurrence_rule.start_date == date(2026, 5, 1)


class TestNoPaymentGeneratesBeforeTheLoan:
    """The behaviour the bound exists for, on the route that produced the defect."""

    def test_the_route_generates_no_pre_origination_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Every generated installment falls after the loan originates.

        Pre-C9a this generated 3 pre-origination payments out of 4 (due
        2026-02-01 / 03-01 / 04-01 against a 2026-04-15 closing).

        NEGATIVE CONTROL: drop the ``rule.start_date`` filter from
        ``recurrence_engine.match_periods`` and this goes red with those three.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert xfers, "the route must still generate the payments that ARE due"
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]
        for xfer in xfers:
            assert xfer.due_date > ORIGINATION

    def test_the_cash_projection_is_unmoved_by_a_loan_that_has_not_closed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setting up the payment cannot debit checking before the loan exists.

        The user-visible half of the defect: the phantom installments were
        PROJECTED expenses on the funding account, in periods already past, so
        the checking projection read $3,220.92 low across the window (period 6
        went 1000.00 -> -2220.92) for a mortgage that had not closed.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        with auth_client.application.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.balance_map(checking, bctx, seed_periods)

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        with auth_client.application.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            after = balance_at.balance_map(checking, bctx, seed_periods)

        # Every period whose payments are all pre-origination is untouched; the
        # first real installment (2026-05-01, period 8) is the first debit.
        for period in seed_periods:
            if period.end_date < date(2026, 5, 1):
                assert after[period.id] == before[period.id], (
                    f"period {period.period_index} "
                    f"({period.start_date}..{period.end_date}) moved before "
                    f"the loan's first installment"
                )

    def test_a_regeneration_cannot_reintroduce_them(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bound holds on the path ``start_period_id`` does NOT.

        ``transfer_recurrence.regenerate_for_template`` passes its own
        ``effective_from``, which suppresses the rule's ``start_period_id``
        entirely -- so a bound expressed there would be silently discarded and
        the phantom installments would come back on the next template edit.
        ``start_date`` is filtered in ``match_periods`` instead, which no caller
        can bypass.

        NEGATIVE CONTROL: move the ``start_date`` filter out of
        ``match_periods`` and into ``resolve_generation_plan``'s
        ``effective_from`` defaulting and this goes red.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        template = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        ).template

        with auth_client.application.app_context():
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, seed_periods), seed_user["scenario"].id,
                effective_from=seed_periods[0].start_date,
            )
            db.session.commit()

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .all()
        )
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]

    def test_the_generic_transfers_route_bounds_a_loan_destination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan payment set up through /transfers is bounded too.

        The transfer form offers every active account as a destination, so a
        loan payment can be created here as readily as on the loan page -- but
        this route builds its rule from the FORM, so nothing ever handed it the
        loan's ``start_date``.  Measured unbounded: 3 pre-origination
        installments (2026-02-01 / 03-01 / 04-01), byte-for-byte the defect the
        loan route produced, on a path ``sync_recurring_payment_bounds`` never
        touches because it runs at loan mutations, not transfer creations.

        NEGATIVE CONTROL: remove the ``bind_rule_to_loan`` call from
        ``_materialize_initial_transfers`` and those three come back.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Debt", item_name="Mortgage",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
        resp = auth_client.post("/transfers", data={
            "name": "Manual Mortgage",
            "from_account_id": str(checking.id),
            "to_account_id": str(acct.id),
            "default_amount": "1200.00",
            "category_id": str(category.id),
            "recurrence_pattern": str(monthly_id),
            "day_of_month": "1",
        })
        assert resp.status_code == 302

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]
        assert xfers[0].template.recurrence_rule.start_date == date(2026, 5, 1)

    def test_a_non_loan_destination_is_left_unbounded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bind is a no-op for every non-loan destination.

        ``bind_rule_to_loan`` is called unconditionally on the generic route, so
        it must not invent a bound for an ordinary savings transfer -- which
        would clip the user's own schedule.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Sav",
            anchor_balance=Decimal("100.00"),
        )
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Save", item_name="Rainy Day",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
        auth_client.post("/transfers", data={
            "name": "To Savings",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "default_amount": "100.00",
            "category_id": str(category.id),
            "recurrence_pattern": str(monthly_id),
            "day_of_month": "1",
        })

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == savings.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert xfers
        assert xfers[0].template.recurrence_rule.start_date is None
        # Unbounded, so the whole window generates -- nothing clipped.
        assert xfers[0].due_date == date(2026, 2, 1)

    def test_an_already_originated_loan_generates_normally(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bound must not clip a normal, long-originated loan.

        The real loans originated 2018 and 2023, so their bounds fall far before
        any materialized period and must exclude nothing.  Without this, a fix
        that over-clipped would look green on the upcoming-loan tests alone.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Old Mortgage",
            principal=PRINCIPAL, rate=Decimal("0.05000"), term=360,
            origination_date=date(2018, 12, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        # One installment per month across the seeded window -- nothing clipped.
        # Feb is the first: periods start 2026-01-02, so no seeded period
        # contains the 2026-01-01 installment.  Nothing is clipped by the
        # bound (2019-01-01) -- every installment the window can hold is here.
        assert [x.due_date for x in xfers] == [
            date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ]
