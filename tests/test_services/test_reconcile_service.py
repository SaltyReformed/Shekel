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

from dataclasses import fields
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import (
    account_service,
    pay_period_service,
    reconcile_service,
    status_seam,
)
from tests._test_helpers import create_transfer


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The twin of ``test_entry_service``'s helper, carried with the tests that
    use it rather than imported across test modules: it exists to build a row
    WITHOUT the service under test, so a shared version would couple two
    modules' fixtures to one shape for no gain.
    """
    entry = TransactionEntry(
        transaction_id=transaction.id,
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
            set(entry_ids), _OBSERVED_ON,
        )

    @staticmethod
    def _listed(seed_user):
        """Return the ids the reader offers for the seed user's account."""
        return [
            purchase.entry_id
            for group in reconcile_service.outstanding_set(
                seed_user["user"].id, seed_user["account"].id, _OBSERVED_ON,
            ).groups
            for purchase in group.purchases
        ]

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

    def test_a_purchase_on_a_settled_parent_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The entry reservation prices only PROJECTED rows.

        A purchase on a settled parent is inert -- offering it would ask the
        user to reconcile something that cannot move a figure, and its parent's
        confirmed cash effect already counts it in full.
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
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

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
            other_period = pay_period_service.generate_pay_periods(
                user_id=seed_second_user["user"].id,
                start_date=date(2026, 1, 2), num_periods=1, cadence_days=14,
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
            other_period = pay_period_service.generate_pay_periods(
                user_id=seed_second_user["user"].id,
                start_date=date(2026, 1, 2), num_periods=1, cadence_days=14,
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
            seed_user["user"].id, seed_user["account"].id, observed_on,
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
                seed_user["user"].id, seed_user["account"].id, observed_on,
            ).groups
            if group.settle is not None
        }

    @staticmethod
    def _settle(seed_user, ids, corrections=None,
                observed_on=_OBSERVED_ON):
        """Run the arm's writer against the seed user's own account."""
        return reconcile_service.record_settled_transactions(
            seed_user["user"].id, seed_user["account"].id,
            set(ids), corrections or {}, observed_on,
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

    def test_a_transfer_shadow_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Transfer invariant 3: a shadow settles through the transfer service.

        Plan step X-f2-c3's arm, and the verb REFUSES one -- so admitting it
        here would turn a design boundary into a 400 mid-reconciliation.  A
        forged id changes nothing rather than raising.

        **Built through the real transfer service**, not by writing a
        ``transfer_id`` onto an ordinary row: the expense shadow that lands on
        this account is a genuine Projected row in the same period, so it is
        inside every other clause of the scope and ONLY the shadow clause can
        exclude it.  A hand-set id would also be filtered by an FK that does
        not exist in production data.
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

            assert shadow.id not in self._offered(seed_user)
            assert self._settle(seed_user, [shadow.id]) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, shadow.id).settled_on is None

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
                other_id, seed_user["account"].id, _OBSERVED_ON,
            ).groups == ()
            assert reconcile_service.record_settled_transactions(
                other_id, seed_user["account"].id, {txn.id}, {}, _OBSERVED_ON,
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
                seed_user["user"].id, other.id, _OBSERVED_ON,
            ).groups == ()
            assert reconcile_service.record_settled_transactions(
                seed_user["user"].id, other.id, {txn.id}, {}, _OBSERVED_ON,
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

    def test_an_empty_submission_is_a_no_op(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ticking nothing settles nothing, and issues no query."""
        with app.app_context():
            assert self._settle(seed_user, []) == 0


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
            assert db.session.get(
                Transaction, txn.id,
            ).actual_amount == Decimal("100.00")

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
            assert reloaded.actual_amount is None
            assert reloaded.effective_amount == Decimal("180.00")

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
            assert reloaded.actual_amount == Decimal("245.32")
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
            assert db.session.get(
                Transaction, txn.id,
            ).actual_amount == Decimal("40.00")

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
                seed_user["user"].id, seed_user["account"].id, _OBSERVED_ON,
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
                seed_user["user"].id, seed_user["account"].id, _OBSERVED_ON,
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
            seed_user["user"].id, seed_user["account"].id, _OBSERVED_ON,
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
                group.section_label for group in self._resolved(seed_user).groups
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
