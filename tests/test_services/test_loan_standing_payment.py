"""R7d-a: a loan's forward plan prices from its own DEFINITION, not the contract.

Plan step **R7d-a** (``docs/plans/implementation_plan_recurrence_redesign.md``).
``balance_at._plan``'s ESTIMATED tier fills every future installment no
materialised row covers.  It used to fill it with the CONTRACT's P&I whatever
the loan's own recurring payment said it would pay, and that made a loan's
payoff a function of which rows happened to have been WRITTEN -- the loop R7d
has to break before it can stop storing the recurrence's closing bound.

Three properties are pinned here:

* :func:`~app.services.recurring_transfer_query.standing_payment` reads what the
  definition says, and tells "no definition" from "a definition saying zero";
* :func:`~app.services.recurring_transfer_query.standing_installment_cash` is total
  over the three shapes a loan can be in, with the arithmetic stated;
* **the payoff does not move when the future rows are deleted** -- the invariant
  the whole step exists to establish, exercised through the production door
  (``pay_period_admin.regenerate_pay_periods``) that presents that state.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.services import (
    balance_at, loan_loaders, pay_period_write, template_amount_service,
)
from app.services import transfer_recurrence
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.balance_at import BalanceContext
from app.services.balance_at._plan import loan_plan
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.services.recurring_transfer_query import (
    StandingPayment,
    standing_installment_cash,
    standing_payment,
)
from app.services.template_amount_service import owns_its_amount
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import MONTHLY

# A short amortizing loan whose whole contractual schedule is enumerable:
# $12,000 at 6% over 6 months, originated 2026-01-01, due on the 1st.  Read at
# 2026-04-15, so 05-01 / 06-01 / 07-01 are the FUTURE installments the ESTIMATED
# tier fills and 02-01 / 03-01 / 04-01 are past (and, with no record, pay
# nothing -- the B-9 fix).
_PRINCIPAL = Decimal("12000.00")
_RATE = Decimal("0.06")
_TERM = 6
_ORIGINATION = date(2026, 1, 1)
_AS_OF = date(2026, 4, 15)


def _loan(seed_user, escrow_annual=None):
    """Create the controlled short loan, optionally escrowing, and its context."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    if escrow_annual is not None:
        params = loan_params_for(db.session, account.id)
        add_escrow_line(
            db.session, account.id, "Property Tax", escrow_annual,
            effective_date=params.origination_date,
        )
        db.session.flush()
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    return account, ctx


def _recurring_payment(seed_user, loan, base, *, derive=None, extra=None,
                       stated=False, effective_on=None):
    """Attach an active recurring payment paying *base* into *loan*.

    Mirrors what ``routes/loan/payment_transfer.py`` writes: the definition
    first, its ``loan_payment_settings`` row when the test needs a MODE, and the
    cadence onto it last (plan step R-F6).

    *stated* opens the price SERIES through ``template_amount_service.set_amount``,
    the one write door, which is what makes the definition one that STATES its
    price -- ``default_amount`` alone is a scalar the amount model refuses to
    read as a price for a date.  *effective_on* dates that statement; it defaults
    to the loan's origination so the series covers every installment.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name="Loan Payment",
        default_amount=base,
    )
    if derive is not None or extra is not None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=bool(derive),
            extra_principal=Decimal("0.00") if extra is None else extra,
        )
    db.session.add(template)
    db.session.flush()
    if stated:
        template_amount_service.set_amount(
            template, base,
            effective_on=_ORIGINATION if effective_on is None else effective_on,
        )
    make_cadence_rule(template, MONTHLY, fires_on_day=1)
    db.session.flush()
    return template


def _contractual_pi(account):
    """Return the contractual P&I of every installment on the test loan.

    The fixture is fixed-rate, so every row carries the same figure; the test
    asserts against it rather than hardcoding a level payment the amortization
    engine derives.
    """
    rows = contractual_schedule_from_origination(
        account.loan_params, loan_loaders.load_rate_changes(account.id),
    )
    return rows[0].payment


def _standing(template, extra="0.00"):
    """A :class:`StandingPayment` over *template*, as the producer builds one."""
    return StandingPayment(
        template=template, extra_principal=Decimal(extra),
    )


class TestStandingPayment:
    """What :func:`standing_payment` reads off a loan's own definition."""

    def test_a_loan_with_no_recurring_payment_has_no_standing_payment(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """No active recurring payment -> ``None``, not a zeroed value.

        The distinction is load-bearing: ``None`` means the contract is the only
        estimate there is, where a definition stating ``0.00`` would mean the
        owner plans to pay nothing.
        """
        account, _ = _loan(seed_user)

        assert standing_payment(account.id, seed_user["user"].id) is None

    def test_it_reads_the_stated_base_when_there_is_no_settings_row(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A definition with no settings row STATES its base and derives nothing.

        This is the shape both of the developer's live loans are in
        (``budget.loan_payment_settings`` holds 0 rows on production).
        """
        account, _ = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )

        standing = standing_payment(account.id, seed_user["user"].id)

        assert standing == StandingPayment(
            template=template, extra_principal=Decimal("0.00"),
        )
        # The PRICE is not on the value: it resolves per installment.
        assert standing_installment_cash(
            standing, Decimal("1293.96"), Decimal("616.99"), _AS_OF,
        ) == Decimal("1910.95")

    def test_it_reads_the_mode_and_the_extra_from_the_settings_row(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A DERIVE-mode payment with a standing extra reports both."""
        account, _ = _loan(seed_user)
        _recurring_payment(
            seed_user, account, Decimal("1.00"),
            derive=True, extra=Decimal("250.00"),
        )

        standing = standing_payment(account.id, seed_user["user"].id)

        assert standing.extra_principal == Decimal("250.00")
        # The MODE is read off the template rather than copied onto the value,
        # so a template switched between modes cannot leave a stale flag behind.
        assert owns_its_amount(standing.template) is False


class TestStandingInstallmentCash:
    """The pricing rule, over each of the shapes a loan can be in."""

    def test_no_standing_payment_costs_the_contract_plus_its_escrow(self):
        """``None`` -> P&I + escrow.  Nothing else is known about the loan."""
        # 1293.96 + 616.99 = 1910.95
        assert standing_installment_cash(
            None, Decimal("1293.96"), Decimal("616.99"), _AS_OF,
        ) == Decimal("1910.95")

    def test_a_derived_price_costs_the_contract_plus_escrow_plus_the_extra(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A definition that STATES no price -> P&I + escrow + extra.

        That is what DERIVE mode means, so reading the contract here is the
        row's own rule rather than a guess about it.  The stored scalar
        (``$1.00``) is deliberately absurd and must not be read.
        """
        account, _ = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("1.00"),
            derive=True, extra=Decimal("250.00"),
        )

        # 1293.96 + 616.99 + 250.00 = 2160.95
        assert standing_installment_cash(
            _standing(template, "250.00"),
            Decimal("1293.96"), Decimal("616.99"), _AS_OF,
        ) == Decimal("2160.95")

    @pytest.mark.parametrize("escrow", [Decimal("0.00"), Decimal("616.99")])
    def test_a_stated_price_costs_that_price_plus_the_extra(
        self, seed_user, db, escrow,  # pylint: disable=unused-argument
    ):
        """A STATED price -> the owner's figure + extra, never the servicer's.

        **Swept over the ESCROW because a version of this test that passed only
        ``0.00`` could not see the arm's one real trap.** A stated price is
        escrow-INCLUSIVE -- an owner types the whole mortgage payment, and the
        developer's own template states ``$1,910.95`` for a ``$1,293.96`` P&I
        and a ``$616.99`` escrow -- so the escrow argument must NOT be added on
        top of it; it is what ``split_payment_cash`` backs out of principal.
        With ``escrow=0.00`` alone the two spellings agree, and the case that
        distinguishes them is the only one that matters.
        """
        account, _ = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("300.00"), stated=True,
        )

        # 300.00 + 25.00 = 325.00 whatever the escrow, against a 531.94
        # contractual installment the stated price overrides.
        assert standing_installment_cash(
            _standing(template, "25.00"), Decimal("531.94"), escrow, _AS_OF,
        ) == Decimal("325.00")

    def test_a_stated_price_is_resolved_AS_OF_the_installment(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A price stated for a FUTURE date must not reach earlier installments.

        **The defect an adversarial review of this step found.** The first cut
        read ``template.default_amount``, which
        ``template_amount_service._resync_scalar`` puts on the NEWEST price the
        series states rather than the price on a date -- so a rise stated as
        effective in 2027 priced every 2026 installment at the 2027 figure.
        Measured on a production clone before the fix: an owner stating
        ``$700.00`` effective 2028-01-01 moved the Van Loan's derived payoff six
        installments EARLY once its future rows were absent.
        """
        account, _ = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("300.00"), stated=True,
        )
        template_amount_service.set_amount(
            template, Decimal("700.00"), effective_on=date(2026, 6, 1),
        )
        db.session.flush()
        # The scalar has moved to the NEWEST price; the series has not.
        assert template.default_amount == Decimal("700.00")

        standing = _standing(template)
        assert standing_installment_cash(
            standing, Decimal("531.94"), Decimal("0.00"), date(2026, 5, 1),
        ) == Decimal("300.00")
        assert standing_installment_cash(
            standing, Decimal("531.94"), Decimal("0.00"), date(2026, 6, 1),
        ) == Decimal("700.00")


class TestEstimatedTierPricing:
    """What :func:`loan_plan` synthesizes for an installment no row covers."""

    def test_an_estimated_installment_takes_the_stated_base_over_the_contract(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A definition paying UNDER contract is modelled at what it pays.

        Before plan step R7d-a every ESTIMATED slot carried the contract's P&I,
        so a loan the owner is underpaying was projected to pay itself off on
        the servicer's schedule.
        """
        account, ctx = _loan(seed_user)
        under = _contractual_pi(account) - Decimal("500.00")
        _recurring_payment(seed_user, account, under, stated=True)

        plan = loan_plan(account, ctx)

        estimated = [payment for payment in plan if payment.is_estimated]
        assert estimated, "the loan has no rows, so every slot is ESTIMATED"
        assert {payment.cash for payment in estimated} == {under}

    def test_an_estimated_installment_states_its_own_escrow_beside_the_cash(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The (cash, escrow) pair is the one ``split_payment_cash`` takes.

        A stated base is escrow-inclusive, so a tier calling its escrow ``0.00``
        would route the escrow into principal and pay the loan down by it every
        month.  $7,403.88 a year is $616.99 a month.
        """
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )

        plan = loan_plan(account, ctx)

        estimated = [payment for payment in plan if payment.is_estimated]
        assert estimated
        for payment in estimated:
            assert payment.cash == Decimal("1910.95")
            assert payment.escrow == Decimal("616.99")

    def test_a_derived_installment_takes_the_contract_plus_escrow_and_extra(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """DERIVE mode keeps reading the contract -- that is what it means."""
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        _recurring_payment(
            seed_user, account, Decimal("1.00"),
            derive=True, extra=Decimal("100.00"),
        )
        contractual_pi = _contractual_pi(account)

        plan = loan_plan(account, ctx)

        estimated = [payment for payment in plan if payment.is_estimated]
        assert estimated
        # The stored 1.00 base is NOT read; the contract's P&I is.
        expected = contractual_pi + Decimal("616.99") + Decimal("100.00")
        assert estimated[0].cash == expected

    def test_a_loan_with_no_recurring_payment_still_takes_the_contract(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """No definition -> the contract's P&I plus escrow, as before the step.

        The regression guard for the case R7d-a must NOT move: a loan the owner
        pays by hand has no stated figure, so the contract is the estimate.
        """
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        contractual_pi = _contractual_pi(account)

        plan = loan_plan(account, ctx)

        estimated = [payment for payment in plan if payment.is_estimated]
        assert estimated
        assert estimated[0].cash == contractual_pi + Decimal("616.99")
        assert estimated[0].escrow == Decimal("616.99")


class TestPayoffDoesNotMoveWithMaterialisation:
    """The invariant plan step R7d-a exists to establish.

    A loan's projected payoff must be the same whether the rows its recurrence
    will generate have been WRITTEN yet or not.  Without it the recurrence's
    closing bound -- which is derived from that payoff -- reads one date at the
    moment generation resolves it and another afterwards, and the difference is
    payments the owner owes that are never budgeted.

    **The property is about FUTURE installments, and that bound is deliberate.**
    A row for an installment already due pays the loan down when it exists and
    is not re-synthesized when it does not, because the ESTIMATED tier never
    fills a past slot -- an overdue installment with no record pays nothing
    (finding B-9, the retired forward walk that amortized `-$15,755.38` per
    period).  So removing a PAST row legitimately moves the payoff later, and
    ``pay_period_admin.regenerate_pay_periods`` -- the door that presents this
    state -- only ever deletes periods that have not started.
    """

    def _materialised(self, seed_user, base_delta):
        """Build the loan, a payment *base_delta* off contract, and GENERATE its rows.

        Returns ``(account, ctx, future_row_ids)``.  The row ids are the test's
        positive control: a fixture that generated nothing would make the
        deletion below a no-op and the assertion a tautology, which is exactly
        how this test read before it was measured against a planted regression.
        """
        account, ctx = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, _contractual_pi(account) + base_delta,
            stated=True,
        )
        # A calendar reaching past the loan's last contractual installment
        # (2026-07-01), so the rule genuinely names every future slot.
        periods = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=date(2026, 1, 2),
            num_periods=20,
            cadence_days=14,
        )
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                calendar_for(template.user_id), {p.id for p in periods},
            ),
            seed_user["scenario"].id,
        )
        db.session.flush()
        future = (
            db.session.query(Transfer.id)
            .filter(
                Transfer.to_account_id == account.id,
                Transfer.due_date >= _AS_OF,
            )
            .all()
        )
        future_ids = [row[0] for row in future]
        assert future_ids, (
            "the fixture must generate rows for the loan's FUTURE installments "
            "-- with none, deleting them proves nothing"
        )
        return account, ctx, future_ids

    def _delete(self, row_ids):
        """Delete *row_ids* and their shadows, as a pay-period CASCADE would."""
        db.session.execute(db.text(
            "DELETE FROM budget.transactions WHERE transfer_id = ANY(:ids)"
        ), {"ids": row_ids})
        db.session.execute(db.text(
            "DELETE FROM budget.transfers WHERE id = ANY(:ids)"
        ), {"ids": row_ids})
        db.session.expire_all()

    # Both non-zero deltas are sized so the FUTURE rows are what decides the
    # payoff, measured against a planted regression rather than reasoned about.
    # Too small and the difference rounds inside one installment; too large --
    # ``+2500.00`` was tried -- and the three ALREADY-DUE rows clear the loan on
    # their own, so deleting the future ones changes nothing and the case agrees
    # for the wrong reason.  ``0.00`` is the control, and it is the shape both of
    # the developer's live loans are in: a definition stating exactly what the
    # contract asks, where no pricing rule can make a difference.
    @pytest.mark.parametrize("base_delta", [
        Decimal("-500.00"),    # the definition pays UNDER contract
        Decimal("0.00"),       # the definition pays the contract exactly
        Decimal("1200.00"),    # the definition pays OVER contract
    ])
    def test_the_payoff_is_the_same_with_the_future_rows_absent(
        self, seed_user, db, base_delta,  # pylint: disable=unused-argument
    ):
        """Deleting every FUTURE payment row must not move the payoff.

        The state is not hypothetical: ``pay_period_admin.regenerate_pay_periods``
        deletes the rebuildable tail of pay periods and repopulates, and
        ``budget.transfers.pay_period_id`` CASCADEs -- so generation resolves
        the loan's closing bound against exactly this.  Measured on a production
        clone before this step: a Van payment standing at `$300.00` answered
        `2029-02-22` at that moment against `2030-02-22` before and after --
        twelve installments the owner owes, never generated.
        """
        account, ctx, future_ids = self._materialised(seed_user, base_delta)

        with_rows = balance_at.loan_payoff_date(account, ctx)
        self._delete(future_ids)
        without_rows = balance_at.loan_payoff_date(
            account, BalanceContext.build(seed_user["user"].id, _AS_OF),
        )

        assert without_rows == with_rows

    def test_the_payoff_is_the_same_however_far_the_horizon_reaches(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """An underpaying loan is modelled at its own figure for its WHOLE life.

        **This varies the HORIZON, which is the property its name states.** An
        earlier version deleted rows instead, making it a byte-copy of the case
        above; an adversarial review caught that the stated property was
        untested. The forward plan used to switch figures at the materialised
        horizon -- the owner's inside it, the servicer's past it -- so extending
        the pay calendar moved a payoff that nothing about the loan had changed.
        Measured on a production clone before the fix: `2030-02-22` with rows to
        2028-07 and `2030-04-22` with rows to 2029-01.
        """
        account, ctx = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, _contractual_pi(account) - Decimal("500.00"),
            stated=True,
        )
        # A SHORT calendar, but one already covering every ALREADY-DUE
        # installment (Feb / Mar / Apr, read at 2026-04-15).  That bound is the
        # test's subject: a past slot with no row pays nothing and the ESTIMATED
        # tier never refills it (finding B-9's fix, recorded as D46), so a
        # calendar that grew across ``as_of`` would move the payoff for that
        # reason instead of the one this test is about.  What the extension
        # below adds is FUTURE slots only.
        short = self._generate(seed_user, template, num_periods=8)
        near_horizon = balance_at.loan_payoff_date(account, ctx)

        # Now extend it well past that installment, so most of the plan is
        # PLANNED rows instead.  Nothing about the LOAN has changed.
        self._generate(
            seed_user, template, num_periods=20,
            first_payday=short[-1].start_date + timedelta(days=14),
        )
        far_horizon = balance_at.loan_payoff_date(
            account, BalanceContext.build(seed_user["user"].id, _AS_OF),
        )

        assert far_horizon == near_horizon

    def _generate(self, seed_user, template, num_periods, first_payday=None):
        """Create *num_periods* paydays and generate *template*'s rows into them."""
        periods = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=(
                date(2026, 1, 2) if first_payday is None else first_payday
            ),
            num_periods=num_periods,
            cadence_days=14,
        )
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                calendar_for(template.user_id), {p.id for p in periods},
            ),
            seed_user["scenario"].id,
        )
        db.session.flush()
        return periods


class TestTheEscrowPairingFoldsToTheSameBalance:
    """The WASH claim, as behaviour rather than as two field assertions.

    Plan step R7d-a made the ESTIMATED tier state an installment's real escrow
    beside its cash where it used to pass ``0.00`` beside an escrow-free
    contractual P&I.  ``split_payment_cash`` computes
    ``principal = cash - interest - escrow``, so adding the escrow to BOTH sides
    moves no balance -- and until this class that claim was pinned only by
    asserting the two FIELDS.  A reader who "restored" ``escrow=0.00`` while the
    cash stayed escrow-inclusive would pay a mortgage down by its whole escrow
    every month, and an adversarial review measured the entire balance, payoff,
    interest and equity suite staying green under exactly that mutation.
    """

    def test_an_escrowing_loan_folds_like_its_escrow_free_twin(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """Adding escrow to a contract-priced loan moves NO projected figure.

        Two identical loans, one escrowing `$7,403.88` a year (`$616.99` a
        month) and one escrowing nothing, both with no recurring payment so the
        whole plan is ESTIMATED.  The escrow is collected and disbursed by the
        servicer; it pays no principal, so the payoff and every projected
        balance must be identical.  Under the mutation this class exists for,
        the escrowing loan pays down `$616.99` a month faster.
        """
        escrowing, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        bare = create_loan_account(
            seed_user, db.session, name="No Escrow",
            principal=_PRINCIPAL, rate=_RATE, term=_TERM,
            origination_date=_ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        dates = [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)]
        assert balance_at.positions(escrowing, ctx, dates) == \
            balance_at.positions(bare, ctx, dates)
        assert balance_at.loan_payoff_date(escrowing, ctx) == \
            balance_at.loan_payoff_date(bare, ctx)
