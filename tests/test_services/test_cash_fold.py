"""X-b / X-g4a: the cash FOLD, graded on a hand-computed oracle.

Plan steps X-b and X-g4a (``docs/audits/balance_architecture/README.md``).
Grades ``app.services.balance_at._cash_fold`` -- ``fold_cash_balances`` at day
grain, and since X-g4a ``cash_period_balances`` over a 52-period horizon.

**The fold is no longer ADDITIVE and this header used to say it was** (corrected
at X-g4a).  Plan step X-c2b2 pointed all three cash seam entries at it and
X-g3b the grid, so every figure here is a figure the app RENDERS: the cash-flow
family (``_cash_flow``), the grid's balance row (``_grid``), the modelled
replay's cash base (``_asset_fold``) and the net-worth kernel all read this
producer.  A test in this file that moves is a screen that moves.

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

from app.utils.dates import DISPLAY_TIMEZONE
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.services.balance_at._cash_fold import (
    cash_period_balances,
    fold_cash_balances,
)
from app.services.balance_at._fold import sample_cumulative
from app.services.cash_ledger import dated_deltas, walk_cash_ledger
from tests._test_helpers import (
    add_entry,
    add_txn,
    append_balance_assertion,
    basis_for,
    create_envelope_txn,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    mark_purchase_settled,
    override_anchor,
    period_window,
    restamp_opening_assertion,
)

# An as-of far past every valuation date these ACTUAL-tier tests read, so the
# PLANNED tier (which clamps to ``as_of + 1``) cannot reach them.  The tiers are
# graded separately on purpose: a test that mixed them could pass with either
# one wrong.
_LATE_AS_OF = date(2026, 12, 31)


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return the aware-UTC instant of a wall-clock moment on the USER's day.

    The arguments are read as the DISPLAY timezone -- the clock the user is
    actually looking at -- and converted to UTC for storage, which is the
    direction production runs in: the settle day and ``created_at`` are stamped
    when the user acts and stored UTC.

    **It read them as UTC until ruling R-DH (b)** (2026-07-31), and the default
    ``hour=0`` then meant midnight UTC -- 7pm or 8pm the PREVIOUS Eastern day.
    So a fixture writing ``date(2026, 1, 15)`` to mean "this settled on the
    15th" pinned an event the fold correctly places on the 14th, and five tests
    in this class asserted figures for a day their own setup had not built.
    Reading the arguments as Eastern makes the helper mean what every call site
    already said it meant, and it preserves same-day ORDERING exactly: two
    moments on one day shift by the same offset.
    """
    return datetime(
        year, month, day, hour, minute, second, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)


def _fold(account, scenario, days, as_of=_LATE_AS_OF):
    """Fold *account* at each of *days*, returning ``{date: Decimal}``."""
    return fold_cash_balances(account, basis_for(account, scenario), as_of, list(days))


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
            settled_on=date(2026, 1, 15), name="pre-opening",
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
                settled_on=date(2026, 4, day), name=f"apr-{day}",
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
        )
        _opened_at(account, _instant(2026, 4, 6))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("500.00"),
            account=account, is_income=True,
            settled_on=date(2026, 3, 27), name="deposit",
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
            settled_on=date(2026, 1, 15), name="pre-opening",
        )
        append_balance_assertion(
            db.session, account, seed_periods[3], Decimal("2000.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("250.00"),
            settled_on=date(2026, 4, 1), name="post",
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
        self, db, monkeypatch, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The reported bug, reproduced: $5,644.27 - $2,000.00 = $3,644.27.

        The real Money Market shape: opening $1,000.00 (2026-01-01), the user
        asserts $5,644.27 (2026-03-01 12:20:20), and a $2,000.00 transfer to
        Checking settles a month later (2026-04-01 19:47:44).

        **Today is moved to the end of that history**, overriding this suite's
        module freeze at 2026-03-20 (which the suite's own conftest invites a
        test to do).  The narrative is a PAST: the transfer HAS settled.  Under
        the module's clock it would settle a fortnight in its own future, which
        ruling R-EJ refuses at the write door -- correctly, because a settled
        row asserts that money has already moved.  The fixture's calendar has to
        contain its own today.

        Hand-computed: the transfer is attributed AFTER the assertion, so it
        rides on top of it and the fold reads $5,644.27 through 03-31 and
        $3,644.27 from 04-01.  $3,644.27 is also what the posted double-entry
        ledger holds for the real account, while every projected balance on
        screen answers $5,644.27 -- the divergence is the finding.

        The transfer reaches the fold as its SHADOW rows (Transfer Invariant 5),
        exactly as it reaches the shipping projection; neither queries
        ``Transfer``.
        """
        freeze_today(monkeypatch, date(2026, 4, 5))
        scenario = seed_user["scenario"]
        money_market = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("1000.00"),
        )
        _opened_at(money_market, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, money_market, seed_periods[4], Decimal("5644.27"),
            _instant(2026, 3, 1, 12, 20, 20),
        )
        create_settled_transfer(
            seed_user, db.session, money_market, seed_user["account"],
            seed_periods[6], amount=Decimal("2000.00"),
            settled_on=date(2026, 4, 1),
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
            settled_on=date(2026, 3, 1), name="earlier",
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

    def test_both_same_day_settles_go_with_the_assertion(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The discriminating control: one civil day, ONE answer (ruling R-DH (a)).

        The production shape, at the FOLD.  A -$40.00 settle at 09:00, an
        assertion of $2,932.41 at 12:57:08, and a -$60.00 settle at 20:00 --
        all three on the user's SAME civil day.  The assertion is that day's
        CLOSING balance, so both settles are inside it and the fold reads the
        asserted $2,932.41 on 03-01.

        An instant-keyed implementation answers ``2872.41`` and fails, which is
        the same discriminating role this test had before ruling R-DH inverted
        it (2026-07-31) -- see ``anchor_settle_partition.md`` for the
        ``-$4,021.37`` the instant partition rendered on production.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("40.00"),
            settled_on=date(2026, 3, 1), name="before",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("2932.41"),
            _instant(2026, 3, 1, 12, 57, 8),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[4], Decimal("60.00"),
            settled_on=date(2026, 3, 1), name="after",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 2, 28), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 28)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2932.41")


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
            settled_on=date(2026, 2, 1), name="feb spend",
        )
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("900.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("300.00"),
            settled_on=date(2026, 4, 1), name="apr spend",
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
        """R-G's worked Checking figures, re-based on ruling R-DH (a).

        The ruling's shape: an assertion of $2,932.41 on 2026-04-02, a -$108.15
        expense recorded ten minutes later the same day, and a still-projected
        $50.00 bill due four days EARLIER (2026-03-29).  Read at
        ``as_of = 2026-04-02``.

        Hand-computed: the same-day settle is inside the closing balance
        (R-DH (a)), so 04-02 reads $2,932.41; the overdue bill clamps to
        ``as_of + 1`` and lands 04-03, so 04-03 reads
        ``2932.41 - 50.00 = 2882.41``.  **R-G is what this test grades and R-G
        is untouched** -- the base moved by ``$108.15`` because the partition
        did, and the ``-$50.00`` step one day later is the clamp arm, unchanged.
        The ruling's rejected alternative -- the reset erasing the bill --
        answers $2,932.41 forever, which is exactly this test's 04-02 value
        carried forward.
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
            settled_on=date(2026, 4, 2), name="settled late",
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "overdue bill", "50.00",
            due_date=date(2026, 3, 29),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 9),
        ], as_of=as_of)
        assert folded[date(2026, 4, 2)] == Decimal("2932.41")
        assert folded[date(2026, 4, 3)] == Decimal("2882.41")
        assert folded[date(2026, 4, 9)] == Decimal("2882.41")

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

        A $200.00 grocery envelope with one $120.00 debit purchase made and
        posted 2026-03-19, against a balance the user declared for that same
        day, holds back ``max(200.00 - 120.00 - 0, 0) = 80.00``: the posted
        debit is already inside the declared balance, so only the unreconciled
        remainder is still to come.  Hand-computed: $1,000.00 on 04-04 and
        $920.00 from 04-05 (the envelope is due 04-05 and the reader's as_of
        is 03-20, so ruling R-G's clamp is a no-op).  A fold valuing the row
        at ``effective_amount`` would answer $800.00 and fail.

        **The opening assertion is dated 03-19 rather than 01-01, and that is
        finding N-132 / R8 from a third direction** (plan step S1-c,
        Section 13.1).  The retired ``is_cleared`` flag let this fixture claim
        the purchase was inside an anchor asserted three months EARLIER -- a
        state production cannot reach, because the way a purchase gets inside a
        declared balance is that the user declared the balance after it posted.

        **Every date here sits at or before this suite's frozen today
        (2026-03-20), and that is the OTHER half of the same rule.**  The first
        conversion moved the assertion FORWARD to 04-01 to cover an 04-01
        purchase, which is unreachable in the opposite direction:
        ``anchor_service.resolve_observation_day`` refuses an
        ``observed_on`` after the user's today, and R-M refuses a
        ``purchased_on`` after it.  ``mark_purchase_settled`` now checks both
        bounds and named that fixture, which is why the SCENARIO moved back
        rather than the assertion moving further out.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 3, 19))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 3, 19),
        )
        mark_purchase_settled(db.session, account, txn.entries[0])
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 4), date(2026, 4, 5),
        ], as_of=date(2026, 3, 20))
        assert folded[date(2026, 4, 4)] == Decimal("1000.00")
        assert folded[date(2026, 4, 5)] == Decimal("920.00")

    def test_the_reservation_reads_no_clock_whatever_the_readers_as_of(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """RULED (R-M) and pinned: what a row is WORTH is not a function of *as_of*.

        The firing control for plan step X-c2c1's deletion, and the negative
        twin of the test above.  The SAME envelope and the same $120.00
        purchase, read at an ``as_of`` of 2026-03-10 -- NINE DAYS BEFORE the
        purchase was made -- must reduce the reservation identically: the fold
        reads ``max(200.00 - 120.00 - 0, 0) = 80.00`` and answers the same
        $920.00 on 04-05 that the test above produces.

        **The reader's clock moves, not the purchase's dates, and that is what
        makes the fixture reachable.**  The property is that a row's WORTH is
        independent of the reader's ``as_of``; the earlier form got the same
        separation by dating the purchase into the app's future, which BOTH
        write doors refuse (R-M on ``purchased_on``,
        ``anchor_service.resolve_observation_day`` on the assertion covering it).
        Sliding the READER backwards is the same experiment on a state
        production can actually hold.

        **The purchase must be in the SETTLED bucket for this to discriminate**
        (plan step S1-c).  An OUTSTANDING purchase is worth
        ``max(200 - 0 - 0, 120) = 200`` -- which is exactly what a
        re-introduced window would also answer, by dropping the entry and
        falling through the empty-entries short circuit to
        ``effective_amount``.  The two would be indistinguishable and the test
        would be over-determined (finding N-69's shape).  Only the settled
        bucket separates them, so the account asserts a balance covering the
        purchase and ``mark_purchase_settled`` checks that it does.

        This was finding N-39, and it was a genuine three-way fork: the retired
        calendar scalar windowed entries by the reader's now while the grid and
        the daily ramp did not, so the fold had to pick one.  Ruling R-M closed
        it at the SOURCE instead of picking -- plan step X-c0 refuses
        ``purchased_on > display_today()`` at both write doors, so no stored entry
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
        _opened_at(account, _instant(2026, 3, 19))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 3, 19),
        )
        mark_purchase_settled(db.session, account, txn.entries[0])
        db.session.commit()

        # The purchase (03-19) is NINE DAYS after the reader's as_of.
        folded = _fold(account, scenario, [date(2026, 4, 5)],
                       as_of=date(2026, 3, 10))
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
            settled_on=date(2026, 1, 15), name="pre-opening",
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
            account=card, settled_on=date(2026, 3, 1), name="charge",
        )
        db.session.commit()

        folded = _fold(card, scenario, [
            date(2026, 2, 1), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 1)] == Decimal("-300.00")
        assert folded[date(2026, 3, 1)] == Decimal("-375.00")


# -- The 52-period drift oracle (plan step X-g4a, ruling R-AT) --------------
#
# Every parameter of the shape is named HERE, at module scope, so the BUILDER
# and the ORACLE below read the same numbers without either deriving them from
# the other -- and so a reader can see the whole fixture without reading either.

# The ``seed_periods_52`` fixture's own opening assertion: $1,000.00 stamped at
# midnight UTC on period 0's start (``_drop_seed_user_bootstrap`` re-points the
# factory row and re-stamps it).
_DRIFT_OPENING = Decimal("1000.00")
_DRIFT_INCOME = Decimal("2500.00")
# Non-round, so a cent dropped or double-counted anywhere in 52 periods shows up
# in the total instead of cancelling.
_DRIFT_EXPENSE = Decimal("1175.53")
# Period 15's settled expense carries an ACTUAL over its estimate, so
# ``settled_cash_leg``'s ``effective_amount`` is graded rather than assumed:
# a walk reading the ESTIMATE understates every column from 15 on by $24.47.
_DRIFT_ACTUAL_EXPENSE = Decimal("1200.00")
_DRIFT_ACTUAL_INDEX = 15
# Three of these sum to $99.99, NOT $100.00.
_DRIFT_THIRD = Decimal("33.33")
_DRIFT_THIRDS_PER_PERIOD = 3
_DRIFT_THIRD_EVERY = 3
_DRIFT_INCOME_DAY = 2
_DRIFT_EXPENSE_DAY = 5
_DRIFT_THIRD_DAY = 7

# 1..16, straddling the re-assertion at 13.
_DRIFT_SETTLED_PERIODS = range(1, 17)
# 20..51.
_DRIFT_PLANNED_PERIODS = range(20, 52)
# Period 19's end.  Period 20 starts the very next day, so every ordinary
# planned row's own attribution date is strictly after ``as_of + 1`` and ruling
# R-G's clamp is a no-op for it -- the ONE row it must bite is the overdue one
# below, which is what makes the clamp gradeable here rather than inert.
_DRIFT_AS_OF = date(2026, 10, 8)

# The RESET, 09:00 UTC on period 13's first day.
_DRIFT_REASSERTION = Decimal("5412.83")
_DRIFT_REASSERTION_AT = _instant(2026, 7, 3, 9, 0)
_DRIFT_REASSERTION_INDEX = 13
# Period 13's two settled rows sit on the assertion's OWN CIVIL DAY, and under
# ruling R-DH (a) an assertion is the CLOSING balance for its day -- so BOTH are
# inside it and the reset absorbs both.  They were two instants an hour either
# side of the assertion's when ruling R-B partitioned by instant; R-DH replaced
# that rule and the figures below were recomputed for it then, so collapsing the
# pair to the one day they always shared moves nothing.  Kept as a named
# constant rather than inlined because "the settles that share the assertion's
# day" is the property period 13 is here to grade.
_DRIFT_STRADDLE_DAY = date(2026, 7, 3)

# Rows worth exactly nothing, in four shapes.  See the class docstring for what
# they are and are NOT: three independent layers zero them, so no single-point
# defect leaks one, and they are defence-in-depth rather than controls.
_DRIFT_EXCLUDED_ONLY_INDEX = 17
_DRIFT_CANCELLED = Decimal("600.00")
_DRIFT_CREDIT = Decimal("350.00")
_DRIFT_EXCLUDED_MIXED_INDEX = 29
_DRIFT_MIXED_CANCELLED = Decimal("500.00")
_DRIFT_MIXED_CREDIT = Decimal("450.00")
_DRIFT_ZERO_INDEX = 25
_DRIFT_DELETED_INDEX = 45
_DRIFT_DELETED = Decimal("999.00")

# Ruling R-G: a still-Projected row whose own date has PASSED is clamped
# forward to ``as_of + 1``, never absorbed.  It sits in period 16, which ALREADY
# holds two settled rows -- the ACTUAL and PLANNED tiers in ONE column, which is
# what every user's current period looks like -- and lands on 2026-10-09,
# period 20's first day.
_DRIFT_OVERDUE_INDEX = 16
_DRIFT_OVERDUE = Decimal("412.19")
_DRIFT_OVERDUE_DUE = date(2026, 8, 20)
_DRIFT_OVERDUE_LANDS_INDEX = 20
# ``attribution_date`` clamps a due date outside its own period to the nearer
# boundary, and BOTH directions are graded.  Period 40's row is due 30 days
# EARLY (2027-06-16, inside period 37); period 44's is due 20 days LATE
# (2027-10-13, inside period 46).  Unclamped, each would land on a different
# column than the one it is budgeted to.
_DRIFT_STRAY_INDEX = 40
_DRIFT_STRAY = Decimal("77.11")
_DRIFT_STRAY_EARLY_DAYS = 30
_DRIFT_LATE_STRAY_INDEX = 44
_DRIFT_LATE_STRAY = Decimal("88.23")
_DRIFT_STRAY_LATE_DAYS = 20


def _drift_settle_days(period, index):
    """Return ``(income_day, expense_day)`` for a settled period.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` being filled.
        index: Its ``period_index``.

    Returns:
        The two civil settle days.  Period 13's both fall on the re-assertion's
        own day; every other period's sit on ordinary days inside their own
        period.  They were noon-UTC INSTANTS until plan step X-f1 (ruling
        R-EC) -- noon UTC is the same civil day in the display timezone, which
        is the day the readers derived from them, so every figure below is
        unchanged.
    """
    if index == _DRIFT_REASSERTION_INDEX:
        return _DRIFT_STRADDLE_DAY, _DRIFT_STRADDLE_DAY
    return (
        period.start_date + timedelta(days=_DRIFT_INCOME_DAY),
        period.start_date + timedelta(days=_DRIFT_EXPENSE_DAY),
    )


def _add_drift_zero_worth_rows(db_session, seed_user, periods):
    """Add the four row shapes the fold must value at exactly ZERO.

    A Cancelled and a Credit row in a period holding nothing else (the
    original's S5), the same pair inside a period that also holds contributing
    rows (S3 / S4), a zero-amount projected row (S8), and a soft-deleted row.
    None appears in :func:`_drift_oracle`.  What they are NOT is stated in the
    class docstring: three layers zero them independently, so they are
    defence-in-depth rather than firing controls.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods.
    """
    for index, cancelled, credit in (
        (_DRIFT_EXCLUDED_ONLY_INDEX, _DRIFT_CANCELLED, _DRIFT_CREDIT),
        (
            _DRIFT_EXCLUDED_MIXED_INDEX,
            _DRIFT_MIXED_CANCELLED,
            _DRIFT_MIXED_CREDIT,
        ),
    ):
        add_txn(
            db_session, seed_user, periods[index], f"cancelled p{index}",
            cancelled, status_enum=StatusEnum.CANCELLED,
        )
        add_txn(
            db_session, seed_user, periods[index], f"credit p{index}", credit,
            status_enum=StatusEnum.CREDIT,
        )
    add_txn(
        db_session, seed_user, periods[_DRIFT_ZERO_INDEX],
        f"zero p{_DRIFT_ZERO_INDEX}", Decimal("0.00"),
    )
    add_txn(
        db_session, seed_user, periods[_DRIFT_DELETED_INDEX],
        f"deleted p{_DRIFT_DELETED_INDEX}", _DRIFT_DELETED, is_deleted=True,
    )


def _add_drift_stray_dated_rows(db_session, seed_user, periods):
    """Add the two rows whose due dates fall OUTSIDE their own pay period.

    One 30 days early and one 20 days late, so ``attribution_date``'s two
    clamp arms are each graded: unclamped, the early row lands on period 37 and
    the late one on period 46, neither of which is the column it is budgeted to.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods.
    """
    early = periods[_DRIFT_STRAY_INDEX]
    add_txn(
        db_session, seed_user, early, "stray early due date", _DRIFT_STRAY,
        due_date=early.start_date - timedelta(days=_DRIFT_STRAY_EARLY_DAYS),
    )
    late = periods[_DRIFT_LATE_STRAY_INDEX]
    add_txn(
        db_session, seed_user, late, "stray late due date", _DRIFT_LATE_STRAY,
        due_date=late.end_date + timedelta(days=_DRIFT_STRAY_LATE_DAYS),
    )


def _build_drift_shape(db_session, seed_user, periods):
    """Create the 52-period mixed shape on the seed user's Checking account.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods from ``seed_periods_52``.
    """
    for index in _DRIFT_SETTLED_PERIODS:
        period = periods[index]
        income_day, expense_day = _drift_settle_days(period, index)
        create_settled_cash_transaction(
            seed_user, db_session, period, _DRIFT_INCOME, is_income=True,
            name=f"paycheck p{index}", settled_on=income_day,
        )
        create_settled_cash_transaction(
            seed_user, db_session, period, _DRIFT_EXPENSE,
            name=f"rent p{index}", settled_on=expense_day,
            settled_amount=(
                _DRIFT_ACTUAL_EXPENSE if index == _DRIFT_ACTUAL_INDEX else None
            ),
        )

    override_anchor(
        db_session, seed_user["account"], periods[_DRIFT_REASSERTION_INDEX],
        _DRIFT_REASSERTION,
        at=_DRIFT_REASSERTION_AT,
    )

    add_txn(
        db_session, seed_user, periods[_DRIFT_OVERDUE_INDEX], "overdue bill",
        _DRIFT_OVERDUE, due_date=_DRIFT_OVERDUE_DUE,
    )

    for index in _DRIFT_PLANNED_PERIODS:
        period = periods[index]
        add_txn(
            db_session, seed_user, period, f"paycheck p{index}", _DRIFT_INCOME,
            is_income=True,
            due_date=period.start_date + timedelta(days=_DRIFT_INCOME_DAY),
        )
        add_txn(
            db_session, seed_user, period, f"rent p{index}", _DRIFT_EXPENSE,
            due_date=period.start_date + timedelta(days=_DRIFT_EXPENSE_DAY),
        )
        if index % _DRIFT_THIRD_EVERY == 0:
            for slot in range(_DRIFT_THIRDS_PER_PERIOD):
                add_txn(
                    db_session, seed_user, period, f"third {slot} p{index}",
                    _DRIFT_THIRD,
                    due_date=period.start_date
                    + timedelta(days=_DRIFT_THIRD_DAY),
                )

    _add_drift_stray_dated_rows(db_session, seed_user, periods)
    _add_drift_zero_worth_rows(db_session, seed_user, periods)
    db_session.commit()


def _drift_oracle(periods):
    """Return ``{period_id: balance}`` from an INDEPENDENT running total.

    Iterates the 52 periods by INDEX and applies each tier's per-period effect
    as the rulings say it should land.  It imports nothing and calls nothing in
    ``balance_at`` or ``cash_ledger``, and it does not re-derive any of the
    producer's dating arithmetic -- it states each ruling's OUTCOME as a
    constant, which is the stronger of the two oracle forms here: the producer
    computes a landing DAY from ``attribution_date`` and ``max(nominal, as_of +
    1)`` and prefix-sums it, while this names the landing PERIOD outright
    (``_DRIFT_OVERDUE_LANDS_INDEX``, ``_DRIFT_STRAY_INDEX``), so a broken clamp
    is caught rather than mirrored.  Likewise it ASSIGNS on the re-assertion
    where the producer books ``anchor_balance - balance_before``.

    Args:
        periods: The 52 pay periods, ordered by ``period_index``.

    Returns:
        ``dict`` mapping period id to the expected end balance.
    """
    running = _DRIFT_OPENING
    expected = {}
    for index, period in enumerate(periods):
        if index == _DRIFT_REASSERTION_INDEX:
            # The reset discards everything before it AND everything else on
            # its own civil day -- the income recorded earlier and the expense
            # recorded later alike -- because an assertion is that day's
            # CLOSING balance (ruling R-DH (a), which superseded R-B's instant
            # partition on 2026-07-31).  So the column IS the asserted figure.
            running = _DRIFT_REASSERTION
        elif index in _DRIFT_SETTLED_PERIODS:
            expense = (
                _DRIFT_ACTUAL_EXPENSE if index == _DRIFT_ACTUAL_INDEX
                else _DRIFT_EXPENSE
            )
            running += _DRIFT_INCOME - expense
        elif index in _DRIFT_PLANNED_PERIODS:
            running += _DRIFT_INCOME - _DRIFT_EXPENSE
            if index % _DRIFT_THIRD_EVERY == 0:
                running -= _DRIFT_THIRD * _DRIFT_THIRDS_PER_PERIOD
        if index == _DRIFT_OVERDUE_LANDS_INDEX:
            running -= _DRIFT_OVERDUE
        if index == _DRIFT_STRAY_INDEX:
            running -= _DRIFT_STRAY
        if index == _DRIFT_LATE_STRAY_INDEX:
            running -= _DRIFT_LATE_STRAY
        expected[period.id] = running
    return expected


def _drift_period_map(seed_user, periods):
    """Return the fold's period-end balance map for the drift shape."""
    return cash_period_balances(
        seed_user["account"],
        basis_for(seed_user["account"], seed_user["scenario"]),
        _DRIFT_AS_OF, period_window(periods),
    )


class TestTheDriftOracleWalksFiftyTwoPeriods:
    """52 periods, every tier, against an independent running total.

    The long-horizon cumulative-accuracy oracle, ported at plan step **X-g4a**
    (ruling R-AT) from ``test_balance_calculator.py``'s
    ``test_52_period_penny_accuracy``, which dies with ``_calculator`` at
    X-g4b.  The original walked 52 periods of still-projected rows carried
    forward from an anchor -- exactly ONE of the tiers this fold has -- so a
    faithful port would have been a drift oracle for a third of the producer it
    now grades.  The shape:

    * the **OPENING** assertion ($1,000.00 at 2026-01-02 00:00 UTC);
    * a **SETTLED** past, periods 1-16, every row stamped at a pinned instant,
      one of them (period 15) carrying an ACTUAL over its estimate;
    * a mid-horizon **RE-ASSERTION** on 2026-07-03 -- the RESET the original
      could not express at all -- whose own period's two rows sit either side of
      it on its own civil day, so ruling **R-DH (a)**'s CLOSING-BALANCE
      partition decides period 13's figure and an instant-keyed partition
      cannot reproduce it;
    * period 16 holding the SETTLED and PLANNED tiers at once -- two settled
      rows plus an overdue bill due 2026-08-20 that ruling **R-G** must clamp
      forward onto period 20.  That coexistence is what every user's CURRENT
      period looks like (plan Section 7.4);
    * periods 17-19 holding nothing that reaches the balance, one of them
      holding ONLY a Cancelled and a Credit row, so a column with nothing in it
      is proved to carry the running total forward;
    * a still-**PROJECTED** future, periods 20-51, including two rows whose due
      dates fall outside their own period -- one 30 days early, one 20 days
      late -- so both of ``attribution_date``'s clamp arms are graded;
    * rows worth exactly nothing in four shapes: Cancelled, Credit,
      zero-amount and soft-deleted.

    **What the original covered and this does not, measured rather than
    asserted.** Its done / received exclusions (S2 / S6 / S7) are SUPERSEDED
    rather than dropped -- the fold counts a settled row as an ACTUAL from the
    day the money moved, which is finding cash D1 and the whole reason
    ``_calculator`` is being deleted.  Its non-round ANCHOR is not reproduced:
    the fixture's opening is $1,000.00 and cannot be changed without writing a
    cache-versus-history divergence production cannot reach, so the
    truncation-exposing role is carried by the non-round re-assertion
    ($5,412.83) and by every per-period amount.  Ruling **R-I** is NOT graded
    here and no shape could grade it at this grain -- no period end precedes the
    opening -- so it stays with :class:`TestTheOpeningMovesIntoTheSeed` above.

    **The oracle is a test-local running total and never the fold reading
    itself** (plan Section 7.2).  Sampling is forbidden: every one of the 52
    columns is asserted, never a sample of them.  **Ruling R-AT's "cumulative
    cross-check" is the seven hand-written figures** in the second test -- human
    arithmetic is a second instrument over the same facts, where the original's
    flat re-summation would only have re-checked this file's own loop.

    **Firing controls, run at X-g4a** (plan Section 7.3; each a one-line
    production mutation, reverted).  EIGHT fail this class, seven of them on
    BOTH tests: the assertion no longer resetting the walked total
    (``cash_ledger._walk``); the planned tier never merging into the running
    steps; the closing-balance partition re-keyed onto the INSTANT
    (``cash_ledger.ReconciledThrough.covers``) -- the control that
    ran the other way until ruling R-DH (a), and still fires, because period 13
    is built to separate the two rules; ruling R-G's clamp
    deleted; its floor off by one (``not_before = as_of``); the map sampling
    each period's START; and ``settled_cash_leg`` valuing a settled row at its
    ESTIMATE rather than its ACTUAL.  The eighth, ``attribution_date``'s clamp
    deleted, fails the 52-column test in EITHER direction while the hand-figure
    test survives it -- the stray rows move to periods neither test names
    individually and net out again by the horizon, which is precisely why the
    52-column walk is not redundant with the eight named figures.

    **Three mutation CLASSES do NOT fail it, and the reason is structural --
    stated so the boundary is known instead of discovered.**  (1) Ignoring
    ``due_date`` entirely lands every ordinary planned row on its own period's
    start, invisible at period-END grain; the day a row lands on is graded at
    day grain by :class:`TestThePlannedTier` above.  (2) Shifting every
    assertion's ``visible_on`` by a day moves neither assertion out of its own
    period; that is :class:`TestEveryAssertionIsReplayed`'s subject.  (3) The
    four zero-worth rows cannot be leaked by any SINGLE-point defect: the SQL
    clause pair (``balance_contributing_clause`` / ``is_projected_clause``), the
    Python predicate ``sum_projected`` re-applies, and
    ``Transaction.effective_amount``'s own zero-guards each zero them
    independently.  Only the soft-deleted row fires, and only under a
    simultaneous two-point break.  They are retained as defence-in-depth
    verification, NOT as controls, and this paragraph is what stops a later
    reader crediting them as coverage.

    ``tests/test_services`` freezes ``date.today()`` to 2026-03-20 while this
    shape stamps instants from 2026-01-18 to 2026-08-19 and reads at
    2026-10-08, so the fixture is production-shaped only when read as "today is
    2026-10-08".  Nothing under test consults that clock: the walk takes none,
    the read's ``as_of`` is explicit, and neither live-override seam has a
    candidate row here (no salary-linked template, no loan-payment shadow).
    """

    def test_every_period_end_matches_the_independent_running_total(
        self, db, seed_user, seed_periods_52,
    ):
        """All 52 columns, each against the oracle's own figure.

        The cumulative property: a one-cent error anywhere in the walk survives
        to every later column, so each column is asserted individually rather
        than only the total.
        """
        _build_drift_shape(db.session, seed_user, seed_periods_52)
        expected = _drift_oracle(seed_periods_52)

        actual = _drift_period_map(seed_user, seed_periods_52)

        assert len(seed_periods_52) == 52
        assert len(actual) == 52
        for index, period in enumerate(seed_periods_52):
            assert actual[period.id] == expected[period.id], (
                f"period {index} (id={period.id}, ending "
                f"{period.end_date}): expected {expected[period.id]}, got "
                f"{actual[period.id]}, diff "
                f"{actual[period.id] - expected[period.id]}"
            )

    def test_the_named_columns_are_the_hand_computed_figures(
        self, db, seed_user, seed_periods_52,
    ):
        """Eight columns computed by hand, one per structural feature.

        The oracle above is a loop; these are arithmetic written out, so an
        error shared by the builder and the oracle still fails here -- which is
        ruling R-AT's cumulative cross-check.  Net per ordinary period is
        ``2,500.00 - 1,175.53 = 1,324.47``.

          * **period 12** (ends 2026-07-02, the last column before the reset):
            ``1,000.00 + 12 x 1,324.47 = $16,893.64``.
          * **period 13** (ends 2026-07-16, the reset's own column): the
            assertion REPLACES that total and BOTH of its own day's rows go with
            it -- the income recorded earlier and the expense recorded later --
            so the column IS the asserted ``$5,412.83`` (ruling R-DH (a)).  A
            fold that ignored the assertion would read
            ``16,893.64 + 1,324.47 = $18,218.11``; one partitioning on the
            INSTANT keeps the later expense and reads
            ``5,412.83 - 1,175.53 = $4,237.30``, which is what this figure was
            until 2026-07-31.
          * **period 15** (ends 2026-08-13): its settled expense is worth its
            ACTUAL $1,200.00, not its $1,175.53 estimate, so this period nets
            ``2,500.00 - 1,200.00 = 1,300.00`` --
            ``5,412.83 + 1,324.47 + 1,300.00 = $8,037.30``.
          * **period 16** (ends 2026-08-27): settled and planned in ONE column.
            Its two settled rows net ``+1,324.47``; its overdue bill is clamped
            OUT of it by ruling R-G -- ``8,037.30 + 1,324.47 = $9,361.77``.
          * **period 17** (ends 2026-09-10): holds ONLY a $600.00 Cancelled and
            a $350.00 Credit row, so it carries ``$9,361.77`` unchanged.
          * **period 19** (ends 2026-10-08, the read's own as-of): still
            ``$9,361.77``.
          * **period 20** (ends 2026-10-22): the clamp lands the overdue bill
            here, ``9,361.77 + 1,324.47 - 412.19 = $10,274.05``.
          * **period 51** (ends 2027-12-30, the horizon): periods 21-51 add
            ``31 x 1,324.47 = 41,058.57``, the 11 of them divisible by three
            each hold back ``3 x 33.33 = 99.99``, and the two stray-dated rows
            hold back ``77.11`` (period 40) and ``88.23`` (period 44) --
            ``10,274.05 + 41,058.57 - 1,099.89 - 77.11 - 88.23 = $50,067.39``.
        """
        _build_drift_shape(db.session, seed_user, seed_periods_52)

        actual = _drift_period_map(seed_user, seed_periods_52)

        assert actual[seed_periods_52[12].id] == Decimal("16893.64")
        assert actual[seed_periods_52[13].id] == Decimal("5412.83")
        assert actual[seed_periods_52[15].id] == Decimal("8037.30")
        assert actual[seed_periods_52[16].id] == Decimal("9361.77")
        assert actual[seed_periods_52[17].id] == Decimal("9361.77")
        assert actual[seed_periods_52[19].id] == Decimal("9361.77")
        assert actual[seed_periods_52[20].id] == Decimal("10274.05")
        assert actual[seed_periods_52[51].id] == Decimal("50067.39")
