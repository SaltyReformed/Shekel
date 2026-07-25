"""X-b: the fold parallel-run against all three shipping cash producers.

Plan step X-b (``docs/audits/balance_architecture/README.md``).  Runs
``balance_at._cash_fold.fold_cash_balances`` against the three producers plan
step X-c will replace -- ``cash_balance_map``, ``cash_balance_at`` and
``cash_daily_balance_series`` -- on **EVERY DAY** of each shape's domain.

**Equality is NOT the pass condition here, and saying so is part of the step.**
The shipping producers carry the live findings this whole phase exists to close,
so on the shapes that trigger one the fold MUST differ; a file that demanded
equality everywhere would be demanding the defects.  What is asserted instead is
sharper:

* on a shape that triggers NO finding, the fold agrees with all three on every
  day of the domain (:class:`TestACleanShapeAgreesOnEveryDay`) -- so the fold is
  not a differently-wrong producer that happens to be right on the broken cases;
* on a shape that triggers one, the divergence is EXACTLY the finding, in
  hand-computed dollars and on named days
  (:class:`TestEveryDivergenceIsANamedFinding`) -- so an unexplained divergence
  fails rather than being absorbed into a tolerance.

**Sampling is forbidden.**  A 14-day sample once scored perfect while wrong by
$178,103.41 on 22% of days (plan Section 7.2), so each comparison walks every
day of its range and guards the loop so a vacuous domain cannot pass.

Note the shape of the three counterparties, which is itself finding cash D2: the
scalar is PERIOD-FLAT (it adds the target period's whole projected net for every
day inside that period) while the daily series RAMPS.  They cannot both agree
with a daily fold, so the clean-shape assertions below compare the fold to the
scalar and the map at period ENDS -- where all three are defined to coincide --
and to the daily series on every day.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import fold_cash_balances
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
    create_settled_cash_transaction,
    restamp_opening_assertion,
)
from tests.test_services.test_cash_fold import _instant


def _days(first_day, last_day):
    """Return every calendar day in ``[first_day, last_day]``, ascending."""
    span = (last_day - first_day).days
    return [first_day + timedelta(days=offset) for offset in range(span + 1)]


def _context(seed_user, as_of):
    """Return the read pass's :class:`BalanceContext` pinned at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of=as_of)


def _folded(seed_user, account, as_of, days):
    """Fold *account* at every day in *days*."""
    return fold_cash_balances(
        account, seed_user["scenario"].id, as_of, list(days),
    )


def _clean_shape(db_session, seed_user, seed_periods):
    """Build a shape that triggers NONE of the cash findings.

    One opening assertion inside the first period and no later true-up (so no
    assertion history to replay -- cash D3), no settled rows at all (so nothing
    can be attributed after the assertion -- cash D1), and every planned row due
    well after the read's as-of (so ruling R-G's clamp is a no-op).  On this
    shape the fold and the three producers are all answering the same question
    about the same facts, and must agree.

    Returns:
        The ``as_of`` the caller reads at.
    """
    restamp_opening_assertion(
        db_session, seed_user["account"], _instant(2026, 1, 2),
    )
    add_txn(
        db_session, seed_user, seed_periods[3], "paycheck", "1500.00",
        is_income=True, due_date=date(2026, 2, 20),
    )
    add_txn(
        db_session, seed_user, seed_periods[4], "rent", "1200.00",
        due_date=date(2026, 3, 5),
    )
    add_txn(
        db_session, seed_user, seed_periods[5], "undated bill", "75.00",
    )
    db_session.commit()
    return date(2026, 1, 2)


class TestACleanShapeAgreesOnEveryDay:
    """With no finding triggered, the fold is the producers' own answer.

    This is the half that proves the fold is not simply a DIFFERENT producer.
    Every divergence asserted in the next class would be worthless if the fold
    also disagreed with the shipping answer where the shipping answer is right.
    """

    def test_it_matches_the_daily_series_on_every_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every day from the anchor period's start to the horizon's end.

        The daily series is the only one of the three that is daily, so this is
        the comparison with no shape mismatch to allow for.
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        first_day, last_day = seed_periods[0].start_date, seed_periods[9].end_date
        days = _days(first_day, last_day)
        assert len(days) == 140  # the loop is not vacuous

        series = balance_at.cash_daily_balance_series(
            account, _context(seed_user, as_of), first_day, last_day,
        )
        # Non-vacuity: two producers that both answered one flat number every
        # day would satisfy the equality below while proving nothing.  The
        # shape's three planned rows must actually move the line.
        assert len(set(series.values())) == 4, "the shape does not move"

        folded = _folded(seed_user, account, as_of, days)
        assert {day: folded[day] for day in days} == dict(series)

    def test_it_matches_the_scalar_at_every_period_end(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every period end in the horizon, not a sample of them.

        A period END is where the period-flat scalar and a daily ramp are
        defined to coincide; inside a period they cannot (finding cash D2, whose
        magnitude the next class measures).
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        ctx = _context(seed_user, as_of)
        ends = [period.end_date for period in seed_periods]
        assert len(ends) == 10  # the loop is not vacuous

        folded = _folded(seed_user, account, as_of, ends)
        assert len(set(folded.values())) == 4, "the shape does not move"
        for end in ends:
            assert folded[end] == balance_at.cash_balance_at(
                account, ctx, end,
            ), f"diverged at period end {end}"

    def test_it_matches_the_period_map_everywhere_the_map_answers(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every period the map carries a balance for.

        The map is keyed by period and omits pre-anchor periods entirely; the
        anchor here is the first period, so it answers for all ten.
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        ctx = _context(seed_user, as_of)

        mapped = balance_at.cash_balance_map(account, ctx, seed_periods).balances
        assert len(mapped) == 10  # the loop is not vacuous
        assert len(set(mapped.values())) == 4, "the shape does not move"

        folded = _folded(
            seed_user, account, as_of,
            [period.end_date for period in seed_periods],
        )
        for period in seed_periods:
            assert folded[period.end_date] == mapped[period.id], (
                f"diverged at period {period.id} ({period.end_date})"
            )


class TestEveryDivergenceIsANamedFinding:
    """Where the fold differs, the difference IS the finding, to the cent."""

    def test_cash_d1_the_settled_row_the_producers_count_nowhere(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settle attributed after the assertion: $2,000.00, invisible.

        The developer's reported bug, run against the producers that lose it.
        Assertion $5,644.27 on 2026-03-01; a $2,000.00 expense settles
        2026-04-01, a month later.

        Hand-computed: the fold reads $3,644.27 from 04-01 while all three
        producers read $5,644.27 -- the anchor, unmoved, because a settled row
        contributes zero to the projection and the anchor predates it.  The
        $2,000.00 gap is finding cash D1, and it is asserted as an exact figure
        rather than as "they differ".
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 5)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("5644.27"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[6], Decimal("2000.00"),
            paid_at=_instant(2026, 4, 1), name="the transfer out",
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 4, 1), date(2026, 4, 9))
        assert len(days) == 9  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            assert folded[day] == Decimal("3644.27")
            assert series[day] == Decimal("5644.27")
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == Decimal("5644.27")
            assert series[day] - folded[day] == Decimal("2000.00")

        mapped = balance_at.cash_balance_map(account, ctx, seed_periods).balances
        assert mapped[seed_periods[6].id] == Decimal("5644.27")
        assert folded[seed_periods[6].end_date] == Decimal("3644.27")

    def test_cash_d2_the_scalar_is_period_flat_and_the_fold_is_not(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The scalar answers a whole period's net on that period's first day.

        A $1,200.00 bill due 2026-04-08, inside period 6 (2026-03-27 .. 04-09).

        Hand-computed: the fold holds $1,000.00 until 04-07 and steps to
        -$200.00 on 04-08, while the scalar answers -$200.00 for EVERY day of
        the period including 03-27 -- twelve days before the money moves.  The
        daily series agrees with the fold, which is the contradiction cash D2
        names: two shipping producers, one question, different answers.
        """
        account = seed_user["account"]
        as_of = date(2026, 3, 27)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "big bill", "1200.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(seed_periods[6].start_date, seed_periods[6].end_date)
        assert len(days) == 14  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            expected = (
                Decimal("1000.00") if day < date(2026, 4, 8)
                else Decimal("-200.00")
            )
            assert folded[day] == expected, f"fold wrong on {day}"
            assert series[day] == expected, f"daily series wrong on {day}"
            # ...and the scalar is flat at the period-END value all fourteen days.
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == Decimal("-200.00")

    def test_cash_d3_a_past_date_is_fabricated_or_omitted(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Before the latest assertion the scalar answers TODAY's balance.

        Two assertions: $1,000.00 opening (2026-01-01) and $5,644.27
        (2026-03-01).  Read at 2026-02-01, a date the account demonstrably held
        $1,000.00 at.

        Hand-computed: the fold answers $1,000.00 -- the assertion in force
        then.  The scalar answers $5,644.27, a balance the account did not hold
        until a month later, and the period map omits the pre-anchor periods
        entirely (its anchor is period 4, so periods 0-3 carry no key at all).
        Two producers, two different wrong answers, which is the finding.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 5)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, account, seed_periods[4], Decimal("5644.27"),
            _instant(2026, 3, 1),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 1, 2), date(2026, 2, 28))
        assert len(days) == 58  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        for day in days:
            assert folded[day] == Decimal("1000.00"), f"fold wrong on {day}"
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == Decimal("5644.27")

        mapped = balance_at.cash_balance_map(account, ctx, seed_periods).balances
        for period in seed_periods[:4]:
            assert period.id not in mapped
            assert folded[period.end_date] == Decimal("1000.00")

    def test_ruling_r_g_the_overdue_bill_the_producers_keep_in_the_past(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An overdue projected bill lands tomorrow, not on its stale due date.

        A $50.00 bill due 2026-03-29, read at ``as_of = 2026-04-02``.

        Hand-computed: the fold clamps it to 04-03, so 04-02 reads $1,000.00 and
        04-03 reads $950.00.  The daily series lands it on 03-29, so it reads
        $950.00 from 03-29 -- five days before the fold does.  Both reach
        $950.00 by the period end, so this divergence is confined to the window
        between the stale due date and ``as_of + 1``; what makes the ruling
        load-bearing is not that window but what happens at the NEXT re-anchor,
        which under the rejected alternative would absorb the bill and delete it
        from the projection entirely.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 2)
        restamp_opening_assertion(db.session, account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "overdue bill", "50.00",
            due_date=date(2026, 3, 29),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 3, 27), date(2026, 4, 9))
        assert len(days) == 14  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            assert folded[day] == (
                Decimal("1000.00") if day < date(2026, 4, 3)
                else Decimal("950.00")
            ), f"fold wrong on {day}"
            assert series[day] == (
                Decimal("1000.00") if day < date(2026, 3, 29)
                else Decimal("950.00")
            ), f"daily series wrong on {day}"
        assert folded[seed_periods[6].end_date] == series[
            seed_periods[6].end_date
        ]
