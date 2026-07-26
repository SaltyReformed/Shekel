"""X-c1: the cash PERIOD VIEW -- one row set, two clocks, one named remainder.

Plan step X-c1 (``docs/audits/balance_architecture/README.md``, ruling R-K).
Grades ``app.services.balance_at._cash_fold.cash_period_view`` -- the producer
plan step X-c2 points the grid's balance row, its subtotal rows and its new
Reconciliation row at.  The view is ADDITIVE here: no production surface reads
it yet, so nothing in this file can move a shipped balance.

**The identity is the point, and it is asserted against an INDEPENDENT fold.**
For every period::

    balance(p.end) - balance(p.start - 1 day) == net[p] + reconciliation[p]

The left side is sampled from ``fold_cash_balances`` -- the X-b producer, graded
on its own hand-computed oracle -- and the right side is the view's grouping of
the same rows.  Neither is derived from the other: the view computes its
remainder from the row set (what MOVED in the column minus what was BUDGETED to
it, plus the assertions), never as ``balance_delta - net``, precisely so this
assertion can fail (plan Section 7.2 -- a residual proving itself is the
forbidden shape).

**Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it**, per component: the basis change, TIMING (a row settled outside its
own column), the TRUE-UP (a balance assertion), the CLAMP (ruling R-G moving an
overdue plan forward), and the OPENING that must book nothing (ruling R-I).
Where a figure exists in the shipping producer it is asserted too, so the basis
change is shown as a measured difference rather than claimed.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger
from app.services.balance_at._cash_fold import (
    CashPeriodFigures,
    cash_period_view,
    fold_cash_balances,
)
from tests._test_helpers import (
    add_entry,
    add_txn,
    append_balance_assertion,
    create_envelope_txn,
    create_settled_cash_transaction,
    restamp_opening_assertion,
)
from tests.test_services.test_cash_fold import _instant

_ONE_DAY = timedelta(days=1)
# An as-of before every fixture date below, so ruling R-G's clamp is a no-op
# except in the tests that exist for it.  The tiers are graded separately on
# purpose: a test that mixed them could pass with either one wrong.
_EARLY_AS_OF = date(2026, 1, 20)


def _view(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return the period view keyed by period id."""
    return cash_period_view(account, scenario.id, as_of, list(periods))


def _identity_holds(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return ``[(period, balance_delta, net + reconciliation)]`` per period.

    The balance delta is sampled from the INDEPENDENT fold at the boundary
    dates; the right-hand side is the view's own grouping.  The caller asserts
    the pairs are equal -- and reads the same pairs to check the run was not
    vacuous.
    """
    figures = _view(account, scenario, periods, as_of=as_of)
    boundaries = [period.start_date - _ONE_DAY for period in periods]
    boundaries += [period.end_date for period in periods]
    folded = fold_cash_balances(account, scenario.id, as_of, boundaries)
    return [
        (
            period,
            folded[period.end_date] - folded[period.start_date - _ONE_DAY],
            figures[period.id].net + figures[period.id].reconciliation,
        )
        for period in periods
    ]


class TestTheSubtotalsCountEveryAttributedRow:
    """Ruling R-K's basis change: budget-vs-actual, not unpaid-only.

    Today's subtotal counts only rows that are still UNPAID, so every past
    column reads ``$0.00`` income and ``$0.00`` expenses while thousands moved
    through it (finding N-41, measured on the real Checking account).  These
    pin the new basis AND the old answer beside it, so the change is a measured
    difference rather than a claim.
    """

    def test_a_settled_row_counts_where_the_old_subtotal_read_zero(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled income and a settled expense, plus one still-projected row.

        Period 2 (2026-01-30..02-12) carries a ``$500.00`` income settled 02-03,
        a ``$200.00`` expense settled 02-05 and a ``$75.00`` still-projected
        expense.  Opening assertion ``$1,000.00`` on 2026-01-01.

        Hand-computed, new basis: income ``$500.00``; expenses
        ``200.00 + 75.00 = $275.00``; net ``$225.00``.  Every row settled inside
        its own column and nobody re-anchored, so the remainder is ``$0.00`` and
        the balance is ``1000 + 500 - 200 - 75 = $1,225.00``.

        The shipping producer answers ``$0.00`` income and ``$75.00`` expenses
        for the same column -- the two settled rows are invisible to it -- which
        is the defect R-K's basis change exists to end.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("500.00"),
            is_income=True, paid_at=_instant(2026, 2, 3), name="paycheck",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("200.00"),
            paid_at=_instant(2026, 2, 5), name="rent",
        )
        add_txn(db.session, seed_user, seed_periods[2], "bill", "75.00")
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.income == Decimal("500.00")
        assert figures.expense == Decimal("275.00")
        assert figures.net == Decimal("225.00")
        assert figures.reconciliation == Decimal("0.00")
        assert figures.balance == Decimal("1225.00")

        old = cash_ledger.period_subtotals(
            account, scenario.id, list(seed_periods),
        )[seed_periods[2].id]
        assert old.income == Decimal("0.00")
        assert old.expense == Decimal("75.00")

    def test_a_settled_envelope_counts_its_confirmed_cash_leg(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Credit-card purchases never left checking, so they are not expense.

        A ``$200.00`` envelope settled 2026-02-05 in period 2, whose entries are
        a ``$120.00`` DEBIT purchase and an ``$80.00`` CREDIT purchase.

        Hand-computed: the confirmed cash leg is
        ``effective_amount - Sigma(credit) = 200.00 - 80.00 = $120.00``, because
        the credit purchase leaves through its own CC Payback sibling.  So the
        column's expense row reads ``$120.00``, not the envelope's ``$200.00``
        actual, and the balance reads ``1000 - 120 = $880.00`` -- the subtotal
        and the balance priced the row through ONE rule.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("200.00"),
            paid_at=_instant(2026, 2, 5), name="Groceries",
        )
        for amount, is_credit in (
            (Decimal("120.00"), False), (Decimal("80.00"), True),
        ):
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=amount,
                description="purchase",
                entry_date=date(2026, 2, 4),
                is_credit=is_credit,
            ))
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.expense == Decimal("120.00")
        assert figures.net == Decimal("-120.00")
        assert figures.reconciliation == Decimal("0.00")
        assert figures.balance == Decimal("880.00")

    def test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The one shape where the leg's SIGN and the row's TYPE disagree.

        A Groceries envelope in period 2 whose only purchase was an ``$80.00``
        credit-card one, settled at that ``$80.00``, and whose ``actual_amount``
        the user then corrects down to ``$50.00`` (the transaction edit route
        honours a manual actual and does not re-derive it from the entries --
        only an ENTRY mutation does that).

        Hand-computed: the confirmed cash leg is
        ``-(50.00 - 80.00) = +$30.00`` -- a settled EXPENSE that nets money INTO
        checking.  It belongs on the expense row as ``-$30.00``, not on the
        income row as ``+$30.00``: an expense that came back is not income.  The
        net (``+$30.00``) and the balance are the same either way, which is
        exactly why this shape has to be pinned -- it is the only one that can
        tell a TYPE classification from a sign test, and without it the fact's
        ``is_income`` would be carrying an untested claim.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("80.00"),
            actual_amount=Decimal("50.00"),
            paid_at=_instant(2026, 2, 5), name="Groceries",
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("80.00"),
            description="credit purchase",
            entry_date=date(2026, 2, 4),
            is_credit=True,
        ))
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("-30.00")
        assert figures.net == Decimal("30.00")
        assert figures.reconciliation == Decimal("0.00")
        assert figures.balance == Decimal("1030.00")

    def test_a_projected_envelope_counts_its_entries_aware_reservation(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A still-projected envelope is worth what it still holds back.

        A ``$200.00`` Groceries envelope due 2026-02-10 in period 2, carrying one
        CLEARED ``$120.00`` debit purchase made 01-31, read at
        ``as_of = 2026-02-01``.

        Hand-computed: the reservation is
        ``max(200.00 - 120.00 - 0.00, 0.00) = $80.00`` -- the cleared purchase is
        already inside the anchor, so only the unspent budget is still held back.
        The expense row reads ``$80.00`` (never the ``$200.00`` estimate) and the
        balance ``1000 - 80 = $920.00``.  Its due date is past ``as_of + 1``, so
        ruling R-G's clamp is a no-op and the remainder stays ``$0.00``.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[2], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 2, 10)
        db.session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("120.00"),
            description="purchase",
            entry_date=date(2026, 1, 31),
            is_credit=False,
            is_cleared=True,
        ))
        db.session.commit()

        figures = _view(
            account, scenario, seed_periods, as_of=date(2026, 2, 1),
        )[seed_periods[2].id]
        assert figures.expense == Decimal("80.00")
        assert figures.reconciliation == Decimal("0.00")
        assert figures.balance == Decimal("920.00")


class TestTheRemainderHoldsWhatTheSubtotalsCannot:
    """The three components of the Reconciliation row, one test each."""

    def test_timing_a_row_that_settled_outside_its_own_column(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Budgeted to period 3, paid in period 5: two remainders that cancel.

        A ``$300.00`` expense attributed to period 3 (2026-02-13..02-26) but
        settled 2026-03-20, inside period 5 (2026-03-13..03-26).  This is 19 of
        the real Checking account's 130 settled rows.

        Hand-computed.  Period 3: expenses ``$300.00`` (it is that column's
        budget), nothing moved, so the remainder is ``0 - (-300) = +$300.00`` and
        the balance does not change.  Period 5: no row is budgeted there, but
        ``-$300.00`` moved, so the remainder is ``-$300.00`` and the balance
        drops by it.  The two net to ``$0.00`` across history -- the row is
        counted once as budget and once as cash, never twice.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[3], Decimal("300.00"),
            paid_at=_instant(2026, 3, 20), name="late rent",
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)
        budgeted = figures[seed_periods[3].id]
        assert budgeted.expense == Decimal("300.00")
        assert budgeted.net == Decimal("-300.00")
        assert budgeted.reconciliation == Decimal("300.00")
        assert budgeted.balance == Decimal("1000.00")

        moved = figures[seed_periods[5].id]
        assert moved.expense == Decimal("0.00")
        assert moved.net == Decimal("0.00")
        assert moved.reconciliation == Decimal("-300.00")
        assert moved.balance == Decimal("700.00")

        assert (
            budgeted.reconciliation + moved.reconciliation == Decimal("0.00")
        )

    def test_a_true_up_is_the_remainder_no_row_can_explain(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The user asserts a new balance mid-period; no transaction moved.

        Opening ``$1,000.00`` on 2026-01-01, then a true-up on 2026-03-01
        (inside period 4, 2026-02-27..03-12) asserting ``$1,500.00`` with no rows
        in between.

        Hand-computed: the correction is ``1500.00 - 1000.00 = +$500.00``.  Both
        subtotal rows are ``$0.00`` -- there is no transaction to count -- so the
        remainder is the whole ``+$500.00`` and it is exactly the balance change.
        This is 51 corrections and ``-$2,906.31`` net on the real Checking
        account, money no transaction row will ever explain.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("1500.00"),
            _instant(2026, 3, 1),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[4].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("0.00")
        assert figures.net == Decimal("0.00")
        assert figures.reconciliation == Decimal("500.00")
        assert figures.balance == Decimal("1500.00")

    def test_the_opening_assertion_books_nothing_in_its_own_period(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling R-I puts the OPENING in the fold's seed, not in a column.

        Opening asserted 2026-01-05 at ``$1,000.00``, inside period 0
        (2026-01-02..01-15), with a ``$400.00`` expense already settled 01-03 --
        two days before it, in the same column.

        Hand-computed.  The fold back-projects: the records at or before the
        opening sum to ``-$400.00``, so the seed is
        ``1000.00 - (-400.00) = $1,400.00`` and the account read ``$1,400.00``
        before the spend.  Period 0 therefore changes by
        ``1000.00 - 1400.00 = -$400.00``, which its own expense row explains in
        full -- remainder ``$0.00``.  Counting the opening's ``+$1,400.00``
        correction here would claim a jump the balance never took.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 5))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("400.00"),
            paid_at=_instant(2026, 1, 3), name="pre-opening spend",
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[0].id]
        assert figures.expense == Decimal("400.00")
        assert figures.net == Decimal("-400.00")
        assert figures.reconciliation == Decimal("0.00")
        assert figures.balance == Decimal("1000.00")

        folded = fold_cash_balances(
            account, scenario.id, _EARLY_AS_OF,
            [seed_periods[0].start_date - _ONE_DAY],
        )
        assert folded[seed_periods[0].start_date - _ONE_DAY] == Decimal("1400.00")

    def test_the_clamp_moves_an_overdue_plan_out_of_its_own_column(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling R-G: a plan cannot have already happened.

        A ``$50.00`` bill budgeted to period 5 (2026-03-13..03-26) due
        2026-03-20, read at ``as_of = 2026-04-02`` -- still unpaid, a fortnight
        overdue.

        Hand-computed: its effective day is ``max(03-20, 04-03) = 2026-04-03``,
        inside period 6 (2026-03-27..04-09).  So period 5 still BUDGETS the
        ``$50.00`` expense while nothing moves there (remainder ``+$50.00``), and
        period 6 carries the ``-$50.00`` the balance actually takes (remainder
        ``-$50.00``).  Under the rejected alternative the bill would land on its
        stale due date and the next re-anchor would absorb it -- deleting it from
        the projection entirely.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = date(2026, 4, 2)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[5], "overdue bill", "50.00",
            due_date=date(2026, 3, 20),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods, as_of=as_of)
        budgeted = figures[seed_periods[5].id]
        assert budgeted.expense == Decimal("50.00")
        assert budgeted.reconciliation == Decimal("50.00")
        assert budgeted.balance == Decimal("1000.00")

        landing = figures[seed_periods[6].id]
        assert landing.expense == Decimal("0.00")
        assert landing.reconciliation == Decimal("-50.00")
        assert landing.balance == Decimal("950.00")


class TestTheIdentityHoldsOnEveryPeriod:
    """``balance(p.end) - balance(p.start - 1d) == net + reconciliation``.

    Asserted with both sides computed independently -- the left from the X-b
    fold, the right from the view's grouping -- over EVERY period of a shape
    carrying all four components at once.
    """

    def _rich_shape(self, seed_user, seed_periods):
        """Build a shape triggering every component the remainder can hold.

        A pre-opening record, an in-column settle, an out-of-column settle, a
        mid-history true-up, an entries-carrying envelope, an overdue plan the
        clamp moves forward, and ordinary future rows.

        Returns:
            The ``as_of`` the caller reads at.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 2)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 5))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("400.00"),
            paid_at=_instant(2026, 1, 3), name="pre-opening spend",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("1800.00"),
            is_income=True, paid_at=_instant(2026, 2, 3), name="paycheck",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[3], Decimal("300.00"),
            paid_at=_instant(2026, 3, 20), name="late rent",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("1500.00"),
            _instant(2026, 3, 1),
        )
        envelope = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        envelope.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, envelope, Decimal("60.00"), date(2026, 4, 1),
        )
        add_txn(
            db.session, seed_user, seed_periods[5], "overdue bill", "50.00",
            due_date=date(2026, 3, 20),
        )
        add_txn(
            db.session, seed_user, seed_periods[8], "future paycheck", "1500.00",
            is_income=True, due_date=date(2026, 4, 24),
        )
        db.session.commit()
        return as_of

    def test_every_period_reconciles_against_the_independent_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """All ten periods, not a sample of them.

        The shape carries a pre-opening record, a settle inside its own column,
        a settle two columns late, a true-up, an entries-aware envelope and a
        clamped overdue bill, so no single component can carry the whole run.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = self._rich_shape(seed_user, seed_periods)

        rows = _identity_holds(account, scenario, seed_periods, as_of=as_of)
        assert len(rows) == 10  # the loop is not vacuous
        for period, balance_delta, explained in rows:
            assert balance_delta == explained, (
                f"period {period.period_index} "
                f"({period.start_date}..{period.end_date}) "
                f"moved {balance_delta} but its rows explain {explained}"
            )

    def test_the_shape_actually_exercises_the_remainder(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Non-vacuity: the identity above is not ten copies of ``0 == 0``.

        Four of the ten periods must carry a non-zero remainder, each
        hand-computed from a different component.  Without this a view that
        returned zeros everywhere would satisfy the identity perfectly.

        * period 3 -- ``+$300.00``: it BUDGETS the late rent, which moved
          elsewhere.
        * period 4 -- ``-$1,300.00``: the true-up, which no transaction row can
          explain.  The records had walked the account to
          ``1400.00 - 400.00 + 1800.00 = $2,800.00`` by 2026-03-01, and the user
          asserted ``$1,500.00``, so the correction is ``1500 - 2800``.  Note it
          is NOT ``1500 - 1000``: an assertion's correction is measured against
          the RECORDS, which is the whole reason this row exists.
        * period 5 -- ``-$250.00``: the late rent MOVED here (``-$300.00``, and
          it is budgeted two columns back) while the ``$50.00`` overdue bill is
          budgeted here and moves elsewhere (``+$50.00``).
        * period 6 -- ``-$50.00``: the overdue bill LANDS here on ruling R-G's
          clamp (2026-04-03), where nothing budgets it.  The Groceries envelope
          beside it is budgeted AND lands in this column, so it contributes
          nothing to the remainder.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = self._rich_shape(seed_user, seed_periods)

        figures = _view(account, scenario, seed_periods, as_of=as_of)
        remainders = {
            period.period_index: figures[period.id].reconciliation
            for period in seed_periods
        }
        assert remainders[3] == Decimal("300.00")
        assert remainders[4] == Decimal("-1300.00")
        assert remainders[5] == Decimal("-250.00")
        assert remainders[6] == Decimal("-50.00")
        assert len({figures[p.id].balance for p in seed_periods}) > 1

    def test_the_identity_holds_over_a_non_contiguous_window(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A window is a window: a day between two reported periods is nobody's.

        Reports periods 0 and 2 only, with a ``$250.00`` expense budgeted to
        period 0 but settled 2026-01-20 -- inside period 1, which is NOT
        reported.

        Hand-computed.  Period 0 budgets the ``$250.00`` (net ``-$250.00``) while
        nothing moves inside its span, so its remainder is ``+$250.00`` and its
        balance is unchanged at ``$1,000.00``.  Period 2 budgets nothing and
        nothing moves inside ITS span either -- the settle happened before
        02-12's opening boundary -- so every figure is ``$0.00`` against a
        ``$750.00`` balance.  A nearest-period fallback would pull the 01-20
        settle into period 0 and break its identity by the row's whole amount.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("250.00"),
            paid_at=_instant(2026, 1, 20), name="settled next period",
        )
        db.session.commit()

        window = [seed_periods[0], seed_periods[2]]
        figures = _view(account, scenario, window)
        assert figures[seed_periods[0].id].net == Decimal("-250.00")
        assert figures[seed_periods[0].id].reconciliation == Decimal("250.00")
        assert figures[seed_periods[0].id].balance == Decimal("1000.00")
        assert figures[seed_periods[2].id].net == Decimal("0.00")
        assert figures[seed_periods[2].id].reconciliation == Decimal("0.00")
        assert figures[seed_periods[2].id].balance == Decimal("750.00")

        rows = _identity_holds(account, scenario, window)
        assert len(rows) == 2  # the loop is not vacuous
        for period, balance_delta, explained in rows:
            assert balance_delta == explained, f"period {period.period_index}"

    def test_an_empty_period_reports_zeros_against_its_folded_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every requested period is present, TOTAL over the window.

        With no rows at all, all ten periods report ``$0.00`` on every subtotal
        and remainder while carrying the opening balance -- a missing key would
        make a consumer branch on absence, which is the partiality this whole
        arc deletes.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        db.session.commit()

        figures = _view(account, scenario, seed_periods)
        assert len(figures) == 10
        assert list(figures) == [period.id for period in seed_periods]
        empty = CashPeriodFigures(
            balance=Decimal("1000.00"),
            income=Decimal("0.00"),
            expense=Decimal("0.00"),
            net=Decimal("0.00"),
            reconciliation=Decimal("0.00"),
        )
        for period in seed_periods:
            assert figures[period.id] == empty


    def test_money_that_moved_here_but_is_budgeted_off_window_is_all_remainder(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The user's sharpest question: the balance dropped, both rows are zero.

        Reports period 2 ONLY, with a ``$180.00`` expense budgeted to period 0
        -- outside the window -- but settled 2026-02-05, inside period 2.

        Hand-computed: nothing is ATTRIBUTED to period 2, so both subtotal rows
        read ``$0.00``, while ``-$180.00`` moved through it.  The whole change is
        the remainder (``-$180.00``) against a ``$820.00`` balance.  This is the
        mirror of the case above (budgeted in, moved out) and the one that would
        otherwise read as a balance dropping for no reason.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("180.00"),
            paid_at=_instant(2026, 2, 5), name="budgeted two columns back",
        )
        db.session.commit()

        window = [seed_periods[2]]
        figures = _view(account, scenario, window)[seed_periods[2].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("0.00")
        assert figures.net == Decimal("0.00")
        assert figures.reconciliation == Decimal("-180.00")
        assert figures.balance == Decimal("820.00")

        rows = _identity_holds(account, scenario, window)
        assert len(rows) == 1  # the loop is not vacuous
        assert rows[0][1] == rows[0][2] == Decimal("-180.00")
