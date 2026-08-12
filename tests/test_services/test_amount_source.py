"""
Shekel Budget App -- Where one row's amount comes from (plan step X-au-b)

The total dispatch of :mod:`app.services.cash_ledger._amount_source`: ruling
**R-FI**'s five amount rules, the order that makes two of them subsets of two
others rather than rivals, and the refusal each one raises where its producer
cannot answer.

**Every fixture here gives the row's own stored column a figure NO rule may
answer**, and that is not decoration.  An adversarial review mutated the
resolver to ``return txn.estimated_amount`` -- deleting the dispatch, all five
rules and every refusal -- and the exhaustive production oracle beside this file
reported *997 rows, 0 mismatches, OK*, because for 946 of those rows the app's
own answer IS that column.  A fixture whose stored figure equals its derived one
cannot tell the two implementations apart, so none here does.  The same review
found three tests that passed under that mutation; each is now built from three
DISTINCT figures and says which one it means.

**The refusals are why most of this file exists.**  Zero rows on the 2026-08-12
production clone take any refusal arm, so production evidence cannot show that a
single one of them fires.  Every guard therefore carries its own control, built
to reach the arm and shown to raise, per ``docs/plans/verification.md``
standard 4.

The LOAN_PAYMENT rule is graded ONLY here: ``budget.loan_payment_settings`` is
empty on production, so the oracle prices ``$0.00`` through it and a seeded loan
is the only place it runs.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum, TxnTypeEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import income_service, template_amount_service
from app.services.cash_ledger import (
    AmountRule,
    amount_basis,
    amount_rule,
    live_amount_overrides,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.cash_ledger._amount_source import _RULE_ANSWERS
from tests._test_helpers import (
    add_escrow_line,
    add_txn,
    create_loan_account,
    create_savings_account,
    create_transfer,
    loan_params_for,
    make_salary_profile,
)


# The definition's price history, and the row dates that select from it.  TWO
# versions rather than one, deliberately: a single-version series answers the
# same figure on every day, so a test built on one cannot tell a resolver that
# reads DATES from one that ignores them -- nor the series from the
# ``default_amount`` scalar it replaced, which ``set_amount`` keeps equal to the
# NEWEST version.  Production has exactly one multi-version template of each
# kind, which is why this discrimination lives here rather than in the oracle.
_OLD_PRICE = Decimal("178.00")
_NEW_PRICE = Decimal("165.30")
_PRICE_ROSE_ON = date(2026, 1, 1)
_PRICE_FELL_ON = date(2026, 3, 1)
_DUE_UNDER_OLD_PRICE = date(2026, 2, 14)
_DUE_UNDER_NEW_PRICE = date(2026, 4, 14)
_DUE_BEFORE_THE_SERIES = date(2025, 11, 30)

# The figure every fixture stores on the row itself.  No rule may answer it, so
# any test that returns it has caught a resolver reading the column.
_NOT_AN_ANSWER = "999.99"


def _basis_for(seed_user, rows):
    """The AmountBasis for *rows*, built the way a caller must build it.

    Every test resolves against a real basis rather than an empty one: the
    resolver refuses a row the basis was not built over, and reaching a refusal
    by violating that contract would grade the contract instead of the rule an
    adversarial review pointed out.
    """
    return amount_basis(
        seed_user["account"], seed_user["scenario"].id, list(rows),
    )


def _resolve(seed_user, txn):
    """Resolve one row against a basis built over exactly that row."""
    return resolve_transaction_amount(txn, _basis_for(seed_user, [txn]))


def _priced_template(seed_user, name="Geico", txn_type=TxnTypeEnum.EXPENSE):
    """A transaction template whose series states two prices, two months apart."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
        default_amount=_OLD_PRICE,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, _OLD_PRICE, effective_on=_PRICE_ROSE_ON,
    )
    template_amount_service.set_amount(
        template, _NEW_PRICE, effective_on=_PRICE_FELL_ON,
    )
    db.session.flush()
    return template


def _seriesless_template(seed_user, name="Never Stated"):
    """A transaction template created around the write door, so its series is empty."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=Decimal("42.00"),
    )
    db.session.add(template)
    db.session.flush()
    return template


def _salary_template(seed_user, txn_type=TxnTypeEnum.INCOME):
    """A template an ACTIVE salary profile drives, and the profile."""
    template = _priced_template(
        seed_user, name="Data Manager", txn_type=txn_type,
    )
    profile = make_salary_profile(seed_user, db.session)
    profile.template = template
    db.session.flush()
    return template, profile


def _template_row(seed_user, period, template, **kwargs):
    """A generated row on *template*, due under the OLD price unless told otherwise."""
    kwargs.setdefault("due_date", _DUE_UNDER_OLD_PRICE)
    txn = add_txn(
        db.session, seed_user, period, template.name, _NOT_AN_ANSWER, **kwargs,
    )
    txn.template_id = template.id
    db.session.flush()
    return txn


def _shadow_of(xfer, *, income=False):
    """Return one shadow of *xfer* -- the expense leg by default."""
    wanted = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
    )
    return next(
        shadow for shadow in xfer.shadow_transactions
        if shadow.transaction_type_id == wanted
    )


def _generated_transfer(
    seed_user, period, to_account, *, due_date,
    series=_OLD_PRICE, later=_NEW_PRICE, stored=Decimal("111.11"),
):
    """A transfer carrying a template whose series states TWO prices.

    The parent's own ``amount`` column and both shadows' ``estimated_amount``
    are set to ``stored`` -- a figure no rule may answer -- so a resolver that
    reads either column is caught, which the review found neither transfer test
    doing.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="Money Market Contribution",
        default_amount=series,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, series, effective_on=_PRICE_ROSE_ON,
    )
    if later is not None:
        template_amount_service.set_amount(
            template, later, effective_on=_PRICE_FELL_ON,
        )
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], to_account, period,
        amount=stored, due_date=due_date,
    )
    xfer.transfer_template_id = template.id
    db.session.flush()
    return xfer, template


def _mortgage(seed_user, escrow_annual=Decimal("3600.00")):
    """A $200k / 6% / 360mo mortgage with an escrow line, paid on the 1st.

    P&I = amortize(200000, 0.06, 360) = 1,199.10; escrow = 3,600 / 12 = 300.00.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Live Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    params = loan_params_for(db.session, loan.id)
    add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )
    return loan


def _loan_payment(
    seed_user, period, *, derive, extra=None, to_account=None,
    series=Decimal("1300.00"), stored=Decimal("1250.00"),
):
    """A mortgage payment transfer in one of its two modes, and its rows.

    Three DISTINCT figures by default -- the definition states ``$1,300.00``,
    the parent transfer's column holds ``$1,250.00`` and each shadow's holds
    ``$1,200.00`` -- so a manual-mode assertion names which of the three it
    means.  The review found the earlier fixture setting all three to one
    number, which made the test pass for any of the three implementations.

    Returns ``(shadow, rows)``: the checking-side expense shadow, and both
    shadows, which is what a basis is built over.
    """
    loan = _mortgage(seed_user) if to_account is None else to_account
    xfer, template = _generated_transfer(
        seed_user, period, loan, due_date=date(2026, 2, 1),
        series=series, later=None, stored=stored,
    )
    settings = LoanPaymentSettings(derive_from_loan=derive)
    if extra is not None:
        settings.extra_principal = extra
    template.settings = settings
    shadows = list(xfer.shadow_transactions)
    for shadow in shadows:
        shadow.estimated_amount = Decimal("1200.00")
    db.session.flush()
    return _shadow_of(xfer), shadows


class TestTheDispatchIsTotal:
    """Every rule has an answer, and the enum is the list of rules."""

    def test_every_amount_rule_has_an_answer(self):
        """The dispatch table covers the enum exactly -- no rule, no orphan.

        The predicate behind the module's "TOTAL dispatch" claim.  A member
        added to :class:`AmountRule` without an answer would raise a ``KeyError``
        at the lookup rather than silently taking whichever branch happened to
        be last, and this is what says so before a row does.
        """
        assert set(_RULE_ANSWERS) == set(AmountRule)


class TestWhichRulePricesARow:
    """The classification, and the order two of its arms depend on."""

    def test_a_row_with_no_links_owns_its_amount(self, app, db, seed_user, seed_periods):
        """An ad-hoc row states its own figure: nothing else can."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert amount_rule(txn) is AmountRule.OWN

    def test_a_cc_payback_owns_its_amount(self, app, db, seed_user, seed_periods):
        """A payback carries NEITHER link, so today's rules can only call it OWN.

        The kind ruling R-FI names and this step does not move: its figure is
        DERIVED (the sum of the source row's credit entries) and stored, with no
        link for a discriminator to key on -- which is why R-FI's discriminator
        is an explicit column and why finding **N-243** stays open.  21 such rows
        on the production clone, all classified here.
        """
        source = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "500.00",
        )
        payback = add_txn(
            db.session, seed_user, seed_periods[1], "CC Payback: Groceries",
            "123.18",
        )
        payback.credit_payback_for_id = source.id
        db.session.flush()
        assert amount_rule(payback) is AmountRule.OWN

    def test_a_template_row_is_priced_by_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """A generated row's price comes from the template's series."""
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.TEMPLATE

    def test_a_salary_row_beats_the_template_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """SALARY is tested first, because a profile names an ordinary template.

        The precedence control: this row satisfies BOTH arms -- it has a
        template, and that template is salary-linked -- so a dispatch that
        tested TEMPLATE first would price every paycheck off a price series
        nobody ever stated.
        """
        template, _profile = _salary_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template, is_income=True)
        assert amount_rule(txn) is AmountRule.SALARY

    def test_a_loan_payment_shadow_beats_the_transfer_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """LOAN_PAYMENT is tested first, because a loan payment IS a transfer.

        The second precedence control, and its inverse is the defect plan step
        X-au-f's specification names: a loan-payment shadow that placed as a
        plain transfer would be priced from its parent instead of from the loan.
        """
        shadow, _rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        assert amount_rule(shadow) is AmountRule.LOAN_PAYMENT

    def test_a_generic_transfer_shadow_is_priced_by_its_parent(
        self, app, db, seed_user, seed_periods,
    ):
        """A shadow with no loan settings behind it takes the transfer rule."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        assert amount_rule(_shadow_of(xfer)) is AmountRule.TRANSFER

    def test_an_overridden_row_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A re-priced row keeps its figure however it was generated.

        Worth ``$120.00`` on the production clone: only 3 of its 49 override
        rows are still Projected, and deleting this arm re-prices two of them
        (the *Electricity* rows, hand-raised against a series that says
        ``$300.00``).  The other 46 reach OWN through the status arm anyway.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        txn.is_override = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.OWN

    def test_an_overridden_shadow_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """OWN beats TRANSFER too -- 12 such rows exist on the production clone."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        shadow = _shadow_of(xfer)
        shadow.is_override = True
        shadow.estimated_amount = Decimal("77.77")
        db.session.flush()
        assert amount_rule(shadow) is AmountRule.OWN
        assert _resolve(seed_user, shadow) == Decimal("77.77")

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.RECEIVED, StatusEnum.CREDIT,
        StatusEnum.CANCELLED, StatusEnum.SETTLED,
    ])
    def test_a_row_that_is_no_longer_projected_owns_its_amount(
        self, app, db, seed_user, seed_periods, status,
    ):
        """At settle the figure is FROZEN, and every non-Projected status keeps it.

        Not a stylistic choice: 66 of production's settled and excluded template
        rows carry a price their definition may since have left, and re-deriving
        them would rewrite history.  Parameterised over all five non-Projected
        statuses because the rule is "not Projected", not "settled" -- Credit and
        Cancelled are neither, and their stored figure is still a fact.  Each
        case RESOLVES as well as classifies, so the frozen figure is asserted
        rather than assumed.
        """
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template, status_enum=status,
        )
        assert amount_rule(txn) is AmountRule.OWN
        assert _resolve(seed_user, txn) == Decimal(_NOT_AN_ANSWER)

    def test_soft_deleting_a_row_does_not_change_which_rule_prices_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Deletion says whether a row counts, never who owns its figure.

        Making it flip the rule would force plan step X-au-c's ``amount_source``
        to be rewritten on every delete and restore -- a stored value beside a
        second writer, which is the shape this arc removes.  102 of production's
        330 transfer shadows are soft-deleted, so the arm is ordinary rather
        than theoretical.
        """
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template, is_deleted=True,
        )
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE


class TestWhatEachRuleAnswers:
    """The figure each rule produces, hand-computed."""

    def test_an_own_row_answers_its_stored_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """The OWN rule reads the column and nothing else."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert _resolve(seed_user, txn) == Decimal("35.00")

    def test_an_own_row_answers_the_ESTIMATE_not_an_entered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver answers the estimate half; a typed actual is a separate fact.

        ``effective_amount`` is ``actual ?? estimated``, and returning THAT here
        would make the resolver answer a human's realised figure where plan step
        X-au-c expects the budgeted one -- and would write it into the amount
        column at the freeze.  41 rows on the production clone carry an actual.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "500.00",
            status_enum=StatusEnum.DONE, actual_amount="462.34",
        )
        assert txn.effective_amount == Decimal("462.34")
        assert _resolve(seed_user, txn) == Decimal("500.00")

    def test_an_excluded_row_answers_its_amount_not_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled row contributes nothing and still HAS an amount.

        ``effective_amount`` answers ``0`` for it; the amount column does not,
        and at X-au-c a resolver returning zero here would write ``0.00`` into
        the column of every excluded row.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Gym", "40.00",
            status_enum=StatusEnum.CANCELLED,
        )
        assert txn.effective_amount == Decimal("0")
        assert _resolve(seed_user, txn) == Decimal("40.00")

    def test_a_template_row_answers_the_price_in_effect_on_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The series is resolved on the ROW's own due date, not on today.

        Due 2026-02-14 falls between the 2026-01-01 version (``$178.00``) and the
        2026-03-01 one (``$165.30``), so the answer is the older price -- which
        is also the answer the ``default_amount`` scalar CANNOT give, since
        ``set_amount`` keeps that column at the newest stated figure.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        assert template.default_amount == _NEW_PRICE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_later_row_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """One definition, two rows, two prices -- which is what a series is for."""
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template,
            due_date=_DUE_UNDER_NEW_PRICE,
        )
        assert _resolve(seed_user, txn) == _NEW_PRICE

    def test_a_row_older_than_the_series_holds_at_the_earliest_price(
        self, app, db, seed_user, seed_periods,
    ):
        """Before the first version the series holds FLAT (ruling R-I's shape).

        The arm that makes the resolver total for a row generated into a
        historical period; 23 rows on the production clone are due before their
        template's earliest version.
        """
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template,
            due_date=_DUE_BEFORE_THE_SERIES,
        )
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_salary_row_answers_the_live_net_and_not_the_stored_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The SALARY rule routes to the live recompute rather than the column.

        What is gradeable here is the ROUTING: there is no second producer of a
        net paycheck, so the assertion is that the rule returns what
        ``income_service.live_projected_net`` answers for this row and NOT the
        stored figure beside it.  The arithmetic is the paycheck engine's and is
        graded by its own suites.
        """
        template, _profile = _salary_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template, is_income=True)
        live = income_service.live_projected_net(
            seed_user["user"].id, seed_user["scenario"].id, [txn],
        )[txn.id]
        assert live != Decimal(_NOT_AN_ANSWER)
        assert _resolve(seed_user, txn) == live

    def test_a_transfer_shadow_answers_its_parents_RULE_not_either_column(
        self, app, db, seed_user, seed_periods,
    ):
        """Transfer Invariant 3 becomes structural: the shadow reads the parent's rule.

        Three distinct figures, and the assertion names the one that is right:
        the shadow's column says ``$9.99``, the PARENT's column says ``$111.11``,
        and the definition states ``$178.00``.  The review found the earlier
        version of this test setting all three to one number, so it passed for a
        resolver that read the shadow's own column, one that read the parent's,
        and one that resolved the parent's rule alike.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        shadow = _shadow_of(xfer)
        shadow.estimated_amount = Decimal("9.99")
        db.session.flush()
        assert xfer.amount == Decimal("111.11")
        assert _resolve(seed_user, shadow) == _OLD_PRICE

    def test_both_shadows_of_one_transfer_answer_the_same_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Invariant 3 for the PAIR, with the two legs stored at DIFFERENT figures.

        Storing them equal -- which they are in production -- would make this
        pass for a resolver that read each shadow's own column, so the fixture
        drifts them apart on purpose.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        expense_leg, income_leg = _shadow_of(xfer), _shadow_of(xfer, income=True)
        expense_leg.estimated_amount = Decimal("1.00")
        income_leg.estimated_amount = Decimal("2.00")
        db.session.flush()
        rows = list(xfer.shadow_transactions)
        basis = _basis_for(seed_user, rows)
        assert resolve_transaction_amount(expense_leg, basis) == _OLD_PRICE
        assert resolve_transaction_amount(income_leg, basis) == _OLD_PRICE

    def test_a_shadow_of_an_adhoc_transfer_answers_the_parents_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """An ad-hoc parent owns its amount, and the shadow follows it there.

        Zero ad-hoc transfers exist on the production clone, so this shape is
        graded here and nowhere else -- and it is the arm that decides whether
        the transfer rule is total for a transfer nobody generated.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        shadow = _shadow_of(xfer)
        shadow.estimated_amount = Decimal("3.33")
        db.session.flush()
        assert _resolve(seed_user, shadow) == Decimal("75.00")


class TestTheTransferRule:
    """``resolve_transfer_amount``: the second column ruling R-FI's CHECK covers."""

    def test_an_adhoc_transfer_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody generated it, so no definition states its price."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        assert resolve_transfer_amount(xfer) == Decimal("75.00")

    def test_a_generated_transfer_answers_its_definitions_series(
        self, app, db, seed_user, seed_periods,
    ):
        """The stored column is bypassed: the series is asked on the due date."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        assert xfer.amount == Decimal("111.11")
        assert resolve_transfer_amount(xfer) == _OLD_PRICE

    def test_a_later_transfer_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer arm resolves on the TRANSFER's own due date.

        The sibling of the transaction-side date test, and the review's mutation
        proved it was missing: substituting the pay period's START for the due
        date -- exactly what ``_stated_amount``'s docstring argues against, since
        a period begins up to two weeks before the installment it funds -- moved
        no test and no production row.  Here the period starts 2026-01-02, before
        the 2026-03-01 version, while the transfer is due 2026-04-14 after it, so
        the two dates select different prices.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_NEW_PRICE,
        )
        assert seed_periods[0].start_date < _PRICE_FELL_ON < xfer.due_date
        assert resolve_transfer_amount(xfer) == _NEW_PRICE

    def test_an_overridden_transfer_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A re-priced transfer keeps its figure, exactly as a row does."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        xfer.is_override = True
        db.session.flush()
        assert resolve_transfer_amount(xfer) == Decimal("111.11")

    def test_a_settled_transfer_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """The freeze applies to the parent too -- and proves the shared predicate.

        ``balance_predicates.is_projected`` was annotated Transaction-only until
        this rule asked it of a transfer; this is the case that would fail if it
        were re-narrowed or hand-copied into a second comparison here.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        xfer.status_id = ref_cache.status_id(StatusEnum.DONE)
        db.session.flush()
        assert resolve_transfer_amount(xfer) == Decimal("111.11")


class TestEveryRefusalFires:
    """Each guard, reached and shown to raise.  None of them fires on production."""

    def test_a_row_outside_the_basis_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A basis is built for a row SET, and a row outside it has no answer.

        An adversarial review reproduced the alternative: with no such guard a
        MANUAL loan payment resolved outside its own basis answered ``$1,250.00``
        where the right figure was ``$1,400.00``, silently dropping a standing
        ``$150.00`` extra, because a basis MISS was indistinguishable from a
        producer's deliberate omission.
        """
        mine = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        other = add_txn(db.session, seed_user, seed_periods[0], "Fuel", "60.00")
        with pytest.raises(AmountUnresolvable, match="not among the rows"):
            resolve_transaction_amount(mine, _basis_for(seed_user, [other]))

    def test_a_template_row_with_no_due_date_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """There is no date to resolve the price on, and a period is not one.

        Reachable: ``due_date`` is nullable and both edit forms accept an empty
        value on it (finding N-246, X-au-a's set of 2026-08-11).
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template, due_date=None)
        with pytest.raises(AmountUnresolvable, match="no due_date"):
            _resolve(seed_user, txn)

    def test_a_template_row_whose_series_is_empty_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody ever stated a price, so ``default_amount`` is not an answer.

        The scalar carries no time dimension, which is why the refusal is a
        refusal: reading it would price a February row at whatever the template
        says today.
        """
        template = _seriesless_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        with pytest.raises(AmountUnresolvable, match="series is EMPTY"):
            _resolve(seed_user, txn)

    def test_a_row_whose_template_was_deleted_in_session_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A row still naming a definition that is gone gets the arc's refusal.

        The FK is ``ON DELETE SET NULL``, so the database cannot hold this
        pairing -- but a hard template delete leaves identity-mapped rows
        carrying the stale id until the flush, and a review found that state
        raising a bare ``AttributeError`` out of the salary predicate instead.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        basis = _basis_for(seed_user, [txn])
        txn.template = None
        with pytest.raises(AmountUnresolvable, match="could not be loaded"):
            resolve_transaction_amount(txn, basis)

    def test_an_expense_row_on_a_salary_template_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The classifier and its producer disagree, so no rule answers.

        A REAL data state rather than a hand-emptied basis: the classifier calls
        a template salary-linked whatever its transaction type
        (``is_salary_linked_template`` reads only the profiles), while
        ``live_projected_net`` takes INCOME rows only.  Such a row is claimed by
        the SALARY rule and answered by nothing -- which is the refusal, and the
        divergence itself is a finding this step opened against X-au-d.
        """
        template, _profile = _salary_template(
            seed_user, txn_type=TxnTypeEnum.EXPENSE,
        )
        txn = _template_row(seed_user, seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.SALARY
        with pytest.raises(AmountUnresolvable, match="live recompute answered nothing"):
            _resolve(seed_user, txn)

    def test_a_shadow_whose_parent_is_gone_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An orphaned shadow has nothing for its amount to be equal to.

        Reached the way it is reachable -- the parent deleted in this session
        while the shadow still carries its id -- rather than by assigning a
        state the FK forbids.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        shadow = _shadow_of(xfer)
        basis = _basis_for(seed_user, [shadow])
        shadow.transfer = None
        with pytest.raises(AmountUnresolvable, match="could not be loaded"):
            resolve_transaction_amount(shadow, basis)

    def test_a_generated_transfer_with_no_stated_price_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer-level twin of the empty-series refusal."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Unstated Contribution",
            default_amount=Decimal("250.00"),
        )
        db.session.add(template)
        db.session.flush()
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("250.00"),
            due_date=_DUE_UNDER_OLD_PRICE,
        )
        xfer.transfer_template_id = template.id
        db.session.flush()
        with pytest.raises(AmountUnresolvable, match="series is EMPTY"):
            resolve_transfer_amount(xfer)

    def test_an_own_row_carrying_no_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver's own totality contract: it answers a Decimal or raises.

        Unreachable through the database today -- both amount columns are NOT
        NULL -- and reachable from plan step X-au-c, where
        ``ck_transactions_amount_ownership`` becomes what keeps an OWN row's
        figure present.  ``no_autoflush`` is what makes the RAISE the failure: a
        review found the un-flushed ``None`` reaching PostgreSQL first and the
        test passing on an ``IntegrityError`` instead of on its own assertion.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        basis = _basis_for(seed_user, [txn])
        with db.session.no_autoflush:
            txn.estimated_amount = None
            with pytest.raises(
                AmountUnresolvable, match="owns its amount and carries none",
            ):
                resolve_transaction_amount(txn, basis)
            txn.estimated_amount = Decimal("35.00")

    def test_a_transfer_carrying_no_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The same totality contract on the second column X-au-c makes nullable."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        with db.session.no_autoflush:
            xfer.amount = None
            with pytest.raises(
                AmountUnresolvable, match="owns its amount and carries none",
            ):
                resolve_transfer_amount(xfer)
            xfer.amount = Decimal("75.00")


class TestTheLoanPaymentRule:
    """The rule production cannot exercise: ``loan_payment_settings`` is empty there."""

    def test_a_derive_mode_shadow_answers_the_loans_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """P&I + the escrow in effect on the installment's due date.

        Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
            P&I    = amortize(200000, 0.06, 360) = 1,199.10
            escrow = 3,600 / 12                  =   300.00
            PITI                                 = 1,499.10

        The definition says ``$1,300.00``, the parent's column ``$1,250.00`` and
        the shadow's ``$1,200.00`` -- three figures the loan's own answer is not.
        """
        shadow, rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        basis = _basis_for(seed_user, rows)
        assert resolve_transaction_amount(shadow, basis) == Decimal("1499.10")

    def test_a_manual_payment_with_no_extra_answers_its_stated_base(
        self, app, db, seed_user, seed_periods,
    ):
        """Manual mode's base is a STATED amount, so its DEFINITION holds it.

        The definition states ``$1,300.00``; the parent's column says
        ``$1,250.00`` and the shadow's ``$1,200.00``.  A review found the earlier
        fixture setting all three equal, which made the test pass for every
        candidate implementation -- and the loan's own P&I of ``$1,199.10`` must
        not appear either.
        """
        shadow, rows = _loan_payment(seed_user, seed_periods[0], derive=False)
        basis = _basis_for(seed_user, rows)
        assert amount_rule(shadow) is AmountRule.LOAN_PAYMENT
        assert resolve_transaction_amount(shadow, basis) == Decimal("1300.00")

    def test_a_manual_payment_answers_the_same_base_with_and_without_an_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """One mode, one expression: the extra is added, never a different base.

        The defect this test exists for, reproduced by an adversarial review:
        ``live_loan_transfer_amounts`` prices a manual payment from
        ``shadow.estimated_amount + extra``, so a rule that used the live map
        whenever it had an entry answered ``$1,200.00 + $150.00`` with a standing
        extra and ``$1,300.00`` (the series) without one -- the same payment
        priced from two different bases, one of them the stored column ruling
        R-FI deletes.
        """
        shadow, rows = _loan_payment(
            seed_user, seed_periods[0], derive=False, extra=Decimal("150.00"),
        )
        basis = _basis_for(seed_user, rows)
        assert basis.loan_cash[shadow.id] == Decimal("1350.00")
        assert resolve_transaction_amount(shadow, basis) == Decimal("1450.00")

    def test_a_derive_mode_payment_whose_loan_will_not_resolve_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """No loan behind it, so its P&I has no answer and nothing substitutes.

        The destination is an ordinary savings account, so
        ``_resolve_loan_basis`` answers nothing.  A fallback here would publish
        the stored figure, which on a derive-mode payment is a snapshot of
        exactly the computation that just failed.
        """
        savings = create_savings_account(
            seed_user, db.session, "Not A Loan", Decimal("5000.00"),
        )
        shadow, rows = _loan_payment(
            seed_user, seed_periods[0], derive=True, to_account=savings,
        )
        basis = _basis_for(seed_user, rows)
        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            resolve_transaction_amount(shadow, basis)


class TestTheBatchTier:
    """``amount_basis``, and the merged map that is now derived from it."""

    def test_the_basis_keeps_the_two_producers_apart(
        self, app, db, seed_user, seed_periods,
    ):
        """Salary answers and loan answers land in their own maps.

        Which rule prices a row is a fact about the row; merging the maps first
        would make it a question about which map the id turned up in, which is
        the discriminator ruling R-FI refuted.
        """
        shadow, rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        basis = _basis_for(seed_user, rows)
        assert basis.salary_net == {}
        assert basis.loan_cash[shadow.id] == Decimal("1499.10")
        assert basis.priced_ids == {row.id for row in rows}

    def test_live_amount_overrides_holds_the_union_of_both_maps(
        self, app, db, seed_user, seed_periods,
    ):
        """The legacy merged map still answers for both kinds, produced once.

        The regression guard for X-au-b's one wiring change: two call sites in
        ``app/`` read ``live_amount_overrides`` and neither may move.  Asserted
        from OUTSIDE -- the expected keys are the salary row and both loan
        shadows, named explicitly -- rather than by re-expressing the merge,
        which a review pointed out could only fail if the producer were
        nondeterministic.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_user, seed_periods[0], template, is_income=True,
        )
        _shadow, loan_rows = _loan_payment(
            seed_user, seed_periods[0], derive=True,
        )
        rows = [paycheck, *loan_rows]
        merged = live_amount_overrides(
            seed_user["account"], seed_user["scenario"].id, rows,
        )
        assert set(merged) == {paycheck.id, *(row.id for row in loan_rows)}
        assert merged[loan_rows[0].id] == Decimal("1499.10")
        assert merged[paycheck.id] != Decimal(_NOT_AN_ANSWER)


class TestTheRulesDoNotReadTheColumnTheyReplace:
    """The invariance property, which agreement alone cannot show.

    The unit twin of the exhaustive oracle's second pass: a derived row's answer
    must not move when its own stored amount does.  An adversarial review
    deleted the entire resolver -- ``return txn.estimated_amount`` -- and the
    agreement pass still reported 997 of 997 rows correct, so this is the shape
    of assertion that can tell the two apart.
    """

    @pytest.mark.parametrize("nudge", [Decimal("1000.00")])
    def test_a_template_rows_answer_ignores_its_own_column(
        self, app, db, seed_user, seed_periods, nudge,
    ):
        """Re-pricing the row's column moves nothing: the definition decides."""
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        basis = _basis_for(seed_user, [txn])
        before = resolve_transaction_amount(txn, basis)
        with db.session.no_autoflush:
            txn.estimated_amount = txn.estimated_amount + nudge
            assert resolve_transaction_amount(txn, basis) == before

    def test_a_shadows_answer_ignores_both_its_own_column_and_its_parents(
        self, app, db, seed_user, seed_periods,
    ):
        """Only the definition moves a generated transfer's shadow."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, template = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        shadow = _shadow_of(xfer)
        basis = _basis_for(seed_user, [shadow])
        with db.session.no_autoflush:
            shadow.estimated_amount = Decimal("4242.42")
            xfer.amount = Decimal("2424.24")
            assert resolve_transaction_amount(shadow, basis) == _OLD_PRICE
        template_amount_service.set_amount(
            template, Decimal("199.00"), effective_on=_PRICE_ROSE_ON,
        )
        db.session.flush()
        assert resolve_transaction_amount(shadow, basis) == Decimal("199.00")

    def test_an_own_rows_answer_follows_its_own_column(
        self, app, db, seed_user, seed_periods,
    ):
        """The other half of the property: an OWN row's column IS its answer."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        basis = _basis_for(seed_user, [txn])
        with db.session.no_autoflush:
            txn.estimated_amount = Decimal("36.00")
            assert resolve_transaction_amount(txn, basis) == Decimal("36.00")
            txn.estimated_amount = Decimal("35.00")
