"""
Shekel Budget App -- The outstanding set: reader, writer, and their ONE scope

Tests for :mod:`app.services.reconcile_service` -- the reconcile step's offer
set (ruling R-DH (d) / the R-M re-ruling, shipped at plan step S1-c) and the
writer that records a tick against it.

**Moved here from ``test_entry_service`` at plan step X-f2-c1** with the code
it grades, and not one assertion changed on the way: the reader's name and
return SHAPE did (``outstanding_purchases`` -> ``outstanding_set``, grouped by
envelope per ruling R-EW), so ``_listed`` flattens the groups back to the id
list every case below was written against.  Grading the same ids through a new
shape is what makes the move provably behaviour-preserving.
"""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import (
    account_service,
    cash_ledger,
    pay_period_service,
    pay_period_write,
    reconcile_service,
    status_seam,
    transfer_service,
)
from app.services.reconcile_service import _transactions
from app.utils.log_events import (
    EVT_TRANSACTIONS_RECONCILED,
    EVT_TRANSFERS_RECONCILED,
)
from tests._test_helpers import settlement_basis_id, settlement_if_settling
from tests._test_helpers import create_transfer
from app.services.row_valuation import owned_contribution, settled_figure


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The twin of ``test_entry_service``'s helper, carried with the tests that
    use it rather than imported across test modules: it exists to build a row
    WITHOUT the service under test, so a shared version would couple two
    modules' fixtures to one shape for no gain.
    """
    entry = TransactionEntry(
        transaction_id=transaction.id, account_id=transaction.account_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


_OBSERVED_ON = date(2026, 1, 10)


def _statement(account_id, observed_on=_OBSERVED_ON):
    """Return the STATEMENT these tests reconcile against, for *account_id*.

    Plan step **X-f3a-1** (ruling **R-FL**) made every door here take the
    governing ASSERTION rather than a bare civil day, because a tick now records
    WHICH statement showed the money and production carries three days on which
    one account holds more than one assertion.

    **The id is the account's REAL assertion and the day is the test's.**  The
    id has to be real: ``fk_transactions_reconciled_by`` and
    ``fk_transaction_entries_reconciled_by`` refuse a link to a statement that
    does not exist, which is exactly what those composite keys are for.  The day
    does not, and these tests are why it is separable -- they grade the SET
    OPERATION (which rows a bound admits), so the day is an input to the filter
    rather than a fact about the seeded account, as this module's own scope note
    says.

    Args:
        account_id: The account whose governing assertion to borrow.
        observed_on: The civil day to present it for.

    Returns:
        The :class:`~app.services.cash_ledger.AnchorPoint` to reconcile against.
    """
    return replace(
        cash_ledger.governing_anchor(account_id), observed_on=observed_on,
    )
_BEFORE_THE_STATEMENT = date(2026, 1, 5)
_AFTER_THE_STATEMENT = date(2026, 1, 12)


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=_BEFORE_THE_STATEMENT):
    """Attach one debit purchase with NO recorded posting day."""
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )


class TestTheOutstandingSet:
    """``outstanding_set`` / ``record_settled_days`` -- ONE definition.

    The reconcile step's reader and its writer share
    ``reconcile_service._purchases._outstanding_scope``, and that sharing is the security
    property, not a tidiness one: a purchase the panel does not OFFER can never
    be stamped by a forged id, because the writer re-applies the same five
    clauses to whatever ids arrive from the form.  So each clause is graded
    from BOTH doors -- listed-or-not, and stamped-or-not -- and a clause that
    held on one side only would fail here.

    An out-of-scope id is silently skipped rather than raising: this is a set
    operation, and the project's "404 for both not-found and not-yours" posture
    expressed as a filter.  ``record_settled_days`` returns what actually
    CHANGED, never what was asked for, which is what makes the skip observable.

    **Scope: this grades the SET OPERATION, not its consequence.**
    ``_OBSERVED_ON`` is passed in rather than resolved from the account, so a
    row stamped here may still read as outstanding to the projection -- which
    is correct for a unit test of the filter and is why the figures a tick
    MOVES are graded end to end in
    ``test_anchor_settle_partition.py::TestRecordingAPurchaseDoesNotMoveTheProjection``
    and the route's day-resolution in ``test_routes/test_accounts.py``.
    """

    @staticmethod
    def _reconcile(seed_user, entry_ids):
        """Run the writer against the seed user's own checking account."""
        return reconcile_service.record_settled_days(
            seed_user["user"].id, seed_user["account"].id,
            set(entry_ids), _statement(seed_user["account"].id),
        )

    @staticmethod
    def _listed(seed_user):
        """Return the ids the reader offers for the seed user's account."""
        return [
            purchase.entry_id
            for group in reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            ).groups
            for purchase in group.purchases
        ]

    def test_a_tick_records_WHICH_statement_showed_the_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The purchase arm writes the clearing fact, not just the day.

        Ruling **R-FL**: ``settled_on`` is an upper bound on the true posting
        day, and ``reconciled_by_id`` is the observation itself.  The day alone
        cannot carry it, because production holds three days on which Checking
        has more than one assertion -- so a rule over ``settled_on`` could not
        say WHICH statement a tick was made against.

        Graded here because this arm's writer is a bulk ``UPDATE``: a column
        omitted from it fails silently, leaving the panel reporting a
        reconciliation it did not record.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()
            statement = _statement(seed_user["account"].id)

            assert self._reconcile(seed_user, [entry.id]) == 1
            db.session.commit()
            db.session.expire_all()

            reloaded = db.session.get(TransactionEntry, entry.id)
            assert reloaded.reconciled_by_id == statement.anchor_id
            assert reloaded.settled_on == statement.observed_on

    def test_an_outstanding_debit_is_listed_and_stamped(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The happy path, so every refusal below is a real discrimination.

        A debit purchase made before the statement day with no recorded posting
        day is offered, and ticking it stamps ``settled_on`` with the
        assertion's own ``observed_on`` -- an upper bound on the true posting
        day, and the only bound the reconciliation predicate consumes.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()

            assert self._listed(seed_user) == [entry.id]
            assert self._reconcile(seed_user, [entry.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _OBSERVED_ON

    def test_an_already_recorded_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``settled_on IS NULL`` -- the definition of outstanding itself.

        A purchase whose posting day is already recorded is not outstanding
        whatever that day is, so re-submitting its id must not overwrite the
        user's own sharper date with the assertion's coarser upper bound.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            entry.settled_on = _BEFORE_THE_STATEMENT
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _BEFORE_THE_STATEMENT

    def test_a_credit_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A credit-card purchase never touches checking, so it is not on the
        statement being reconciled.

        It leaves through its own CC Payback sibling transaction, so the
        reservation ignores its dates entirely -- recording one would be
        recording a fact nothing reads, against a statement it never appeared
        on.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00", description="Amazon",
                purchased_on=_BEFORE_THE_STATEMENT, is_credit=True,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_purchase_made_after_the_statement_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``purchased_on <= observed_on`` -- and the DB backstop it fronts.

        A purchase made after the day the balance was read cannot be inside it.
        Stamping it would write a ``settled_on`` earlier than its own
        ``purchased_on``, which
        ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
        database; filtering here makes that constraint a backstop rather than a
        reachable 500 on a form submission.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(
                txn, seed_user, purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_purchase_on_a_settled_parent_IS_offered(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A CLOSED envelope's purchase still owes the bank's day.

        **This asserted the exact opposite until the developer's 2026-08-17
        ruling**, on the premise that "the entry reservation prices only
        Projected rows, so a purchase on a settled parent is inert".  Ruling
        **R-FM** had already falsified that one step earlier:
        ``cash_ledger.settled_cash_leg`` subtracts every POSTED purchase from a
        settled row's close, so recording the day moves that purchase's cash out
        of the close's day and onto the bank's.  The total never changes -- the
        two terms always sum to the row's whole debit -- and the DAY is what a
        paper statement reconciles against, which is this panel's whole subject.

        Measured on the 2026-08-17 production dump: 28 closed envelopes hold 61
        debit purchases carrying no posting day, ``$4,360.07`` between them,
        none of which this panel would ever have offered.
        ``entry_service._doors._reject_settled_parent`` is the write side of the
        same ruling -- it admits ``settled_on`` on a settled parent and refuses
        every cost-bearing field.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            # Re-fetch against the live session: the fixture object is
            # loaded upstream and an assignment on a stale instance is not
            # reliably marked dirty for the next flush (the same reason
            # ``test_anchor_service`` re-gets its account).
            status_seam.apply_status_change(
                db.session.get(Transaction, txn.id),
                ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(db.session.get(Transaction, txn.id), ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()

            assert self._listed(seed_user) == [entry.id]
            assert self._reconcile(seed_user, [entry.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _OBSERVED_ON

    def test_a_purchase_on_a_soft_deleted_parent_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted row is not in the plan, so its purchases are not either.

        Its entries survive on the row (the delete is soft), so without the
        filter they would be offered for a bill the user has already removed
        from their budget.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.get(Transaction, txn.id).is_deleted = True
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_another_accounts_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A balance assertion declares the real balance of ONE account.

        A user may hold more than one checking account (there is no per-type
        uniqueness), and reconciling across them would drop the other account's
        reservation without ever raising its anchor -- inflating its projected
        balance by the purchase.  The forged id is submitted to account A's
        reconcile and must change nothing on B.
        """
        with app.app_context():
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Checking 2",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.flush()
            # An AD-HOC envelope rather than a second row off the shared
            # template: ``idx_transactions_template_period_scenario`` is
            # unique, so one template cannot carry two rows in one period.
            txn_b = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=account_b.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Groceries B",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=(
                    seed_entry_template["transaction"].transaction_type_id
                ),
                estimated_amount=Decimal("500.00"),
                is_envelope=True,
            )
            db.session.add(txn_b)
            db.session.flush()
            entry_b = _outstanding_debit(txn_b, seed_user)
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry_b.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry_b.id,
            ).settled_on is None

    def test_another_users_purchase_matches_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_entry_template,
    ):
        """A forged id from another user's data changes nothing.

        The IDOR case at its natural shape: the other user's purchase sits on
        the other user's account, in the other user's pay period.

        **It is OVER-DETERMINED, and that is stated rather than left to be
        discovered** (finding N-69's lesson).  Two clauses reject this row --
        ``Transaction.account_id == account_id`` and
        ``PayPeriod.user_id == owner_id`` -- so deleting either one alone still
        leaves this test green.  The account clause has its own firing control
        (``test_another_accounts_purchase_matches_nothing``); the user clause is
        isolated by the test below, which is the only shape that can.
        """
        with app.app_context():
            other_period = pay_period_write.record_paydays(
                user_id=seed_second_user["user"].id,
                first_payday=date(2026, 1, 2), num_periods=1, cadence_days=14,
            )[0]
            other_txn = Transaction(
                pay_period_id=other_period.id,
                scenario_id=seed_second_user["scenario"].id,
                account_id=seed_second_user["account"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Their groceries",
                transaction_type_id=(
                    seed_entry_template["transaction"].transaction_type_id
                ),
                estimated_amount=Decimal("500.00"),
                is_envelope=True,
            )
            db.session.add(other_txn)
            db.session.flush()
            other_entry = _make_entry(
                other_txn, seed_second_user["user"], amount="50.00",
                description="Theirs", purchased_on=_BEFORE_THE_STATEMENT,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [other_entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, other_entry.id,
            ).settled_on is None

    def test_the_OWNER_clause_is_load_bearing_on_its_own(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_entry_template,
    ):
        """The one shape that isolates ``PayPeriod.user_id == owner_id``.

        A transaction on THIS user's account whose pay period belongs to
        ANOTHER user.  Nothing in the schema forbids the row --
        ``Transaction.account_id`` and ``Transaction.pay_period_id`` are
        independent foreign keys with no composite constraint tying them to one
        owner -- and nothing in the app creates it, so this is a forged /
        corrupt row and the owner clause is the defence in depth against it.

        It is the ONLY shape that grades that clause.  Every other cross-user
        fixture puts the foreign row on the foreign ACCOUNT too, which the
        account clause rejects first, so the owner clause could be deleted and
        the whole reconcile suite would stay green (measured, in this step's
        adversarial review).  Here the account clause PASSES by construction and
        only the owner clause can reject it.

        The consequence if it were dropped: whoever holds the corrupt row's id
        could have another user's purchase stamped as reconciled against a
        balance that user never asserted -- and, because ``settled_on`` is then
        non-NULL, that user's own panel would stop offering it.
        """
        with app.app_context():
            other_period = pay_period_write.record_paydays(
                user_id=seed_second_user["user"].id,
                first_payday=date(2026, 1, 2), num_periods=1, cadence_days=14,
            )[0]
            crossed = Transaction(
                # THIS user's account ...
                account_id=seed_user["account"].id,
                # ... under the OTHER user's pay period.
                pay_period_id=other_period.id,
                scenario_id=seed_second_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Cross-owner groceries",
                transaction_type_id=(
                    seed_entry_template["transaction"].transaction_type_id
                ),
                estimated_amount=Decimal("500.00"),
                is_envelope=True,
            )
            db.session.add(crossed)
            db.session.flush()
            entry = _make_entry(
                crossed, seed_second_user["user"], amount="50.00",
                description="Crossed", purchased_on=_BEFORE_THE_STATEMENT,
            )
            db.session.commit()

            # The account clause cannot reject this row -- it IS on this
            # account.  Stated so the test cannot silently stop isolating.
            assert crossed.account_id == seed_user["account"].id

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_mixed_submission_stamps_only_what_is_in_scope(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A partly-forged submission is neither rejected nor obeyed in full.

        One real outstanding purchase and one out-of-scope id in the same
        request: the count returned is what CHANGED (1), not what was asked for
        (2), so the log records the gap instead of the whole request being
        thrown away or waved through.  A submission that raised on the bad id
        would lose the user's good ticks too.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            good = _outstanding_debit(txn, seed_user, amount="20.00")
            forged = _outstanding_debit(
                txn, seed_user, amount="30.00",
                purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert self._reconcile(seed_user, [good.id, forged.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, good.id,
            ).settled_on == _OBSERVED_ON
            assert db.session.get(
                TransactionEntry, forged.id,
            ).settled_on is None

    def test_an_empty_submission_is_a_no_op(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ticking nothing changes nothing and issues no UPDATE.

        The state a user is in when they open the panel and dismiss it, so it
        must not be an error -- and it must not fall through to an unfiltered
        bulk update, which is precisely what the retired
        ``clear_entries_for_anchor_true_up`` was.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()

            assert self._reconcile(seed_user, []) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_the_list_is_ordered_oldest_purchase_first(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Deterministic order, with ``id`` breaking a same-day tie.

        The panel is a checklist read against a paper statement, which is
        chronological; an unordered list would shuffle between renders of the
        same data and make the two impossible to walk together.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            second = _outstanding_debit(
                txn, seed_user, amount="20.00",
                purchased_on=date(2026, 1, 7),
            )
            first = _outstanding_debit(
                txn, seed_user, amount="30.00",
                purchased_on=date(2026, 1, 3),
            )
            same_day_as_second = _outstanding_debit(
                txn, seed_user, amount="40.00",
                purchased_on=date(2026, 1, 7),
            )
            db.session.commit()

            assert self._listed(seed_user) == [
                first.id, second.id, same_day_as_second.id,
            ]


class TestTheSetIsGroupedByItsParent:
    """Ruling **R-EW**: a purchase nests under the thing it belongs to.

    The reader returned a FLAT list of ORM rows until plan step X-f2-c1, and the
    panel named each purchase's parent in a trailing fragment on its own line.
    R-EW rules the other shape -- a grocery purchase and the grocery envelope
    read as one block -- and rejects grouping by act-type, which is exactly what
    separates those two.

    **These grade the GROUPING, which the class above cannot see**: every case
    there flattens the blocks back to an id list on purpose, so that the move
    from ``entry_service`` could be proved to change no answer.  A grouping
    defect is invisible to a flattened assertion by construction.
    """

    @staticmethod
    def _second_envelope(seed_user, seed_periods, name="Gas"):
        """Build a SECOND envelope transaction on the same account and period.

        Two blocks are the minimum that can be grouped wrongly: with one, every
        grouping rule agrees.

        The type comes from ``ref_cache`` rather than a ``filter_by(name=...)``
        lookup: reference tables are IDs for logic and strings for display, and
        a test fixture is not an exception the checker simply cannot see.
        """
        expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type_id,
            name=name,
            default_amount=Decimal("80.00"),
            is_envelope=True,
        )
        db.session.add(template)
        db.session.flush()
        txn = Transaction(
            template_id=template.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name=name,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=template.transaction_type_id,
            estimated_amount=Decimal("80.00"),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    @staticmethod
    def _resolve(seed_user, observed_on=_OBSERVED_ON):
        return reconcile_service.outstanding_set(
            seed_user["user"].id, seed_user["account"].id,
            _statement(seed_user["account"].id, observed_on),
        )

    def test_each_envelope_is_one_block_carrying_its_own_purchases(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Two envelopes, three purchases, two blocks -- and no crossover.

        Hand arithmetic: Groceries 40.00 + 60.00 = 100.00, Gas 25.00, and the
        set's own total 125.00.  Every figure is asserted, not just the shape:
        a block total that summed the WRONG block's purchases would still give
        two blocks with the right ids.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            gas = self._second_envelope(seed_user, seed_periods)
            _outstanding_debit(groceries, seed_user, amount="40.00")
            _outstanding_debit(groceries, seed_user, amount="60.00")
            _outstanding_debit(gas, seed_user, amount="25.00")
            db.session.commit()

            result = self._resolve(seed_user)

            # The value objects THEMSELVES, field for field.  Grading only
            # ids and totals leaves `purchased_on` and `description` unpinned,
            # and mutating `purchased_on` to the statement day survives the
            # whole suite -- every line in the panel captioned with the wrong
            # date, on the one screen whose job is to be walked against a
            # paper statement.  Measured; this is the case that catches it.
            assert result.groups[0].purchases == (
                reconcile_service.OutstandingPurchase(
                    entry_id=result.groups[0].purchases[0].entry_id,
                    purchased_on=_BEFORE_THE_STATEMENT,
                    description="Kroger",
                    amount=Decimal("40.00"),
                ),
                reconcile_service.OutstandingPurchase(
                    entry_id=result.groups[0].purchases[1].entry_id,
                    purchased_on=_BEFORE_THE_STATEMENT,
                    description="Kroger",
                    amount=Decimal("60.00"),
                ),
            )
            assert [group.name for group in result.groups] == [
                "Weekly Groceries", "Gas",
            ]
            assert [g.period_start for g in result.groups] == [
                seed_periods[0].start_date, seed_periods[0].start_date,
            ]
            assert [len(g.purchases) for g in result.groups] == [2, 1]
            assert [g.total for g in result.groups] == [
                Decimal("100.00"), Decimal("25.00"),
            ]
            assert result.purchase_count == 3
            assert result.purchase_total == Decimal("125.00")
            assert result.is_empty is False
            # The block names its PARENT, which is the key the grouping is
            # built on and the id plan step X-f2-c2's close tick will post.
            assert [g.transaction_id for g in result.groups] == [
                groceries.id, gas.id,
            ]

    def test_one_envelope_in_two_periods_is_two_blocks_the_heading_can_tell_apart(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The recurrence engine gives one template one row PER PERIOD.

        So one envelope in two periods is two parents carrying ONE name, and
        both can hold outstanding purchases at a single assertion -- the scope
        has no period clause, deliberately (a purchase is outstanding or it is
        not, whichever paycheck funded it).  The flat list was equally ambiguous
        per line; GROUPING promotes that ambiguity to the block heading, so the
        leaf that creates the heading is the leaf that has to resolve it.

        Without the period on the group the panel renders two identical
        headings with different subtotals and no way to tell which paycheck
        each belongs to -- and plan step X-f2-c2 hangs a close tick, which
        SETTLES the row, off each of them.
        """
        with app.app_context():
            first = seed_entry_template["transaction"]
            second = Transaction(
                template_id=first.template_id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name=first.name,
                category_id=first.category_id,
                transaction_type_id=first.transaction_type_id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(second)
            db.session.flush()
            _outstanding_debit(first, seed_user, amount="40.00")
            _outstanding_debit(second, seed_user, amount="60.00")
            db.session.commit()

            result = self._resolve(seed_user)

            assert [g.name for g in result.groups] == [
                "Weekly Groceries", "Weekly Groceries",
            ]
            # ONE name, TWO periods -- which is what the heading renders.
            assert [g.period_start for g in result.groups] == [
                seed_periods[0].start_date, seed_periods[1].start_date,
            ]
            assert [g.period_end for g in result.groups] == [
                seed_periods[0].end_date, seed_periods[1].end_date,
            ]
            assert [g.total for g in result.groups] == [
                Decimal("40.00"), Decimal("60.00"),
            ]

    def test_blocks_are_ordered_by_their_oldest_outstanding_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The block a user is most likely looking for is first.

        **The order is the ROW sort's, not a second sort**, so this is the case
        that would catch a reader that grouped with a plain ``dict`` built from
        an unordered query, or that sorted the blocks by name or by id.  Here
        the older purchase belongs to the SECOND-created envelope, so id order
        and date order disagree and only one of them is right.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            gas = self._second_envelope(seed_user, seed_periods)
            _outstanding_debit(
                groceries, seed_user, purchased_on=date(2026, 1, 8),
            )
            _outstanding_debit(
                gas, seed_user, purchased_on=date(2026, 1, 2),
            )
            db.session.commit()
            assert gas.id > groceries.id

            result = self._resolve(seed_user)

            assert [g.name for g in result.groups] == [
                "Gas", "Weekly Groceries",
            ]

    def test_purchases_within_a_block_are_oldest_first(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Within a block the purchase order is the account's own timeline.

        The id breaks a same-day tie, so the sort is total: a nondeterministic
        order in a list the user ticks off against a paper statement is a
        usability defect, and in a financial replay it is a reproducibility one.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            second = _outstanding_debit(
                groceries, seed_user, amount="20.00",
                purchased_on=date(2026, 1, 7),
            )
            first = _outstanding_debit(
                groceries, seed_user, amount="30.00",
                purchased_on=date(2026, 1, 3),
            )
            same_day = _outstanding_debit(
                groceries, seed_user, amount="40.00",
                purchased_on=date(2026, 1, 7),
            )
            db.session.commit()

            result = self._resolve(seed_user)

            assert [p.entry_id for p in result.groups[0].purchases] == [
                first.id, second.id, same_day.id,
            ]

    def test_an_account_with_nothing_outstanding_reports_itself_empty(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """No block, no count, no money -- and ``is_empty`` says so.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with prose rather than an empty form.  Every total is
        asserted to be the quantised zero rather than merely falsy, because
        each is rendered by the money macro.

        **The day moved to before the period at plan step X-f2-c2, and the
        reason is the widening rather than a fixture convenience.**  The seed
        envelope is Projected in a period starting 2026-01-02, so against
        ``_OBSERVED_ON`` (the 10th) its own close is now OVERDUE and the panel
        offers it -- correctly, under ruling R-EW.  "Nothing outstanding" is
        therefore no longer a property of an account with no purchases; it
        needs a day nothing has landed on yet, which is what the 1st is.
        """
        with app.app_context():
            result = self._resolve(seed_user, observed_on=date(2026, 1, 1))

            assert result.is_empty is True
            assert result.groups == ()
            assert result.purchase_count == 0
            assert result.purchase_total == Decimal("0.00")
            assert result.payment_count == 0
            assert result.payment_total == Decimal("0.00")
            assert result.deposit_count == 0
            assert result.deposit_total == Decimal("0.00")

    def test_empty_is_read_off_the_COUNTS_not_off_the_purchase_count(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A panel offering only a BILL is not empty.

        ``is_empty`` was ``purchase_count == 0`` until plan step X-f2-c2, and
        its own docstring predicted this case: a kind arrives that is not a
        purchase, and the old definition answers "empty" for a panel with
        things to offer, suppressing them behind the "nothing is being held
        back twice" copy.

        **It used to be graded by CONSTRUCTING an unreachable set** -- groups
        empty, count non-zero -- because the producer could not build a
        discriminating state.  It can now: the seed envelope carries no
        purchases and its own close is overdue, so ``purchase_count`` is 0
        while ``payment_count`` is 1.  Grading the rule on a state the producer
        really produces is strictly stronger than grading it on a hand-built
        one, and the hand-built one is no longer even reachable under ruling
        R-FC (every offer arrives inside a group).

        Shown to FIRE: reverting ``is_empty`` to ``purchase_count == 0``
        fails here.
        """
        with app.app_context():
            result = self._resolve(seed_user)

            assert result.purchase_count == 0
            assert result.payment_count == 1
            assert result.is_empty is False

    def test_the_empty_constructor_matches_a_genuinely_empty_read(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``OutstandingSet.empty()`` is the route's shape for "no assertion".

        A route reaching for it must get exactly what the producer would have
        returned, or the panel would render one thing for an account with no
        assertion and another for an account with nothing outstanding -- two
        spellings of one empty state, which is the shape this arc removes.

        The day is the 1st for the reason given two cases up.
        """
        with app.app_context():
            assert reconcile_service.OutstandingSet.empty() == self._resolve(
                seed_user, observed_on=date(2026, 1, 1),
            )


class TestTheTransactionArm:
    """Plan step **X-f2-c2**: the panel offers the source ROWS too.

    Ruling **R-EW** widens the offer set past purchases; this arm is the
    envelope's own close and bills, income included (**R-FD**), settled through
    the verb the grid's Mark Paid calls (**R-FA**) on the STATEMENT's day.

    **The bound is the OVERDUE set and it is the whole security story of this
    arm**, so every clause is graded from BOTH doors -- offered-or-not, and
    settled-or-not -- exactly as the purchase arm's is.  A clause that held on
    one side only would let a forged id settle a row the panel never showed.

    The seed fixture's envelope lives in period 0 (2026-01-02 .. 2026-01-15)
    and is Projected at `$500.00`, so ``_OBSERVED_ON`` (the 10th) is after its
    attribution day and it is offered; 2026-01-01 is before it and it is not.
    """

    @staticmethod
    def _bill(seed_user, period, *, name="Electricity", amount="180.00",
              due_date=None, income=False):
        """Create a projected NON-envelope row -- a bill, or a deposit."""
        type_id = ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=type_id,
            name=name,
            default_amount=Decimal(amount),
            is_envelope=False,
        )
        db.session.add(template)
        db.session.flush()
        txn = Transaction(
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name=name,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=type_id,
            estimated_amount=Decimal(amount),
            due_date=due_date,
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    @staticmethod
    def _offered(seed_user, observed_on=_OBSERVED_ON):
        """Return ``{transaction id: offer}`` for the seed user's account."""
        return {
            group.settle.transaction_id: group.settle
            for group in reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id, observed_on),
            ).groups
            if group.settle is not None
        }

    @staticmethod
    def _settle(seed_user, ids, corrections=None,
                observed_on=_OBSERVED_ON):
        """Run the WRITE UNION against the seed user's own account.

        It was ``record_settled_transactions`` until plan step X-f2-c3, which
        made the two source-row arms one writer parameterised by an ``Arm``
        (finding **N-225**) -- so there is no per-arm entry point left to call
        and the union is the door.  Every assertion below is unchanged: the
        ids it settles are still exactly the ones the transaction arm's scope
        admits, because the transfer arm's scope is that scope's COMPLEMENT.
        """
        return reconcile_service.record_reconciliation(
            reconcile_service.ReconcileSubmission(
                owner_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                entry_ids=set(),
                transaction_ids=set(ids),
                corrections=corrections or {},
                anchor=_statement(seed_user["account"].id, observed_on),
            ),
        )

    def test_an_overdue_row_is_offered_and_settles_on_the_statement_day(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The happy path, so every refusal below is a real discrimination.

        The whole point of the leaf: the settle day is the STATEMENT's, not the
        user's today.  The seam stamps ``display_today()`` when no day is
        supplied, so a test asserting only "it settled" would pass with the
        wrong date on every row -- which is the one screen a user reads against
        a paper statement.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            assert txn.id in self._offered(seed_user)
            assert self._settle(seed_user, [txn.id]) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.settled_on == _OBSERVED_ON
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_tick_records_WHICH_statement_showed_the_row(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The transaction arm writes the clearing fact beside the settle.

        Ruling **R-FL**.  It is written by the SHARED writer rather than by
        either arm's settle verb, and that placement is the rule: both verbs are
        also the grid's Mark Paid, which settles a row without any statement
        having shown it, so a link written there would record an observation
        nobody made.  Ticking on this panel IS the observation.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            statement = _statement(seed_user["account"].id)

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.commit()
            db.session.expire_all()

            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.reconciled_by_id == statement.anchor_id
            assert reloaded.settled_on == statement.observed_on

    def test_a_row_that_is_not_yet_overdue_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The date bound, from both doors.

        ``attribution_date <= observed_on`` IS the overdue set (ruling R-G
        clamps a projected row's landing day up to ``as_of + 1``), so a row the
        projection has not reached yet is not something a statement can show.
        A forged id for it settles nothing.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            early = date(2026, 1, 1)

            assert self._offered(seed_user, observed_on=early) == {}
            assert self._settle(seed_user, [txn.id], observed_on=early) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_the_bound_is_the_DUE_DATE_when_the_row_carries_one(
        self, app, db, seed_user, seed_periods,
    ):
        """The landing day is ``attribution_date``, not the period's start.

        A bill due on the 20th of a period that STARTS on the 2nd is not
        overdue on the 10th, even though its period is.  The SQL half of the
        bound admits it (``period.start_date <= observed_on``) and the Python
        half refuses it -- so this is the case that proves the superset is
        actually narrowed rather than merely described.

        Shown to FIRE: dropping ``_lands_on_or_before`` offers this row.
        """
        with app.app_context():
            later = self._bill(
                seed_user, seed_periods[0], name="Rent",
                due_date=date(2026, 1, 14),
            )
            earlier = self._bill(
                seed_user, seed_periods[0], name="Water",
                due_date=date(2026, 1, 8),
            )
            db.session.commit()

            offered = self._offered(seed_user)
            assert later.id not in offered
            assert earlier.id in offered
            assert offered[earlier.id].attributed_on == date(2026, 1, 8)

    def test_a_transfer_shadow_belongs_to_the_OTHER_arm(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A shadow is offered, and it is tagged as the TRANSFER arm's.

        **This case inverted at plan step X-f2-c3 and the inversion is the
        step.**  It read "neither offered nor settled": the panel could not
        settle a transfer at all, because ``settle_transaction`` REFUSES a
        shadow (transfer invariant 3 -- the parent and both legs move
        together), so this arm's scope excluded one and nothing else offered
        it.  The transfer arm settles through ``update_transfer`` instead, so
        the row IS offered now -- and what is graded here is that it belongs to
        that arm and not to this one, which is the clause the two arms
        partition the table on.

        **Built through the real transfer service**, not by writing a
        ``transfer_id`` onto an ordinary row: the expense shadow that lands on
        this account is a genuine Projected row in the same period, so it is
        inside every other clause of the scope and ONLY the membership clause
        can tell the two arms' rows apart.
        """
        with app.app_context():
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Savings",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], other,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == seed_user["account"].id,
                )
                .one()
            )

            offer = self._offered(seed_user)[shadow.id]
            assert offer.kind is reconcile_service.OfferKind.TRANSFER
            assert offer.amount == Decimal("75.00")
            # The transaction arm's own scope still refuses it: asked directly,
            # with the transfer arm out of the picture.  Without this the case
            # above would pass for a panel that offered the shadow through the
            # WRONG arm and settled one leg.
            assert shadow.id not in _transactions.outstanding_transactions(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            )

    def test_a_settled_row_is_neither_offered_nor_re_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Only a PROJECTED row is waiting on the bank.

        And the writer's half is what stops a double-submit re-dating a row
        that already settled: the second POST finds nothing in scope.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.commit()

            assert self._offered(seed_user) == {}
            assert self._settle(seed_user, [txn.id]) == 0

    def test_another_users_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ownership, from both doors -- a forged id from another budget.

        The scope reaches the owner through the row's PAY PERIOD, which is the
        only user_id a Transaction has, so this grades the join as well as the
        clause.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            other_id = seed_user["user"].id + 1000

            assert reconcile_service.outstanding_set(
                other_id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            ).groups == ()
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    owner_id=other_id,
                    account_id=seed_user["account"].id,
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    anchor=_statement(seed_user["account"].id),
                ),
            ) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_another_accounts_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A statement declares ONE account's balance.

        Reconciling across accounts would settle a row the bank never showed on
        this statement, on a day that account was never asserted for.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Second Checking",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.commit()

            assert reconcile_service.outstanding_set(
                seed_user["user"].id, other.id, _statement(other.id),
            ).groups == ()
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    owner_id=seed_user["user"].id,
                    account_id=other.id,
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    anchor=_statement(seed_user["account"].id),
                ),
            ) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_a_soft_deleted_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted row is not money this account owes."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Re-fetched into THIS session, exactly as the purchase arm's own
            # soft-delete case does: the fixture's object is attached to the
            # session the fixture ran in, so assigning to it directly writes
            # nothing and the control passes for no reason.
            db.session.get(Transaction, txn.id).is_deleted = True
            db.session.commit()

            assert self._offered(seed_user) == {}
            assert self._settle(seed_user, [txn.id]) == 0

    def test_an_envelope_still_being_spent_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The VALUE half of the bound, from both doors.

        An envelope settles at ``sum(entries)`` over EVERY entry it holds, so
        one still carrying a purchase made after the statement day would book
        that purchase too -- dated on the statement's day, and with no
        correction box, because an entries-derived row is not correctable
        (**R-FF**).  The purchase arm has always refused such an entry; this is
        the same rule applied to the parent, and without it the two arms
        disagree about the same dollars.

        **Shown to FIRE**: removing the ``_wholly_spent_by`` term from
        ``_outstanding_rows`` offers this row at `$100.00` and settles it.

        Measured on a clone of production before the fix: one `$137.45`
        purchase three days after Checking's 2026-08-06 assertion made the
        panel offer *Close Groceries* at `$622.55` rather than `$485.10`, and
        ticking it raised the projected balance by exactly `$137.45` at +30d,
        +90d and +365d -- already-spent money handed back to the projection.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(
                txn, seed_user["user"], amount="40.00",
                purchased_on=_BEFORE_THE_STATEMENT,
            )
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert txn.id not in self._offered(seed_user)
            assert self._settle(seed_user, [txn.id]) == 0

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.settled_on is None
            assert reloaded.settled_amount is None

    def test_an_envelope_spent_only_BEFORE_the_statement_is_still_offered(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The negative control for the refusal above.

        Without it that test would pass just as well if the arm had stopped
        offering envelopes carrying entries at all, which is a different -- and
        wrong -- rule.  The discriminating fact is the entry's DAY, so the same
        two purchases dated on or before the statement are still offered, at
        their sum.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(
                txn, seed_user["user"], amount="40.00",
                purchased_on=_BEFORE_THE_STATEMENT,
            )
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                purchased_on=_OBSERVED_ON,
            )
            db.session.commit()

            assert self._offered(seed_user)[txn.id].amount == Decimal("100.00")
            assert self._settle(seed_user, [txn.id]) == 1

    def test_a_row_with_no_entries_is_unaffected_by_the_value_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """A bill answers the value bound over an empty set.

        Its own control because the bound is written ``all(...)`` and an
        ``all()`` over nothing is True -- the behaviour a bill and a deposit
        need, and exactly the kind of vacuous truth that deserves a test rather
        than a comment.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0])
            db.session.commit()

            assert bill.id in self._offered(seed_user)
            assert self._settle(seed_user, [bill.id]) == 1

    def test_an_empty_submission_is_a_no_op(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ticking nothing settles nothing, and issues no query."""
        with app.app_context():
            assert self._settle(seed_user, []) == 0


class TestTheTransferArm:
    """Plan step **X-f2-c3**: the panel offers a TRANSFER's shadow too.

    Money moving between two of the owner's own accounts still leaves one of
    them, so a checking statement shows it exactly as it shows a bill.
    Replayed over production's 57 Checking assertion days, 8 would have carried
    a transfer offer, `$5,442.89` -- six `$500.00` savings sweeps, one
    `$1,910.95` Mortgage payment and one `$531.94` Van Loan payment.

    **What makes it a separate arm is the SETTLE.**  A transfer is three rows
    and ``CLAUDE.md`` invariants 3 and 4 say they move together, so
    ``settle_transaction`` refuses a shadow and ``update_transfer`` is the
    verb.  These grade that a tick moves all THREE and stamps the statement's
    day on both legs -- which is the whole reason the panel prints a note
    saying a second account moves.
    """

    @staticmethod
    def _savings(seed_user, name="Savings"):
        """Create a second cash account for the transfer's other leg."""
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                name=name,
                account_type_id=seed_user["account"].account_type_id,
                anchor_balance=Decimal("100.00"),
            ),
        )
        db.session.flush()
        return account

    @classmethod
    def _transfer_out(cls, seed_user, seed_periods, amount="75.00",
                      due_date=None):
        """Return ``(transfer, expense shadow on the seed account)``."""
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"],
            cls._savings(seed_user), seed_periods[0],
            amount=Decimal(amount), due_date=due_date,
        )
        db.session.commit()
        shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer.id,
                Transaction.account_id == seed_user["account"].id,
            )
            .one()
        )
        return transfer, shadow

    _offered = staticmethod(TestTheTransactionArm._offered)
    _settle = staticmethod(TestTheTransactionArm._settle)

    def test_a_tick_records_the_STATEMENT_on_this_accounts_leg_alone(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The clearing fact is NOT mirrored, where the settle day is.

        Ruling **R-FL**, and the asymmetry is the point.  A transfer leaves one
        bank and arrives at another: the asserted account's statement showed its
        own leg, and the other account's statement is a document nobody has read
        in this act.  ``settled_on`` IS mirrored onto both legs (transfer
        invariant 3, graded by the test below); ``reconciled_by_id`` must not
        be, or ticking a savings sweep off a checking statement would record
        that the savings statement showed the money too.

        This is also what makes the posted walk's per-account shadow read
        meaningful -- see
        ``test_account_posting_service.TestWalkAccountLedger``'s own case, which
        measures what reading the wrong leg's link costs.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            statement = _statement(seed_user["account"].id)

            assert self._settle(seed_user, [shadow.id]) == 1
            db.session.commit()
            db.session.expire_all()

            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .all()
            )
            assert len(legs) == 2
            recorded = {
                leg.account_id: leg.reconciled_by_id for leg in legs
            }
            assert recorded[seed_user["account"].id] == statement.anchor_id
            other = next(
                account_id for account_id in recorded
                if account_id != seed_user["account"].id
            )
            assert recorded[other] is None, (
                "The other account's leg named a statement nobody read."
            )

    def test_a_tick_settles_the_parent_and_BOTH_legs_on_the_statement_day(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The arm's whole point: three rows move, dated by the statement.

        The leg on the OTHER account settles too -- which is what the panel's
        section note tells the user -- and both carry the asserted day rather
        than the seam's default of the user's today.  A settle that moved one
        leg would break transfer invariants 3 and 4 silently, because
        ``sync_transaction_postings`` returns nothing for a shadow and the
        ledger would stay flat while the grid showed one side settled.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)

            assert self._settle(seed_user, [shadow.id]) == 1
            db.session.commit()

            db.session.expire_all()
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .all()
            )
            assert len(legs) == 2
            settled = ref_cache.status_id(StatusEnum.DONE)
            assert {leg.status_id for leg in legs} == {settled}
            assert {leg.settled_on for leg in legs} == {_OBSERVED_ON}
            assert db.session.get(
                type(transfer), transfer.id,
            ).status_id == settled

    def test_it_is_offered_under_its_OWN_section_with_the_both_sides_note(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """TRANSFER sorts last and carries the sentence about the other account.

        The section is the ACT and the tally is the LEG (below), so this grades
        the half that decides what the screen says.  A transfer landing in the
        Bills section would read as an observation about one account, which is
        exactly what it is not.
        """
        with app.app_context():
            _transfer, shadow = self._transfer_out(seed_user, seed_periods)

            groups = reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            ).groups
            block = next(
                group for group in groups
                if group.transaction_id == shadow.id
            )
            assert block is groups[-1]
            assert block.section.label == "Transfers"
            assert "both sides" in block.section.note
            assert block.settle_closes_an_envelope is False

    def test_the_expense_leg_counts_as_a_PAYMENT_and_the_income_leg_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One act, one section -- but the statement shows a direction.

        Ruling **R-FD** counts deposits apart from payments because they do not
        sum to anything a reader wants, and a transfer is no exception: money
        leaving Checking is a payment on Checking's statement, and the same
        transfer's other leg is a deposit on the savings account's.  Both are
        graded, from the two panels they appear on.
        """
        with app.app_context():
            savings = self._savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            outgoing = reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            )
            assert outgoing.payment_total >= Decimal("75.00")
            assert outgoing.deposit_count == 0

            incoming = reconcile_service.outstanding_set(
                seed_user["user"].id, savings.id, _statement(savings.id),
            )
            assert incoming.deposit_count == 1
            assert incoming.deposit_total == Decimal("75.00")
            assert incoming.payment_count == 0
            assert transfer.id  # the same transfer, offered on both panels

    def test_a_row_that_is_not_yet_overdue_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The shared bound applies to this arm too, from BOTH doors.

        The transfer's due date is the day after the statement, so nothing the
        bank showed can have included it.  Graded from the write side as well:
        a clause held on the read side only would let a forged id settle a
        transfer the panel never offered -- and settling one moves a SECOND
        account.
        """
        with app.app_context():
            _transfer, shadow = self._transfer_out(
                seed_user, seed_periods, due_date=date(2026, 1, 11),
            )

            assert shadow.id not in self._offered(seed_user)
            assert self._settle(seed_user, [shadow.id]) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, shadow.id).settled_on is None

    def test_another_accounts_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A balance assertion declares ONE account's balance.

        The transfer runs between two accounts that are neither of them the one
        being reconciled, so its shadows are not on this statement.  Settling
        across accounts would record money against a statement that never
        showed it -- and here it would move two accounts at once.
        """
        with app.app_context():
            source = self._savings(seed_user, name="Second Checking")
            target = self._savings(seed_user, name="Third Checking")
            transfer = create_transfer(
                seed_user, db.session, source, target, seed_periods[0],
                amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == source.id,
                )
                .one()
            )

            assert shadow.id not in self._offered(seed_user)
            assert self._settle(seed_user, [shadow.id]) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, shadow.id).settled_on is None

    def test_a_soft_deleted_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted transfer is not money this account owes.

        Graded because the WRITER acts on the PARENT rather than on the shadow
        it was handed: ``update_transfer`` treats a soft-deleted transfer as
        absent and raises ``NotFoundError``, which this route has no handler
        for -- so a scope that admitted one would be a 500 on a money door
        rather than a silent skip.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert shadow.id not in self._offered(seed_user)
            assert self._settle(seed_user, [shadow.id]) == 0

    def test_a_settled_transfer_is_neither_offered_nor_re_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Already recorded is not outstanding, and a replay must not re-date.

        ``done -> done`` is a legal identity transition, so nothing downstream
        would refuse a second submission -- the SCOPE is what stops it, and a
        re-settle would rewrite ``settled_on`` on both legs to a later
        statement's day.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            assert self._settle(seed_user, [shadow.id]) == 1
            db.session.commit()

            assert shadow.id not in self._offered(seed_user)
            assert self._settle(
                seed_user, [shadow.id], observed_on=date(2026, 1, 14),
            ) == 0

            db.session.expire_all()
            assert {
                leg.settled_on
                for leg in db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            } == {_OBSERVED_ON}

    def test_a_correction_books_on_both_legs_and_counts_as_one(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A transfer's tick is correctable, and the figure lands on the pair.

        Ruling **R-FF**: correctable exactly when the settle takes its MANUAL
        branch, and a transfer has no other -- a shadow is never
        purchase-tracked (production: 342 shadows, 0 entries).  The corrected
        figure must reach BOTH legs or the two accounts disagree about how much
        money moved between them.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            assert self._offered(seed_user)[shadow.id].is_correctable is True

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [shadow.id], {shadow.id: Decimal("74.11")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            assert {
                leg.settled_amount
                for leg in db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            } == {Decimal("74.11")}

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert len(events) == 1
            assert events[0].corrected_count == 1

    def test_an_ECHOED_prefill_leaves_both_legs_NULL_and_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """An untouched box is not a correction, on this arm either.

        The panel prefills every correctable row, so a five-row submit posts
        five figures.  Writing an echo would populate a column that is NULL on
        all 17 settled transfer shadows in production -- the only signal that
        says a human read one off a statement.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [shadow.id], {shadow.id: Decimal("75.00")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            # Both legs RECORD what the settle booked, on the ``derived`` basis
            # -- an echoed prefill is not a correction, and since plan step
            # X-au-c3 "not a correction" is a BASIS rather than a NULL figure.
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            )
            assert {leg.settled_basis_id for leg in legs} == {
                settlement_basis_id(SettlementBasisEnum.DERIVED),
            }
            assert {settled_figure(leg) for leg in legs} == {Decimal("75.00")}

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert events[0].corrected_count == 0


class TestWhatATickBooks:
    """Ruling **R-FA** / **R-FB** / **R-FF**: the amount, and who may change it.

    The panel must show the figure a tick will BOOK and offer a box exactly
    where the verb would read one.  Both come from ``transaction_service``
    rather than from a column here, and these grade that they agree.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _offered = staticmethod(TestTheTransactionArm._offered)
    _settle = staticmethod(TestTheTransactionArm._settle)

    def test_an_envelope_with_entries_offers_sum_entries_and_no_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` + `$60` against a `$500` estimate offers `$100.00`, read-only.

        Ruling R-EW refuses an editable close: an envelope's ``actual_amount``
        is DERIVED from its entries, so a box would be a second writer of it.
        The offered figure is what the verb books, not the estimate.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.is_correctable is False

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("100.00")

    def test_an_envelope_with_NO_entries_offers_its_estimate_WITH_a_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FF**: correctable exactly when the verb goes manual.

        Production's `Kayla's Spending Money` is envelope-tracked, budgeted
        `$100.00` and carries ZERO entries, so the verb already treats it as
        manual -- there is no derived value to protect and no reason to make
        the user leave the panel to correct it.  Offered at its estimate, with
        the box.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("500.00")
            assert offer.is_correctable is True

    def test_a_bill_ticked_untouched_leaves_actual_amount_NULL(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The prefill echoed back is not a correction.

        The panel renders the amount box PREFILLED, so an untouched tick
        submits the same figure the row would have booked anyway.  Writing it
        into ``actual_amount`` would populate a column that is NULL on every
        uncorrected row and destroy the only signal that says a human typed
        one -- the signal ruling R-FB's own production measurement is made of.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("180.00")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            # The tick RECORDS what it booked and says the basis is ``derived``
            # -- nobody typed a different figure (plan step X-au-c3).  It
            # asserted ``actual_amount is None`` until that step, because a NULL
            # there was the only signal that no human had corrected the row.
            assert reloaded.settled_basis_id == settlement_basis_id(
                SettlementBasisEnum.DERIVED,
            )
            assert settled_figure(reloaded) == Decimal("180.00")
            assert owned_contribution(reloaded) == Decimal("180.00")

    def test_a_bill_ticked_with_a_different_figure_books_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FB**: a bill's tick MAY correct its amount.

        Production's shape, to the cent: Electricity estimated `$300.00`,
        statement says `$245.32`.  Settling at the estimate would leave
        `$54.68` for plan step X-f3's residual to book as Uncategorized Income
        -- at the one moment the user is holding the paper that says the true
        figure.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("245.32")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            assert reloaded.settled_amount == Decimal("245.32")
            assert reloaded.estimated_amount == Decimal("300.00")

    def test_a_correction_on_a_DERIVED_row_is_ignored_not_applied(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A forged box for an envelope with entries changes nothing.

        The panel renders no input there, so the only way to submit one is by
        hand.  **What refuses it is the VERB, and a review proved that is the
        only thing that ever did**: the writer carried its own "read the box
        only where the panel offered one" guard, and deleting that guard left
        every test green, because ``settle_transaction`` routes an
        entries-derived row to a branch that ignores ``actual_amount`` outright.
        The guard is gone; this grades the rule that was doing the work.
        `$999.99` against `$40.00` of entries books `$40.00`.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._settle(
                seed_user, [txn.id], {txn.id: Decimal("999.99")},
            ) == 1

            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_income_is_offered_as_a_DEPOSIT_and_settles_to_Received(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-FD**: a deposit you are waiting on is what a statement settles.

        Production's largest offered deposit was an FSA reimbursement of
        `$1,958.87`; it settles to Received, not Paid, and the set counts it
        apart from the payments because a deposit and a bill do not sum to
        anything a reader wants.
        """
        with app.app_context():
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            result = reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            )
            assert result.deposit_count == 1
            assert result.deposit_total == Decimal("1958.87")
            assert result.payment_count == 0
            assert self._offered(seed_user)[deposit.id].is_income is True

            assert self._settle(seed_user, [deposit.id]) == 1
            db.session.expire_all()
            assert db.session.get(Transaction, deposit.id).status_id == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

    def test_the_two_totals_are_never_summed_into_one(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The double-count ruling R-FA's own text warns about.

        An envelope with `$40` + `$60` of purchases and its own `$100` close
        offers BOTH -- and a single "total" would report `$200` against `$100`
        of money.  The set publishes the purchase total and the payment total
        separately and nothing adds them.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            result = reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id,
                _statement(seed_user["account"].id),
            )
            assert result.purchase_count == 2
            assert result.purchase_total == Decimal("100.00")
            assert result.payment_count == 1
            assert result.payment_total == Decimal("100.00")
            # Asserted by NAMING the fields, not by ``hasattr``: a probe on a
            # dataclass passes for any typo (``status_seam``'s own X-aa
            # lesson), so it would report "there is no combined total" about a
            # field spelled anything at all.
            assert {field.name for field in fields(result)} == {
                "groups",
                "purchase_count", "purchase_total",
                "payment_count", "payment_total",
                "deposit_count", "deposit_total",
            }


class TestTheCashFigureBesideTheBookedOne:
    """Finding **N-226**: what a tick BOOKS is not always what a statement shows.

    An envelope settles at ``sum(entries)`` over EVERY entry it holds, and a
    card purchase is one of those -- but a card purchase never touches
    checking, which is exactly why the purchase arm refuses to OFFER one.  So a
    `$40` debit plus a `$60` card purchase is offered at `$100.00` on a screen
    captioned "tick everything your statement shows", against a statement
    showing `$40`.

    **The LEDGER was right either way** -- ``settled_cash_leg`` subtracts the
    credit sum -- so the fix prints both figures rather than changing what a
    tick books: ``actual_amount`` legitimately IS total spend, and moving it
    would make the panel disagree with the grid and the analytics.

    Production carries 18 card entries in history and ZERO on a Projected
    envelope today, so this is latent rather than live.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _offered = staticmethod(TestTheTransactionArm._offered)

    def test_an_envelope_holding_a_CARD_purchase_publishes_both_figures(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` debit + `$60` card: booked `$100.00`, statement `$40.00`."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.cash_amount == Decimal("40.00")

    def test_an_envelope_of_DEBITS_publishes_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The negative control, and it is what stops the caption being noise.

        Nothing on the card means the booked figure IS what the statement
        shows, so there is no second number to print.  Without this the case
        above passes for a panel that captions every envelope.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._offered(seed_user)[txn.id].cash_amount is None

    def test_a_bill_and_a_transfer_publish_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A row that carries no entries has no card half to disagree with.

        Graded across BOTH source-row arms because the field is on the shared
        offer type: a bill can hold no entries, and a transfer shadow
        structurally cannot (production: 342 shadows, 0 entries).
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Savings",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == seed_user["account"].id,
                )
                .one()
            )
            offered = self._offered(seed_user)
            assert offered[bill.id].cash_amount is None
            assert offered[shadow.id].cash_amount is None

    def test_the_cash_figure_matches_what_the_LEDGER_will_post(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The panel's second figure IS the posted one, not a lookalike.

        Both come from ``cash_ledger.credit_entry_sum``, and this grades that
        by settling the row and comparing against ``settled_cash_leg`` -- the
        expression the ledger writer and the cash walk both reduce through.
        Two numbers that agree by construction rather than by coincidence is
        the whole reason the term was published instead of re-summed.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offered_cash = self._offered(seed_user)[txn.id].cash_amount
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    owner_id=seed_user["user"].id,
                    account_id=seed_user["account"].id,
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    anchor=_statement(seed_user["account"].id),
                ),
            ) == 1
            db.session.commit()

            db.session.expire_all()
            settled = db.session.get(Transaction, txn.id)
            assert cash_ledger.settled_cash_leg(settled) == -offered_cash


class TestTheCorrectionCountIsWhatAHumanTyped:
    """Finding **N-231**: the count says how many figures a HUMAN supplied.

    ``transactions_reconciled`` describes its rows as "some carrying a
    corrected amount", and ruling **R-FB**'s production measurement ("11 of 93
    settled bills carry a hand-typed correction") is made of this same signal.
    It was read off the COLUMN -- rows whose ``actual_amount`` changed -- and
    an envelope's close ALWAYS writes that column, so every envelope tick
    incremented it.  It now asks the verb's own published predicate
    (``transaction_service.is_correction``) before the settle.

    Four shapes, and the first two are the ones the column reading got wrong.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _settle = staticmethod(TestTheTransactionArm._settle)

    @staticmethod
    def _corrected_count(caplog):
        """Return ``corrected_count`` off the one reconcile event emitted."""
        events = [
            record for record in caplog.records
            if getattr(record, "event", None) == EVT_TRANSACTIONS_RECONCILED
        ]
        assert len(events) == 1, "expected exactly one reconcile event"
        return events[0].corrected_count

    def test_an_envelope_close_with_no_correction_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The defect, reproduced from the failing direction.

        An envelope carrying `$40.00` of purchases settles at `$40.00` and its
        ``actual_amount`` moves from NULL to `$40.00` -- a MACHINE write, in the
        same statement as the status.  Nobody typed anything, so the count is
        zero.  Measured before the fix on a probe of this exact shape: it
        logged ``corrected_count: 1``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(seed_user, [txn.id]) == 1

            assert self._corrected_count(caplog) == 0
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_a_forged_box_on_a_DERIVED_row_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A figure the verb IGNORES was not a correction.

        The panel renders no box for an entries-derived row, so the only way to
        submit one is by hand -- and ``settle_transaction`` drops it.  A count
        that read the submission rather than the outcome would report a
        correction the ledger never made.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [txn.id], {txn.id: Decimal("999.99")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_its_prefill_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The echo: an untouched box is not a correction.

        The panel PREFILLS the box, so every correctable row on the form posts
        a figure whether the user touched it or not.  Counting submissions
        would measure how many boxes the panel drew.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("180.00")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_a_DIFFERENT_figure_counts_ONE(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The one shape that IS a correction, so the count is not inert.

        Production's Electricity, to the cent: estimated `$300.00`, statement
        `$245.32`.  Without this case the three zeros above are satisfied by a
        count that is always zero.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("245.32")},
                ) == 1

            assert self._corrected_count(caplog) == 1


class TestTheSectionsAndTheOrder:
    """Ruling **R-FC**'s three presentational rules, graded.

    **They shipped with ZERO tests and an adversarial review said so.**  Nothing
    referenced ``OfferKind``, ``section_label``, ``kind``, ``_block_order`` or
    ``rank`` -- the entire content of the ruling, and the whole reason the
    assembler took ownership of the block order, was ungraded.  That is the
    balance README's own live lesson (a producer's new fields are the fields
    nobody was asserting) repeating one leaf later.

    The rules: the blocks arrive ordered by kind then by each block's OLDEST
    offer; a section label is emitted where the kind CHANGES and nowhere else;
    and a childless block is one the template prints inline.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)

    @staticmethod
    def _resolved(seed_user):
        return reconcile_service.outstanding_set(
            seed_user["user"].id, seed_user["account"].id,
            _statement(seed_user["account"].id),
        )

    def test_each_arm_TAGS_its_kind_and_income_is_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """An income row is a DEPOSIT, not a bill.

        **The defect this grades was live.**  The kind was DERIVED from the
        block's shape -- purchases meant envelope, correctable meant bill -- and
        an income row is never purchase-tracked, so production's `$1,958.87`
        FSA reimbursement rendered under a heading reading "Bills" three lines
        below a summary counting it as a deposit.  A figure and its caption
        disagreeing, on the one screen read beside a paper statement.

        Shown to FIRE: classifying by ``is_correctable`` puts the deposit in
        ``Bills``.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            by_id = {
                group.transaction_id: group.kind
                for group in self._resolved(seed_user).groups
            }
            assert by_id[envelope.id] is reconcile_service.OfferKind.ENVELOPE
            assert by_id[bill.id] is reconcile_service.OfferKind.BILL
            assert by_id[deposit.id] is reconcile_service.OfferKind.DEPOSIT

    def test_the_blocks_are_ordered_by_KIND_then_by_their_oldest_offer(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Like sits with like, and within a section the oldest is first.

        The bills are created NEWEST-first and given due dates that reverse
        that, so a result matching insertion order would be indistinguishable
        from one matching the rule if they agreed.
        """
        with app.app_context():
            late_bill = self._bill(
                seed_user, seed_periods[0], name="Water",
                due_date=date(2026, 1, 9),
            )
            early_bill = self._bill(
                seed_user, seed_periods[0], name="Electricity",
                due_date=date(2026, 1, 3),
            )
            deposit = self._bill(
                seed_user, seed_periods[0], name="Refund",
                amount="20.00", income=True,
            )
            envelope = seed_entry_template["transaction"]
            db.session.commit()

            order = [
                group.transaction_id
                for group in self._resolved(seed_user).groups
            ]
            assert order == [
                envelope.id, early_bill.id, late_bill.id, deposit.id,
            ]

    def test_a_section_label_is_emitted_ONLY_where_the_kind_changes(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One heading per run, and it names the kind that follows it.

        Two bills between an envelope and a deposit: the second bill continues
        its section and carries no label.  A per-block label would print
        "Bills" twice; a missing one would leave the deposit under the bills'
        heading, which is the mis-captioning this ruling's sections exist to
        prevent.
        """
        with app.app_context():
            self._bill(seed_user, seed_periods[0], name="Electricity",
                       due_date=date(2026, 1, 3))
            self._bill(seed_user, seed_periods[0], name="Water",
                       due_date=date(2026, 1, 9))
            self._bill(seed_user, seed_periods[0], name="Refund",
                       amount="20.00", income=True)
            db.session.commit()

            labels = [
                group.section.label if group.section else None
                for group in self._resolved(seed_user).groups
            ]
            assert labels == ["Envelopes", "Bills", None, "Deposits"]

    def test_a_block_with_purchases_is_NOT_childless_and_one_without_is(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The childless rule's own input, which the template branches on.

        R-FC's first presentational rule is the template's -- a block with no
        children prints inline -- so what the producer owes it is an honest
        ``purchases``.  Graded here rather than by scraping markup: the
        rendering is one ``{% if %}`` over this tuple.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            _make_entry(envelope, seed_user["user"], amount="40.00")
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            db.session.commit()

            by_id = {
                group.transaction_id: group
                for group in self._resolved(seed_user).groups
            }
            assert len(by_id[envelope.id].purchases) == 1
            assert by_id[bill.id].purchases == ()

    def test_every_kind_has_a_rank_and_a_label(self):
        """The section vocabulary is TOTAL over its own members.

        ``rank`` resolves through a map built from the class, so a member added
        without one is impossible rather than silently sorted to the wrong end
        -- which is the failure mode a hand-written rank map has, and the reason
        plan step X-f2-c3 can add ``TRANSFER`` by writing one line.
        """
        kinds = list(reconcile_service.OfferKind)
        assert sorted(kind.rank for kind in kinds) == list(range(len(kinds)))
        assert all(kind.section_label for kind in kinds)
        # And exactly one carries a NOTE.  Asserted as a set rather than as a
        # count so a note appearing on the wrong section fails here rather than
        # on the screen: the sentence is about what a TICK does, and printing
        # "settles both sides" over the Bills section would be a false promise
        # about somebody's money.
        assert {
            kind for kind in kinds if kind.section_note
        } == {reconcile_service.OfferKind.TRANSFER}
