"""
Shekel Budget App -- Where one row's amount comes from (plan step X-au-b)

The total dispatch of :mod:`app.services.cash_ledger._amount_source`: ruling
**R-FI**'s five amount rules, the order that makes two of them subsets of two
others rather than rivals, and the refusal each one raises where its producer
cannot answer.

**A DERIVED fixture here carries no stored figure at all, and an OWN fixture
carries one no rule may answer.**  Neither is decoration.  An adversarial review
mutated the resolver to ``return txn.estimated_amount`` -- deleting the dispatch,
all five rules and every refusal -- and the exhaustive production oracle beside
this file reported *997 rows, 0 mismatches, OK*, because for 946 of those rows
the app's own answer IS that column.  A fixture whose stored figure equals its
derived one cannot tell the two implementations apart, so none here does.  Since
plan step X-au-c2 a derived row cannot even hold a rival figure:
:func:`_declare_derived` empties the column as it stamps the source, because
``ck_transactions_amount_ownership`` pairs the two, so the same mutation now
returns ``None`` where a ``Decimal`` is asserted.  The OWN fixtures still carry
``_NOT_AN_ANSWER`` -- for them the column IS the answer, and the figure names
which of three candidates a test means.

**Which rule prices a row is now a question to the COLUMN** (finding **N-262**,
closed here): ``amount_rule`` reads ``amount_source_id`` and no longer infers
ownership from ``is_override`` or from having left Projected.  So every derived
fixture DECLARES its relation, and the three arms that used to be inferences --
overridden, non-Projected, soft-deleted -- are graded in
:class:`TestTheDeclarationDecides` as the inversions they became: the flag and
the status no longer move the rule in either direction.

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
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import AcctTypeEnum, AmountSourceEnum, StatusEnum, TxnTypeEnum
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
    amounts_by_id,
    contributions_by_id,
    display_amounts_by_id,
    live_amounts,
    owned_amount,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.cash_ledger._amount_source import _RELATION_RULES, _RULE_ANSWERS
from tests._test_helpers import (
    add_escrow_line,
    add_txn,
    capture_sql_statements,
    create_loan_account,
    create_savings_account,
    create_transfer,
    loan_params_for,
    make_salary_profile,
)
from app.services.balance_at import BalanceContext
from app.services.row_valuation import owned_contribution
from app.services.transfer_service import _settle as transfer_settle


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


def _basis_for(seed_user):
    """The AmountBasis for this owner and scenario -- what a caller builds.

    A basis is pinned to an owner and a scenario since plan step X-au-c2b, not
    to a row set, so there is one per test whatever it resolves.  Its two
    derivations are lazy, so building it here costs nothing until a rule asks.
    """
    return amount_basis(seed_user["user"].id, seed_user["scenario"].id)


def _declare_derived(txn, relation=AmountSourceEnum.TEMPLATE):
    """Declare *txn* priced by *relation*, which EMPTIES its own amount column.

    The two writes are one act because ``ck_transactions_amount_ownership`` makes
    them one: a row states either a figure it owns or the relation that prices
    it, never both.  Every derived fixture in this file goes through here, so no
    test can accidentally grade a row the schema would refuse -- which is the
    shape plan step X-au-c1's own build met (finding **N-260**).
    """
    txn.estimated_amount = None
    txn.amount_source_id = ref_cache.amount_source_id(relation)
    db.session.flush()
    return txn


def _declare_transfer_derived(xfer):
    """The transfer twin of :func:`_declare_derived`.

    Only ``template`` can price a transfer -- a transfer has no parent transfer
    -- so the relation is not a parameter here.  ``ck_transfers_adhoc_owns_amount``
    additionally refuses the declaration outright on an ad-hoc transfer, so this
    is only ever called on a generated one.
    """
    xfer.amount = None
    xfer.amount_source_id = ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
    db.session.flush()
    return xfer


def _resolve(seed_user, txn):
    """Resolve one row against a basis built over exactly that row."""
    return resolve_transaction_amount(txn, _basis_for(seed_user))


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


def _template_row(seed_user, period, template, *, owns=False, **kwargs):
    """A generated row on *template*, due under the OLD price unless told otherwise.

    DECLARED as priced by its definition by default, which is the state the
    template cutover (plan step X-au-e) puts every non-override row in.  Pass
    ``owns=True`` for the other half of the model -- a row generated by a
    definition that has since taken its figure back, which is what a hand
    re-price and the freeze both produce -- where the stored ``_NOT_AN_ANSWER``
    IS the answer.
    """
    kwargs.setdefault("due_date", _DUE_UNDER_OLD_PRICE)
    txn = add_txn(
        db.session, seed_user, period, template.name, _NOT_AN_ANSWER, **kwargs,
    )
    txn.template_id = template.id
    db.session.flush()
    return txn if owns else _declare_derived(txn)


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
    owns=False,
):
    """A transfer carrying a template whose series states TWO prices.

    DECLARED derived by default, parent and both shadows: the parent names its
    definition and each shadow names the parent, which is the state plan step
    X-au-f puts every generated transfer in.  Their amount columns are therefore
    EMPTY, so a resolver reading any of the three answers ``None`` against an
    asserted ``Decimal``.

    Pass ``owns=True`` for the pre-cutover shape -- parent and shadows all
    holding ``stored``, a figure no rule may answer -- which is what an
    overridden or settled transfer looks like once the freeze has taken its
    figure back.
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
    if owns:
        return xfer, template
    for shadow in xfer.shadow_transactions:
        _declare_derived(shadow, AmountSourceEnum.PARENT_TRANSFER)
    return _declare_transfer_derived(xfer), template


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
    series=Decimal("1300.00"), stored=Decimal("1250.00"), owns=False,
):
    """A mortgage payment transfer in one of its two modes, and its rows.

    Three DISTINCT figures -- the definition states ``$1,300.00``, the parent
    transfer's column holds ``$1,250.00`` and each shadow's holds ``$1,200.00``
    -- so a manual-mode assertion names which of the three it means.  The review
    found the earlier fixture setting all three to one number, which made the
    test pass for any of the three implementations.

    The three columns are then EMPTIED by the declaration, which is what makes
    the shadow reach rule 4 at all.  ``owns=True`` stops before that step and
    leaves the three figures in place -- production's shape, where nothing is
    declared and ``budget.loan_payment_settings`` is empty -- and is what the one
    test that must watch the PRODUCER read the column uses.

    Returns ``(shadow, rows)``: the checking-side expense shadow, and both
    shadows, which is what a basis is built over.
    """
    loan = _mortgage(seed_user) if to_account is None else to_account
    xfer, template = _generated_transfer(
        seed_user, period, loan, due_date=date(2026, 2, 1),
        series=series, later=None, stored=stored, owns=True,
    )
    settings = LoanPaymentSettings(derive_from_loan=derive)
    if extra is not None:
        settings.extra_principal = extra
    template.settings = settings
    shadows = list(xfer.shadow_transactions)
    for shadow in shadows:
        shadow.estimated_amount = Decimal("1200.00")
    db.session.flush()
    if not owns:
        _declare_loan_payment_derived(xfer)
    return _shadow_of(xfer), shadows


def _declare_loan_payment_derived(xfer):
    """Declare a loan payment's parent and its CHECKING-side shadow derived.

    Separate from :func:`_generated_transfer` for two reasons.  One is ordering:
    a test builds the payment, watches the producer price it off the stored
    column, and only THEN declares it -- the transition the loan cutover (plan
    step X-au-g) performs.

    **The other is that the LOAN-side income leg cannot be declared at all
    today, and that is a finding rather than a fixture convenience.**  The
    resolver's LOAN_PAYMENT rule asks the loan
    (``loan_payment_service.live_loan_transfer_amounts``); the loan resolves
    through ``load_loan_context``, which calls ``get_payment_history``, which
    reads the amount of every shadow income row ON THE LOAN ACCOUNT.  Emptying
    that column therefore breaks the producer that prices the row -- a cycle the
    amount model does not yet cut, recorded against X-au-g.  The checking-side
    expense leg is invisible to ``query_shadow_income`` (it filters to the
    destination account and the income type), so declaring it exercises rule 4
    end to end without entering the cycle, and it is the leg every CASH reader
    prices.
    """
    _declare_derived(_shadow_of(xfer), AmountSourceEnum.PARENT_TRANSFER)
    return _declare_transfer_derived(xfer)


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

    def test_every_declarable_relation_has_a_rule(self):
        """Every relation a row may DECLARE refines into one of the five rules.

        The second half of the same totality claim, and the one plan step
        X-au-c2 added: :func:`amount_rule` now branches on
        ``amount_source_id``, so a member added to
        :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
        charge is the one already known to need one (finding **N-264**) --
        must arrive with a rule beside it.  Without this it would raise a
        ``KeyError`` on the first row that declared it, in production, instead
        of here.
        """
        assert set(_RELATION_RULES) == set(AmountSourceEnum)


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

    def test_a_row_that_took_its_figure_back_owns_it_again(
        self, app, db, seed_user, seed_periods,
    ):
        """A template-generated row that carries a figure owns it, definition or not.

        The state a hand re-price leaves and the state the freeze leaves are the
        same state, and it is the one the CHECK describes: a figure and no
        source.  ``is_override`` is not consulted -- see
        :class:`TestTheDeclarationDecides` for the inverse, which is the half
        finding **N-262** was about.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template, owns=True)
        assert amount_rule(txn) is AmountRule.OWN
        assert _resolve(seed_user, txn) == Decimal(_NOT_AN_ANSWER)

    def test_a_shadow_that_took_its_figure_back_owns_it_again(
        self, app, db, seed_user, seed_periods,
    ):
        """The same on the shadow side -- 12 such rows exist on the production clone."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
            owns=True,
        )
        shadow = _shadow_of(xfer)
        shadow.estimated_amount = Decimal("77.77")
        db.session.flush()
        assert amount_rule(shadow) is AmountRule.OWN
        assert _resolve(seed_user, shadow) == Decimal("77.77")

    def test_soft_deleting_a_row_does_not_change_which_rule_prices_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Deletion says whether a row counts, never who owns its figure.

        Making it flip the rule would force ``amount_source_id`` to be rewritten
        on every delete and restore -- a stored value beside a second writer,
        which is the shape this arc removes.  102 of production's 330 transfer
        shadows are soft-deleted, so the arm is ordinary rather than theoretical.
        """
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template, is_deleted=True,
        )
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE


class TestTheDeclarationDecides:
    """Finding **N-262**: the COLUMN says who owns a figure, and nothing else does.

    Until plan step X-au-c2 the OWN arm was inferred from ``is_override`` and
    from having left Projected -- neither of which
    ``ck_transactions_amount_ownership`` can see -- so four live doors could
    write a row the schema admits and the resolver refuses.  Each of the four is
    reached here on a DECLARED row and shown to resolve through its relation
    rather than falling into OWN and raising.
    """

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.RECEIVED, StatusEnum.CREDIT,
        StatusEnum.CANCELLED, StatusEnum.SETTLED,
    ])
    def test_leaving_projected_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods, status,
    ):
        """A declared row keeps its relation in every status the machine admits.

        Credit and Cancelled are the reachable half and they are why this is a
        parametrize rather than a settled-only case: both leave Projected WITHOUT
        entering the settled band, so no freeze fires and no figure is written
        back.  Under the inference such a row took the OWN arm and
        ``_own_figure`` raised on its empty column -- and production carries 7
        Cancelled and 2 Credit template-linked rows against a grid route that
        loads every row in the window with no status predicate
        (``routes/grid/page.py``'s ``_load_grid_transactions``), so the first bucket to derive would have taken
        out the whole screen.
        """
        template = _priced_template(seed_user)
        txn = _template_row(
            seed_user, seed_periods[0], template, status_enum=status,
        )
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_the_override_flag_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The flag carries four facts and pricing is not one of them.

        ``routes/transactions/mutations.py:295`` sets it for a pay-period MOVE
        with no re-price at all, and finding **N-238** (plan step X-au-h) is the
        split of the other three.  A row that was moved and not re-priced still
        has its definition's price, and a rule reading the flag would refuse it.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        txn.is_override = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_bulk_flag_update_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The carry-forward shape: the flag set by SQL, past every ORM hook.

        ``carry_forward_service/_execute.py:157`` sets ``is_override`` in a bulk
        ``query.update``, which no validator and no session event can see.  It is
        written that way for the partial unique index rather than for pricing
        (the fourth fact finding **N-238** names), so a row it touched must price
        exactly as it did before.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        (
            db.session.query(Transaction)
            .filter(Transaction.id == txn.id)
            .update({"is_override": True}, synchronize_session="fetch")
        )
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_transfer_declaring_a_parent_transfer_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer has no parent transfer, so that relation names nothing.

        The totality of the transfer arm.  ``ref.amount_sources`` holds both
        members and nothing at the storage tier scopes one of them to the
        shadow table, so the refusal is the only thing that tells a writer which
        of the two tables it confused.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        xfer.amount_source_id = ref_cache.amount_source_id(
            AmountSourceEnum.PARENT_TRANSFER,
        )
        db.session.flush()
        with pytest.raises(AmountUnresolvable, match="no parent transfer"):
            resolve_transfer_amount(xfer)


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
        assert owned_contribution(txn) == Decimal("462.34")
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
        assert owned_contribution(txn) == Decimal("0")
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
            txn, income_service.salary_pricing(
                seed_user["user"].id, seed_user["scenario"].id,
            ),
        )
        assert live != Decimal(_NOT_AN_ANSWER)
        assert _resolve(seed_user, txn) == live

    def test_a_transfer_shadow_answers_its_parents_RULE_not_either_column(
        self, app, db, seed_user, seed_periods,
    ):
        """Transfer Invariant 3 becomes structural: the shadow reads the parent's rule.

        Neither column can hold a rival figure any more -- the declaration empties
        both -- so the assertion discriminates between the definition's
        ``$178.00`` and a ``None`` from either. That is a stronger control than
        the three distinct figures this test used to plant, and it is the
        difference plan step X-au-c2 makes: before it, the shadow's own column
        and its parent's were both populated and only the review's three-figure
        fixture told a column-reading resolver from a rule-resolving one.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        shadow = _shadow_of(xfer)
        assert shadow.estimated_amount is None
        assert xfer.amount is None
        assert _resolve(seed_user, shadow) == _OLD_PRICE

    def test_both_shadows_of_one_transfer_answer_the_same_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Invariant 3 for the PAIR: both legs answer their parent, not themselves.

        The two legs used to be stored at DIFFERENT figures so a resolver reading
        each shadow's own column would answer two numbers where one is right.
        The declaration empties both columns instead, so such a resolver now
        answers ``None`` twice -- the same discrimination, made structural.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        expense_leg, income_leg = _shadow_of(xfer), _shadow_of(xfer, income=True)
        rows = list(xfer.shadow_transactions)
        basis = _basis_for(seed_user)
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
        shadow = _declare_derived(
            _shadow_of(xfer), AmountSourceEnum.PARENT_TRANSFER,
        )
        assert xfer.amount_source_id is None
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
        """There is no stored column to bypass: the series is asked on the due date."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        assert xfer.amount is None
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

    def test_a_generated_transfer_that_carries_a_figure_owns_it(
        self, app, db, seed_user, seed_periods,
    ):
        """A definition behind it does not make a transfer derived: the column does.

        The transfer twin of
        :meth:`TestWhichRulePricesARow.test_a_row_that_took_its_figure_back_owns_it_again`,
        and the shape a re-priced transfer and a frozen one both leave.  Its
        definition still states ``$178.00`` on this due date, and the transfer
        answers its own ``$111.11``.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, template = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
            owns=True,
        )
        assert template_amount_service.amount_as_of(
            template, _DUE_UNDER_OLD_PRICE,
        ) == _OLD_PRICE
        assert resolve_transfer_amount(xfer) == Decimal("111.11")

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.CANCELLED,
    ])
    def test_a_transfers_status_does_not_decide_which_rule_prices_it(
        self, app, db, seed_user, seed_periods, status,
    ):
        """Leaving Projected does not take a declared transfer's relation back.

        The transfer half of finding **N-262**, and the reason
        ``resolve_transfer_amount`` stopped consulting
        ``balance_predicates.is_projected`` at plan step X-au-c2: a Cancelled
        transfer never enters the settled band, so no freeze writes it a figure,
        and a status-driven OWN arm would refuse the empty column that is left.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[0], savings, due_date=_DUE_UNDER_OLD_PRICE,
        )
        xfer.status_id = ref_cache.status_id(status)
        db.session.flush()
        assert resolve_transfer_amount(xfer) == _OLD_PRICE


class TestEveryRefusalFires:
    """Each guard, reached and shown to raise.  None of them fires on production."""

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
        basis = _basis_for(seed_user)
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
        basis = _basis_for(seed_user)
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
        _declare_transfer_derived(xfer)
        with pytest.raises(AmountUnresolvable, match="series is EMPTY"):
            resolve_transfer_amount(xfer)

    def test_an_own_row_carrying_no_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver's own totality contract: it answers a Decimal or raises.

        Unreachable through the database: ``ck_transactions_amount_ownership``
        is what keeps an OWN row's figure present, so the state below is a row
        written AROUND the CHECK.  ``no_autoflush`` is what makes the RAISE the
        failure and not the constraint: a review found the un-flushed ``None``
        reaching PostgreSQL first and the test passing on an ``IntegrityError``
        instead of on its own assertion.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        basis = _basis_for(seed_user)
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
        """The same totality contract on the second column the model covers."""
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
        basis = _basis_for(seed_user)
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
        basis = _basis_for(seed_user)
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

        **The payment is built OWNING its figure and declared afterwards**,
        which is the one place in this file that ordering matters.  The producer
        must see a populated column to answer ``$1,350.00`` at all -- it reads
        ``shadow.estimated_amount`` -- so the basis is built first and the
        declaration follows, planting the producer's rival answer in a basis the
        resolver then ignores.  That the producer cannot run at all once the
        column is empty is finding **N-259**, and it is the loan cutover's
        (plan step X-au-g) precondition rather than this leaf's: nothing is
        declared on production and ``budget.loan_payment_settings`` is empty
        there.
        """
        shadow, rows = _loan_payment(
            seed_user, seed_periods[0], derive=False, extra=Decimal("150.00"),
            owns=True,
        )
        basis = _basis_for(seed_user)
        assert basis.loans.live_cash(shadow) == Decimal("1350.00")
        _declare_loan_payment_derived(shadow.transfer)
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
        basis = _basis_for(seed_user)
        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            resolve_transaction_amount(shadow, basis)


class TestTheBatchTier:
    """``amount_basis``, and the merged map that is now derived from it."""

    def test_the_basis_keeps_the_two_derivations_apart(
        self, app, db, seed_user, seed_periods,
    ):
        """Salary answers and loan answers come from their own derivations.

        Which rule prices a row is a fact about the row; merging the two first
        would make it a question about which map the id turned up in, which is
        the discriminator ruling R-FI refuted.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_user, seed_periods[0], template, is_income=True, owns=True,
        )
        shadow, _rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        basis = _basis_for(seed_user)

        # Each derivation answers for its OWN kind and nothing for the other's,
        # which is what "apart" means here: not two empty maps, but two that
        # cannot answer each other's rows.
        assert basis.loans.live_cash(shadow) == Decimal("1499.10")
        assert basis.loans.live_cash(paycheck) is None
        assert income_service.salary_net_for(
            paycheck, basis.salary,
        ) != Decimal(_NOT_AN_ANSWER)
        assert income_service.salary_net_for(shadow, basis.salary) is None

    def test_a_basis_answers_for_a_row_it_was_not_built_over(
        self, app, db, seed_user, seed_periods,
    ):
        """A basis is pinned to an OWNER and a SCENARIO, never to a row set.

        This replaces the membership refusal plan step X-au-c2b deleted.  That
        guard existed because a basis stored per-row ANSWERS, so a MISS was
        indistinguishable from a producer's deliberate omission -- an
        adversarial review reproduced a manual loan payment resolved outside its
        own basis answering ``$1,250.00`` where ``$1,400.00`` was right,
        silently dropping a standing ``$150.00`` extra.  A basis holds the
        DERIVATIONS now, so nothing is ever absent: the payment's cash is
        computed from its own config whenever it is asked.  The control is the
        MANUAL-with-extra shape the review used, resolved against a basis built
        before the row existed at all.
        """
        stale = _basis_for(seed_user)
        shadow, _rows = _loan_payment(
            seed_user, seed_periods[0], derive=False, extra=Decimal("150.00"),
            owns=True,
        )
        assert stale.loans.live_cash(shadow) == Decimal("1350.00")
        other = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
        )
        assert resolve_transaction_amount(other, stale) == Decimal("60.00")

    def test_live_amounts_holds_the_union_of_both_maps(
        self, app, db, seed_user, seed_periods,
    ):
        """The merged map answers for both kinds, produced once.

        The regression guard for the surfaces that want a LOOKUP rather than a
        per-row question -- the grid publishes it so a cell and the balance row
        beside it read one object (ruling R-Q).  Asserted from OUTSIDE -- the
        expected keys are the salary row and both loan shadows, named explicitly
        -- rather than by re-expressing the merge, which a review pointed out
        could only fail if the producer were nondeterministic.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_user, seed_periods[0], template, is_income=True,
        )
        _shadow, loan_rows = _loan_payment(
            seed_user, seed_periods[0], derive=True,
        )
        rows = [paycheck, *loan_rows]
        merged = live_amounts(_basis_for(seed_user), rows)
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
        """Re-pricing the row's column moves nothing: the definition decides.

        The nudge is written in memory and never flushed, because since plan
        step X-au-c2 it CANNOT be flushed:
        ``ck_transactions_amount_ownership`` refuses a figure beside a
        declaration, which is what turns this property from a measurement into a
        construction.  The in-memory write is still what grades the rule.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        basis = _basis_for(seed_user)
        before = resolve_transaction_amount(txn, basis)
        with db.session.no_autoflush:
            txn.estimated_amount = nudge
            assert resolve_transaction_amount(txn, basis) == before
            txn.estimated_amount = None

    def test_a_declared_row_cannot_carry_a_rival_figure_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """The invariance above, made structural rather than asserted.

        A derived row whose column holds a figure is the state the whole arc
        exists to delete, and it is the state the CHECK forbids -- so the
        rival figure the test above writes in memory cannot survive a flush.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)
        txn.estimated_amount = Decimal("1000.00")
        with pytest.raises(IntegrityError, match="ck_transactions_amount_ownership"):
            db.session.flush()
        db.session.rollback()

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
        basis = _basis_for(seed_user)
        with db.session.no_autoflush:
            shadow.estimated_amount = Decimal("4242.42")
            xfer.amount = Decimal("2424.24")
            assert resolve_transaction_amount(shadow, basis) == _OLD_PRICE
            shadow.estimated_amount = None
            xfer.amount = None
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
        basis = _basis_for(seed_user)
        with db.session.no_autoflush:
            txn.estimated_amount = Decimal("36.00")
            assert resolve_transaction_amount(txn, basis) == Decimal("36.00")
            txn.estimated_amount = Decimal("35.00")


def _touch(*rows):
    """Refresh *rows* from the database, outside any statement capture.

    ``db.session.commit()`` EXPIRES every instance, so the next attribute read
    emits a PK SELECT.  The query-count controls below are about whether a
    DERIVATION re-runs, and a refresh landing inside their capture would make
    them assertions about SQLAlchemy's expiry policy instead.

    Args:
        rows: The ORM instances to load back.
    """
    for row in rows:
        _ = row.estimated_amount


class TestTheBasisIsOneDerivationPerReadPass:
    """The property plan step X-au-c2b's restructure exists to make structural.

    A basis held per-row ANSWERS for one row SET until that step, so a request
    that loaded two row sets ran the paycheck engine and the loan resolver twice
    -- findings **N-268** and **N-269**, two filings of that one cause.  Holding
    the DERIVATIONS instead makes "one pricing pass per read pass" a property of
    the object rather than a discipline every surface has to remember, and these
    are the controls that say so.

    Each is a QUERY-COUNT assertion, because that is the only thing that can
    tell a memo from a re-derivation: both answer the same figure, which is
    exactly why the duplication went unnoticed long enough to be filed twice.
    """

    def test_no_paycheck_and_no_loan_payment_means_no_query_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """A row set with neither kind resolves nothing and asks nothing.

        The "fast no-op when there are no candidates" property the row-set
        producers had, KEPT rather than traded away for the sharing: both
        derivations are lazy and each half of :func:`live_override` answers
        ``None`` from the row's own columns before it touches one.  Without that
        the salary projection would run on every read pass in the app.

        **The BASIS is built inside the capture, and that is what makes this a
        control.**  Built outside it, an eagerly-loading derivation issues its
        query before the capture opens and this passes anyway -- which a
        mutation check caught it doing: making ``LoanPricing`` load its config
        map in its constructor left the assertion green.
        """
        row = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        db.session.commit()
        # The commit EXPIRED the row, so the first attribute read refreshes it
        # from the database.  Done here rather than inside the capture: that
        # SELECT is SQLAlchemy's, not the derivation's, and counting it would
        # make this assertion about the session rather than about laziness.
        _touch(row)
        # The two pins are read out HERE for the same reason: the commit
        # expired the seeded ``User`` and ``Scenario`` too, and their refresh
        # SELECTs are the session's rather than the amount model's.
        user_id = seed_user["user"].id
        scenario_id = seed_user["scenario"].id

        def _price():
            return live_amounts(amount_basis(user_id, scenario_id), [row])

        _answer, statements = capture_sql_statements(_price)

        assert _answer == {}, (
            "an ordinary expense row has no live figure; a map that answered "
            f"one would mean the gate never ran: {_answer}"
        )
        assert statements == [], (
            "building a basis and pricing an ordinary expense row must "
            "resolve neither derivation; got "
            f"{[text for text, _params in statements]}"
        )

    def test_the_salary_projection_runs_ONCE_however_many_row_sets_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Two row sets, one basis, one paycheck engine run.

        The direct control on **N-268**: the dashboard pulse priced rows the
        cash fold had already priced, and paid a second full projection for it.
        The second ask must issue no statement at all -- not merely fewer.
        """
        template, _profile = _salary_template(seed_user)
        first = _template_row(
            seed_user, seed_periods[0], template, is_income=True, owns=True,
        )
        second = _template_row(
            seed_user, seed_periods[1], template, is_income=True, owns=True,
        )
        db.session.commit()
        basis = _basis_for(seed_user)
        _touch(first, second)

        _first, first_statements = capture_sql_statements(
            lambda: live_amounts(basis, [first]),
        )
        _second, second_statements = capture_sql_statements(
            lambda: live_amounts(basis, [second]),
        )

        assert first_statements, "the first ask must resolve the projection"
        assert second_statements == [], (
            "the second row set must answer from the derivation the first "
            f"resolved; got {[text for text, _params in second_statements]}"
        )
        # The ANSWER, not just the query count.  A memo that answers correctly
        # once and empty afterwards satisfies a count assertion perfectly, and
        # an adversarial review of this file caught exactly that omission: the
        # second ask must be the same live net, not merely cheap.
        assert _first[first.id] == _second[second.id] != Decimal(_NOT_AN_ANSWER)

    def test_the_loan_resolve_runs_ONCE_however_many_shadows_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Both legs of one payment, one basis, one loan resolve.

        The direct control on **N-269**: the transfer settle door re-queried the
        transfer and re-resolved the loan for every offered row, because each
        offered row built its own basis.  The two legs of one payment are the
        smallest shape that shows it -- they share the transfer, the config and
        the destination loan, so everything the second ask needs is what the
        first resolved.
        """
        _shadow, rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        first, second = rows
        db.session.commit()
        basis = _basis_for(seed_user)
        _touch(first, second)

        _first, first_statements = capture_sql_statements(
            lambda: basis.loans.live_cash(first),
        )
        _second, second_statements = capture_sql_statements(
            lambda: basis.loans.live_cash(second),
        )

        assert first_statements, "the first ask must resolve the loan"
        assert second_statements == [], (
            "the second leg must answer from the loan already resolved; got "
            f"{[text for text, _params in second_statements]}"
        )
        # The FIGURE, and on this shape it is Transfer Invariant 3: both legs
        # of one payment must be worth the same, so a memo that answered
        # ``None`` the second time would show two figures for one transfer
        # while passing every query-count assertion.
        assert _first == _second == Decimal("1499.10")

    def test_the_settle_freeze_and_the_display_ask_ONE_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """``live_cash`` is the single rule, where there were two functions.

        ``live_loan_transfer_amounts`` (the display) and
        ``live_loan_payment_amount`` (the settle freeze) were two
        implementations of one rule, the second's docstring stating that it
        "mirrors" the first's candidate filter -- kept in step by hand, which is
        the shape that eventually disagrees.  The control is that the transfer
        settle door's own seam and the display map answer the SAME figure for
        the same shadow through the same call.
        """
        shadow, rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        db.session.commit()
        basis = _basis_for(seed_user)
        _touch(*rows)

        displayed = live_amounts(basis, rows)[shadow.id]
        frozen = transfer_settle.frozen_amount(shadow, basis)

        assert frozen == displayed == Decimal("1499.10")

    def test_one_read_pass_hands_out_the_SAME_basis(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``BalanceContext.amounts()`` memoizes, so a render holds one.

        Identity, not equality: two bases with the same pins ARE equal (the
        derivations are excluded from the comparison, exactly as
        ``BalanceContext``'s own memo caches are), so an equality assertion
        would pass on two objects that each resolve the projection separately.
        """
        ctx = BalanceContext.build(seed_user["user"].id)

        assert ctx.amounts() is ctx.amounts()
        # And that it memoized the RIGHT one.  A defect passing the wrong ids
        # -- ``amount_basis(self.user_id, self.user_id)``, or a hardcoded
        # baseline -- memoizes just as well, and on a single-user single-
        # scenario seed the two ids are often equal, so identity alone cannot
        # see it.  The pins are public fields precisely so this is assertable.
        assert ctx.amounts().user_id == ctx.user_id
        assert ctx.amounts().scenario_id == ctx.scenario_id


class TestABudgetIsNotAContribution:
    """Ruling E-21's base, and the two accessors that answer it.

    A row's BUDGET and what it CONTRIBUTES are different questions, and plan
    step X-au-c2b's reader routing turns on their being different: an
    entry-tracked row's remaining, its over-budget flag and its amount cell all
    answer on the budget, which E-21 fixes on the row's own amount
    unconditionally -- never the entered actual, never status-dependent.  A
    reader handed a contribution instead would answer ``$0.00`` for a Cancelled
    envelope whose budget is still its budget, and would re-base a settled row's
    variance on the very number it is being compared against.
    """

    def test_a_cancelled_envelopes_budget_is_still_its_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch answers the amount; the contribution answers zero.

        Both are correct answers to their own question, which is why the reader
        has to pick.  ``$0.00`` on a Cancelled row is what a balance should
        count it as; it is not what the envelope was budgeted.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "400.00",
        )
        txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.flush()
        basis = _basis_for(seed_user)

        assert amounts_by_id([txn], basis) == {txn.id: Decimal("400.00")}
        assert contributions_by_id([txn], basis) == {txn.id: Decimal("0")}

    def test_an_entered_actual_does_not_move_the_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """A human's figure is what the row COST, never what it was budgeted.

        The control on the surprises list: its two terms are the estimate and
        the actual, so an estimate accessor that answered the actual would make
        every delta zero and the list empty.  ``owned_amount`` and
        ``owned_contribution`` are the pair, and this is the row that separates
        them.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
        )
        txn.actual_amount = Decimal("81.40")
        db.session.flush()

        assert owned_amount(txn) == Decimal("60.00")
        assert owned_contribution(txn) == Decimal("81.40")
        assert amounts_by_id(
            [txn], _basis_for(seed_user),
        ) == {txn.id: Decimal("60.00")}

    def test_the_batch_refuses_rather_than_skipping_a_derived_row(
        self, app, db, seed_user, seed_periods,
    ):
        """``owned_amount`` raises where the column it replaced answered ``None``.

        The reason every settled-only reader takes the accessor rather than the
        column: on the day a cutover points a derived row at one of them, the
        failure is named and loud instead of a ``None`` reaching a subtraction.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_user, seed_periods[0], template)

        with pytest.raises(AmountUnresolvable, match="owns its amount"):
            owned_amount(txn)


class TestThePinsAreTheContractNow:
    """The guard that replaced ``priced_ids``, and the axis it protects.

    Deleting the membership set removed a refusal, and an adversarial review of
    this step's own build pointed out that its replacement control proved the
    SAFE direction -- a basis answers for a row it was not built over -- while
    the unsafe one had nothing asserting it.  The unsafe direction is not a
    miss, it is a silently different number: ``LoanPricing`` resolves a loan
    against ITS scenario's payment history, so a foreign basis answers a
    different ``monthly_payment`` and says nothing.

    ``scenario_id`` is a NOT NULL column on every row, so the check is total
    where the membership set was only ever as good as the caller's row list.
    """

    def test_a_row_from_another_scenario_is_REFUSED(
        self, app, db, seed_user, seed_periods,
    ):
        """The pins are checked, and the refusal names both scenarios."""
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Haircut", "35.00",
        )
        db.session.flush()
        foreign = amount_basis(
            seed_user["user"].id, seed_user["scenario"].id + 1,
        )

        with pytest.raises(AmountUnresolvable, match="prices scenario"):
            resolve_transaction_amount(txn, foreign)

    def test_the_batch_refuses_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):
        """``amounts_by_id`` inherits it: no row is priced past the pins.

        The batch has no status gate above the resolve, so it is the surface a
        cross-scenario row reaches first on a page that loads every status.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Fuel", "60.00")
        db.session.flush()
        foreign = amount_basis(
            seed_user["user"].id, seed_user["scenario"].id + 1,
        )

        with pytest.raises(AmountUnresolvable, match="prices scenario"):
            amounts_by_id([txn], foreign)


class TestOneRowHasOneDisplAyedFigure:
    """Every surface shows a row's amount by ONE rule (``display_amounts_by_id``).

    An adversarial review found that rule written twice and differently: the
    grid merged the seam's live-override map over its resolved one, while every
    HTMX fragment and the companion view published the resolved map ALONE under
    the same context key.  So a projected salary row whose profile had moved
    past its cached column showed the live net on the grid and the stale column
    in the quick-edit box the same click opened -- and that box is what a save
    posts back from, which is how a figure nobody saw gets booked.
    """

    def test_the_displayed_figure_supersedes_the_stored_column(
        self, app, db, seed_user, seed_periods,
    ):
        """A salary row displays its LIVE net, not the figure it stores.

        The row is built OWNING a deliberately wrong column, so the two answers
        are distinguishable: reading the column gives ``$999.99`` and the rule
        gives what the profile pays.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_user, seed_periods[0], template, is_income=True, owns=True,
        )
        basis = _basis_for(seed_user)

        displayed = display_amounts_by_id([paycheck], basis)[paycheck.id]

        assert displayed != Decimal(_NOT_AN_ANSWER)
        assert displayed == income_service.salary_net_for(
            paycheck, basis.salary,
        )

    def test_an_ordinary_row_displays_what_it_resolves_to(
        self, app, db, seed_user, seed_periods,
    ):
        """No live producer answers, so the rule is the resolver's answer.

        The non-vacuity partner: without it the test above would pass for a
        rule that returned the live map alone and answered nothing for every
        row that has no live figure -- which is most of the grid.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Rent", "1200.00")
        db.session.flush()
        basis = _basis_for(seed_user)

        assert display_amounts_by_id([txn], basis) == {
            txn.id: Decimal("1200.00"),
        }


class TestPricingReadsNoSTATUS:
    """Rules 2 and 4 answer whatever a row's status is, and that is the point.

    Plan step X-au-c2b's headline behaviour change, and an adversarial review of
    this file found it graded by NO case: every salary and loan fixture here is
    Projected, so planting ``if not is_projected(txn): return None`` at the top
    of either rule failed nothing.  The maps those rules used to index were
    built by the read-time REPAIR, which filters to Projected non-overridden
    rows -- so a Cancelled paycheck was refused for a reason that has nothing to
    do with what a paycheck is worth, and a Cancelled loan payment was refused
    as though its LOAN would not resolve, which is a different and alarming
    statement.

    Finding **N-262**'s rule one tier down: status says whether a row COUNTS,
    never what prices it.  ``routes/grid/page.py`` prices every loaded row with
    no status predicate, and this file records that production carries 7
    Cancelled and 2 Credit template-linked rows, so the arm is reachable the day
    a cutover declares that bucket derived.
    """

    @pytest.mark.parametrize(
        "status_enum",
        [StatusEnum.CANCELLED, StatusEnum.CREDIT, StatusEnum.RECEIVED],
    )
    def test_a_paycheck_prices_the_same_whatever_its_status(
        self, app, db, seed_user, seed_periods, status_enum,
    ):
        """Rule 2 answers the live net for a row no repair would touch."""
        template, _profile = _salary_template(seed_user)
        projected = _template_row(
            seed_user, seed_periods[0], template, is_income=True,
        )
        moved = _template_row(
            seed_user, seed_periods[1], template, is_income=True,
        )
        moved.status_id = ref_cache.status_id(status_enum)
        db.session.flush()
        basis = _basis_for(seed_user)

        # Same template, two periods, so the figures are each period's own --
        # what is asserted is that BOTH answer rather than that they are equal.
        assert resolve_transaction_amount(projected, basis) != Decimal(
            _NOT_AN_ANSWER,
        )
        assert resolve_transaction_amount(moved, basis) != Decimal(
            _NOT_AN_ANSWER,
        )

    @pytest.mark.parametrize(
        "status_enum", [StatusEnum.CANCELLED, StatusEnum.CREDIT],
    )
    def test_a_loan_payment_prices_the_same_whatever_its_status(
        self, app, db, seed_user, seed_periods, status_enum,
    ):
        """Rule 4 resolves the loan for a shadow the repair skips.

        The repair (:meth:`LoanPricing.live_cash`) answers ``None`` here, which
        is correct -- there is no stored figure to supersede on a row nobody is
        counting -- and the RULE still prices it.  Those two being different
        questions is the whole split.
        """
        shadow, _rows = _loan_payment(seed_user, seed_periods[0], derive=True)
        shadow.status_id = ref_cache.status_id(status_enum)
        db.session.flush()
        basis = _basis_for(seed_user)

        assert basis.loans.live_cash(shadow) is None
        assert resolve_transaction_amount(shadow, basis) == Decimal("1499.10")
