"""The feed's reader: one Bridge account answer to what the feed states.

Plan step ``bank_import:X-f6b-2``, the sync leaf; rulings **R-BI30** (the
reader is a source adapter beside the CSV's, pure, never in the file table),
**R-BI22** (what is final; the yesterday cap; the merchant prefix and the MX
category; the derived claim), **R-BI34** (a raw day is a hole: runs around
every raw day, no raw line recorded), **R-BI35** (the claim on the present
window's FIRST run: Bridge's balance minus every line after it), **R-BI36**
(a raw ``$0.00`` line's day is a hole) and **R-BI37** (a categorised line
under an unknown name is raw of the second kind, and named).

Three things are graded here:

* **the measured night reproduces a figure worked by hand** -- the developer's
  2026-09-20 fetch, by day-and-amount structure, gives runs ``09-13..09-15``,
  ``09-17`` and ``09-19``, holes on 09-16 and 09-18, eight held lines (seven
  of them non-zero), and on the FIRST run a claim of ``2073.40 - 265.18 =
  1808.22`` as of 09-15, every line after 09-15 subtracted -- the figure
  the reader gives over the real account
  (:func:`~tests.test_services.test_statement_import._feed_builder.measured_night`
  says how those came to be);
* **the run boundaries**, as a battery looped inside one test (a raw first day,
  a raw last day, two adjacent raw days, a raw day on yesterday, a raw day
  after the cap, every day raw, a cap before the start, a back-walk window);
* **the refusals are designed** -- a non-dollar account, a missing or
  mis-shaped field, an amount that is not a decimal string, a line outside
  the window -- each :class:`~app.exceptions.FeedAnswerUnreadable` naming
  the field for the log and no URL.

The reader is pure, so nothing here touches the database beyond the clone
every test is given.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import FeedAnswerUnreadable
from app.services.statement_import import FeedReading, ParsedStatement, read_account

from . import _feed_builder as build

_TODAY = date(2026, 9, 20)
#: The present window on the measured night: ``[today - 7, tomorrow)``.
_START = date(2026, 9, 13)
_END = date(2026, 9, 21)


def _read(transactions, *, start=_START, end=_END, today=_TODAY, **account):
    """Read one account built over *transactions* for the window."""
    return read_account(
        build.account(transactions, **account),
        window_start=start, window_end=end, today=today,
    )


def _september(day: int) -> date:
    """Return 2026-09-*day*: the battery's month."""
    return date(2026, 9, day)


def _declared(reading: FeedReading) -> list:
    """Return the runs as ``(first, last)`` pairs."""
    return [(run.declared_start, run.declared_end) for run in reading.runs]


class TestTheMeasuredNight:
    """The 2026-09-20 fetch, by structure, through the reader."""

    def test_the_runs_the_holes_and_the_held(self, app, db):
        """Runs around the two raw days; the ``$0.00`` hold counted apart."""
        reading = _read(build.measured_night())

        assert _declared(reading) == [
            (date(2026, 9, 13), date(2026, 9, 15)),
            (date(2026, 9, 17), date(2026, 9, 17)),
            (date(2026, 9, 19), date(2026, 9, 19)),
        ]
        assert reading.holes == (date(2026, 9, 16), date(2026, 9, 18))
        assert reading.unknown_categories == ()
        assert reading.held_count == 7
        assert reading.zero_skipped == 1
        assert [len(run.lines) for run in reading.runs] == [12, 1, 0]

    def test_the_first_run_claims_balance_minus_every_line_after_it(self, app, db):
        """``2073.40 - 265.18 = 1808.22`` as of 09-15, and only there.

        Ruling **R-BI35**: every line posted after the first run's end is
        subtracted -- the four on 09-16, the 09-17 line and the four on
        09-18 (the ``$0.00`` adds nothing) -- held or in a later run alike,
        so the claim is the level the RECORDED lines reach on 09-15 whenever
        Bridge's balance holds every line it lists.  The run the claim sits
        on is the one whose opening the previous nights' coverage prices, so
        the record door can SOLVE it.  (Before the ruling the claim sat on
        the last run as ``balance - held``, ``2073.40 - 286.52 = 1786.88`` as
        of 09-19, whose opening is a hole.)  The figure is the real fetch's:
        the fixture carries its days and amounts line for line.
        """
        reading = _read(build.measured_night())

        first, middle, last = reading.runs
        assert first.stated_balance == Decimal("1808.22")
        assert first.stated_balance_on == date(2026, 9, 15)
        assert (middle.stated_balance, middle.stated_balance_on) == (None, None)
        assert (last.stated_balance, last.stated_balance_on) == (None, None)

    def test_a_back_walk_window_states_no_balance(self, app, db):
        """Bridge's balance is as of NOW; only the present window -- the one
        whose end is tomorrow, read off the window itself -- may claim.  The
        same lines under a window ending today: no claim anywhere."""
        reading = _read(build.measured_night(), end=date(2026, 9, 20))

        assert all(run.stated_balance is None for run in reading.runs)
        assert all(run.stated_balance_on is None for run in reading.runs)
        assert _declared(reading) == [
            (date(2026, 9, 13), date(2026, 9, 15)),
            (date(2026, 9, 17), date(2026, 9, 17)),
            (date(2026, 9, 19), date(2026, 9, 19)),
        ]

    def test_what_a_clean_line_states(self, app, db):
        """The one line shape, filled as ruling R-BI22 says the feed fills it."""
        reading = _read(build.measured_night())

        [line] = [
            line for line in reading.runs[0].lines
            if line.amount == Decimal("-364.60")
        ]
        assert line.posted_on == date(2026, 9, 14)
        assert line.transaction_on is None
        assert line.description == "Power Company Utilities/Gas and Electric"
        assert line.merchant == "Power Company"
        assert line.source_category == "Utilities/Gas and Electric"
        assert line.external_id.startswith("TRN-") and len(line.external_id) == 40
        assert line.running_balance is None
        assert all(
            run.external_account_id == build.CHECKING for run in reading.runs
        )

    def test_every_run_is_a_parsed_statement_in_chronological_order(self, app, db):
        """Bridge lists newest first; a run's lines are oldest first, and a
        day's lines keep Bridge's order reversed, as the CSV adapter's do."""
        same_day = [
            build.clean(date(2026, 9, 14), "-1.00", "First", "Shopping/A"),
            build.clean(date(2026, 9, 14), "-2.00", "Second", "Shopping/B"),
            build.clean(date(2026, 9, 14), "-3.00", "Third", "Shopping/C"),
            build.clean(date(2026, 9, 13), "-4.00", "Earlier", "Shopping/D"),
        ]
        answer = build.account(same_day)
        # Bridge's own order within 09-14: as listed above, reversed by the
        # builder's newest-first sort being stable.
        listed = [item["description"] for item in answer["transactions"]]
        assert listed[-1].startswith("Earlier")

        [run] = read_account(
            answer, window_start=_START, window_end=_END, today=_TODAY,
        ).runs

        assert isinstance(run, ParsedStatement)
        assert [line.merchant for line in run.lines] == [
            "Earlier", "Third", "Second", "First",
        ]

    def test_an_account_with_no_transactions_covers_its_window_and_claims(
        self, app, db,
    ):
        """The Share account: zero lines, one run over the whole span, the
        balance stated as of yesterday (ruling **R-BAL71**: a quiet window
        is covered)."""
        reading = _read([], balance="25.80", external_id=build.SHARE)

        [run] = reading.runs
        assert run.external_account_id == build.SHARE
        assert run.lines == []
        assert (run.declared_start, run.declared_end) == (_START, date(2026, 9, 19))
        assert run.stated_balance == Decimal("25.80")
        assert run.stated_balance_on == date(2026, 9, 19)
        assert reading.holes == ()
        assert (reading.held_count, reading.zero_skipped) == (0, 0)


class TestTheRunBoundaries:
    """Ruling R-BI34's partition, one case per shape, looped in one test."""

    def test_the_battery(self, app, db):
        """Each case: the lines, then the runs, holes and held count expected."""
        d = _september
        filler = [
            build.clean(d(day), "-1.00", "Filler", "Shopping/Filler")
            for day in range(13, 20)
        ]
        cases = {
            "no raw day: one run over the capped span": (
                filler,
                [(d(13), d(19))], (), 0,
            ),
            "a line on today is after the cap: held, not a hole": (
                filler + [build.clean(d(20), "-2.00", "Today", "Shopping/T")],
                [(d(13), d(19))], (), 1,
            ),
            "a raw first day": (
                filler + [build.raw(d(13), "-2.00", "RAW TEXT")],
                [(d(14), d(19))], (d(13),), 2,
            ),
            "a raw last day (yesterday, the cap)": (
                filler + [build.raw(d(19), "-2.00", "RAW TEXT")],
                [(d(13), d(18))], (d(19),), 2,
            ),
            "two adjacent raw days": (
                filler + [build.raw(d(15), "-2.00", "RAW A"), build.raw(d(16), "-2.00", "RAW B")],
                [(d(13), d(14)), (d(17), d(19))], (d(15), d(16)), 4,
            ),
            "a raw day after the cap is not a hole; its lines are held": (
                filler + [build.raw(d(20), "-2.00", "RAW TEXT")],
                [(d(13), d(19))], (), 1,
            ),
            "every day raw: no run, every day a hole": (
                [build.raw(d(day), "-2.00", f"RAW {day}") for day in range(13, 20)],
                [], tuple(d(day) for day in range(13, 20)), 7,
            ),
            "a raw $0.00 line alone makes its day a hole (R-BI36)": (
                filler + [build.raw(d(15), "0.00", "VISA PROVISIONING SERVI")],
                [(d(13), d(14)), (d(16), d(19))], (d(15),), 1,
            ),
            "a clean $0.00 line makes no hole and is not held": (
                filler + [build.clean(d(15), "0.00", "Nothing", "Shopping/Nothing")],
                [(d(13), d(19))], (), 0,
            ),
            "an unknown category is raw: its day a hole (R-BI37)": (
                filler + [build.clean(d(15), "-2.00", "Dr Smith", "Health/Doctor")],
                [(d(13), d(14)), (d(16), d(19))], (d(15),), 2,
            ),
        }
        for name, (lines, runs, holes, held) in cases.items():
            reading = _read(lines)
            assert _declared(reading) == runs, name
            assert reading.holes == holes, name
            assert reading.held_count == held, name

    def test_the_two_kinds_of_raw_are_told_apart_and_only_one_is_named(
        self, app, db,
    ):
        """Ruling **R-BI37**: the bank's own text opens a hole the bank will
        close by posting; a categorised line under a name the code lacks
        opens a hole only a code line closes, so the reading names it."""
        reading = _read([
            build.raw(date(2026, 9, 14), "-9.99", "APPLE.COM/BILL           CUPERTINO    CA"),
            build.clean(date(2026, 9, 16), "-80.00", "Dr Smith", "Health/Doctor"),
            build.clean(date(2026, 9, 16), "-12.00", "Dr Smith", "Health/Doctor"),
            build.clean(date(2026, 9, 18), "-300.00", "Inn", "Travel/Hotel"),
        ])

        assert reading.holes == (date(2026, 9, 14), date(2026, 9, 16), date(2026, 9, 18))
        assert reading.unknown_categories == (
            (date(2026, 9, 16), "Smith Health/Doctor"),
            (date(2026, 9, 18), "Travel/Hotel"),
        )
        assert reading.held_count == 4

    def test_a_zero_line_is_still_never_a_line_of_a_run(self, app, db):
        """A ``$0.00`` line on a run's day is skipped and counted; on a raw
        day it is held with the rest and still counted apart."""
        reading = _read([
            build.clean(date(2026, 9, 14), "0.00", "Nothing", "Shopping/Nothing"),
            build.clean(date(2026, 9, 14), "-5.00", "Something", "Shopping/Thing"),
        ])

        [run] = reading.runs
        assert [line.amount for line in run.lines] == [Decimal("-5.00")]
        assert (reading.held_count, reading.zero_skipped) == (0, 1)

    def test_a_cap_before_the_start_yields_no_run_and_holds_everything(
        self, app, db,
    ):
        """A window asked for today alone: nothing is final yet."""
        reading = _read(
            [build.clean(date(2026, 9, 20), "-1.00", "Today", "Shopping/T")],
            start=date(2026, 9, 20), end=date(2026, 9, 21),
        )

        assert reading.runs == ()
        assert reading.holes == ()
        assert reading.held_count == 1

    def test_a_hole_re_fetch_closes_when_the_day_is_clean(self, app, db):
        """A one-day window in the past, its line now cleaned: one run, no
        claim (it is not the present window), the day declared."""
        reading = _read(
            [build.clean(date(2026, 9, 16), "-9.99", "Apple", "Shopping/Software")],
            start=date(2026, 9, 16), end=date(2026, 9, 17),
        )

        [run] = reading.runs
        assert (run.declared_start, run.declared_end) == (date(2026, 9, 16), date(2026, 9, 16))
        assert [line.merchant for line in run.lines] == ["Apple"]
        assert run.stated_balance is None
        assert reading.holes == ()

    def test_a_hole_re_fetch_stays_open_while_the_line_is_raw(self, app, db):
        """The same window, the line still raw: no run, the hole named."""
        reading = _read(
            [build.raw(date(2026, 9, 16), "-9.99", "APPLE.COM/BILL           CUPERTINO    CA")],
            start=date(2026, 9, 16), end=date(2026, 9, 17),
        )

        assert reading.runs == ()
        assert reading.holes == (date(2026, 9, 16),)
        assert reading.held_count == 1

    def test_a_hole_re_fetch_that_comes_back_empty_declares_the_day(self, app, db):
        """Ruling R-BI34's other closing: the raw line vanished.  One run
        over the day, no line, no claim -- the day is covered, empty."""
        reading = _read([], start=date(2026, 9, 16), end=date(2026, 9, 17))

        [run] = reading.runs
        assert (run.declared_start, run.declared_end) == (date(2026, 9, 16), date(2026, 9, 16))
        assert run.lines == []
        assert (run.stated_balance, run.stated_balance_on) == (None, None)
        assert reading.holes == ()


class TestTheSplit:
    """Ruling R-BI22's merchant prefix and category, on the measured shapes."""

    def test_the_rule_over_the_measured_shapes(self, app, db):
        """The EARLIEST marker in the text wins (so ``Services`` inside
        ``Financial Services`` names nothing, and a second category later in
        the text stays in the first one's string), two slashes stay in the
        category, a known category with NO merchant is clean and names none
        (``Financial Services/...`` alone once split at ``Services`` with
        ``Financial`` the merchant: the second pass's L1), and the bank's
        text and an unknown category are raw."""
        cases = {
            "Big Store Financial Services/Credit Card Payment": (
                "Big Store", "Financial Services/Credit Card Payment",
            ),
            "Pizza Food & Drink/Restaurants Financial Services/X": (
                "Pizza", "Food & Drink/Restaurants Financial Services/X",
            ),
            "Mortgage Co Home/Mortgage/Rent": ("Mortgage Co", "Home/Mortgage/Rent"),
            "Cracker Barrel Food & Drink/Dining Out": (
                "Cracker Barrel", "Food & Drink/Dining Out",
            ),
            "Home Depot Shopping/Home Improvement": (
                "Home Depot", "Shopping/Home Improvement",
            ),
            "Shopping/Clothing": (None, "Shopping/Clothing"),
            "Financial Services/Credit Card Payment": (
                None, "Financial Services/Credit Card Payment",
            ),
            "Food & Drink/Groceries": (None, "Food & Drink/Groceries"),
            "McDonald's Food & Drink/Fast Food": ("McDonald's", "Food & Drink/Fast Food"),
            "AT&T Utilities/Phone": ("AT&T", "Utilities/Phone"),
            "Some Place Unknown Category/Thing": None,
            "POINT OF SALE DEBIT L340 DATE 09-16": None,
        }
        named = {
            "Some Place Unknown Category/Thing": "Place Unknown Category/Thing",
        }
        for description, expected in cases.items():
            reading = _read([build.line(date(2026, 9, 15), "-5.00", description)])
            if expected is None:
                assert reading.runs and reading.holes == (date(2026, 9, 15),), description
                assert reading.held_count == 1, description
                assert reading.unknown_categories == (
                    ((date(2026, 9, 15), named[description]),)
                    if description in named else ()
                ), description
            else:
                [line] = [line for run in reading.runs for line in run.lines]
                assert (line.merchant, line.source_category) == expected, description
                assert line.description == description


class TestTheCivilDay:
    """``posted`` is an instant; the day is the display timezone's."""

    def test_a_late_evening_eastern_posting_keeps_its_eastern_day(self, app, db):
        """03:00 UTC on 09-16 is 23:00 on 09-15 in America/New_York."""
        reading = _read([
            build.line(date(2026, 9, 16), "-5.00", "Late Shopping/Night", hour_utc=3),
        ])

        [line] = [line for run in reading.runs for line in run.lines]
        assert line.posted_on == date(2026, 9, 15)


class TestTheRefusals:
    """Every refusal is designed: the class, the field, no URL."""

    def test_an_account_not_in_dollars_is_refused(self, app, db):
        """The mortgage in EUR, say: refused before a line is read."""
        with pytest.raises(FeedAnswerUnreadable) as caught:
            _read(build.measured_night(), currency="EUR")

        assert caught.value.field == "currency"
        assert caught.value.log_details == {
            "field": "currency", "problem": "currency",
        }
        assert "'EUR'" in str(caught.value) and "http" not in str(caught.value)

    def test_a_currency_string_is_bounded_in_the_sentence(self, app, db):
        """Bridge's text reaches a flash; eight characters of it, no more."""
        with pytest.raises(FeedAnswerUnreadable) as caught:
            _read([], currency="X" * 200)

        assert "'XXXXXXXX'" in str(caught.value)
        assert "X" * 9 not in str(caught.value)

    def test_a_missing_or_mis_shaped_field_names_itself(self, app, db):
        """Each field the reader needs, absent or the wrong shape, in a loop."""
        good = build.account(build.measured_night())
        account_cases = {
            "currency": [None, 7],
            "id": [None, "", 7],
            "balance": [None, 2073.40, "abc", "NaN", "1E+40"],
            "transactions": [None, "not a list", {"id": 1}],
        }
        for field, values in account_cases.items():
            for value in values:
                item = dict(good)
                if value is None:
                    del item[field]
                else:
                    item[field] = value
                with pytest.raises(FeedAnswerUnreadable) as caught:
                    read_account(
                        item, window_start=_START, window_end=_END,
                        today=_TODAY,
                    )
                assert caught.value.field == field, (field, value)
                assert caught.value.problem == (
                    "missing" if value is None else "shape"
                ), (field, value)

        line_cases = {
            "id": [None, "", 7],
            "posted": [None, 0, -1, True, "1789732800", 10**20],
            "amount": [None, -9.99, "abc", "NaN", "Infinity", "1E+40"],
            "description": [None, "", 7],
        }
        for field, values in line_cases.items():
            for value in values:
                transaction = dict(good["transactions"][0])
                if value is None:
                    del transaction[field]
                else:
                    transaction[field] = value
                with pytest.raises(FeedAnswerUnreadable) as caught:
                    _read([transaction])
                assert caught.value.field == field, (field, value)
                assert caught.value.problem == (
                    "missing" if value is None else "shape"
                ), (field, value)

    def test_a_line_outside_the_window_is_refused(self, app, db):
        """Bridge honours the window it was asked; an answer that does not
        cannot be trusted to have delivered every line the claim needs."""
        with pytest.raises(FeedAnswerUnreadable) as caught:
            _read([
                build.clean(date(2026, 9, 12), "-1.00", "Before", "Shopping/B"),
                build.clean(date(2026, 9, 21), "-1.00", "AtTheEnd", "Shopping/E"),
            ])

        assert caught.value.field == "posted"
        assert caught.value.problem == "outside_window"
        assert "2 line(s)" in str(caught.value)

    def test_a_transaction_that_is_not_an_object_is_refused(self, app, db):
        """A bare string in the list has no fields to read."""
        with pytest.raises(FeedAnswerUnreadable) as caught:
            _read(["not an object"])

        assert caught.value.field == "id"
