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

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.ref import AmountSource, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import load_migration_module
from app.services.cash_ledger import resolve_transfer_amount
from app.services.row_valuation import owned_contribution

_MIGRATION = load_migration_module("b3f7c2a9d514_amount_ownership.py")


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return an unflushed Projected expense row, with *overrides* applied.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **overrides: Column values to set or replace -- notably
            ``estimated_amount`` and ``amount_source_id``, the pair under test.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    fields = {
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
    }
    fields.update(overrides)
    return Transaction(**fields)


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
        "amount": Decimal("100.00"),
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
            txn = _make_transaction(
                seed_user, seed_periods,
                estimated_amount=Decimal("300.00"),
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
                ),
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                db.session.flush()
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_user, seed_periods
    ):
        """Emptying the figure with no source declared is refused.

        The mirror shape: a row with neither a figure nor a statement of who
        prices it is unpriceable, and nothing on the row would say why.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, estimated_amount=None,
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                db.session.flush()
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
                estimated_amount=None,
                amount_source_id=template_source,
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
            xfer = _make_transfer(
                seed_full_user_data,
                amount=Decimal("100.00"),
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
                ),
            )
            db.session.add(xfer)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                db.session.flush()
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_full_user_data
    ):
        """A transfer with no figure and no source is refused."""
        with app.app_context():
            xfer = _make_transfer(seed_full_user_data, amount=None)
            db.session.add(xfer)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                db.session.flush()
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
                amount=None, amount_source_id=template_source,
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
                amount=None,
                transfer_template_id=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
            # ``idx_transactions_template_period_scenario`` would raise on THAT
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
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
    (``income_service.live_projected_net`` runs the paycheck engine over every
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
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
                amount=None,
                due_date=date(2026, 3, 15),
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
                ),
            )
            db.session.add(xfer)
            db.session.flush()

            with pytest.raises(
                AmountUnresolvable, match="price series is EMPTY",
            ):
                _ = resolve_transfer_amount(xfer)

    def test_a_humans_actual_answers_for_a_derived_row(
        self, app, db, seed_user, seed_periods
    ):
        """A derived row carrying an entered actual answers WITH it, not a refusal.

        The ruling plan step X-au-c owed and this leaf makes structural: a
        figure a human read off a statement OUTRANKS a derivation (ruling
        **R-FH** reserves ``actual_amount`` for exactly that), so the refusal arm
        sits BELOW it.  Getting the order wrong would refuse the one row on the
        production clone that has both -- a Projected, non-override template row
        carrying an operator-typed actual.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
                ),
                actual_amount=Decimal("412.55"),
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
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
                estimated_amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
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
                amount=None,
                amount_source_id=ref_cache.amount_source_id(
                    AmountSourceEnum.TEMPLATE
                ),
            )
            db.session.add(xfer)
            db.session.flush()

            with pytest.raises(RuntimeError, match="budget.transfers.amount"):
                _MIGRATION.refuse_rows_without_a_figure(db.session.connection())
