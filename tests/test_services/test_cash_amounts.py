"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules of :mod:`app.services.cash_ledger._amounts`,
tested against that module directly.  The cash analog of the loan side's split
tests: given a single row, "how much of this hits the checking balance right
now?"

**These tests MOVED here at plan step X-c2c2a**
(``docs/audits/balance_architecture/README.md``), from
``test_balance_calculator_entries.py`` and ``test_balance_calculator.py``'s
``TestIncomeOverridesSeam``.  They always tested THIS rule; they reached it
through ``balance_at._calculator.calculate_balances``, a PRODUCER that deletes
at plan step X-c2c4, so every assertion read ``anchor - reservation`` and the
anchor arithmetic was scenery.  **Every hand-computed reservation figure is
preserved verbatim** -- the ``5000.00 -`` wrapper is what dropped, and each test
that had one still carries its original arithmetic comment so the two forms can
be diffed against each other.

What did NOT move, and why the split is not "the whole file":

* the STATUS gates (a settled / cancelled / Credit row contributes nothing) and
  the reductions over a SET of rows are :mod:`app.services.cash_ledger._flows`'
  question, and moved to ``test_cash_flows.py``;
* the two ANCHOR-PERIOD tests discriminate a ``_calculator`` branch -- which of
  its two arms calls the shared reduction -- so they stayed with that module and
  died with it at plan step X-g4b.

**Plan step X-g4b then moved C5-8 here too** --
:meth:`TestTheEntriesRelationshipIsNotASeam.test_the_silent_degrade_seam_is_absent_from_source`,
the STATIC guard that the CRIT-01 short-circuit has not been re-introduced.  It
lived in ``test_balance_resolver.py``, which died with ``balances_for``; its
home is the module holding the rule it guards, and its scan WIDENED with the
move (see the method for why that is the guard's own stated logic rather than
opportunism).

The plan's own one-liner said "``test_balance_calculator_entries.py`` (27 tests)
is the three-bucket reservation formula"; tracing measured 18, and the
correction is recorded at the step.

The reservation formula under test::

    settled_debit     = sum(amount where not is_credit
                            and coverage.is_cleared(entry))
    outstanding_debit = sum(amount where not is_credit and every other case)
    sum_credit        = sum(amount where is_credit)

    impact = max(estimated - settled_debit - sum_credit, outstanding_debit)

**Which bucket a debit falls in is a RECORDED FACT** (plan step X-f3a-1, ruling
**R-FL**), and its history is three answers deep.  It was a stored
``is_cleared`` boolean, and every fixture below carried it as a third bool in a
triple -- an unconditional claim that a purchase was inside the anchor, which
its own account could not always support.  Ruling R-DH (d) replaced it with a
DATE comparison, and the triples took the day the bank was seen to take the
money.  The developer's own bank exports then falsified that comparison (33 of
110 matched movements carry the day the bank posted them), so the bucket is now
:meth:`~app.services.cash_ledger.StatementCoverage.is_cleared` -- which
statement was RECORDED as showing this purchase, falling back to the date rule
only where none was.  Every fixture below leaves the link unset, so every case
here exercises that fallback and answers exactly what it answered before.

**The link arm answers the SAME as the fallback, always, and that is a bound
rather than an oversight**: a link may not contradict the posting day while an
assertion resets the ledger, so the only freedom it has -- choosing between two
assertions that share a civil day -- is invisible to a bool.
``TestALinkMayNotContradictThePostingDay`` grades both the refusal and the
admitted case.

Three consequences of the date era are still test cases rather than renames: a
NULL posting day is OUTSTANDING, a posting day AFTER the day the balance was
read is OUTSTANDING, and an account that has never asserted a balance
reconciles nothing.  None of the three was expressible while the answer was a
flag.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy.exc

from app.services.cash_ledger._amount_source import AmountBasis
from app.services.cash_ledger._amounts import (
    ProjectedBasis,
    _entry_aware_amount,
    _expense_amount,
    income_amount,
)
from app.services.cash_ledger._clearing import StatementCoverage
from app.extensions import db
from app.models.account import AccountAnchorHistory
from tests._test_helpers import add_entry, add_txn, create_envelope_txn


# The four days these tests turn on, named for the fact each one is.  They are
# DISTINCT on purpose: a purchase made, taken by the bank a day later, and read
# on a statement two days after that is the ordinary debit-card shape, and
# collapsing them onto one date would hide which comparison the rule makes.
_PURCHASED_ON = date(2026, 1, 20)
_POSTED_ON = date(2026, 1, 21)
_STATEMENT_DAY = date(2026, 1, 22)
# The assertion that statement day belongs to, as the account's whole clearing
# rule.  It is a TYPE and not a date so that `settled_on <= statement day`
# cannot be written anywhere but inside the rule itself -- see
# `ReconciledThrough` for what the fifth spelling of that comparison cost
# production, and `StatementCoverage` for why the comparison is now only the
# fallback.
#
# The id is arbitrary and is never dereferenced: nothing here loads the
# assertion row, and a purchase reaches the LINK arm only by naming this id,
# which is what `TestALinkOutranksTheDate` does deliberately.
_STATEMENT_ID = 4001
_ASSERTED_COVERAGE = StatementCoverage(
    anchor_ids=(_STATEMENT_ID,), observed_days=(_STATEMENT_DAY,),
)


def _basis(*rows, overrides=None, coverage=_ASSERTED_COVERAGE):
    """The :class:`ProjectedBasis` a producer would hand these rows.

    Built HONESTLY rather than zeroed: ``priced_ids`` covers exactly the rows
    passed, because the resolver refuses a row its basis was not built over, and
    a test that reached a refusal by violating that contract would be grading
    the contract instead of the rule.  ``overrides`` lands in the SALARY map --
    either producer's map answers :func:`live_override` identically, and which
    rule prices a row is never decided by which map its id turned up in.
    """
    return ProjectedBasis(
        amounts=AmountBasis(
            priced_ids=frozenset(row.id for row in rows),
            salary_net=dict(overrides or {}),
            loan_cash={},
        ),
        coverage=coverage,
    )
_POSTED_AFTER_THE_STATEMENT = date(2026, 1, 23)


def _envelope(db_session, seed_user, period, estimated, entries=()):
    """Build a Projected envelope expense carrying *entries*, and return it.

    The shared setup for the reservation tests.  Each moved test built ~50
    lines of template + transaction + entry construction inline and then
    re-queried with ``selectinload``; the re-query is not reproduced because
    the rule under test reads ``txn.entries`` through the relationship
    descriptor, which resolves either way -- a property
    :class:`TestTheEntriesRelationshipIsNotASeam` below pins explicitly rather
    than leaving it implied in 18 setups.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`~app.models.pay_period.PayPeriod` to place it in.
        estimated: The envelope's budgeted amount, as a string.
        entries: An iterable of ``(amount, is_credit, settled_on)`` triples --
            a string amount, a bool, and the day the bank was seen to take the
            money (or ``None`` for a purchase not yet seen on a statement).
            The third element was a BOOL until plan step S1-c, which is the
            whole finding: a flag claimed a purchase was inside the anchor
            without saying which day made it so, while a date can be compared
            against the day the balance was actually read.  Passing the day at
            the call site is what makes each test's bucket readable there
            instead of in this helper.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = create_envelope_txn(
        seed_user, db_session, period, "Groceries", Decimal(estimated),
    )
    for amount, is_credit, settled_on in entries:
        add_entry(
            db_session, seed_user, txn, Decimal(amount), _PURCHASED_ON,
            is_credit=is_credit, settled_on=settled_on,
        )
    db_session.commit()
    return txn


class TestTheEntryAwareReservation:
    """The three-bucket reservation for a still-Projected envelope expense.

    The six scope-doc scenarios (Section 4.2) plus the boundary shapes.  Every
    figure is the reservation itself; before X-c2c2a each was asserted as
    ``5000.00 - reservation`` through the balance walk.
    """

    def test_no_entries_holds_the_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 1: a tracked expense with no entries holds its estimate.

        est=500, debit=0, credit=0 -> the empty-entries short circuit returns
        ``effective_amount``, which for an unfilled Projected expense is the
        estimate: 500.00.  (Was: 5000 - 500 = 4500.)
        """
        with app.app_context():
            txn = _envelope(db.session, seed_user, seed_periods[1], "500.00")

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_debit_under_budget_holds_the_full_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 2: an uncleared debit under budget does not reduce it.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = max(500, 200) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_a_credit_entry_reduces_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 3: mixed debit + credit under budget -- credit reduces.

        est=500, uncleared_debit=300, credit=100.
        max(500 - 0 - 100, 300) = max(400, 300) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", False, None), ("100.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("400.00")

    def test_all_credit_leaves_only_the_uncovered_portion(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 4: all-credit entries -- only the uncovered part hits cash.

        est=500, debit=0, credit=400.
        max(500 - 0 - 400, 0) = max(100, 0) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("100.00")

    def test_debit_overspend_raises_the_reservation_to_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 5: overspend -- the uncleared debit total is the floor.

        est=500, uncleared_debit=530, credit=0.
        max(500 - 0 - 0, 530) = max(500, 530) = 530.  (Was: 4470.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("530.00", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("530.00")

    def test_mixed_overspend_takes_the_debit_floor_over_the_reduction(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 6: the debit floor beats the credit-reduced reservation.

        est=500, uncleared_debit=400, credit=200.
        max(500 - 0 - 200, 400) = max(300, 400) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", False, None), ("200.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("400.00")

    def test_zero_estimate_with_a_debit_reserves_the_debit(
        self, app, db, seed_user, seed_periods,
    ):
        """A zero-budget envelope still holds back what was actually spent.

        est=0, uncleared_debit=50, credit=0.
        max(0 - 0 - 0, 50) = 50.  (Was: 4950.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "0.00",
                [("50.00", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("50.00")

    def test_credit_exceeding_the_estimate_floors_at_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit beyond the budget cannot drive the reservation negative.

        est=500, uncleared_debit=100, credit=600.
        max(500 - 0 - 600, 100) = max(-100, 100) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, None), ("600.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("100.00")

    def test_one_cent_debit_does_not_disturb_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """The smallest representable entry: a one-cent uncleared debit.

        est=500, uncleared_debit=0.01, credit=0.
        max(500 - 0 - 0, 0.01) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("0.01", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_values_near_the_column_limit_do_not_overflow(
        self, app, db, seed_user, seed_periods,
    ):
        """Values at the ``Numeric(12,2)`` ceiling survive the ``max()``.

        est=9999999999.99, uncleared_debit=9999999999.99, credit=0.
        max(9999999999.99 - 0 - 0, 9999999999.99) = 9999999999.99.
        (Was: 10000000000.00 - 9999999999.99 = 0.01.)
        """
        with app.app_context():
            large = "9999999999.99"
            txn = _envelope(
                db.session, seed_user, seed_periods[1], large,
                [(large, False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal(large)

    def test_a_row_with_no_template_is_worth_its_effective_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain (non-envelope) expense carries no entries, so no reduction.

        The pre-entries behaviour, and still the common case: no template
        means no entries, so the short circuit returns ``effective_amount``
        -- 1200.00.  (Was: 5000 - 1200 = 3800.)
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[1], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("1200.00")


class TestTheRecordedPostingDay:
    """``settled_on`` moves a debit from the FLOOR into the reduction.

    A purchase the account's latest asserted balance already contains is
    subtracted from the reservation; every other purchase has hit checking
    without the anchor knowing, so it acts as the floor instead.

    **This class tested a stored ``is_cleared`` boolean until plan step S1-c**
    (ruling R-DH (d)).  The flag was written by a bulk UPDATE at every anchor
    true-up over "every entry dated on or before the SERVER's today", so which
    bucket a purchase fell in was decided by the order two buttons were
    pressed.  The bucket is now ``coverage.is_cleared(entry)``, evaluated at
    read time -- the same rule, in the same units, the read replay and the
    posting walk apply to a settled transaction.

    **No purchase here names a statement**, so every case exercises that rule's
    DATE fallback and answers exactly what it answered under R-DH (d).  That is
    the point of these cases surviving X-f3a-1 unchanged: the recording step
    moves no figure.  ``TestALinkOutranksTheDate`` grades the arm that is new.

    Three of the cases below could not be WRITTEN against a flag, and they are
    the ones that matter: an unobserved purchase (NULL), a purchase the bank
    took after the statement was read, and an account that has never declared a
    balance.  Each is OUTSTANDING, which is the conservative arm -- the engine
    never guesses a posting day on the user's behalf.
    """

    def test_the_grocery_bug_after_a_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """The user-reported defect: three posted purchases against $500.

        est=500, settled_debit=106.86+249.71+105.77=462.34, outstanding=0,
        credit=0.  max(500 - 462.34 - 0, 0) = 37.66 -- only the unreconciled
        remainder is still held.  (Was: 5000 - 37.66 = 4962.34.)

        All three were taken by the bank on 01-21 and the statement the user
        read was true through 01-22, so all three are inside it.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("106.86", False, _POSTED_ON),
                 ("249.71", False, _POSTED_ON),
                 ("105.77", False, _POSTED_ON)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("37.66")

    def test_partial_settled_and_outstanding(
        self, app, db, seed_user, seed_periods,
    ):
        """A posted purchase reduces, an unobserved one floors, in one envelope.

        est=500, settled_debit=100, outstanding_debit=50, credit=0.
        max(500 - 100 - 0, 50) = max(400, 50) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, _POSTED_ON), ("50.00", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("400.00")

    def test_settled_overspend_floors_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Posted debits beyond the budget hold back nothing further.

        est=500, settled_debit=600, outstanding_debit=0, credit=0.
        max(500 - 600 - 0, 0) = max(-100, 0) = 0 -- the money already left and
        the anchor already knows.  (Was: 5000 - 0 = 5000.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("600.00", False, _POSTED_ON)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("0.00")

    def test_all_outstanding_reduces_to_the_legacy_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """With nothing posted the three buckets collapse to the old two.

        est=500, outstanding_debit=200, credit=0.
        max(500 - 0 - 0, 200) = 500, which is what the pre-cleared-flag
        formula gave.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_settled_debit_plus_credit_both_reduce(
        self, app, db, seed_user, seed_periods,
    ):
        """A posted debit and a credit reduce the same reservation.

        est=500, settled_debit=200, outstanding_debit=0, credit=100.
        max(500 - 200 - 100, 0) = 200.  (Was: 4800.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON), ("100.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("200.00")

    def test_a_new_purchase_has_no_posting_day_and_is_outstanding(
        self, app, db, seed_user, seed_periods,
    ):
        """A purchase written without a posting day is OUTSTANDING, and safe.

        The default matters to money: a purchase defaulting to SETTLED would
        subtract it from the reservation before the anchor reflected it,
        double-counting it out of the projection.  est=500,
        outstanding_debit=200 -> max(500 - 0 - 0, 200) = 500.  (Was: 4500.)

        ``settled_on`` being NULL is a FACT, not a gap (ruling R-DH (d)): it
        means the user has not seen this purchase on a statement, so the
        envelope keeps holding its whole budget back until they confirm the
        money has left.  The column is asserted directly beside the figure so a
        regression that started defaulting it fails as itself rather than as a
        balance drift.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries",
                Decimal("500.00"),
            )
            add_entry(
                db.session, seed_user, txn, Decimal("200.00"), _PURCHASED_ON,
            )
            db.session.commit()

            assert txn.entries[0].settled_on is None
            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_a_purchase_posted_after_the_statement_is_outstanding(
        self, app, db, seed_user, seed_periods,
    ):
        """The case a flag could not hold: posted, but AFTER the balance was read.

        A ``$200.00`` purchase made 01-20 whose bank took it on 01-23, against a
        balance the user read for 01-22.  ``23 > 22``, so it is NOT inside that
        balance and stays on the floor: max(500 - 0 - 0, 200) = 500.

        Under the retired ``is_cleared`` boolean this state was INEXPRESSIBLE.
        The flag said "inside the anchor" with no day attached, so a purchase
        the bank had taken was reconciled against every balance the account had
        ever asserted -- including ones read before the money moved.  That is
        the direction of the defect that opened the arc: subtracting a purchase
        from a reservation whose anchor never contained it double-counts it out
        of the projection, and the resulting figure is too HIGH by the purchase.

        The mirror of this is the test above it, one day earlier
        (``test_partial_settled_and_outstanding``'s ``$100.00`` on 01-21), so
        the pair straddles the boundary rather than probing one side of it.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_AFTER_THE_STATEMENT)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_a_purchase_posted_ON_the_statement_day_is_inside_it(
        self, app, db, seed_user, seed_periods,
    ):
        """The boundary itself: an assertion is its day's CLOSING balance.

        A ``$200.00`` purchase the bank took on 01-22, against a balance read
        for 01-22.  ``22 <= 22``, so it is inside -- ruling R-DH (a), the same
        inclusive boundary the read fold and the posting walk apply to a settled
        transaction.  max(500 - 200 - 0, 0) = 300.

        The off-by-one this pins is worth a cent-exact figure rather than a
        direction: an EXCLUSIVE boundary here would hold the full $500.00 and
        the projection would be $200.00 too low every time a user entered their
        balance the same day they shopped -- which, on the developer's real
        data, is 53 of 53 same-day entries.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _STATEMENT_DAY)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("300.00")

    def test_an_account_that_has_never_asserted_reconciles_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A rule with no statements in it puts every purchase on the floor.

        A ``$200.00`` purchase the bank was seen to take on 01-21, priced on an
        EMPTY coverage -- what ``cash_ledger.coverage_for`` returns for an
        account that has never asserted a balance.  There is nothing for the
        purchase to be inside of, so it is outstanding and the reservation stays
        at max(500 - 0 - 0, 200) = 500.

        :meth:`~app.services.cash_ledger.StatementCoverage.clearing_anchor_id`
        is TOTAL in both the line and the assertion set for exactly this reason
        -- every absence means "not inside" -- so no caller has to remember a
        precondition.  A rule that treated a missing assertion as "everything is
        reconciled" would empty every envelope on an account the user had never
        trued up.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON)],
            )

            assert _entry_aware_amount(
                txn,
                _basis(txn, coverage=StatementCoverage((), ())),
            ) == Decimal("500.00")


def _real_statement(account_id):
    """Return ``(assertion id, coverage)`` for an account that really has one.

    The LINK tests cannot use :data:`_ASSERTED_COVERAGE`'s fabricated id:
    ``fk_transaction_entries_reconciled_by`` refuses a link to a statement that
    does not exist, which is the whole point of the composite key and is the
    first thing this helper proved when it was not here.

    So the id is the account's REAL opening assertion and the day is this
    module's ``_STATEMENT_DAY``.  The two need not agree, and nothing checks
    that they do: a :class:`StatementCoverage` is a VALUE describing what an
    account's owner has declared, and these tests declare one statement on the
    day the whole module turns on.  Building the coverage from the seeded row's
    own 2024 day instead would put every purchase after the statement and grade
    nothing.

    Args:
        account_id: The account whose assertion to borrow.

    Returns:
        ``(anchor_id, StatementCoverage)``.
    """
    anchor_id = (
        db.session.query(AccountAnchorHistory.id)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .scalar()
    )
    return anchor_id, StatementCoverage(
        anchor_ids=(anchor_id,), observed_days=(_STATEMENT_DAY,),
    )


class TestALinkMayNotContradictThePostingDay:
    """A recorded statement and a posting day must agree about which closed over it.

    Ruling **R-FL** records which statement showed a purchase.  Ruling R-S and
    the fold's own construction then bound how far that record may travel: a
    line cleared by a statement whose day is not the one the date rule picks is
    UNRENDERABLE while an assertion RESETS the ledger, and
    ``StatementCoverage._recorded_anchor_id`` carries the theorem and the
    production measurement; where the record cannot decide, the date rule
    answers and no balance moves.

    **So the reservation's answer is UNCHANGED by this step, and that is the
    finding rather than a disappointment.**  A first implementation let the link
    outrank the day here too, and an adversarial review found what it bought: a
    purchase ticked on a statement and then dated FORWARD would read as cleared,
    releasing its envelope's reservation and putting already-spent money back in
    the projection -- the exact failure ``status_seam.reject_future_settle_day``
    exists to prevent, arriving through the one door that deliberately admits a
    future day.  The date rule answers instead, and
    ``entry_service.update_entry`` releases the link on any day move, so the
    contradictory row is transient rather than a state the app keeps.

    What the link IS for in this leaf is the record itself -- which statement
    was walked -- which plan steps X-f3b, X-f3c and X-f6a all need and none of
    which can be derived later.
    """

    def test_a_link_cannot_release_a_purchase_posted_after_the_statement(
        self, app, db, seed_user, seed_periods,
    ):
        """A purchase posted AFTER the statement stays reserved, link or no link.

        The two facts contradict -- a statement of the 22nd cannot have shown
        money the bank took on the 23rd -- and the date rule wins, so the whole
        `$500.00` stays held back.

        **This is what keeps ``status_seam.reject_future_settle_day``'s stated
        exemption true.**  That refusal explains why a FUTURE
        ``TransactionEntry.settled_on`` is deliberately permitted where a
        transaction's is refused: it is the CONSERVATIVE direction, because no
        assertion closes over it and the debit stays reserved.  Had the link
        been allowed to override, ticking a purchase and then dating it forward
        would drop the reservation to `$300.00` and hand `$200.00` the bank has
        not taken back to the projection -- the exact failure that refusal
        exists to prevent, arriving through the one door that admits a future
        day.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_AFTER_THE_STATEMENT)],
            )
            anchor_id, coverage = _real_statement(txn.account_id)

            assert _entry_aware_amount(
                txn, _basis(txn, coverage=coverage),
            ) == Decimal("500.00"), (
                "Unlinked, the date rule calls a purchase posted after the "
                "statement outstanding -- max(500 - 0 - 0, 200)."
            )

            txn.entries[0].reconciled_by_id = anchor_id
            db.session.flush()

            assert _entry_aware_amount(
                txn, _basis(txn, coverage=coverage),
            ) == Decimal("500.00")
            db.session.rollback()

    def test_a_purchase_may_not_name_a_statement_with_no_posting_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The same contradiction at the database tier, one column earlier.

        ``settled_on`` NULL is the state every fresh purchase is in.  A link on
        one asserts both that a statement showed the money and that nothing has
        been observed to leave the account, and
        ``ck_transaction_entries_cleared_needs_settle_day`` refuses the pair
        before any rule is asked.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )
            anchor_id, _coverage = _real_statement(txn.account_id)

            txn.entries[0].reconciled_by_id = anchor_id
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transaction_entries_cleared_needs_settle_day",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_link_that_AGREES_with_the_day_answers_what_the_day_answers(
        self, app, db, seed_user, seed_periods,
    ):
        """The admitted case, and it moves nothing -- which is the point.

        A purchase the bank took on the 21st, named by the statement of the
        22nd: the date rule already puts it inside that statement, so the record
        confirms rather than overrides.  The reservation reads
        max(500 - 200 - 0, 0) = 300 with the link and without it.

        Without this the suite could not tell a rule that admits a coherent link
        from one that refuses every link.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON)],
            )
            anchor_id, coverage = _real_statement(txn.account_id)

            assert _entry_aware_amount(
                txn, _basis(txn, coverage=coverage),
            ) == Decimal("300.00")

            txn.entries[0].reconciled_by_id = anchor_id
            db.session.flush()

            assert _entry_aware_amount(
                txn, _basis(txn, coverage=coverage),
            ) == Decimal("300.00")
            db.session.rollback()


class TestTheEntriesRelationshipIsNotASeam:
    """The reduction applies whether or not the caller pre-loaded entries.

    The structural fix for CRIT-01 / F-009 / E-25, and the reason
    :func:`_envelope` above does not re-query with ``selectinload``: symptom #1
    ($160 on the grid against $114.29 on ``/savings`` for one row) was exactly
    this seam, where the rule returned ``effective_amount`` whenever the
    consuming query had not issued the eager load.  The rule now reads
    ``txn.entries`` through the relationship descriptor, which lazy-loads on
    demand, so the value is a function of the DATA and not of the caller's
    query plan.
    """

    def test_an_expired_instance_still_reduces(
        self, app, db, seed_user, seed_periods,
    ):
        """Entries NOT resident on the instance -- the descriptor loads them.

        est=500, uncleared_debit=0, credit=300.
        max(500 - 0 - 300, 0) = 200.  (Was: 5000 - 200 = 4800.)

        The instance is expired first so ``entries`` is genuinely absent from
        its ``__dict__``, which is the state a caller that skipped the eager
        load produces.  Pre-E-25 that state returned 500.00.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", True, None)],
            )
            db.session.expire(txn)
            assert "entries" not in txn.__dict__

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("200.00")

    def test_the_silent_degrade_seam_is_absent_from_source(self):
        """C5-8: the ``'entries' not in __dict__`` short-circuit is not in source.

        The static half of the guard above: the test beside it proves the rule
        BEHAVES correctly today, and this one proves the shape that broke it
        cannot be re-introduced anywhere a balance is folded.  A future
        regression re-adding the eager-load presence check fails this loud.

        **It MOVED here at plan step X-g4b, and its scan WIDENED with the
        move.**  It lived in ``test_balance_resolver.py`` and scanned
        ``balance_at/_cash_engine.py``, ``balance_at/_calculator.py`` and the
        whole ``cash_ledger`` package; the first two were deleted at that step
        and the guard would have raised on their paths.  Its own rationale is
        why the replacement is a WIDENING rather than a subtraction: it enumerates
        a PACKAGE rather than naming files precisely because "a scan keyed on a
        file NAME would have gone quiet at exactly the moment the code it guards
        moved" (finding N-28, the shape where creating a module escapes a
        module-keyed gate).  Every balance producer now lives in ``balance_at``,
        so that package is scanned whole beside ``cash_ledger`` -- and
        ``rglob``, not ``glob``, so a future nested subpackage cannot escape
        either.

        Verified free at the move: neither forbidden pattern appears in either
        package on this date, so the widening costs nothing and closes the
        larger surface.
        """
        services = Path(__file__).resolve().parents[2] / "app" / "services"
        packages = {
            name: sorted((services / name).rglob("*.py"))
            for name in ("cash_ledger", "balance_at")
        }
        # The walk must really have happened AND must have reached the module
        # the forbidden patterns would actually appear in.  Asserting a total
        # instead would be a magic count that breaks when a package legitimately
        # gains or sheds a module.
        for name, sources in packages.items():
            assert sources, f"the {name} package enumerated to nothing"
        assert any(
            source.name == "_amounts.py" for source in packages["cash_ledger"]
        ), (
            "the entry-aware rule's module was not scanned: "
            f"{[s.name for s in packages['cash_ledger']]}"
        )
        assert any(
            source.name == "_cash_fold.py" for source in packages["balance_at"]
        ), (
            "the cash fold's module was not scanned: "
            f"{[s.name for s in packages['balance_at']]}"
        )

        forbidden_patterns = ("not in txn.__dict__", "'entries' not in")
        for sources in packages.values():
            for source_path in sources:
                source = source_path.read_text(encoding="utf-8")
                for pattern in forbidden_patterns:
                    assert pattern not in source, (
                        f"Forbidden seam pattern {pattern!r} found in "
                        f"{source_path}.  E-25 / CRIT-01 / F-009 regression: no "
                        "balance path may consult the instance __dict__ to "
                        "decide whether the entries-aware reduction applies."
                    )


class _FakeRow:  # pylint: disable=too-few-public-methods
    """A non-ORM stand-in carrying only what a valuation rule may read.

    Deliberately missing ``entries`` and ``status_id``: the ordering of
    :func:`_entry_aware_amount`'s two guards is load-bearing, and this shape is
    what proves it (see
    :meth:`TestTheLiveOverride.test_no_entries_short_circuits_before_the_status_read`).
    """

    def __init__(self, txn_id=None, effective_amount="77.00"):
        self.id = txn_id
        # What the row OWNS, which since plan step X-au-c2 is what the
        # valuation reads: ``amount_source_id IS NULL`` says the figure is the
        # row's own, and the four attributes below are every column the OWN arm
        # and the contribution gate touch.  There is no ``effective_amount``
        # property any more -- the resolved figure arrives as an argument.
        self.estimated_amount = Decimal(effective_amount)
        self.amount_source_id = None
        self.actual_amount = None
        self.is_deleted = False
        self.status = None


class TestTheLiveOverride:
    """A live-derived amount replaces the stored figure, on both legs.

    The read-time seam (Workstream B): a projected salary paycheck reflects the
    CURRENT salary profile and a recurring loan-payment shadow the loan's
    current P&I + escrow, rather than a stored amount a later profile,
    calibration or code change may have invalidated without firing a
    regeneration.

    Moved from ``TestIncomeOverridesSeam`` (X-c2c2a) and from
    ``test_balance_resolver.py`` (the expense leg's precedence and the guard
    ordering, which arrived there at plan step X-c2c1).  The fourth
    ``TestIncomeOverridesSeam`` test did NOT move: it pins that the override is
    honoured in the POST-ANCHOR period specifically, which is a ``_calculator``
    branch rather than a valuation rule.

    **Both legs take a whole :class:`ProjectedBasis` since plan step S1-c.**  The
    argument was a bare, optional ``amount_overrides`` map; the expense leg now
    also needs the day through which the account's purchases are reconciled, and
    two optional arguments would be two ways to hand a reduction half a basis --
    which would silently value every purchase as outstanding and hold whole
    budgets back.  One required record makes that shape unwritable, and the
    income leg takes it too although it reads only one field, so there is no
    shape in which one leg is valued on a basis the other was not.
    """

    def test_an_override_replaces_the_income_amount(self):
        """An income row whose id is in the map contributes the override.

        Override $2473.38 wins over the stored $2000.00.  (Was asserted as
        anchor $100.00 + override = $2573.38.)
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")
        basis = _basis(row, overrides={101: Decimal("2473.38")})

        assert income_amount(row, basis) == Decimal("2473.38")

    def test_an_empty_map_uses_the_stored_amount(self):
        """An empty override map is byte-identical pre-seam behaviour.

        ``{}`` rather than ``None``: the basis is required and every producer
        builds its map (``live_amount_overrides`` returns ``{}`` when neither
        seam has a candidate, which is the common case), so an absent map is
        not a state a caller can be in.
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")
        basis = _basis(row)

        assert income_amount(row, basis) == Decimal("2000.00")

    def test_an_unlisted_id_falls_back_to_the_stored_amount(self):
        """A non-empty map overrides only the ids it lists.

        The map keys id 999; row 101 keeps its stored $2000.00.
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")
        basis = _basis(row, overrides={999: Decimal("5.00")})

        assert income_amount(row, basis) == Decimal("2000.00")

    def test_an_override_wins_over_the_entry_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """On the EXPENSE leg an override short-circuits the reduction.

        A live-derived amount is what the row is worth now and carries no
        entries to reduce, so the override is returned verbatim rather than the
        50.00 the three-bucket reservation would give (est=500, two debits of
        200.00 and 250.00 both taken by the bank on 01-21, inside a balance read
        for 01-22: max(500 - 450 - 0, 0) = 50.00).

        Both calls are made on the SAME basis except for the map, so the figure
        that changes is attributable to the override alone.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, _POSTED_ON), ("250.00", False, _POSTED_ON)],
            )
            no_override = _basis(txn)
            overridden = _basis(
                txn, overrides={txn.id: Decimal("123.45")},
            )

            assert _expense_amount(txn, no_override) == Decimal("50.00")
            assert _expense_amount(txn, overridden) == Decimal("123.45")

    def test_no_entries_short_circuits_before_the_status_read(self):
        """The guard ORDER holds: no entries returns before ``is_projected``.

        ``_entry_aware_amount`` checks ``not entries`` FIRST and that is
        load-bearing rather than stylistic -- ``is_projected`` reads
        ``status_id`` through ``ref_cache``, so a non-ORM row with neither
        attribute must still be valued rather than raising.

        Mutation-verified: swapping the two guards fails this with
        ``AttributeError: '_FakeRow' object has no attribute 'status_id'`` --
        ``is_projected`` reads ``status_id`` BEFORE it consults ``ref_cache``,
        so that attribute, not the cache, is what the ordering protects.
        """
        row = _FakeRow(txn_id=1)
        assert _entry_aware_amount(row, _basis(row)) == Decimal("77.00")
