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
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.ref import AmountSource, FilingStatus, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    load_migration_module,
    settle_day_columns,
    settlement_columns,
)
from app.services.cash_ledger import resolve_transfer_amount
from app.services.row_valuation import owned_contribution

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


def _salary_template(seed_user):
    """A definition an ACTIVE salary profile names, stating a distant scalar.

    ``default_amount`` is ``$11.11`` and every settled figure the cases below
    record is in the thousands, so the two restore arms of migration
    ``d7b2e6c1a483`` can never answer the same number by accident -- which is
    what lets a reversed statement order fail on the FIGURE rather than on a
    count.

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
    """Return an unflushed Projected expense row, with *overrides* applied.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **overrides: Column values to set or replace.  The amount-ownership
            pair is one of them -- ``amount_ownership`` -- and since plan step
            X-au-k it is the ONLY way an ORM caller can state it; the two
            unpaired shapes this module grades are written by
            :func:`_insert_transaction_row` instead.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.
    """
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
    # **The settle DAY carries its basis unless the caller states one** (plan
    # step **X-az**).  These builders write bare columns on purpose -- a control
    # routed through a door would grade the door -- but a row is only bare on
    # the axis its test is ABOUT: a day with no basis violates
    # ``ck_*_settle_day_basis_pairing`` before it can reach the constraint the
    # test is grading, so the pair is completed here and a test that means to
    # break it says ``settled_day_basis_id`` outright.
    if "settled_day_basis_id" not in overrides:
        fields.update(settle_day_columns(fields.get("settled_on")))
    return Transaction(**fields)


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

            # Period 1, not 0: the fixture already holds a non-override row for
            # this template in period 0, and
            # the undated generation index would raise on THAT
            # instead -- a control that fires for the wrong reason proves
            # nothing.
            txn = _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                template_id=data["template"].id,
                transfer_id=xfer.id,
            )
            db.session.add(txn)
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
            db.session.add(payback)
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
            db.session.add(txn)
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


class TestTheCheapAccessorRefusesADerivedRow:
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
    ``row_valuation.owned_contribution`` and
    ``cash_ledger.resolve_transfer_amount``, which is where the refusal lives.
    Keeping them is the point: the refusal is what makes the per-kind cutovers
    (X-au-d..X-au-i) safe to ship one at a time, because a reader they have not
    routed fails LOUDLY rather than publishing a wrong number.
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
                AmountUnresolvable, match="owns its amount and carries none",
            ):
                _ = owned_contribution(txn)

    def test_a_derived_transfer_refuses(self, app, db, seed_full_user_data):
        """The transfer twin refuses on the same shape.

        It carries a ``due_date`` so the refusal is the one this case is about
        -- its definition states no price for that day -- rather than the
        no-date arm, which is a different defect (and has its own control in
        ``test_services/test_amount_source.py``).  Without the date the row
        refuses for the wrong reason and the test would pass while proving
        nothing about the missing FIGURE.
        """
        with app.app_context():
            xfer = _make_transfer(
                seed_full_user_data,
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
                _ = resolve_transfer_amount(xfer)

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

            assert owned_contribution(txn) == Decimal("412.55")

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

            assert owned_contribution(txn) == Decimal("0")

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

            assert owned_contribution(txn) == Decimal("0")


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
        """
        with app.app_context():
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
        """Return a flushed INCOME row of *template*, DECLARED derived."""
        income = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )
        txn = _make_transaction(
            seed_user, seed_periods,
            template_id=template.id,
            transaction_type_id=income.id,
            amount_ownership=AmountOwnership.derived(
                ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
            ),
            **overrides,
        )
        db.session.add(txn)
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
            income = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=salary_template.id,
                transaction_type_id=income.id,
                is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("2562.67")),
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                settled_on=date(2026, 1, 5),
                **settlement_columns(
                    date(2026, 1, 5), Decimal("2562.67"),
                    submitted=Decimal("2524.62"),
                ),
            )
            db.session.add(txn)
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
            income = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            owned = _make_transaction(
                seed_user, seed_periods,
                template_id=salary_template.id,
                transaction_type_id=income.id,
                is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("1234.56")),
            )
            db.session.add(owned)
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
    same device ``_salary_template`` uses, and for the same reason.

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
        """Return a flushed EXPENSE row of *template*, DECLARED derived."""
        txn = _make_transaction(
            seed_user, seed_periods,
            template_id=template.id,
            amount_ownership=AmountOwnership.derived(
                ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
            ),
            **overrides,
        )
        db.session.add(txn)
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
            db.session.add(owned)
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
