"""The amount model's ONE constraint: a figure and a source are paired.

Plan step **X-au-c1**, ruling **R-FI**: *a row's amount is either its OWN -- a
human authored the figure, or the money moved -- or it is DERIVED, and a derived
amount is not stored at all.*  ``budget.transactions.estimated_amount`` and
``budget.transfers.amount`` became NULLABLE in migration ``b3f7c2a9d514``, and
what makes that safe rather than merely permissive is
``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``: the
presence of a figure and the presence of an ``amount_source_id`` are exact
complements, so neither column can move without the other saying so.

**Every test here is a FIRING CONTROL, and the distinction matters** (
``docs/plans/verification.md`` standard 4).  No production row is derived as of
this step, so nothing in the app exercises these constraints; a test that merely
asserted the constraint EXISTS would pass against a constraint that admitted
everything.  Each test below writes the state the constraint is supposed to
refuse and asserts the write is refused, by NAME, at the database tier -- which
is the only tier that can see a writer bypassing the ORM.

The shapes under test, and the real writer each one stands for:

* **a figure kept while a source is declared** -- a cutover that stamps the
  declaration and forgets to empty the column, leaving exactly the stale derived
  figure this arc exists to delete;
* **a figure emptied with no source declared** -- the mirror, which would make a
  row unpriceable with nothing recording why;
* **both moved together** -- the legitimate act, which must be ACCEPTED, because
  a constraint that refuses the correct write is worse than none;
* **two pricing links on one row** -- ``ck_transactions_one_pricing_link``,
  which makes a documented convention structural (the balance README states
  ``template_id`` / ``transfer_id`` exclusivity as a CONVENTION with nothing
  enforcing it; ``credit_payback_for_id`` is the third link);
* **deleting a ``ref.amount_sources`` row a derived row names** -- the FK's
  RESTRICT, without which the ref DELETE would silently convert a derived row
  into one claiming to own an amount it does not have;
* **an AD-HOC transfer declaring a source** -- ``ck_transfers_adhoc_owns_amount``,
  which is what keeps ``uq_transfers_adhoc_dedupe`` working now that the column in
  its key can be NULL (PostgreSQL indexes NULLs as DISTINCT, so two ad-hoc
  transfers with no figure would both insert and the double-submit guard would be
  off);
* **the DOWNGRADE guard**, the migration's only non-DDL logic, driven directly
  because the Alembic chain never leaves a NULL figure behind for it to meet.

The model-property half is here too: ``Transaction.effective_amount`` and
``Transfer.effective_amount`` cannot resolve a derived figure -- they are pure
in-memory reads and the SALARY rule needs the owner's whole pay-period set -- so
they REFUSE rather than answering ``None``.  Those arms are unreachable in the
app today, and these tests are what prove they behave as designed when plan
steps X-au-d..X-au-i make them reachable.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc
from sqlalchemy import insert

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.ref import AmountSource, FilingStatus, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction_template import TransactionTemplate
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    generate_row_of,
    load_migration_module,
    make_every_period_rule,
    settle_day_columns,
    settlement_columns,
)
from app.services.cash_ledger import (
    derived_amount_basis,
    resolve_transfer_amount,
    settled_cash_leg,
)
from app.utils.balance_predicates import is_balance_contributing
from app.services.row_valuation import settled_contribution
from app.models.loan_payment_settings import LoanPaymentSettings
from app.services.amount_ownership import state_own_amount
from app.models.transfer_template import TransferTemplate

_MIGRATION = load_migration_module("b3f7c2a9d514_amount_ownership.py")
_SHADOW_CUTOVER = load_migration_module(
    "c9a4e7b21d58_a_transfer_shadow_is_derived.py",
)
_SALARY_CUTOVER = load_migration_module(
    "d7b2e6c1a483_a_projected_paycheck_is_not_stored.py",
)
_TEMPLATE_CUTOVER = load_migration_module(
    "c8f3a5d2e714_a_template_row_reads_its_templates_series.py",
)
_TRANSFER_CUTOVER = load_migration_module(
    "b7e4c1f38a20_a_generated_transfer_reads_its_definition.py",
)


def _salary_template(seed_user):
    """A definition an ACTIVE salary profile names, stating a distant scalar.

    ``default_amount`` is ``$11.11`` and every settled figure the cases below
    record is in the thousands, so the two restore arms of migration
    ``d7b2e6c1a483`` can never answer the same number by accident -- which is
    what lets a reversed statement order fail on the FIGURE rather than on a
    count.  It carries a cadence and NO price series: its rows are the
    engine's (:func:`_make_transaction`), and the migrations driven here read
    the scalar, never the series.

    Args:
        seed_user: The ``seed_user`` fixture payload.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    income = db.session.query(TransactionType).filter_by(name="Income").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=income.id,
        name="Paycheck",
        default_amount=Decimal("11.11"),
    )
    db.session.add(template)
    db.session.flush()
    make_every_period_rule(db.session, template)
    db.session.add(SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=db.session.query(FilingStatus).first().id,
        template_id=template.id,
        name="X-au-d control",
        annual_salary=Decimal("104000.00"),
        state_code="NC",
        is_active=True,
    ))
    db.session.flush()
    return template


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return a Projected row, with *overrides* applied.

    Two arms, decided by whether *overrides* names a definition:

    * **A row of a DEFINITION** (``template_id`` set) is the ENGINE's row of
      that template in the named paycheck (:func:`generate_row_of`, plan step
      balance:X-cf), flushed, with every OTHER override then laid onto it
      bare.  The row arrives derived, dated and answering an occurrence; a
      case that means the OWNER's re-priced row states ``amount_ownership``
      and ``is_override`` and gets exactly the two acts the re-price door
      performs.  ``pay_period_id`` picks the paycheck the engine writes into
      and is not restated afterwards.
    * **An AD-HOC row** (no ``template_id``, or ``None``) is constructed bare
      and returned UNFLUSHED, as before.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.  A ``pay_period_id``
            override must name one of them.
        **overrides: Column values to set or replace.  The amount-ownership
            pair is one of them -- ``amount_ownership`` -- and since plan step
            X-au-k it is the ONLY way an ORM caller can state it; the two
            unpaired shapes this module grades are written by
            :func:`_insert_transaction_row` instead.

    Returns:
        The :class:`~app.models.transaction.Transaction`: flushed on the
        definition arm, unflushed on the ad-hoc one.
    """
    template_id = overrides.pop("template_id", None)
    if template_id is not None:
        return _row_of_definition(seed_periods, template_id, overrides)
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    fields = {
        "user_id": seed_periods[0].user_id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "amount_ownership": AmountOwnership.own(Decimal("300.00")),
    }
    fields.update(overrides)
    fields.update(_settle_day_pair(overrides))
    return Transaction(**fields)


def _row_of_definition(seed_periods, template_id, overrides):
    """The definition arm of :func:`_make_transaction`: the engine's row.

    Args:
        seed_periods: The ``seed_periods`` fixture list.
        template_id: The definition whose row is wanted.
        overrides: The caller's remaining column values, laid onto the
            generated row bare after ``pay_period_id`` has chosen the paycheck.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    period_id = overrides.pop("pay_period_id", seed_periods[0].id)
    period = next(p for p in seed_periods if p.id == period_id)
    txn = generate_row_of(db.session.get(TransactionTemplate, template_id), period)
    for column, value in {**overrides, **_settle_day_pair(overrides)}.items():
        setattr(txn, column, value)
    return txn


def _settle_day_pair(overrides):
    """Complete the settle DAY with its basis unless the caller stated one.

    (Plan step **X-az**.)  These builders write bare columns on purpose -- a
    control routed through a door would grade the door -- but a row is only
    bare on the axis its test is ABOUT: a day with no basis violates
    ``ck_*_settle_day_basis_pairing`` before it can reach the constraint the
    test is grading, so the pair is completed here and a test that means to
    break it says ``settled_day_basis_id`` outright.

    Args:
        overrides: The caller's column values.

    Returns:
        The two settle-day columns to lay on, or nothing when the caller
        stated the basis.
    """
    if "settled_day_basis_id" in overrides:
        return {}
    return settle_day_columns(overrides.get("settled_on"))


def _insert_transaction_row(seed_user, seed_periods, *, figure, source_id,
                            **overrides):
    """INSERT a transaction row through Core, bypassing the ORM entirely.

    **This is how the unpaired shapes are written since plan step X-au-k, and
    the change makes the control stronger rather than weaker.**  Before it,
    ``estimated_amount`` and ``amount_source_id`` were two mapped columns and a
    test could hand the ORM either half; now they are one mapped attribute over
    :class:`~app.models.amount_ownership.AmountOwnership`, which refuses a
    figure beside a relation, so an ORM path cannot reach the state the CHECK
    is supposed to refuse.  Routing the control through the ORM would therefore
    have graded the new TYPE -- and the constraint's own job is the writer that
    is NOT this application: a migration, a ``psql`` session, a trigger.  Core
    is that writer.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        figure: What to write to ``estimated_amount`` (may be ``None``).
        source_id: What to write to ``amount_source_id`` (may be ``None``).
        **overrides: Any other column values to set or replace.

    Returns:
        The Core ``insert()`` result.

    Raises:
        sqlalchemy.exc.IntegrityError: When the row breaks a constraint, which
            is what every caller here is asserting.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    values = {
        "user_id": seed_periods[0].user_id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": figure,
        "amount_source_id": source_id,
    }
    # Overrides FIRST: ``settle_day_columns`` reads ``settled_on`` to decide
    # the basis beside it, so applying them the other way round would pair a
    # caller's settle day with no basis and kill the row on
    # ``ck_transactions_settle_day_basis_pairing`` rather than on the
    # constraint under test -- a control that fires for the wrong reason.
    values.update(overrides)
    values.update(settle_day_columns(values.get("settled_on")))
    return db.session.execute(insert(Transaction).values(**values))


def _insert_transfer_row(data, *, figure, source_id, **overrides):
    """INSERT a transfer row through Core.  The twin of the function above.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        figure: What to write to ``amount`` (may be ``None``).
        source_id: What to write to ``amount_source_id`` (may be ``None``).
        **overrides: Any other column values to set or replace.

    Returns:
        The Core ``insert()`` result.

    Raises:
        sqlalchemy.exc.IntegrityError: When the row breaks a constraint.
    """
    values = {
        "user_id": data["user"].id,
        "from_account_id": data["account"].id,
        "to_account_id": data["savings_account"].id,
        "transfer_template_id": data["transfer_template"].id,
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "amount": figure,
        "amount_source_id": source_id,
    }
    values.update(overrides)
    return db.session.execute(insert(Transfer).values(**values))


def _make_transfer(data, **overrides):
    """Return an unflushed Projected GENERATED transfer, with *overrides* applied.

    It carries the fixture's transfer template by default, and that default is
    load-bearing rather than convenient: ``ck_transfers_adhoc_owns_amount`` refuses
    a declaration on a transfer no definition prices, so a test that declares a
    source has to build a generated transfer to be talking about a legal row at
    all.  The two ad-hoc controls below pass ``transfer_template_id=None``
    explicitly and say why.

    Args:
        data: The ``seed_full_user_data`` fixture payload (it carries the second
            account a transfer needs -- ``ck_transfers_different_accounts`` -- and
            the transfer template).
        **overrides: Column values to set or replace.

    Returns:
        The unflushed :class:`~app.models.transfer.Transfer`.
    """
    fields = {
        "user_id": data["user"].id,
        "from_account_id": data["account"].id,
        "to_account_id": data["savings_account"].id,
        "transfer_template_id": data["transfer_template"].id,
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "amount_ownership": AmountOwnership.own(Decimal("100.00")),
    }
    fields.update(overrides)
    return Transfer(**fields)


class TestTransactionAmountOwnership:
    """``ck_transactions_amount_ownership`` refuses both unpaired states."""

    def test_a_declared_source_may_not_keep_a_figure(
        self, app, db, seed_user, seed_periods
    ):
        """Declaring a source while keeping the figure is refused.

        The forgetful-writer shape, and the one with money in it: every private
        repair mechanism ruling R-FI names writes the amount column ALONE, so a
        cutover that stamps the declaration without emptying the column would
        leave a figure that no longer follows its own inputs.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                _insert_transaction_row(
                    seed_user, seed_periods,
                    figure=Decimal("300.00"),
                    source_id=ref_cache.amount_source_id(
                        AmountSourceEnum.TEMPLATE
                    ),
                )
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_user, seed_periods
    ):
        """Emptying the figure with no source declared is refused.

        The mirror shape: a row with neither a figure nor a statement of who
        prices it is unpriceable, and nothing on the row would say why.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                _insert_transaction_row(
                    seed_user, seed_periods, figure=None, source_id=None,
                )
            db.session.rollback()

    def test_declaring_a_source_and_emptying_the_figure_is_accepted(
        self, app, db, seed_user, seed_periods
    ):
        """The legitimate cutover act -- both columns moved together -- is allowed.

        Without this the suite could not tell a correct constraint from one that
        refuses every write to the pair.
        """
        with app.app_context():
            template_source = ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE
            )
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(template_source),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.estimated_amount is None
            assert txn.amount_source_id == template_source

    def test_a_row_that_owns_its_amount_is_accepted(
        self, app, db, seed_user, seed_periods
    ):
        """The state every production row is in today: a figure and no source."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()

            assert txn.estimated_amount == Decimal("300.00")
            assert txn.amount_source_id is None


class TestTransferAmountOwnership:
    """``ck_transfers_amount_ownership`` is the same rule on the second column."""

    def test_a_declared_source_may_not_keep_a_figure(
        self, app, db, seed_full_user_data
    ):
        """A transfer declaring a source while holding a figure is refused.

        The writer this stands for is named in the constraint's own comment:
        ``transfer_service`` copies the parent's figure onto both shadows and a
        drift corrector repairs the copies that got away.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                _insert_transfer_row(
                    seed_full_user_data,
                    figure=Decimal("100.00"),
                    source_id=ref_cache.amount_source_id(
                        AmountSourceEnum.TEMPLATE
                    ),
                )
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_full_user_data
    ):
        """A transfer with no figure and no source is refused."""
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                _insert_transfer_row(
                    seed_full_user_data, figure=None, source_id=None,
                )
            db.session.rollback()

    def test_declaring_a_source_and_emptying_the_figure_is_accepted(
        self, app, db, seed_full_user_data
    ):
        """A derived transfer -- source declared, no figure -- is allowed.

        ``ck_transfers_positive_amount`` (``amount > 0``) does NOT block this: a
        comparison with NULL is UNKNOWN and a CHECK admits UNKNOWN, so the
        ownership pairing is the only thing deciding when the column may be
        empty.  Stated as a test because a reader meeting ``amount > 0`` on a
        nullable column has to work that out.
        """
        with app.app_context():
            template_source = ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE
            )
            xfer = _make_transfer(
                seed_full_user_data,
                amount_ownership=AmountOwnership.derived(template_source),
            )
            db.session.add(xfer)
            db.session.flush()

            assert xfer.amount is None
            assert xfer.amount_source_id == template_source


class TestAdHocTransferOwnsItsAmount:
    """``ck_transfers_adhoc_owns_amount``: no definition, no declaration.

    ``cash_ledger.resolve_transfer_amount`` answers OWN for a transfer with no
    template, so a declaration on an ad-hoc transfer names a relation that cannot
    be reached.  The reason it is a CONSTRAINT and not a comment is
    ``uq_transfers_adhoc_dedupe``: its key includes ``amount``, and an ad-hoc
    transfer with a NULL figure would slip past the index that exists to stop a
    double-submit from doubling a projected debit and credit (F-050 / C-22).
    """

    def test_an_adhoc_transfer_may_not_declare_a_source(
        self, app, db, seed_full_user_data
    ):
        """A transfer with no template and a declared source is refused."""
        with app.app_context():
            xfer = _make_transfer(
                seed_full_user_data,
                transfer_template_id=None,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_adhoc_owns_amount",
            ):
                db.session.flush()
            db.session.rollback()

    def test_the_dedupe_index_still_sees_two_identical_adhoc_transfers(
        self, app, db, seed_full_user_data
    ):
        """The guard the constraint protects, shown still guarding.

        This is the control that gives the constraint its meaning: with a figure
        present, ``uq_transfers_adhoc_dedupe`` refuses the second of two identical
        ad-hoc transfers.  Without ``ck_transfers_adhoc_owns_amount`` the same
        pair could evade it by carrying no figure at all.
        """
        with app.app_context():
            first = _make_transfer(
                seed_full_user_data, transfer_template_id=None,
            )
            db.session.add(first)
            db.session.flush()

            second = _make_transfer(
                seed_full_user_data, transfer_template_id=None,
            )
            db.session.add(second)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="uq_transfers_adhoc_dedupe",
            ):
                db.session.flush()
            db.session.rollback()


class TestOnePricingLink:
    """``ck_transactions_one_pricing_link``: a row is priced through at most one relation.

    Measured before it was imposed, on a 2026-08-12 production clone at
    ``a9d3c15e7f42``: 997 rows -- 606 template-linked, 342 transfer shadows, 21
    CC paybacks, 28 with no link -- and 0 holding two of the three.
    """

    def test_a_template_row_may_not_also_name_a_transfer(
        self, app, db, seed_full_user_data
    ):
        """template_id + transfer_id on one row is refused.

        Two links means two candidate answers for "who prices this row", with
        only dispatch ORDER separating them -- which is the link-derived
        discriminator ruling R-FI refused, arriving as data instead of as code.
        """
        with app.app_context():
            data = seed_full_user_data
            xfer = _make_transfer(data)
            db.session.add(xfer)
            db.session.flush()

            # Period 1, not 0, and it is REQUIRED rather than a precaution:
            # the fixture's row is the engine's (plan step balance:X-cf) and
            # already answers period 0's occurrence, so ``generate_row_of``
            # refuses to write a second one there ("wrote 0 rows") before the
            # constraint under test is ever reached.  It began as a precaution
            # against the undated index firing first, while the fixture's row
            # was hand-built; that cause is gone and this one replaced it.
            txn = _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                template_id=data["template"].id,
                transfer_id=xfer.id,
            )
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_one_pricing_link",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_payback_may_not_also_name_a_template(
        self, app, db, seed_full_user_data
    ):
        """credit_payback_for_id + template_id on one row is refused.

        The third link is the one ruling R-FI's own evidence turns on: a CC
        payback carries NEITHER template nor transfer while its amount IS
        derived, which is why the discriminator is declared rather than read off
        the links.  Its exclusivity is the same convention.
        """
        with app.app_context():
            data = seed_full_user_data
            source_row = _make_transaction(data, data["periods"])
            db.session.add(source_row)
            db.session.flush()

            payback = _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                name="Payback control",
                credit_payback_for_id=source_row.id,
                template_id=data["template"].id,
            )
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_one_pricing_link",
            ):
                db.session.flush()
            db.session.rollback()

    def test_exactly_one_link_is_accepted(
        self, app, db, seed_full_user_data
    ):
        """A template-linked row -- the commonest shape on production -- is allowed."""
        with app.app_context():
            data = seed_full_user_data
            txn = _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                template_id=data["template"].id,
            )
            db.session.flush()

            assert txn.template_id == data["template"].id


class TestAmountSourceReferentialIntegrity:
    """The FK is RESTRICT, so a referenced source row cannot vanish."""

    def test_deleting_a_named_source_is_refused(
        self, app, db, seed_user, seed_periods
    ):
        """Deleting a ``ref.amount_sources`` row a derived row names is refused.

        With SET NULL or NO ACTION this DELETE would convert every derived row
        naming it into a row claiming to own an amount it does not have -- the
        exact state ``ck_transactions_amount_ownership`` exists to forbid,
        arriving through the ref table's back door.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            source_row = (
                db.session.query(AmountSource)
                .filter_by(name=AmountSourceEnum.TEMPLATE.value).one()
            )
            db.session.delete(source_row)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transactions_amount_source_id",
            ):
                db.session.flush()
            db.session.rollback()


class TestTheCheapAccessorRefusesAnUnsettledRow:
    """The producer-free accessors refuse a row whose amount they cannot resolve.

    They read the row and nothing else, and the SALARY rule's producer needs the
    owner's whole pay-period set to answer at all
    (``income_service.SalaryPricing`` runs the paycheck engine over every
    period), so no accessor of this shape can hold that derivation.  Answering
    ``None`` would put one into a money path; answering zero would remove real
    money from a balance in silence.

    **The subject moved at plan step X-au-c2 and the rule did not.**  These
    cases graded ``Transaction.effective_amount`` and
    ``Transfer.effective_amount``, both now deleted; they grade
    ``row_valuation.settled_contribution`` and
    ``cash_ledger.resolve_transfer_amount``, which is where the refusal lives.
    Keeping them is the point: the refusal is what makes the per-kind cutovers
    (X-au-d..X-au-i) safe to ship one at a time, because a reader they have not
    routed fails LOUDLY rather than publishing a wrong number.

    **THE TRANSACTION HALF'S REFUSAL WIDENED AT PLAN STEP X-bx, and the class
    is renamed for it** (finding **BAL-465**).  It was "refuses a DERIVED row",
    and that was the accidental half of the guarantee: the accessor fell
    through to ``estimated_amount``, so a row was refused only when the column
    happened to be empty.  A Projected row that OWNS its figure was priced
    silently -- and the accessor's every reader asks what a row's money DID, so
    that answer reported a movement which had not happened.  It refuses on the
    STATUS now, so both cases below refuse and neither depends on the column.
    """

    def test_a_derived_transaction_refuses(
        self, app, db, seed_user, seed_periods
    ):
        """A Projected row with no figure of its own raises rather than answering."""
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(
                AmountUnresolvable, match="has not settled",
            ):
                _ = settled_contribution(txn)

    def test_a_projected_row_that_OWNS_its_figure_refuses_too(
        self, app, db, seed_user, seed_periods
    ):
        """The case plan step X-bx added, and the one that was silently WRONG.

        This row carries ``estimated_amount`` of ``$100.00``, so the deleted
        fall-through answered ``$100.00`` here -- a figure this accessor's
        readers would have booked as CONFIRMED cash, for a row whose money has
        not moved.  Nothing about the column decides it: the row has not
        SETTLED, so it recorded nothing and there is nothing to report.

        The refusal names the producer that DOES answer, because the next
        caller to trip this needs to be told what to call instead.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.add(txn)
            db.session.flush()

            # The column IS populated -- this is not the derived shape above.
            assert txn.estimated_amount == Decimal("100.00")

            with pytest.raises(
                AmountUnresolvable, match="cash_ledger.contribution_of",
            ):
                _ = settled_contribution(txn)

    def test_the_CASH_LEDGER_reader_refuses_the_same_row(
        self, app, db, seed_user, seed_periods
    ):
        """The refusal reaches a real READER, not just the accessor.

        A guard nobody has watched fire is ungraded, and the case above drives
        the accessor directly.  This drives
        :func:`~app.services.cash_ledger.settled_cash_leg` -- the "confirmed
        cash effect" reader the posting writer and the cash walk both book from
        -- with the same Projected, plan-owning row.

        **What it pins is the money.**  ``settled_cash_leg`` guards only on
        ``is_balance_contributing``, which does NOT test status, so before plan
        step X-bx this returned ``-$100.00``: a confirmed outflow, booked onto
        the account's ledger, for a bill that has not been paid.  The row still
        carries that ``$100.00``, so nothing about the column stops it -- the
        refusal one call down is the whole of what does.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.add(txn)
            db.session.flush()

            # The row CONTRIBUTES, so the reader's own guard lets it through.
            assert is_balance_contributing(txn) is True
            assert txn.estimated_amount == Decimal("100.00")

            with pytest.raises(AmountUnresolvable, match="has not settled"):
                _ = settled_cash_leg(txn)

    def test_a_derived_transfer_refuses(self, app, db, seed_full_user_data):
        """The transfer twin refuses on the same shape.

        It carries a ``due_date`` so the refusal is the one this case is about
        -- its definition states no price for that day -- rather than the
        no-date arm, which is a different defect (and has its own control in
        ``test_services/test_amount_source.py``).  Without the date the row
        refuses for the wrong reason and the test would pass while proving
        nothing about the missing FIGURE.

        **It builds its OWN definition since plan step X-au-f**, and that is
        this case being kept alive rather than tidied.  X-au-f gave the shared
        ``seed_full_user_data`` transfer template a price SERIES -- every
        app-side create door states one, and without it every generated transfer
        in the suite became unpriceable -- which silently disarmed this control:
        the refusal simply stopped firing and the case read DID NOT RAISE.  A
        template with no version is the state the refusal is ABOUT, so the case
        now constructs one instead of borrowing a fixture that no longer has it.
        """
        with app.app_context():
            template = TransferTemplate(
                user_id=seed_full_user_data["user"].id,
                from_account_id=seed_full_user_data["account"].id,
                to_account_id=seed_full_user_data["savings_account"].id,
                name="States No Price",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            xfer = _make_transfer(
                seed_full_user_data,
                transfer_template_id=template.id,
                due_date=date(2026, 3, 15),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.flush()

            with pytest.raises(
                AmountUnresolvable, match="price series is EMPTY",
            ):
                _ = resolve_transfer_amount(
                    xfer,
                    derived_amount_basis(
                        seed_full_user_data["user"].id,
                        seed_full_user_data["scenario"].id,
                    ),
                )

    def test_a_settlement_record_answers_for_a_derived_row(
        self, app, db, seed_user, seed_periods
    ):
        """A derived row that has SETTLED answers from its record, not a refusal.

        The ruling plan step X-au-c owed and this leaf makes structural: what a
        row RECORDED as having moved outranks any derivation of what it was
        expected to be, so the refusal arm sits BELOW it.  Getting the order
        wrong would refuse every settled row a per-kind cutover
        (X-au-d..X-au-i) has emptied the plan of.

        **The row it grades changed at plan step X-au-c3.**  It was a PROJECTED
        derived row carrying ``actual_amount = 412.55`` -- the shape the
        production clone had -- and the five rows in that state were promoted
        into their PLAN by migration ``e4b8a71c0f36``, because a figure now
        RECORDS a settle.  The state is not UNCONSTRUCTIBLE, and saying it was
        (of ``ck_transactions_settled_amount_needs_basis``, corrected after
        adversarial review 2026-08-17) misread that CHECK: it pairs a figure
        with its provenance and says nothing about status, so an unsettled row
        carrying BOTH is legal and is what a revert leaves behind.  Such a row
        is worth its PLAN, which is the status gate's doing.  The ORDER under test is the same one, on
        the row that can still hold both: a settled row whose plan is DERIVED
        (no ``estimated_amount``) and whose record states ``$412.55``.
        """
        with app.app_context():
            settled_on = seed_periods[0].start_date
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
                settled_on=settled_on,
                **settlement_columns(
                    settled_on, None, submitted=Decimal("412.55"),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_contribution(txn) == Decimal("412.55")

    def test_an_excluded_status_answers_zero_for_a_derived_row(
        self, app, db, seed_user, seed_periods
    ):
        """A Cancelled derived row is worth zero, not a refusal.

        The status gates stay ABOVE the refusal: a row that contributes nothing
        needs no amount resolved, so cancelling an unpriceable row is a way OUT
        of the state rather than a second error.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_contribution(txn) == Decimal("0")

    def test_a_soft_deleted_derived_row_answers_zero(
        self, app, db, seed_user, seed_periods
    ):
        """A soft-deleted derived row is worth zero, for the same reason."""
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
                is_deleted=True,
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_contribution(txn) == Decimal("0")


class TestTheDowngradeRefusesToInventAFigure:
    """Migration ``b3f7c2a9d514``'s only non-DDL logic, driven directly.

    ``refuse_rows_without_a_figure`` is module-level for exactly this reason (the
    pattern the previous revision uses for its backfill): a guard nothing
    exercises is a guard nobody has seen work, and this one stands between a
    downgrade and a ``NOT NULL`` restore it cannot satisfy honestly.

    Definition of Done item 7 asks for both directions.  The DDL halves are
    exercised on every test-template rebuild -- ``scripts/build_test_template.py``
    replays the whole Alembic chain rather than calling ``create_all`` -- and the
    upgrade/downgrade round trip was run against a production clone (997
    transactions, 171 transfers) before this leaf shipped.  What no rebuild can
    reach is the refusal, because the chain never leaves a NULL figure behind.
    """

    def test_it_passes_when_every_row_owns_its_figure(
        self, app, db, seed_user, seed_periods
    ):
        """The state the chain leaves: no row is derived, so a downgrade is safe."""
        with app.app_context():
            db.session.add(_make_transaction(seed_user, seed_periods))
            db.session.flush()

            # Returns None rather than raising -- the assertion is the absence of
            # a refusal, so the negative control below is what gives it meaning.
            assert _MIGRATION.refuse_rows_without_a_figure(
                db.session.connection()
            ) is None

    def test_it_refuses_and_names_a_derived_transaction(
        self, app, db, seed_user, seed_periods
    ):
        """One derived row is enough to stop the downgrade, and it is NAMED.

        The id matters: the operator's next act is to downgrade the cutover that
        emptied that column, and a refusal that does not say which rows are
        derived cannot tell them which one.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(RuntimeError, match=str(txn.id)):
                _MIGRATION.refuse_rows_without_a_figure(db.session.connection())

    def test_it_refuses_a_derived_transfer_too(self, app, db, seed_full_user_data):
        """The second table is probed, not just the first.

        Both columns are in the guard's loop, and a guard that checked only
        ``transactions`` would let a downgrade fail mid-DDL on the transfers
        ``SET NOT NULL`` -- after it had already dropped the constraints.

        The fixture's own transaction is the ENGINE's row since plan step
        balance:X-cf and so is derived, which is the state the FIRST arm
        refuses; the guard raises at the first table it finds a NULL in, so
        that row would answer for the transfer's arm.  The owner re-prices it
        first -- the two acts the re-price door performs -- and the world is
        then the one this case is about: every transaction owns its figure
        and one transfer does not.
        """
        with app.app_context():
            # Re-fetched by key: the fixture committed, so its instance is
            # expired and an attribute set on it may never reach a flush.
            fixture_row = db.session.get(
                Transaction, seed_full_user_data["transaction"].id,
            )
            state_own_amount(fixture_row, Decimal("1200.00"))
            fixture_row.is_override = True
            db.session.flush()
            xfer = _make_transfer(
                seed_full_user_data,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.flush()

            with pytest.raises(RuntimeError, match="budget.transfers.amount"):
                _MIGRATION.refuse_rows_without_a_figure(db.session.connection())


class TestTheShadowCutoverDowngradeRefusesToInventAFigure:
    """Migration ``c9a4e7b21d58``'s only non-DDL logic, driven directly.

    Plan step **X-au-g-2c-2** declares every transfer SHADOW derived; its
    downgrade restores each shadow's figure from the parent transfer's own
    ``amount``.  ``refuse_a_shadow_whose_parent_states_no_figure`` is
    module-level for the reason ``b3f7c2a9d514``'s guard is: a guard nothing
    exercises is a guard nobody has seen work.

    **The DDL-free halves were driven against a copy of PRODUCTION before this
    leaf shipped** (2026-09-01, stamp ``a4c6f1d92b73`` restored into a throwaway
    database and migrated to ``dev``'s head): the upgrade declared 350 shadows
    and touched no other row, and the downgrade was BYTE-IDENTICAL over all
    1,028 transactions.  What no replay can reach is this refusal, because the
    chain never leaves a parent transfer without a figure -- plan step X-au-f is
    what creates that state, and it does not exist yet.
    """

    def test_it_passes_when_every_parent_states_a_figure(
        self, app, db, seed_full_user_data,
    ):
        """The state the chain leaves: every parent owns an amount.

        Returns ``None`` rather than raising, so the negative controls below
        are what give this meaning.
        """
        with app.app_context():
            td = seed_full_user_data
            xfer = _make_transfer(td)
            db.session.add(xfer)
            db.session.flush()
            txn = _make_transaction(
                td, td["periods"],
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.PARENT_TRANSFER),
                ),
                transfer_id=xfer.id,
                template_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            assert _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                db.session.connection(),
            ) is None

    def test_it_refuses_and_names_the_shadow_whose_parent_is_derived(
        self, app, db, seed_full_user_data,
    ):
        """A parent with no figure stops the downgrade, and the SHADOW is named.

        The id matters: the operator's next act is to downgrade the cutover
        that emptied the parent's column (plan step X-au-f), and a refusal that
        does not say which rows are stranded cannot tell them where to look.
        """
        with app.app_context():
            td = seed_full_user_data
            xfer = _make_transfer(
                td,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.flush()
            txn = _make_transaction(
                td, td["periods"],
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.PARENT_TRANSFER),
                ),
                transfer_id=xfer.id,
                template_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            # Anchored on the ids LIST rather than the bare digits.  The
            # message also carries the revision id ``c9a4e7b21d58``, whose
            # digits include 9, 4, 7, 2, 1, 5 and 8 -- so ``match=str(txn.id)``
            # is ``re.search`` over a string that already contains most small
            # ids, and would pass on a guard that named the TRANSFER instead of
            # the shadow.  That is the exact property this case exists for.
            with pytest.raises(
                RuntimeError, match=rf"\(ids [^)]*\b{txn.id}\b",
            ):
                _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                    db.session.connection(),
                )

    def test_a_derived_parent_with_no_declared_shadow_does_not_refuse(
        self, app, db, seed_full_user_data,
    ):
        """The probe is scoped to DECLARED shadows, not to derived parents.

        The mutation this rules out is a guard written as "any transfer with no
        amount", which would refuse a downgrade that had nothing to restore --
        turning a safe round trip into a dead end. The parent here is derived
        and its shadow owns its own figure, so there is no restore to attempt.
        """
        with app.app_context():
            td = seed_full_user_data
            xfer = _make_transfer(
                td,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.flush()
            txn = _make_transaction(
                td, td["periods"],
                amount_ownership=AmountOwnership.own(Decimal("25.00")),
                transfer_id=xfer.id,
                template_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            assert _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                db.session.connection(),
            ) is None


class TestTheSalaryCutoverKnowsWhatItCannotRestore:
    """Migration ``d7b2e6c1a483``'s only non-DDL logic, driven directly.

    Plan step **X-au-d** declares every non-override SALARY row derived; its
    downgrade restores a settled row from ``settled_amount`` and every other row
    from its template's ``default_amount``.  The first arm is EXACT only where
    the settlement basis is ``derived`` -- that basis MEANS the settle recorded
    the app's own resolution, which is the plan the upgrade emptied.  On any
    other basis the plan at settle is stored nowhere,
    :func:`settled_rows_whose_plan_is_not_recoverable` says so, and the row
    falls to the placeholder arm.

    It is module-level for the reason ``b3f7c2a9d514``'s and ``c9a4e7b21d58``'s
    guards are: a guard nothing exercises is a guard nobody has seen work.  An
    adversarial review of this step found the sentence saying so with no case
    behind it, which is exactly the shape it warns about.

    **The DDL-free halves were driven against a copy of PRODUCTION before this
    step shipped** (2026-09-02, stamp ``a4c6f1d92b73`` restored into a throwaway
    database and migrated to ``dev``'s head ``b7a41e2c9d63``): the upgrade
    declared 59 rows and touched no other, the downgrade restored 8 exactly from
    their settlement record and 51 from the template's scalar, and the probe
    returned empty because all four ``corrected`` salary settlements carry
    ``is_override`` and are therefore never declared.
    """

    @staticmethod
    def _declared_salary_row(seed_user, seed_periods, template, **overrides):
        """Return a flushed INCOME row of *template*, DECLARED derived.

        The engine's row of a salary definition is exactly that -- income by
        its template, derived by construction -- so nothing here restates
        either; *overrides* are the settlement the case lays on.
        """
        txn = _make_transaction(
            seed_user, seed_periods, template_id=template.id, **overrides,
        )
        db.session.flush()
        return txn

    def test_a_derived_basis_settlement_is_recoverable(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordinary shape: the record IS the plan, so nothing is named.

        Returns an empty list rather than raising, so the two negative controls
        below are what give this meaning.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            self._declared_salary_row(
                seed_user, seed_periods, salary_template,
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                settled_on=date(2026, 1, 5),
                **settlement_columns(date(2026, 1, 5), Decimal("2473.38")),
            )

            assert _SALARY_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == []

    def test_a_CORRECTED_settlement_is_named(
        self, app, db, seed_user, seed_periods,
    ):
        """A human's figure is not the plan, so the plan is unrecoverable.

        The id is what the operator needs: the downgrade restores such a row
        from the template's scalar, and a report that did not say which rows
        took the placeholder could not be checked against anything.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            txn = self._declared_salary_row(
                seed_user, seed_periods, salary_template,
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                settled_on=date(2026, 1, 5),
                **settlement_columns(
                    date(2026, 1, 5), Decimal("2473.38"),
                    submitted=Decimal("2400.00"),
                ),
            )

            assert _SALARY_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == [txn.id]

    def test_a_CORRECTED_row_that_is_NOT_declared_is_not_named(
        self, app, db, seed_user, seed_periods,
    ):
        """The probe is scoped to DECLARED rows, not to corrected ones.

        The mutation this rules out is a probe written as "any corrected
        settlement", which would report every hand-corrected row in the
        database on a downgrade that has nothing to do with them -- and on
        production that is exactly the four rows the upgrade deliberately left
        alone.  This row OWNS its figure, so the downgrade restores nothing to
        it and there is nothing to warn about.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=salary_template.id,
                is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("2562.67")),
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                settled_on=date(2026, 1, 5),
                **settlement_columns(
                    date(2026, 1, 5), Decimal("2562.67"),
                    submitted=Decimal("2524.62"),
                ),
            )
            db.session.flush()

            assert _SALARY_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == []


class TestTheSalaryCutoverRestoresEachRowFromTheRightPlace:
    """The downgrade's two arms, driven over one connection.

    **The ORDER of the two statements is load-bearing and nothing else asserts
    it**: the exact restore runs first, so a row it covers is no longer declared
    when the placeholder restore's predicate is evaluated.  Reversed, every
    ``derived``-basis settled row silently comes back at the template's
    ``default_amount`` instead of the figure it recorded -- which on production
    is eight paychecks restored ``$99.40`` too high each.  Found by an
    adversarial review of this step, which noted the ordering was stated in a
    comment and graded nowhere.
    """

    def test_a_settled_row_comes_back_from_its_RECORD_and_not_the_scalar(
        self, app, db, seed_user, seed_periods,
    ):
        """Both arms in one run, so the ordering is what is under test.

        Two rows of one template: a settled one whose record says
        ``$2,473.38`` and a projected one with no record at all.  The template's
        scalar is ``$11.11``, far from either, so a reversed order gives the
        settled row ``$11.11`` and this fails on the figure rather than on a
        count.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            settled = TestTheSalaryCutoverKnowsWhatItCannotRestore.\
                _declared_salary_row(
                    seed_user, seed_periods, salary_template,
                    status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                    settled_on=date(2026, 1, 5),
                    **settlement_columns(date(2026, 1, 5), Decimal("2473.38")),
                )
            projected = TestTheSalaryCutoverKnowsWhatItCannotRestore.\
                _declared_salary_row(
                    seed_user, seed_periods, salary_template,
                    pay_period_id=seed_periods[1].id,
                )
            db.session.commit()

            _SALARY_CUTOVER.downgrade_rows(db.session.connection())
            db.session.expire_all()

            assert settled.estimated_amount == Decimal("2473.38")
            assert settled.amount_source_id is None
            assert projected.estimated_amount == Decimal("11.11")
            assert projected.amount_source_id is None

    def test_a_row_that_OWNS_its_figure_is_not_touched(
        self, app, db, seed_user, seed_periods,
    ):
        """The scoping control: the downgrade restores only what it declared.

        The mutation this rules out is a predicate written as "every row of a
        salary template", which would overwrite the figure a human typed on an
        overridden row with the template's scalar -- the one class the upgrade
        deliberately never declared.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            owned = _make_transaction(
                seed_user, seed_periods,
                template_id=salary_template.id,
                is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("1234.56")),
            )
            db.session.commit()

            _SALARY_CUTOVER.downgrade_rows(db.session.connection())
            db.session.expire_all()

            assert owned.estimated_amount == Decimal("1234.56")


def _plain_template(seed_user):
    """An ordinary expense definition NO salary profile names.

    ``default_amount`` is ``$7.77`` and every settled figure the cases below
    record is in the hundreds, so migration ``c8f3a5d2e714``'s two restore arms
    can never answer the same number by accident -- which is what lets a
    reversed statement order fail on the FIGURE rather than on a count.  The
    same device ``_salary_template`` uses, and for the same reason -- and,
    like it, a cadence and no price series: the cases that need a series
    state one themselves.

    Args:
        seed_user: The ``seed_user`` fixture payload.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    expense = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="X-au-e Rent",
        default_amount=Decimal("7.77"),
    )
    db.session.add(template)
    db.session.flush()
    make_every_period_rule(db.session, template)
    return template


class TestTheTemplateCutoverKnowsWhatItCannotRestore:
    """Migration ``c8f3a5d2e714``'s probe, driven directly.

    Plan step **X-au-e** declares every non-override TEMPLATE row derived; its
    downgrade restores a settled row from ``settled_amount`` and every other row
    from its template's ``default_amount``.  The first arm is EXACT only where
    the settlement basis is ``derived``.

    **Where this differs from the salary cutover above, and it is the reason
    the probe matters more here**: ``d7b2e6c1a483`` had ZERO unrecoverable rows
    on production, so its report was a guard against a state that did not
    exist.  This step has **20**, all on the ``purchases`` basis (measured on a
    clone of production restored 2026-09-03 from stamp ``a4c6f1d92b73`` and
    migrated to ``d4a92f6b13c8``), so the placeholder arm is exercised in
    practice rather than theoretically.
    """

    @staticmethod
    def _declared_row(seed_user, seed_periods, template, **overrides):
        """Return a flushed EXPENSE row of *template*, DECLARED derived.

        The engine's row, which is derived by construction; *overrides* are
        the settlement the case lays on.
        """
        txn = _make_transaction(
            seed_user, seed_periods, template_id=template.id, **overrides,
        )
        db.session.flush()
        return txn

    def test_a_derived_basis_settlement_is_recoverable(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordinary shape: the record IS the plan, so nothing is named."""
        with app.app_context():
            template = _plain_template(seed_user)
            self._declared_row(
                seed_user, seed_periods, template,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=date(2026, 1, 5),
                **settlement_columns(date(2026, 1, 5), Decimal("450.00")),
            )

            assert _TEMPLATE_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == []

    def test_a_settlement_on_ANY_OTHER_BASIS_is_named(
        self, app, db, seed_user, seed_periods,
    ):
        """A figure that is not the app's own resolution is not the plan.

        The probe's predicate is ``settled_basis_id <> derived``, so
        ``corrected`` and ``purchases`` take the SAME arm; this drives
        ``corrected`` because it is the one of the two a door can write onto a
        bare-built row (``settlement_columns`` says why: a settled ENVELOPE has
        to be settled through the seam, and one assembled column by column here
        would be a row no door in the app produces).  **Production's 20 are all
        ``purchases``** -- measured on the 2026-09-03 clone -- and they reach
        this branch by the same predicate.

        The id is what the operator needs: such a row restores from the
        template's scalar, and a report that did not say which rows took the
        placeholder could not be checked against anything.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            txn = self._declared_row(
                seed_user, seed_periods, template,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=date(2026, 1, 5),
                **settlement_columns(
                    date(2026, 1, 5), Decimal("450.00"),
                    submitted=Decimal("399.00"),
                ),
            )

            assert _TEMPLATE_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == [txn.id]

    def test_a_row_on_a_SALARY_template_is_not_named(
        self, app, db, seed_user, seed_periods,
    ):
        """The scoping control: those rows belong to ``d7b2e6c1a483``.

        The mutation this rules out is a probe written without the
        ever-salary exclusion, which would report -- and then RESTORE -- rows
        the salary cutover's own downgrade is responsible for, running after
        this one.  A row restored twice takes the second answer, and the
        second is the template's scalar rather than its settlement record.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            TestTheSalaryCutoverKnowsWhatItCannotRestore._declared_salary_row(
                seed_user, seed_periods, salary_template,
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                settled_on=date(2026, 1, 5),
                **settlement_columns(
                    date(2026, 1, 5), Decimal("2473.38"),
                    submitted=Decimal("2400.00"),
                ),
            )

            assert _TEMPLATE_CUTOVER.settled_rows_whose_plan_is_not_recoverable(
                db.session.connection(),
            ) == []


class TestTheTemplateCutoverRefusesRatherThanStrandingARow:
    """Migration ``c8f3a5d2e714``'s PRE-FLIGHT, driven directly.

    **No cutover in this family had a test of its UPGRADE until this one**, and
    an adversarial review of X-au-e is what said so: all three exposed their
    downgrade at module scope "so a test can drive it -- a guard nothing
    exercises is a guard nobody has seen work", and left the destructive half
    ungraded. The measurement behind "0 dateless, 0 empty series, `$0.00`
    differing" was taken on a clone; production moves between a measurement and
    a deploy, and each of those three violated by ONE row is a silently
    unpriceable row or a silently moved figure.

    Driven against a clone of production 2026-09-03: empty on all 525, and
    naming the row after one due date was nulled by hand.
    """

    def test_a_clean_population_strands_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordinary shape: every row is priceable and agrees."""
        with app.app_context():
            template = _plain_template(seed_user)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=template.id,
                effective_date=date(2026, 1, 1), amount=Decimal("7.77"),
            ))
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("7.77")),
            )
            db.session.commit()

            assert _TEMPLATE_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_row_with_NO_DUE_DATE_is_named_and_the_upgrade_refuses(
        self, app, db, seed_user, seed_periods,
    ):
        """The row X-au-e would empty and nothing could then price.

        ``_stated_amount`` refuses a derived row with no due date, and ruling
        D5 forbids substituting the pay period's bounds -- so declaring such a
        row makes it permanently unpriceable, and ``routes/grid/page`` prices
        every row it loads with no handler.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=template.id,
                effective_date=date(2026, 1, 1), amount=Decimal("7.77"),
            ))
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                due_date=None,
                amount_ownership=AmountOwnership.own(Decimal("7.77")),
            )
            db.session.commit()

            stranded = _TEMPLATE_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [txn.id]
            assert "no due_date" in stranded[0][3]

    def test_a_row_whose_FIGURE_DISAGREES_with_the_series_is_named(
        self, app, db, seed_user, seed_periods,
    ):
        """The cutover deletes a COPY; a disagreeing row would lose a FACT.

        This is the arm that makes the pre-flight more than a null check: the
        row is perfectly priceable, and declaring it would still change what
        it is worth.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=template.id,
                effective_date=date(2026, 1, 1), amount=Decimal("7.77"),
            ))
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("999.00")),
            )
            db.session.commit()

            stranded = _TEMPLATE_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [txn.id]
            assert "disagrees" in stranded[0][3]


class TestTheTemplateCutoverRestoresEachRowFromTheRightPlace:
    """The downgrade's two arms, driven over one connection.

    **The ORDER of the two statements is load-bearing and this is what grades
    it**: the exact restore runs first, so a row it covers is no longer
    declared when the placeholder restore's predicate is evaluated.  Reversed,
    every ``derived``-basis settled row comes back at the template's
    ``default_amount`` instead of the figure it recorded.  Measured on the
    2026-09-03 production clone: reversing the two statements moves 7 rows
    (three Geico, four Apple Music) and the placeholder arm reports
    ``UPDATE 525`` where the exact arm should have taken 46 of them first.
    """

    def test_a_settled_row_comes_back_from_its_RECORD_and_not_the_scalar(
        self, app, db, seed_user, seed_periods,
    ):
        """Both arms in one run, so the ordering is what is under test.

        Two rows of one template: a settled one whose record says ``$450.00``
        and a projected one with no record at all.  The template's scalar is
        ``$7.77``, far from either, so a reversed order gives the settled row
        ``$7.77`` and this fails on the figure rather than on a count.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            settled = TestTheTemplateCutoverKnowsWhatItCannotRestore.\
                _declared_row(
                    seed_user, seed_periods, template,
                    status_id=ref_cache.status_id(StatusEnum.DONE),
                    settled_on=date(2026, 1, 5),
                    **settlement_columns(date(2026, 1, 5), Decimal("450.00")),
                )
            projected = TestTheTemplateCutoverKnowsWhatItCannotRestore.\
                _declared_row(
                    seed_user, seed_periods, template,
                    pay_period_id=seed_periods[1].id,
                )
            db.session.commit()

            _TEMPLATE_CUTOVER.downgrade_rows(db.session.connection())
            db.session.expire_all()

            assert settled.estimated_amount == Decimal("450.00")
            assert settled.amount_source_id is None
            assert projected.estimated_amount == Decimal("7.77")
            assert projected.amount_source_id is None

    def test_a_row_that_OWNS_its_figure_is_not_touched(
        self, app, db, seed_user, seed_periods,
    ):
        """The scoping control: the downgrade restores only what it declared.

        The mutation this rules out is a predicate written as "every row of a
        template", which would overwrite the figure a human typed on an
        overridden row with the template's scalar -- the one class the upgrade
        deliberately never declared.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            owned = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("321.00")),
            )
            db.session.commit()

            _TEMPLATE_CUTOVER.downgrade_rows(db.session.connection())
            db.session.expire_all()

            assert owned.estimated_amount == Decimal("321.00")
            assert owned.amount_source_id is None

    def test_a_declared_row_on_a_SALARY_template_is_LEFT_declared(
        self, app, db, seed_user, seed_periods,
    ):
        """``d7b2e6c1a483``'s downgrade runs after this one and owns them.

        Both restore statements carry the ever-salary exclusion, and this is
        what says so.  Without it a salary row would be restored here from the
        template's ``default_amount`` -- and the salary cutover's own exact
        arm, which would have restored it from its settlement record, then
        finds nothing left declared to restore.
        """
        with app.app_context():
            salary_template = _salary_template(seed_user)
            row = TestTheSalaryCutoverKnowsWhatItCannotRestore.\
                _declared_salary_row(
                    seed_user, seed_periods, salary_template,
                    status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                    settled_on=date(2026, 1, 5),
                    **settlement_columns(date(2026, 1, 5), Decimal("2473.38")),
                )
            db.session.commit()

            _TEMPLATE_CUTOVER.downgrade_rows(db.session.connection())
            db.session.expire_all()

            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.estimated_amount is None


def _state_series(data, amount, effective_on=date(2026, 1, 1)):
    """Add ONE amount version to the fixture's transfer template, directly.

    **Not through ``template_amount_service.set_amount``**, and the reason is a
    harness fact rather than a preference: the template belongs to the FIXTURE's
    session, so a write through its relationship inside a fresh
    ``app.app_context()`` appends an object that never flushes -- the ORM then
    answers the new price while the database still holds only the fixture's,
    and a probe reading SQL and an assertion reading the model disagree about
    the same template.  Measured while building these cases.  The transaction
    twin's cases add their versions the same way for the same reason.

    Dated ``2026-01-01`` by default, which is BEFORE every ``due_date`` these
    cases use and AFTER nothing -- the fixture's own version is stated at the
    owner's today, so a due date in March resolves to this one and the two
    versions together make the series' supersession observable rather than
    incidental.

    Args:
        data: The ``seed_full_user_data`` payload.
        amount: The price to state.
        effective_on: The date it takes effect.

    Returns:
        The fixture's transfer template, for a caller that goes on to attach
        loan-payment settings to it.
    """
    template = data["transfer_template"]
    db.session.add(TemplateAmountVersion(
        transfer_template_id=template.id,
        effective_date=effective_on, amount=amount,
    ))
    return template


class TestTheTransferCutoverRefusesRatherThanStrandingARow:
    """Migration ``b7e4c1f38a20``'s PRE-FLIGHT, driven directly.

    The transfer twin of :class:`TestTheTemplateCutoverRefusesRatherThanStrandingARow`,
    and it grades the half that has no sibling: **the probe asks different
    things of three CLASSES of row**, because the three have three producers
    (ruling **R-BAL10**).  An ordinary generated transfer and a MANUAL loan
    payment are priced by the definition's series and must agree with it; a
    DERIVE-mode payment is priced by the LOAN and is asked only whether that
    loan resolves.

    **The manual arm is graded against series + the standing extra, not the
    series alone**, and that is the distinction most able to go wrong silently:
    a probe comparing against the series would name every manual payment
    carrying an extra as stranded, and the upgrade would then refuse a
    population that is entirely correct.
    """

    def test_a_clean_population_strands_nothing(
        self, app, db, seed_full_user_data,
    ):
        """The ordinary shape: the row agrees with what its definition answers."""
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.add(xfer)
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_transfer_whose_figure_DISAGREES_is_named(
        self, app, db, seed_full_user_data,
    ):
        """The cutover deletes a COPY; a row where the two differ holds a FACT.

        Its figure is ``$250.00`` against a definition stating ``$100.00``, so
        emptying the column would move that row's amount by ``$150.00`` with
        nothing to say so.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("250.00")),
            )
            db.session.add(xfer)
            db.session.commit()

            stranded = _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [xfer.id]
            assert "disagrees" in stranded[0][3]

    def test_a_MANUAL_payment_stores_the_BASE_and_is_graded_against_it(
        self, app, db, seed_full_user_data,
    ):
        """The standing extra is not in the stored figure, so it is not in the test.

        ``routes/loan/payment_transfer`` opens the series at the typed BASE and
        keeps the extra on the settings row -- *"added live to every payment, in
        BOTH modes"* -- so a manual payment's row stores exactly what an
        ordinary generated transfer's does.  Series ``$100.00``, standing extra
        ``$25.00``, row ``$100.00``: a CORRECT row, and the probe must not name
        it.

        **A first draft of the probe graded this arm against ``series + extra``
        and this case hand-built an ``own($125.00)`` row to match it** -- a shape
        no writer in ``app/`` produces.  The case was green and blind, and the
        probe it defended would have RAISED on every real manual payment
        carrying an extra: migrations auto-run in the deploy pipeline, so that
        is a failed deploy. Found by an adversarial review of this step.

        What declaring the row DOES move is the PARENT's answer, from ``$100.00``
        to ``$125.00`` -- the cash that leaves the bank (**R-BAL10**) -- while
        its two legs already answered ``$125.00``, so the fold moves ``$0.00``.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            # Added DIRECTLY, for the reason ``_state_series`` states: the
            # fixture's template is not in this context's session, so assigning
            # through its ``settings`` relationship never flushes and the probe
            # -- which reads SQL -- would not see the payment at all.
            db.session.add(LoanPaymentSettings(
                transfer_template_id=data["transfer_template"].id,
                derive_from_loan=False, extra_principal=Decimal("25.00"),
            ))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.add(xfer)
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_DERIVE_payment_is_asked_for_a_LOAN_and_not_for_agreement(
        self, app, db, seed_full_user_data,
    ):
        """Its stored figure is a snapshot, so a difference is the point.

        A derive-mode payment stores what P&I + escrow came to when it was set
        up and has been free to drift ever since -- that drift IS what this
        cutover deletes.  Requiring agreement would refuse exactly the rows the
        step exists for, so the probe asks only whether the loan resolves.  Here
        the destination is the fixture's SAVINGS account, which carries no
        ``LoanParams``, so the row is named for the reason that actually makes
        it unpriceable.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            # Added DIRECTLY; see the sibling case.
            db.session.add(LoanPaymentSettings(
                transfer_template_id=data["transfer_template"].id,
                derive_from_loan=True,
            ))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("999.99")),
            )
            db.session.add(xfer)
            db.session.commit()

            stranded = _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [xfer.id]
            assert "LoanParams" in stranded[0][3]

    def test_an_OVERRIDDEN_transfer_is_not_in_the_population_at_all(
        self, app, db, seed_full_user_data,
    ):
        """The owner's figure is kept, so it is neither declared nor graded.

        Its figure disagrees with the definition by ``$150.00`` -- the shape the
        second case above names -- and it is silent here, which is what says the
        exclusion is on the PREDICATE rather than on the probe's reasons.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1), is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("250.00")),
            )
            db.session.add(xfer)
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []


class TestTheTransferCutoverRestoresEachRowFromTheRightPlace:
    """Migration ``b7e4c1f38a20``'s DOWNGRADE, and the ORDER of its two arms.

    The transfer twin of
    :class:`TestTheTemplateCutoverRestoresEachRowFromTheRightPlace`, with one
    difference that is the whole reason it needs its own case: a transfer
    carries no settlement column, so the exact restore reads its EXPENSE LEG's
    record.  A restore that read the parent would find nothing and every settled
    transfer would come back at its template's scalar.
    """

    def test_a_SETTLED_transfer_restores_EXACTLY_from_its_legs_record(
        self, app, db, seed_full_user_data,
    ):
        """The exact arm, which is the one a transfer needs its own case for.

        A transfer carries no settlement column: its money moves on its two
        LEGS and each records what it did.  So the exact restore reads the
        EXPENSE leg's ``settled_amount`` on the ``derived`` basis -- the figure
        the app itself resolved at the moment of the settle, which IS the plan
        this migration emptied.  A restore that looked for the record on the
        PARENT would find nothing and every settled transfer would come back at
        its template's scalar instead.

        The definition states ``$100.00`` and the leg recorded ``$137.42``, so
        the two arms cannot answer the same number by accident -- which is what
        lets a reversed statement order, or a restore reading the wrong row,
        fail on the FIGURE rather than on a count.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            xfer = _make_transfer(
                data,
                due_date=date(2026, 3, 1),
                status_id=ref_cache.status_id(StatusEnum.DONE),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.flush()
            db.session.add(Transaction(
                user_id=data["user"].id,
                account_id=data["account"].id,
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                transfer_id=xfer.id,
                name="Expense leg",
                due_date=date(2026, 3, 1),
                settled_amount=Decimal("137.42"),
                settled_basis_id=ref_cache.settlement_basis_id(
                    SettlementBasisEnum.DERIVED,
                ),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(
                        AmountSourceEnum.PARENT_TRANSFER,
                    ),
                ),
            ))
            db.session.commit()

            _TRANSFER_CUTOVER.downgrade_rows(db.session.connection())
            db.session.commit()
            db.session.expire_all()

            restored = db.session.get(Transfer, xfer.id)
            assert restored.amount == Decimal("137.42")
            assert restored.amount_source_id is None

    def test_an_unsettled_transfer_restores_from_its_definitions_scalar(
        self, app, db, seed_full_user_data,
    ):
        """Nothing recorded a plan for it, so the definition's own scalar answers."""
        with app.app_context():
            data = seed_full_user_data
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            db.session.commit()

            _TRANSFER_CUTOVER.downgrade_rows(db.session.connection())
            db.session.commit()
            db.session.expire_all()

            restored = db.session.get(Transfer, xfer.id)
            assert restored.amount == data["transfer_template"].default_amount
            assert restored.amount_source_id is None
