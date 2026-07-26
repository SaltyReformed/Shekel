"""X-b: the cash FOLD, graded on a hand-computed oracle.

Plan step X-b (``docs/audits/balance_architecture/README.md``).  Grades
``app.services.balance_at._cash_fold.fold_cash_balances`` -- the producer plan
step X-c will point all three cash seam entries at.  The fold is ADDITIVE here:
no production surface reads it yet, so nothing in this file can move a shipped
balance.

**Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.**  None is taken from a shipping producer, and that is not a style
preference: the shipping projection is WRONG about exactly the cases this file
exists for (findings cash D1 / D3, and ruling R-G's absorbed past-due bill), so
grading the fold against it would prove the defect rather than the fix (plan
Section 7.2, finding N-7).  The every-day parallel run against those producers
is a separate file (``test_cash_fold_parallel.py``), where DIVERGENCE is the
expected result and each one is explained.

Three rulings are graded here, and each has a control that a wrong
implementation fails rather than a comment asserting it:

* **R-I** -- before the first assertion the fold BACK-PROJECTS over the records
  (:class:`TestTheOpeningMovesIntoTheSeed`), including both real production
  shapes with the ruling's own figures.  Its
  :meth:`~TestTheOpeningMovesIntoTheSeed.test_at_and_after_the_opening_it_equals_the_zero_seeded_walk`
  is the pin on the one re-derivation the fold performs.
* **R-G** -- a still-Projected row whose date has passed is CLAMPED FORWARD to
  ``as_of + 1``, never absorbed (:class:`TestThePlannedTier`), with the ruling's
  worked Checking figures.
* the **instant partition** -- an assertion covers exactly the settles that
  PRECEDED it, to the second (:class:`TestSettledMoneyRidesOnTheAssertionItFollowed`),
  which is finding cash D1 and the defect the developer hit on a real Money
  Market transfer.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services.balance_at._cash_fold import fold_cash_balances
from app.services.balance_at._fold import sample_cumulative
from app.services.cash_ledger import dated_deltas, walk_cash_ledger
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
    create_envelope_txn,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    restamp_opening_assertion,
)

# An as-of far past every valuation date these ACTUAL-tier tests read, so the
# PLANNED tier (which clamps to ``as_of + 1``) cannot reach them.  The tiers are
# graded separately on purpose: a test that mixed them could pass with either
# one wrong.
_LATE_AS_OF = date(2026, 12, 31)


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return an aware-UTC instant, for pinning assertion / settle moments."""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone.utc,
    )


def _fold(account, scenario, days, as_of=_LATE_AS_OF):
    """Fold *account* at each of *days*, returning ``{date: Decimal}``."""
    return fold_cash_balances(account, scenario.id, as_of, list(days))


def _opened_at(account, at):
    """Pin the account's OPENING assertion instant (shared builder)."""
    return restamp_opening_assertion(db.session, account, at)


class TestTheOpeningMovesIntoTheSeed:
    """Ruling R-I: the first assertion back-projects over its own records.

    A cash assertion is a RESET, not an origination.  The walk seeds at zero, so
    the prefix BEFORE an account's first assertion is that assertion's preceding
    records summed from nothing -- a balance the account never had.  The fold
    moves the opening's correction out of the step list and into the SEED, which
    is the same thing as saying the FIRST assertion books no correction while
    every later one keeps its reset.
    """

    def test_a_pre_opening_record_back_projects_over_it(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The leaf's -$500.00 partial sum reads $1,500.00 through the fold.

        The exact fixture ``test_cash_walk.TestPreOpeningSources`` pins on the
        LEAF: an opening asserting $1,000.00 on 2026-02-01, with a $500.00
        expense already attributed 2026-01-15.

        Hand-computed.  The records at or before the opening sum to -$500.00, so
        the seed is ``1000.00 - (-500.00) = 1500.00``: the user asserted $1,000
        having already spent $500, so before that spend the account held $1,500.
        Reads 1500.00 before 01-15, 1000.00 from 01-15 on, and 1000.00 at the
        opening and after.

        The rejected alternatives, on this shape: ``0.00`` (claims the account
        did not exist -- but its first anchor row is a TRACKING start, on real
        data a backfill created weeks after the account held money), flat-carry
        (would answer 1000.00 on 01-14, contradicting the recorded -$500.00),
        and the zero-seeded prefix (-$500.00, the leaf's own partial sum).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 2, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("500.00"),
            paid_at=_instant(2026, 1, 15), name="pre-opening",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 1, 14), date(2026, 1, 15), date(2026, 1, 31),
            date(2026, 2, 1), date(2026, 3, 1),
        ])
        assert folded[date(2026, 1, 14)] == Decimal("1500.00")
        assert folded[date(2026, 1, 15)] == Decimal("1000.00")
        assert folded[date(2026, 1, 31)] == Decimal("1000.00")
        assert folded[date(2026, 2, 1)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("1000.00")

    def test_the_real_money_market_shape(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-I's own worked figures, on the production Money Market shape.

        Assertion 2026-05-01 $4,879.26, with four records already inside it:
        +$500.00 on 04-06, +$500.00 on 04-09, +$500.00 on 04-11 and -$1,500.00
        on 04-23.

        Hand-computed: those records sum to $0.00, so the seed is
        ``4879.26 - 0 = 4879.26`` and the fold reads $4,879.26 before 04-06,
        $5,379.26 on 04-06, $5,879.26 on 04-10, $6,379.26 on 04-22 (the
        withdrawal not yet taken) and $4,879.26 from 04-23 -- the ruling's
        figures to the cent.
        """
        scenario = seed_user["scenario"]
        account = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("4879.26"),
            anchor_period_id=seed_periods[8].id,
        )
        _opened_at(account, _instant(2026, 5, 1))
        for amount, day, is_income in (
            (Decimal("500.00"), 6, True),
            (Decimal("500.00"), 9, True),
            (Decimal("500.00"), 11, True),
            (Decimal("1500.00"), 23, False),
        ):
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[7], amount,
                account=account, is_income=is_income,
                paid_at=_instant(2026, 4, day), name=f"apr-{day}",
            )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 10),
            date(2026, 4, 22), date(2026, 4, 23), date(2026, 5, 1),
        ])
        assert folded[date(2026, 4, 5)] == Decimal("4879.26")
        assert folded[date(2026, 4, 6)] == Decimal("5379.26")
        assert folded[date(2026, 4, 10)] == Decimal("5879.26")
        assert folded[date(2026, 4, 22)] == Decimal("6379.26")
        assert folded[date(2026, 4, 23)] == Decimal("4879.26")
        assert folded[date(2026, 5, 1)] == Decimal("4879.26")

    def test_the_real_savings_shape(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-I's other worked shape: one record before the assertion.

        Assertion 2026-04-06 $5,363.56 with a single +$500.00 record on 03-27.

        Hand-computed: the seed is ``5363.56 - 500.00 = 4863.56``, so the fold
        reads $4,863.56 on 03-26 and $5,363.56 from 03-27 -- the ruling's
        figures.  A flat-carry would answer $5,363.56 on 03-26, contradicting
        the recorded deposit.
        """
        scenario = seed_user["scenario"]
        account = create_savings_account(
            seed_user, db.session, "Fidelity Savings", Decimal("5363.56"),
            anchor_period_id=seed_periods[7].id,
        )
        _opened_at(account, _instant(2026, 4, 6))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("500.00"),
            account=account, is_income=True,
            paid_at=_instant(2026, 3, 27), name="deposit",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 3, 26), date(2026, 3, 27), date(2026, 4, 6),
        ])
        assert folded[date(2026, 3, 26)] == Decimal("4863.56")
        assert folded[date(2026, 3, 27)] == Decimal("5363.56")
        assert folded[date(2026, 4, 6)] == Decimal("5363.56")

    def test_at_and_after_the_opening_it_equals_the_zero_seeded_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The pin on the fold's ONE re-derivation.

        The fold re-derives the opening's emitted correction
        (``anchor_balance - balance_before``, keyed on the assertion's UTC day)
        in order to cancel it, because ``dated_deltas`` returns bare tuples that
        carry no identity.  That is a second statement of what the leaf emits,
        so it is PINNED rather than trusted: at and after the opening the fold
        must be byte-identical to a zero-seeded sample of the very same steps,
        and it is not equal before it.  If the leaf's emission and the fold's
        compensator ever stop agreeing, the equality below breaks.

        Stream: opening $1,000.00 (2026-02-01) with a -$500.00 record already
        inside it (2026-01-15), a true-up to $2,000.00 (2026-03-01), and a
        -$250.00 record after that (2026-04-01).  Hand-computed, the fold reads
        $1,500.00 on 01-14, $1,000.00 from 01-15, $2,000.00 from 03-01 and
        $1,750.00 from 04-01; the zero-seeded walk agrees on every one of those
        EXCEPT the first, where it answers -$500.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 2, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("500.00"),
            paid_at=_instant(2026, 1, 15), name="pre-opening",
        )
        append_balance_assertion(
            db.session, account, seed_periods[3], Decimal("2000.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("250.00"),
            paid_at=_instant(2026, 4, 1), name="post",
        )
        db.session.commit()

        at_and_after = [
            date(2026, 2, 1) + timedelta(days=offset) for offset in range(75)
        ]
        # The equality below is a claim about the ACTUAL tier alone, so the
        # comparison window must end before the PLANNED tier's clamp floor could
        # place anything inside it.  Structural, not incidental: this holds even
        # if a future fixture change adds a projected row.
        assert at_and_after[-1] < _LATE_AS_OF + timedelta(days=1)
        zero_seeded = sample_cumulative(
            Decimal("0.00"),
            sorted(
                dated_deltas(walk_cash_ledger(account.id, scenario.id)),
                key=lambda step: step[0],
            ),
            at_and_after,
        )
        folded = _fold(account, scenario, at_and_after)
        assert folded == zero_seeded

        # ...and the hand-computed values the equality is standing on, so a
        # jointly-broken pair cannot pass by agreeing with each other.
        assert folded[date(2026, 2, 1)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2000.00")
        assert folded[date(2026, 4, 1)] == Decimal("1750.00")

        # Before the opening is exactly where R-I changed the answer, and the
        # two dates before it differ for two different reasons -- both asserted,
        # because "the fold differs there" is the whole ruling.
        #
        #   01-14, before EVERY step: the zero-seeded walk reads its empty
        #     prefix, $0.00; the fold reads its seed, $1,500.00.
        #   01-20, after the record but before the opening: the zero-seeded
        #     walk reads the un-absorbed partial sum -$500.00 (the balance the
        #     account never had, pinned on the leaf by
        #     ``TestPreOpeningSources``); the fold reads $1,000.00.
        earlier, between = date(2026, 1, 14), date(2026, 1, 20)
        folded_before = _fold(account, scenario, [earlier, between])
        assert folded_before[earlier] == Decimal("1500.00")
        assert folded_before[between] == Decimal("1000.00")
        zero_seeded_before = sample_cumulative(
            Decimal("0.00"),
            sorted(
                dated_deltas(walk_cash_ledger(account.id, scenario.id)),
                key=lambda step: step[0],
            ),
            [earlier, between],
        )
        assert zero_seeded_before[earlier] == Decimal("0.00")
        assert zero_seeded_before[between] == Decimal("-500.00")


class TestSettledMoneyRidesOnTheAssertionItFollowed:
    """Finding cash D1: an assertion covers the settles that PRECEDED it.

    The defect the developer hit on 2026-07-25: a $2,000.00 transfer out of the
    Money Market, marked Paid a week after that account was last anchored,
    reduced no balance on any screen.  The shipping projection excludes every
    settled row (the anchor is assumed to reflect it) and the anchor predates it,
    so it is counted by NO producer.
    """

    def test_a_transfer_settled_after_the_assertion_reduces_the_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The reported bug, reproduced: $5,644.27 - $2,000.00 = $3,644.27.

        The real Money Market shape: opening $1,000.00 (2026-01-01), the user
        asserts $5,644.27 (2026-03-01 12:20:20), and a $2,000.00 transfer to
        Checking settles a month later (2026-04-01 19:47:44).

        Hand-computed: the transfer is attributed AFTER the assertion, so it
        rides on top of it and the fold reads $5,644.27 through 03-31 and
        $3,644.27 from 04-01.  $3,644.27 is also what the posted double-entry
        ledger holds for the real account, while every projected balance on
        screen answers $5,644.27 -- the divergence is the finding.

        The transfer reaches the fold as its SHADOW rows (Transfer Invariant 5),
        exactly as it reaches the shipping projection; neither queries
        ``Transfer``.
        """
        scenario = seed_user["scenario"]
        money_market = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("1000.00"),
            anchor_period_id=seed_periods[0].id,
        )
        _opened_at(money_market, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, money_market, seed_periods[4], Decimal("5644.27"),
            _instant(2026, 3, 1, 12, 20, 20),
        )
        create_settled_transfer(
            seed_user, db.session, money_market, seed_user["account"],
            seed_periods[6], amount=Decimal("2000.00"),
            paid_at=_instant(2026, 4, 1, 19, 47, 44),
        )
        db.session.commit()

        folded = _fold(money_market, scenario, [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 3, 31),
            date(2026, 4, 1), date(2026, 4, 5),
        ])
        assert folded[date(2026, 2, 1)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("5644.27")
        assert folded[date(2026, 3, 31)] == Decimal("5644.27")
        assert folded[date(2026, 4, 1)] == Decimal("3644.27")
        assert folded[date(2026, 4, 5)] == Decimal("3644.27")

    def test_a_settle_before_the_assertion_stays_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The over-correction guard: money already inside the assertion.

        A $50.00 expense settled at 12:00:00 and an assertion of $2,932.41 at
        12:57:08 the same day.  The user asserted that figure HAVING already
        spent the $50.00, so it is inside it.

        Hand-computed: the fold reads $2,932.41 on the assertion day, NOT
        $2,882.41.  A fold that counted every settled row unconditionally --
        the naive inverse of the shipping defect -- would fail here.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("50.00"),
            paid_at=_instant(2026, 3, 1, 12, 0, 0), name="earlier",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("2932.41"),
            _instant(2026, 3, 1, 12, 57, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 2, 28), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 28)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2932.41")

    def test_same_day_settles_land_on_opposite_sides_of_the_assertion(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The discriminating control: one civil day, two different answers.

        The production shape, at the FOLD.  A -$40.00 settle at 09:00, an
        assertion of $2,932.41 at 12:57:08, and a -$60.00 settle at 20:00 --
        all three on the SAME UTC civil day, so a partition keyed on the DATE
        has no information with which to separate them and would absorb both
        settles into the assertion.

        Hand-computed: keyed on the INSTANT the earlier is absorbed and the
        later rides on top, so the fold reads ``2932.41 - 60.00 = 2872.41`` on
        03-01.  A date-keyed implementation answers $2,932.41 and fails.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("40.00"),
            paid_at=_instant(2026, 3, 1, 9, 0, 0), name="before",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("2932.41"),
            _instant(2026, 3, 1, 12, 57, 8),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("60.00"),
            paid_at=_instant(2026, 3, 1, 20, 0, 0), name="after",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 2, 28), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 28)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2872.41")


class TestEveryAssertionIsReplayed:
    """Finding B-18 / cash D3: a past date reads the balance in force THEN."""

    def test_a_past_date_reads_its_own_assertion_not_todays(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Five events, five different answers across the history.

        Stream: opening $1,000.00 (2026-01-01); -$200.00 (2026-02-01); assert
        $900.00 (2026-03-01); -$300.00 (2026-04-01); assert $500.00
        (2026-05-01).

        Hand-computed, the fold reads 1000 / 800 / 900 / 600 / 500 on those five
        days.  The shipping scalar answers TODAY's $500.00 for every one of
        them, and the shipping period map omits the pre-anchor periods entirely
        -- two producers, two different wrong answers, which is the finding.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("200.00"),
            paid_at=_instant(2026, 2, 1), name="feb spend",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("900.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("300.00"),
            paid_at=_instant(2026, 4, 1), name="apr spend",
        )
        append_balance_assertion(
            db.session, account, seed_periods[8], Decimal("500.00"),
            _instant(2026, 5, 1),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ])
        assert folded[date(2026, 1, 1)] == Decimal("1000.00")
        assert folded[date(2026, 2, 1)] == Decimal("800.00")
        assert folded[date(2026, 3, 1)] == Decimal("900.00")
        assert folded[date(2026, 4, 1)] == Decimal("600.00")
        assert folded[date(2026, 5, 1)] == Decimal("500.00")


class TestThePlannedTier:
    """Ruling R-G: a plan cannot have already happened.

    A still-Projected row lands at ``max(its attribution date, as_of + 1 day)``.
    Rejected at the ruling: landing it on its nominal date and letting the next
    assertion's reset erase it, which on real data (one Checking re-anchor every
    2.3 days) silently deletes nearly every unpaid past-due bill within days of
    its being entered.
    """

    def test_an_overdue_bill_clamps_forward_instead_of_being_erased(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-G's worked Checking figures: $2,774.26, not $2,824.26.

        The ruling's shape: an assertion of $2,932.41 at 2026-04-02 12:57:08, a
        -$108.15 expense settling ten minutes later at 13:07:11, and a still
        -projected $50.00 bill due four days EARLIER (2026-03-29).  Read at
        ``as_of = 2026-04-02``.

        Hand-computed: the settle rides on the assertion, so 04-02 reads
        ``2932.41 - 108.15 = 2824.26``; the overdue bill clamps to ``as_of + 1``
        and lands 04-03, so 04-03 reads ``2824.26 - 50.00 = 2774.26``.  The
        ruling's rejected alternative -- the reset erasing the bill -- answers
        $2,824.26 forever, which is exactly this test's 04-02 value carried
        forward.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = date(2026, 4, 2)
        _opened_at(account, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, account, seed_periods[6], Decimal("2932.41"),
            _instant(2026, 4, 2, 12, 57, 8),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("108.15"),
            paid_at=_instant(2026, 4, 2, 13, 7, 11), name="settled late",
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "overdue bill", "50.00",
            due_date=date(2026, 3, 29),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 9),
        ], as_of=as_of)
        assert folded[date(2026, 4, 2)] == Decimal("2824.26")
        assert folded[date(2026, 4, 3)] == Decimal("2774.26")
        assert folded[date(2026, 4, 9)] == Decimal("2774.26")

    def test_a_future_bill_lands_on_its_own_due_date(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The clamp is a FLOOR, not a re-key: a future bill keeps its date.

        A $75.00 bill due 2026-04-08 read at ``as_of = 2026-04-02`` lands on
        04-08, not on 04-03.  Hand-computed: the fold reads $1,000.00 through
        04-07 and ``1000.00 - 75.00 = 925.00`` from 04-08.  An implementation
        that clamped every planned row to ``as_of + 1`` would answer $925.00 on
        04-03 and fail.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "future bill", "75.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 3), date(2026, 4, 7), date(2026, 4, 8),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 3)] == Decimal("1000.00")
        assert folded[date(2026, 4, 7)] == Decimal("1000.00")
        assert folded[date(2026, 4, 8)] == Decimal("925.00")

    def test_a_due_date_outside_its_period_is_pulled_to_the_boundary(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The shared attribution clamp, pinned at the fold.

        The recurrence engine can date a row just outside its own period, so a
        $90.00 bill in period 6 (2026-03-27 .. 2026-04-09) carrying a due date
        of 2026-05-20 is pulled back to that period's END, 04-09 -- the same
        rule the calendar groups its day cells by, so a flow's cell and the
        balance step for it cannot land on different days.

        Hand-computed: the fold reads $1,000.00 on 04-08 and $910.00 from
        04-09.  Without the clamp the step would land on 05-20 and 04-09 would
        still read $1,000.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        assert seed_periods[6].end_date == date(2026, 4, 9)
        add_txn(
            db.session, seed_user, seed_periods[6], "stray due date", "90.00",
            due_date=date(2026, 5, 20),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 8), date(2026, 4, 9), date(2026, 5, 20),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("1000.00")
        assert folded[date(2026, 4, 9)] == Decimal("910.00")
        assert folded[date(2026, 5, 20)] == Decimal("910.00")

    def test_a_row_with_no_due_date_lands_on_its_period_start(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The attribution fallback, clamped forward like any other.

        A $30.00 row with no ``due_date`` in period 8 (2026-04-24 ..) falls back
        to that period's START, 04-24.  Read at ``as_of = 2026-04-02`` that is
        already in the future, so the floor does not move it.  Hand-computed:
        $1,000.00 on 04-23, $970.00 from 04-24.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        assert seed_periods[8].start_date == date(2026, 4, 24)
        add_txn(
            db.session, seed_user, seed_periods[8], "undated", "30.00",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 23), date(2026, 4, 24),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 23)] == Decimal("1000.00")
        assert folded[date(2026, 4, 24)] == Decimal("970.00")

    def test_income_and_expense_net_within_a_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two planned rows sharing a day contribute their signed net.

        A +$1,200.00 paycheck and a -$450.00 bill both due 2026-04-08.
        Hand-computed: ``1000.00 + 1200.00 - 450.00 = 1750.00`` from 04-08.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "paycheck", "1200.00",
            is_income=True, due_date=date(2026, 4, 8),
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "bill", "450.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 7), date(2026, 4, 8),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 7)] == Decimal("1000.00")
        assert folded[date(2026, 4, 8)] == Decimal("1750.00")

    def test_an_envelope_holds_back_its_entries_aware_reservation(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A planned envelope is worth its reservation, not its raw estimate.

        The fold reduces each day's group through the SHARED
        ``cash_ledger.sum_projected``, so an envelope carries the
        three-bucket reservation the grid already shows rather than a second
        copy of the formula.

        A $200.00 grocery envelope with one CLEARED $120.00 debit purchase
        (2026-04-01) holds back ``max(200.00 - 120.00 - 0, 0) = 80.00``: the
        cleared debit has already left, so only the unreconciled remainder is
        still to come.  Hand-computed: $1,000.00 on 04-04 and $920.00 from
        04-05.  A fold valuing the row at ``effective_amount`` would answer
        $800.00 and fail.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        db.session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("120.00"),
            description="purchase",
            entry_date=date(2026, 4, 1),
            is_credit=False,
            is_cleared=True,
        ))
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 4), date(2026, 4, 5),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 4)] == Decimal("1000.00")
        assert folded[date(2026, 4, 5)] == Decimal("920.00")

    def test_the_reservation_reads_no_clock_whatever_the_readers_as_of(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """RULED (R-M) and pinned: what a row is WORTH is not a function of *as_of*.

        The firing control for plan step X-c2c1's deletion, and the negative
        twin of the test above.  The SAME envelope and the same $120.00 cleared
        purchase, moved to 2026-04-06 -- four days AFTER the reader's ``as_of``
        of 2026-04-02 -- must reduce the reservation identically: the fold reads
        ``max(200.00 - 120.00 - 0, 0) = 80.00`` and answers the same $920.00 on
        04-05 that the entry dated 04-01 produces.

        This was finding N-39, and it was a genuine three-way fork: the retired
        calendar scalar windowed entries by the reader's now while the grid and
        the daily ramp did not, so the fold had to pick one.  Ruling R-M closed
        it at the SOURCE instead of picking -- plan step X-c0 refuses
        ``entry_date > display_today()`` at both write doors, so no stored entry
        can be dated after any reader's now and the window provably dropped
        nothing.  What it could still have done is fire on a HISTORICAL read,
        whose plan is TODAY's still-Projected rows clamped forward (ruling R-G)
        rather than the plan as it stood then -- a partial as-of purity inside a
        tier that has none.

        So ``as_of`` now means exactly ONE thing in this fold, R-G's clamp
        floor, and this test is what fails if a window is ever re-introduced:
        restoring it answers $800.00 here.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        db.session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("120.00"),
            description="purchase",
            entry_date=date(2026, 4, 6),
            is_credit=False,
            is_cleared=True,
        ))
        db.session.commit()

        folded = _fold(account, scenario, [date(2026, 4, 5)],
                       as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 5)] == Decimal("920.00")


class TestScope:
    """The fold sees this account's rows in this scenario, and nothing else."""

    def test_another_scenarios_planned_rows_do_not_enter_the_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A what-if scenario's plan must not move the baseline.

        Assertions are per-ACCOUNT and replay in every scenario; only the
        transaction rows are scenario-scoped.  Hand-computed: a $400.00 planned
        expense in a non-baseline scenario leaves the baseline fold at
        $1,000.00 on 04-08, while the SAME assertions fold to $600.00 in the
        scenario that owns the row.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        other = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(other)
        db.session.flush()
        add_txn(
            db.session, seed_user, seed_periods[6], "what-if bill", "400.00",
            due_date=date(2026, 4, 8), scenario=other,
        )
        db.session.commit()

        as_of = date(2026, 4, 2)
        baseline = _fold(account, scenario, [date(2026, 4, 8)], as_of=as_of)
        assert baseline[date(2026, 4, 8)] == Decimal("1000.00")
        what_if = _fold(account, other, [date(2026, 4, 8)], as_of=as_of)
        assert what_if[date(2026, 4, 8)] == Decimal("600.00")

    def test_another_accounts_planned_rows_do_not_enter_the_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A sibling account's plan is not this account's plan.

        Hand-computed: a $250.00 planned expense on a Savings account leaves
        Checking's fold at $1,000.00 on 04-08.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("50.00"),
            anchor_period_id=seed_periods[0].id,
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "their bill", "250.00",
            due_date=date(2026, 4, 8), account=savings,
        )
        db.session.commit()

        folded = _fold(account, scenario, [date(2026, 4, 8)],
                       as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("1000.00")


class TestTotality:
    """No date is refused and no account is (plan Section 3).

    A partial producer forces every caller to compose it with a seed, a flag or
    a fallback, and every composition is a new producer that can disagree with
    the others.  A total function has nothing to compose with.
    """

    def test_a_date_before_every_event_reads_the_seed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Holding flat before the earliest record is R-I's other half.

        With the $500.00 record on 2026-01-15 inside a $1,000.00 opening, the
        seed is $1,500.00 and every date before 01-15 reads it -- 2020, 2025,
        the day before.  Hand-computed and identical across all three.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 2, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("500.00"),
            paid_at=_instant(2026, 1, 15), name="pre-opening",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2020, 1, 1), date(2025, 6, 30), date(2026, 1, 14),
        ])
        assert set(folded.values()) == {Decimal("1500.00")}

    def test_an_account_with_no_assertion_history_folds_from_zero(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Production-unreachable, and honestly zero rather than a raise.

        Migration ``cfb15e782f86`` plus the account factory guarantee an opening
        row, so this state cannot occur; the fold answers $0.00 instead of
        raising because a caller that must distinguish "holds nothing" from "no
        account" asks the account row, never this number.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        db.session.query(AccountAnchorHistory).filter_by(
            account_id=account.id,
        ).delete()
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 1, 1), date(2026, 6, 1),
        ])
        assert folded[date(2026, 1, 1)] == Decimal("0.00")
        assert folded[date(2026, 6, 1)] == Decimal("0.00")

    def test_an_empty_dates_list_is_an_empty_map(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Asking about no dates answers about no dates."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        db.session.commit()

        assert _fold(account, scenario, []) == {}

    def test_a_far_future_date_holds_the_last_step_flat(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Beyond every event the running total simply stops moving.

        Hand-computed: a $1,000.00 opening with a -$250.00 planned bill due
        2026-04-08 reads $750.00 on 04-08 and still $750.00 in 2040.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "bill", "250.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 8), date(2040, 1, 1),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("750.00")
        assert folded[date(2040, 1, 1)] == Decimal("750.00")

    def test_a_liability_accounts_negative_anchor_folds_ledger_native(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fold never branches on account class (ruling R-J, kind-blind).

        A Credit Card carries an owed-as-NEGATIVE anchor, and a direct charge on
        it is an EXPENSE, so it must make the balance MORE negative.
        Hand-computed: an asserted -$300.00 with a $75.00 charge settled after it
        folds to -$375.00.  Nothing in the fold consults the account's kind to
        get that right, which is what makes the claim structural.
        """
        from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel

        scenario = seed_user["scenario"]
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", Decimal("-300.00"),
        )
        _opened_at(card, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("75.00"),
            account=card, paid_at=_instant(2026, 3, 1), name="charge",
        )
        db.session.commit()

        folded = _fold(card, scenario, [
            date(2026, 2, 1), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 1)] == Decimal("-300.00")
        assert folded[date(2026, 3, 1)] == Decimal("-375.00")
