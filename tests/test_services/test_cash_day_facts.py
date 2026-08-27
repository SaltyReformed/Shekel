"""
Shekel Budget App -- The cash fold read at DAY grain, split into its tiers.

Pins ``balance_at.cash_daily_facts_series`` and the fold behind it (plan step
**bank_import:X-f6e-2**).  A reader comparing this account against a record
kept OUTSIDE the app -- the bank's own statement -- needs a day's MOVEMENT
split three ways, because a balance difference alone cannot tell the three
apart and they mean different things: money the app's rows say moved, a
balance the owner ASSERTED, and a plan that has not happened.

**The load-bearing property is the identity**::

    balance(d) - balance(d - 1) == recorded(d) + asserted(d) + planned(d)

It holds by construction today -- ``_running_steps`` assembles exactly
``dated_deltas`` plus the opening's compensator plus the planned nets -- and
this suite exists because that is a property of TODAY's assembly rather than of
the contract.  A fourth tier joining the running total with no entry here would
otherwise be absorbed silently into whichever component a consumer derived by
subtraction, and labelled as something it is not.

**The OPENING assertion is the case worth naming.**  Ruling R-I moves its
correction into the SEED and books an equal-and-opposite step on its own day,
so its net contribution there is ZERO: the figure it establishes is part of the
level every later day is measured from, not money moving that day.  Deriving
``asserted`` from the walk's corrections without excluding it puts the opening's
whole delta on its day -- ``$798.03`` on the developer's real account -- and the
identity above is what catches it.

Scenario: the ``seed_periods`` biweekly calendar from 2026-01-02, whose account
opens with a $1000.00 assertion.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.services import balance_at, cash_ledger
from app.services.balance_at import BalanceContext
from app.services.balance_at._assertions import assertion_corrections
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import (
    append_balance_assertion,
    default_settle_day,
    settle_day_columns,
    settlement_columns,
)
from tests.test_services.test_cash_fold import _instant

_ZERO = Decimal("0.00")


def _settled(db, seed_user, period, name, amount, day, *, is_income=False):
    """Insert one SETTLED row whose cash moved on *day*."""
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        name=name,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
        estimated_amount=Decimal(str(amount)),
        **settlement_columns(day, amount, amount),
        **settle_day_columns(day),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _projected(db, seed_user, period, name, amount, due_date):
    """Insert one still-PROJECTED row -- the fold's planned tier."""
    status_id = ref_cache.status_id(StatusEnum.PROJECTED)
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        name=name,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal(str(amount)),
        **settlement_columns(
            default_settle_day(period, status_id), amount, None,
        ),
        due_date=due_date,
        **settle_day_columns(default_settle_day(period, status_id)),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _series(seed_user, first_day, last_day):
    """Return the seam's day-facts series for the seed user's account."""
    return balance_at.cash_daily_facts_series(
        seed_user["account"],
        BalanceContext.build(seed_user["user"].id),
        first_day,
        last_day,
    )


class TestTheThreeTiersSumToTheDaysMovement:
    """The identity, and what breaks it."""

    def test_a_settled_row_lands_wholly_in_RECORDED(
        self, app, seed_user, seed_periods, db,
    ):
        """Cash the app's own row moved is the only tier that saw money move."""
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            db.session.commit()

            facts = _series(
                seed_user, date(2026, 4, 5), date(2026, 4, 7),
            ).facts

            assert facts[date(2026, 4, 6)].recorded == Decimal("-500.00")
            assert facts[date(2026, 4, 6)].asserted == _ZERO
            assert facts[date(2026, 4, 6)].planned == _ZERO

    def test_a_balance_assertion_lands_wholly_in_ASSERTED(
        self, app, seed_user, seed_periods, db,
    ):
        """A true-up is the owner correcting the app, never money moving.

        Reported apart from ``recorded`` because a report that added the two
        would say the bank should have shown the correction -- and would read
        a day where a true-up exactly cancels a real error as agreement.
        """
        with app.app_context():
            append_balance_assertion(
                db.session, seed_user["account"], seed_periods[6],
                Decimal("1300.00"), _instant(2026, 4, 6),
            )
            db.session.commit()

            facts = _series(
                seed_user, date(2026, 4, 5), date(2026, 4, 7),
            ).facts

            assert facts[date(2026, 4, 6)].recorded == _ZERO
            assert facts[date(2026, 4, 6)].asserted == Decimal("300.00")

    def test_the_OPENING_assertion_contributes_NOTHING_to_its_own_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-I puts it in the SEED, so it is level and not movement.

        **A FIRING control.**  Summing every correction by its day -- the
        obvious spelling -- puts the opening's whole delta on the opening's own
        day, which on the developer's real account is ``$798.03`` of movement
        the bank is then expected to explain and never can.  The account's
        first assertion is the ``seed_user`` fixture's own, so every case in
        this file runs over it and only this one names it.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = assertion_corrections(walk)[0]

            facts = _series(
                seed_user, opening.observed_on, opening.observed_on,
            ).facts

            assert opening.delta != _ZERO
            assert facts[opening.observed_on].asserted == _ZERO

    def test_the_three_tiers_sum_to_the_days_change_in_balance(
        self, app, seed_user, seed_periods, db,
    ):
        """The identity, over a span carrying all three kinds at once.

        Asserted over EVERY day of the range rather than the interesting ones,
        because a tier appearing where none of the three accounts for it is
        exactly what this is here to catch, and it would appear on whichever
        day the new behaviour happened to fall on.

        **The range starts at the account's OPENING assertion**, and that is a
        correction this test needed rather than a flourish: written over April
        alone it ran entirely after the opening, so the mutation that stops
        excluding the opening from ``asserted`` left it green -- the seeded
        calendar opens in 2024 and April 2026 never sees that day.  A test of
        an identity must span the one day the identity has a special case on.
        """
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            _settled(
                db, seed_user, seed_periods[6], "Salary", "2000.00",
                date(2026, 4, 9), is_income=True,
            )
            append_balance_assertion(
                db.session, seed_user["account"], seed_periods[7],
                Decimal("2600.00"), _instant(2026, 4, 10),
            )
            _projected(
                db, seed_user, seed_periods[7], "Car", "800.00",
                date(2026, 4, 20),
            )
            db.session.commit()

            opening = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            ).anchor_facts[0]
            first, last = opening.observed_on, date(2026, 4, 30)
            series = _series(seed_user, first - timedelta(days=1), last)
            facts = series.facts

            previous = facts[first - timedelta(days=1)].balance
            for offset in range((last - first).days + 1):
                day = first + timedelta(days=offset)
                fact = facts[day]
                assert fact.balance - previous == (
                    fact.recorded + fact.asserted + fact.planned
                ), f"unattributed movement on {day}"
                previous = fact.balance

    def test_a_still_PROJECTED_row_never_lands_on_a_PAST_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-G: a plan cannot have already happened.

        What lets a reader comparing PAST days against a bank ignore the
        planned tier entirely -- and the reason the books-vs-bank report bounds
        its span at the reader's NOW rather than trusting the statement's dates.
        """
        with app.app_context():
            _projected(
                db, seed_user, seed_periods[0], "Old bill", "125.00",
                date(2026, 1, 5),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            facts = balance_at.cash_daily_facts_series(
                seed_user["account"], ctx, date(2026, 1, 1), ctx.as_of,
            ).facts

            assert all(fact.planned == _ZERO for fact in facts.values())


    def test_a_SECOND_assertion_on_the_opening_day_is_still_a_true_up(
        self, app, seed_user, seed_periods, db,
    ):
        """Only the FIRST correction is the seed; a later one that day is not.

        ``corrections[1:]`` skips exactly one row, and this is the case
        that says whether "one" is the right number: two assertions sharing the
        opening day means the opening is in the seed and the second is an
        ordinary true-up on its own day.  Skipping by DAY instead of by
        position would swallow it, and the identity is what notices.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = walk.anchor_facts[0]
            append_balance_assertion(
                db.session, seed_user["account"], seed_periods[0],
                Decimal("1750.00"),
                _instant(opening.observed_on.year, opening.observed_on.month,
                         opening.observed_on.day, 18),
            )
            db.session.commit()

            reread = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            second = assertion_corrections(reread)[1]
            facts = _series(
                seed_user,
                opening.observed_on - timedelta(days=1),
                opening.observed_on,
            ).facts
            fact = facts[opening.observed_on]

            assert second.observed_on == opening.observed_on
            assert fact.asserted == second.delta
            assert fact.balance - facts[
                opening.observed_on - timedelta(days=1)
            ].balance == fact.recorded + fact.asserted + fact.planned


class TestItCannotDisagreeWithTheBalanceSeriesBesideIt:
    """Two seam entries reporting one day's balance is the drift shape."""

    def test_both_entries_report_the_same_balance_on_every_day(
        self, app, seed_user, seed_periods, db,
    ):
        """``cash_daily_balance_series`` and this one are one running total.

        The codebase has measured what the alternative costs: before plan step
        X-c2b2 the cash scalar and the cash series were separate producers of
        one quantity and stood ``$15.96`` apart on the real Checking account.
        Both entries here sample ``(folded.seed, folded.steps)`` through the
        same :func:`sample_cumulative`, so this asserts a property rather than
        maintaining an agreement -- and it is the assertion that notices if one
        of them ever grows its own assembly.
        """
        with app.app_context():
            _settled(
                db, seed_user, seed_periods[6], "Rent", "500.00",
                date(2026, 4, 6),
            )
            append_balance_assertion(
                db.session, seed_user["account"], seed_periods[6],
                Decimal("1300.00"), _instant(2026, 4, 8),
            )
            _projected(
                db, seed_user, seed_periods[7], "Car", "800.00",
                date(2026, 4, 20),
            )
            db.session.commit()

            first, last = date(2026, 4, 1), date(2026, 4, 30)
            ctx = BalanceContext.build(seed_user["user"].id)
            plain = balance_at.cash_daily_balance_series(
                seed_user["account"], ctx, first, last,
            )
            facts = balance_at.cash_daily_facts_series(
                seed_user["account"], ctx, first, last,
            ).facts

            assert list(plain) == list(facts)
            for day, balance in plain.items():
                assert facts[day].balance == balance


class TestWhereTheAccountsRecordsBegin:
    """The fact a reader needs to tell disagreement from absence."""

    def test_it_is_the_earliest_cash_fact_the_account_has(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled row BEFORE the opening assertion moves it earlier.

        The developer's own account is the case: his records begin 2026-03-26
        with a settled row, one day before the 03-27 assertion, while his bank
        statement starts 2026-01-02.  Reporting those 83 days as disagreements
        would be reporting finding N-314 as this arc's defect.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = walk.anchor_facts[0]
            earlier = opening.observed_on - timedelta(days=10)
            _settled(
                db, seed_user, seed_periods[0], "Early", "20.00", earlier,
            )
            db.session.commit()

            series = _series(seed_user, earlier, opening.observed_on)

            assert series.first_event_on == earlier

    def test_an_INVERTED_range_still_reports_where_records_begin(
        self, app, seed_user, seed_periods, db,
    ):
        """The account HAS a first event whether or not a day was asked about.

        A range yielding no days is not a statement that the account holds no
        records, and answering ``None`` there would let a caller conclude one.
        """
        with app.app_context():
            series = _series(seed_user, date(2026, 4, 10), date(2026, 4, 1))

            assert series.facts == {}
            assert series.first_event_on is not None
