"""The books may not be restated past a still-projected recurring row (ruling R-PC88).

Plan step ``pay_calendar:C18-a``.  Ruling **R-PC85** made a recurring
definition's occurrences start above the books of every account it moves money
in, compared on the row's due day with a day ON the opening inside it (ruling
**R-PC86**).  So moving an account's books LATER past a recurring row that is
still Projected -- an unpaid, overdue bill the rule generated -- would leave
that row named by no occurrence, and the maintain pass retires such a row the
next time it re-runs the rule over its paycheck, raising the forecast without a
word.  Josh ruled the door refuses instead (2026-09-22, "Refuse the move"): the
owner marks the row paid, cancels it or moves it, then restates the books.

Each account here is built with :func:`~tests._test_helpers.account_never_asserted`
and an opening placed before the seeded schedule (2026-01-02 onward), so the
restatement's OTHER record rules -- movements, matched lines, assertions --
have nothing to refuse and the planned-row rule is the one graded.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import recurrence_engine, status_seam, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import account_opening_fact
from app.services.generation_schedule import GenerationSchedule
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from tests._test_helpers import (
    account_never_asserted,
    make_cadence_rule,
    one_off_row_of,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

_ONE_DAY = timedelta(days=1)

#: Where the test accounts' books open: before the seeded schedule, so every
#: generated row starts above them.
_EARLY_OPENING = date(2025, 12, 1)


class TestAProjectedRecurringRowBoundsTheRestatement:
    """R-PC88 at the boundary, from both sides, for both row kinds."""

    def test_the_rows_due_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Opening ON the first row's due day would put the row inside it."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            before = _opening_count(account)

            with pytest.raises(ValidationError, match=r"still projected and due"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            # Refused before anything is staged: no restatement row appended.
            assert _opening_count(account) == before

    def test_the_day_BEFORE_the_rows_due_day_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side, so a ``<`` written for ``<=`` fails one of the pair."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            day = min(row.due_date for row in rows) - _ONE_DAY

            outcome = apply_opening_restatement(
                account=account, opening=BooksOpening(day, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(account.id).opened_on == day

    def test_the_refusal_names_the_row_and_its_due_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The owner is told WHICH row and WHEN, so the repair is findable."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(
                        first.due_date + _ONE_DAY, Decimal("0.00"),
                    ),
                )

            message = str(refused.value)
            assert first.name in message
            assert first.due_date.isoformat() in message

    def test_a_CANCELLED_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Cancelling is one of the three repairs the refusal names."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            status_seam.apply_status_change(
                first, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            _db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_SOFT_DELETED_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A row the owner removed is not a live claim of money owed."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            first.is_deleted = True
            _db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_HAND_ENTERED_projected_row_bounds_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """No rule names a hand-entered row, so nothing would retire it.

        R-PC88 is about RECURRING rows.  A one-off mints a RULE-LESS
        definition (plan step balance:X-bi-7c), so it IS template-linked --
        which is exactly the class a "template_id is not null" predicate
        would wrongly refuse.
        """
        with app.app_context():
            account = _account_opened_early(seed_user)
            one_off = one_off_row_of(
                seed_periods[1], name="Typed by hand", amount=Decimal("12.00"),
                user_id=seed_user["user"].id, account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            _db.session.flush()
            assert one_off.template_id is not None
            assert one_off.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    seed_periods[1].start_date, Decimal("0.00"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_a_projected_recurring_TRANSFER_into_the_account_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A transfer moves money in BOTH accounts; the destination's books bind.

        The account here is the transfer's DESTINATION, the side a
        transaction-only predicate would miss.
        """
        with app.app_context():
            account = _account_opened_early(seed_user)
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=account.id,
                name="Monthly save",
                default_amount=Decimal("50.00"),
            )
            _db.session.add(template)
            _db.session.flush()
            state_template_price(template)
            make_cadence_rule(
                template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
            )
            _db.session.refresh(template)
            created = transfer_recurrence.generate_for_template(
                template, _schedule(seed_user, seed_periods),
                seed_user["scenario"].id,
            )
            _db.session.flush()
            first_due = min(transfer.due_date for transfer in created)

            with pytest.raises(ValidationError, match=r"Monthly save"):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )


# ── helpers ──────────────────────────────────────────────────────────────


def _opening_count(account):
    """Return how many opening records *account* carries."""
    return _db.session.query(AccountOpening).filter_by(
        account_id=account.id,
    ).count()


def _account_opened_early(seed_user):
    """Return a never-asserted account whose books open before the schedule."""
    account = account_never_asserted(
        seed_user, _db.session, name="Planned-rows account",
    )
    _db.session.flush()
    _db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=_EARLY_OPENING,
        opening_equity=Decimal("0.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    _db.session.flush()
    return account


def _schedule(seed_user, seed_periods):
    """Return a generation schedule over every seeded period."""
    return GenerationSchedule.for_period_ids(
        BalanceContext.build(seed_user["user"].id),
        {period.id for period in seed_periods},
    )


def _account_with_projected_rows(seed_user, seed_periods):
    """Return an early-opened account and the Projected rows its rule generated."""
    account = _account_opened_early(seed_user)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Recurring rent",
        default_amount=Decimal("10.00"),
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    _db.session.refresh(template)
    rows = recurrence_engine.generate_for_template(
        template, _schedule(seed_user, seed_periods), seed_user["scenario"].id,
    )
    _db.session.flush()
    assert rows, "the fixture must generate rows for the rule to bound"
    return account, rows
