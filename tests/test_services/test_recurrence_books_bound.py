"""A recurring definition names no occurrence below its accounts' books.

Plan step ``pay_calendar:C18-a``.

**The rule, in one sentence** (rulings **R-PC85** and **R-PC86**, developer
2026-09-22): a recurring definition's occurrences run from the LATER of its
own first occurrence and the day after the books open on every account it
moves money in, and the day compared is the ROW's cash day -- its due day --
with a day ON the opening inside it (ruling **R-HG**).

**Why it needed stating at all** -- measured on a production clone before the
step: nothing said "no books before an account opens".  An occurrence with no
paycheck to land in is skipped, so the owner's first payday (2026-03-26) stood
in for the books, and a paycheck recorded at 2026-03-12 let the rules fill it:
a ``$531.94`` Van Payment on 03-22 and a ``$100.00`` birthday on 03-24, both
before Checking's books, moving 112 of 4,745 figures by exactly ``-$631.94``.

What each class covers:

* :class:`TestTheWalkStopsAtTheBooks` -- the pure walk.  One composition
  (``recurrence._placement._placements``) serves both public walks, and these
  pin both, at the boundary from both sides.
* :class:`TestTheCashDayIsCompared` -- ruling R-PC86: a bill scheduled before
  the books but due after them is kept; one due ON the opening is not.
* :class:`TestTheRowDateIsOneDerivation` -- the day the walk bounds by and the
  day ``compute_due_date`` stamps on the row are one body
  (``recurrence._row_day.date_row``); graded against the rule-row reading.
* :class:`TestTheFloorIsTheAccounts` -- the read pass composes the floor from
  every account the definition moves money in, and the pure resolver leaves
  it empty.
* :class:`TestGenerationStopsAtTheBooks` -- the regression PC-500 named: a
  pay period below an account's books is filled with nothing for it.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import RecurrenceUnitEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import (
    RecurrenceSpec,
    compute_due_date,
    occurrence_placements,
    projected_occurrence_placements,
    resolve,
)
from app.services import recurrence_engine
from tests._test_helpers import make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests.test_services.test_recurrence_resolution import build_calendar

_USER_ID = 1

#: A fortnightly calendar opening one cadence BEFORE the production owner's
#: first payday -- the 2026-03-12 prepend ruling R-PC87 makes recordable.
_PREPENDED = date(2026, 3, 12)


def _monthly(day: date, *, due_day_of_month: int | None = None):
    """Return a monthly rule's resolved value on the prepended calendar."""
    return resolve(
        RecurrenceSpec(
            user_id=_USER_ID,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=day,
            due_day_of_month=due_day_of_month,
        ),
        build_calendar(first_payday=_PREPENDED, cadence_days=14, count=12),
    )


def _dates(placements) -> list[date]:
    """Return the occurrence dates of *placements*, in walk order."""
    return [placement.occurrence for placement in placements]


class TestTheWalkStopsAtTheBooks:
    """Both walks drop an occurrence whose row would land on or before the books."""

    def test_an_occurrence_BEFORE_the_books_is_not_walked(self):
        """The Van Payment on 03-22 against Checking's 03-26 opening.

        Unbounded, the prepended 03-12 paycheck places it -- which is the
        PC-500 row.  Bounded, it is gone and the next month's stays.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = _monthly(date(2026, 3, 22))
        assert date(2026, 3, 22) in _dates(
            occurrence_placements(resolved, calendar),
        )

        bounded = replace(resolved, books_opened_on=date(2026, 3, 26))

        walked = _dates(occurrence_placements(bounded, calendar))
        assert date(2026, 3, 22) not in walked
        assert walked[0] == date(2026, 4, 22)

    def test_an_occurrence_ON_the_opening_is_not_walked(self):
        """R-HG's strict reading: the opening is the CLOSE of its day.

        Paired with the case below so the comparison cannot be off by one in
        either direction.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 4, 22),
        )

        walked = _dates(occurrence_placements(bounded, calendar))

        assert date(2026, 4, 22) not in walked
        assert walked[0] == date(2026, 5, 22)

    def test_the_day_AFTER_the_opening_is_walked(self):
        """The other side of the boundary: a day after the books is theirs."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 3, 21),
        )

        assert _dates(occurrence_placements(bounded, calendar))[0] == (
            date(2026, 3, 22)
        )

    def test_the_PROJECTED_walk_drops_the_same_occurrences(self):
        """The loan estimate's walk and generation's cannot disagree.

        Both go through one composition; this pins that the projected one --
        which places past the saved horizon -- agrees with the saved one
        inside it and drops the same pre-books occurrence.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 22)), books_opened_on=date(2026, 3, 26),
        )
        horizon = calendar.horizon()

        saved = _dates(occurrence_placements(bounded, calendar))
        projected = _dates(
            projected_occurrence_placements(bounded, calendar, through=horizon),
        )

        assert date(2026, 3, 22) not in projected
        assert projected == saved

    def test_an_UNPLACED_occurrence_is_kept_as_it_always_was(self):
        """An occurrence below the first payday has no row day and no row.

        ``period`` is ``None`` there under ``CONTAINING_DATE``; no reader
        writes, estimates or counts it (ruling R-R64), and the walk's answer
        for it is unchanged by the bound -- it is below every books floor
        this case states and still listed.
        """
        calendar = build_calendar(
            first_payday=date(2026, 3, 26), cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 22),
            ),
            calendar,
        )
        unbounded = occurrence_placements(resolved, calendar)
        bounded = occurrence_placements(
            replace(resolved, books_opened_on=date(2026, 4, 25)), calendar,
        )

        unplaced = [p.occurrence for p in unbounded if p.period is None]
        assert unplaced == [date(2026, 1, 22), date(2026, 2, 22), date(2026, 3, 22)]
        assert [p.occurrence for p in bounded if p.period is None] == unplaced
        # The one PLACED occurrence on or before the floor is what went.
        assert date(2026, 4, 22) in _dates(unbounded)
        assert date(2026, 4, 22) not in _dates(bounded)

    def test_no_floor_walks_exactly_what_it_walked_before(self):
        """``None`` is the pure resolver's answer and bounds nothing."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = _monthly(date(2026, 3, 22))

        assert resolved.books_opened_on is None
        assert occurrence_placements(
            replace(resolved, books_opened_on=None), calendar,
        ) == occurrence_placements(resolved, calendar)


class TestTheCashDayIsCompared:
    """Ruling R-PC86: the day the money lands, not the day the rule schedules."""

    def test_a_bill_scheduled_BEFORE_the_books_and_due_AFTER_is_kept(self):
        """A card bill scheduled the 25th, due the 15th of the next month.

        Scheduled 03-25, one day before the 03-26 opening; its money lands
        04-15, after it -- so it is a real payment the books do not contain.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 25), due_day_of_month=15),
            books_opened_on=date(2026, 3, 26),
        )

        placements = occurrence_placements(bounded, calendar)

        assert placements[0].occurrence == date(2026, 3, 25)
        assert bounded.row_date(placements[0].period) == date(2026, 4, 15)

    def test_the_same_bill_with_no_separate_due_day_is_dropped(self):
        """Without the due day its money lands on 03-25, inside the opening."""
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        bounded = replace(
            _monthly(date(2026, 3, 25)), books_opened_on=date(2026, 3, 26),
        )

        assert _dates(occurrence_placements(bounded, calendar))[0] == (
            date(2026, 4, 25)
        )

    def test_a_PAYCHECK_cadence_is_bounded_by_its_payday(self):
        """A pay-period rule's row is dated from its paycheck's start.

        The 03-12 paycheck's row lands 03-12, before a 03-26 opening, and the
        03-26 paycheck's lands ON it -- both dropped; 04-09 is the first.
        """
        calendar = build_calendar(
            first_payday=_PREPENDED, cadence_days=14, count=12,
        )
        resolved = resolve(
            RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=_PREPENDED,
            ),
            calendar,
        )
        bounded = replace(resolved, books_opened_on=date(2026, 3, 26))

        assert _dates(occurrence_placements(bounded, calendar))[:2] == [
            date(2026, 4, 9), date(2026, 4, 23),
        ]


class TestTheRowDateIsOneDerivation:
    """The day the walk bounds by is the day the written row carries."""

    def test_row_date_agrees_with_compute_due_date_on_every_placed_period(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Graded against the rule-row reading, across a due-day offset.

        ``compute_due_date`` reads a stored rule; ``row_date`` reads the
        resolved value.  Both hand their coordinates to one body, and this is
        what would fail if either re-derived the day on its own.
        """
        with app.app_context():
            template = _transaction_template(seed_user, "Card bill")
            scheduled = seed_periods[0].start_date + timedelta(days=3)
            # A due day BEFORE the scheduling day: the next-month convention,
            # the branch where the row day and the occurrence part company.
            assert scheduled.day > 1
            rule = _monthly_rule(template, scheduled, due_day_of_month=1)
            ctx = BalanceContext.build(seed_user["user"].id)
            resolved = ctx.resolved_recurrence_of(rule)

            placed = [
                p.period for p in occurrence_placements(resolved, ctx.calendar())
                if p.period is not None
            ]
            assert placed
            for period in placed:
                assert resolved.row_date(period) == compute_due_date(rule, period)


class TestTheFloorIsTheAccounts:
    """The read pass composes the floor; the pure resolver leaves it empty."""

    def test_the_pure_resolver_attaches_no_floor(self):
        """A fact about accounts is not the pure resolver's to read."""
        assert _monthly(date(2026, 3, 22)).books_opened_on is None

    def test_a_transaction_template_takes_its_account_s_opening(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The one account a transaction template moves money in."""
        with app.app_context():
            opened = seed_periods[3].start_date
            account = _account_opened_on(seed_user, "Later books", opened)
            template = _transaction_template(
                seed_user, "Later bill", account_id=account.id,
            )
            rule = _monthly_rule(template, seed_periods[0].start_date)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert ctx.resolved_recurrence_of(rule).books_opened_on == opened

    def test_a_transfer_takes_the_LATER_of_its_two_openings(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Money leaves one account and reaches the other; both books bind.

        The later opening is the binding one, whichever end it is on.
        """
        with app.app_context():
            later = seed_periods[4].start_date
            destination = _account_opened_on(seed_user, "Late savings", later)
            ctx = BalanceContext.build(seed_user["user"].id)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=seed_periods[0].start_date,
            )

            class _Transfer:  # pylint: disable=too-few-public-methods
                """A definition naming two accounts, as a transfer does."""

                from_account_id = seed_user["account"].id
                to_account_id = destination.id

            assert ctx.resolved_for(spec, _Transfer()).books_opened_on == later
            swapped = _Transfer()
            swapped.from_account_id, swapped.to_account_id = (
                destination.id, seed_user["account"].id,
            )
            assert ctx.resolved_for(spec, swapped).books_opened_on == later

    def test_a_definition_moving_no_money_has_no_floor(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A payroll line's rule: no template, no account, no floor."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=seed_periods[0].start_date,
            )

            assert ctx.resolved_for(spec, None).books_opened_on is None


class TestGenerationStopsAtTheBooks:
    """PC-500's regression: no row is generated on or before its books."""

    def test_a_template_on_a_LATER_opened_account_generates_only_above_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The seeded account's books open before the schedule; this one's do not.

        Two templates, one rule each, every paycheck: the one on the seeded
        account fills every period; the one on the account opened at the
        fourth paycheck's payday fills only the paychecks AFTER it -- its
        own opening paycheck included in neither, because a row dated ON the
        opening is inside it.
        """
        with app.app_context():
            opened = seed_periods[3].start_date
            account = _account_opened_on(seed_user, "Later books", opened)
            early = _every_paycheck_template(
                seed_user, "Early", seed_user["account"].id, seed_periods,
            )
            late = _every_paycheck_template(
                seed_user, "Late", account.id, seed_periods,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(seed_user["user"].id),
                {period.id for period in seed_periods},
            )

            recurrence_engine.generate_for_template(
                early, schedule, seed_user["scenario"].id,
            )
            recurrence_engine.generate_for_template(
                late, schedule, seed_user["scenario"].id,
            )
            _db.session.flush()

            def due_days(template):
                return sorted(
                    row.due_date for row in _db.session.query(Transaction)
                    .filter_by(template_id=template.id, is_deleted=False)
                )

            assert due_days(early) == sorted(p.start_date for p in seed_periods)
            assert due_days(late) == sorted(
                p.start_date for p in seed_periods if p.start_date > opened
            )


# ── helpers ──────────────────────────────────────────────────────────────


def _account_opened_on(seed_user, name, opened_on):
    """Create a checking account whose books open on *opened_on*."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name=name,
            anchor_balance=Decimal("0.00"),
            observed_on=opened_on,
        ),
    )
    _db.session.flush()
    return account


def _transaction_template(seed_user, name, *, account_id=None):
    """Create a live, priced expense template on *account_id* (default seeded)."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account_id or seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=Decimal("10.00"),
    )
    _db.session.add(template)
    _db.session.flush()
    # A generated row stores no figure and is priced by this series on its own
    # due date (plan step balance:X-au-e), so a template must state one.
    state_template_price(template)
    return template


def _monthly_rule(template, starts_on, *, due_day_of_month=None):
    """Author a monthly rule onto *template* through the write door."""
    rule = make_cadence_rule(
        template, MONTHLY, starts_on=starts_on,
        due_day_of_month=due_day_of_month,
    )
    _db.session.refresh(template)
    return rule


def _every_paycheck_template(seed_user, name, account_id, seed_periods):
    """Create a template on *account_id* recurring every paycheck."""
    template = _transaction_template(seed_user, name, account_id=account_id)
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    _db.session.refresh(template)
    return template
