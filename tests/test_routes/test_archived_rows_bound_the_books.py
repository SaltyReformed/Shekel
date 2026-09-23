"""An archived definition's hidden rows bound its accounts' books (ruling R-PC93).

Plan step ``pay_calendar:C18-a``, the round-2 adversarial review's H-B.
Archiving a recurring definition hides its still-Projected rows and
unarchiving brings them back, so a books move made while it was archived saw
nothing to strand: measured before the fix, the restatement COMMITTED, the
transaction template's unarchive restored a row below the new books, and the
transfer template's unarchive -- whose maintain pass retires a row the rule no
longer names -- HARD-DELETED the current paycheck's transfer.  Josh ruled
2026-09-23, "Refuse the books move": the rows an unarchive would restore count
as planned rows at every door that moves a definition's books, and the
refusal names such a row as the archived definition's with the remedy that
reaches it.

The archive and the unarchive run through their ROUTES here, so what they
hide and restore is theirs and not a fixture's imitation; the restatement runs
through its service door, whose refusal is what R-PC93 changes.
"""
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes.accounts import opening as opening_route
from app.services import planned_rows_books, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceResolutionError
from app.utils.balance_predicates import is_projected_clause
from tests._test_helpers import make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests.test_services.test_opening_restatement_planned_rows import (
    _ONE_DAY,
    _account_opened_early,
    _account_with_projected_rows,
    _opening_count,
    _schedule,
)


class TestATransactionDefinitionsHiddenRows:
    """The archive hides the rows; the books still may not move past them."""

    def test_the_first_hidden_rows_due_day_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The review's probe, refused: the row is named as the archived definition's."""
        with app.app_context():
            account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            before = _opening_count(account)

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            message = str(refused.value)
            assert (
                f'the recurring "Recurring rent" is archived and still holds an '
                f"unpaid item due {first_due.isoformat()} that unarchiving "
                "would bring back"
            ) in message
            assert (
                "Unarchive it and mark it paid, cancel it or move it later, or "
                'delete "Recurring rent" for good, then restate the books.'
            ) in message
            _db.session.rollback()
            assert _opening_count(account) == before
            assert _hidden_rows(template_id), "the rows are still hidden"

    def test_the_day_BEFORE_it_is_accepted_and_the_unarchive_restores_above_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The other side of the boundary, and the harm it prevents stays prevented.

        The books move to the day before the first hidden row; the unarchive
        then restores every row it hid, and none of them lands on or before
        the books.
        """
        with app.app_context():
            account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            hidden = {row.id for row in _hidden_rows(template_id)}

            outcome = apply_opening_restatement(
                account=_fresh(account),
                opening=BooksOpening(first_due - _ONE_DAY, Decimal("0.00")),
            )
            assert outcome is OpeningRestatementOutcome.COMMITTED
            resp = auth_client.post(f"/templates/{template_id}/unarchive")

            assert resp.status_code == 302
            _db.session.expire_all()
            assert {row.id for row in _live_rows(template_id)} == hidden
            assert planned_rows_books.first_row_an_opening_strands(
                account.id, first_due - _ONE_DAY,
                calendar_for(seed_user["user"].id),
            ) is None

    def test_a_row_deleted_by_hand_BEFORE_the_archive_counts_too(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """THE test that fails if the refusal and the unarchive disagree about their rows.

        An unarchive restores EVERY still-Projected soft-deleted row of its
        definition, including one its owner deleted by hand while it was
        active -- so the refusal must count that row as well, and does,
        because both read ``definition_unarchive.rows_an_unarchive_restores``.
        Refused while archived, then unarchived: the hand-deleted row IS
        restored, which is the fact the refusal counted on.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            first.is_deleted = True
            _db.session.commit()
            template_id, first_id, first_due = (
                first.template_id, first.id, first.due_date,
            )
            assert auth_client.post(
                f"/templates/{template_id}/archive",
            ).status_code == 302

            with pytest.raises(ValidationError, match=r"is archived"):
                apply_opening_restatement(
                    account=_fresh(account),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )
            _db.session.rollback()
            assert auth_client.post(
                f"/templates/{template_id}/unarchive",
            ).status_code == 302

            _db.session.expire_all()
            assert first_id in {row.id for row in _live_rows(template_id)}

    def test_the_edit_door_counts_them_and_an_ACTIVE_definitions_deletions_do_not(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """R-PC93 at the edit doors: an archived definition's account move is refused.

        The control is the same move on an ACTIVE definition whose rows its
        owner deleted by hand: nothing restores those but the conflict
        chooser (ledger row REC-535), so they are not planned rows.
        """
        with app.app_context():
            _account, template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )
            later = _account_opened_early(seed_user, name="Later books")
            _restate_directly(later, first_due)
            template = _db.session.get(
                Transaction, _hidden_rows(template_id)[0].id,
            ).template
            template.account_id = later.id

            refusal = planned_rows_books.definition_edit_refusal(
                template, BalanceContext.build(seed_user["user"].id),
            )

            assert refusal is not None
            assert '"Recurring rent" is archived and still holds' in refusal
            template.is_active = True
            _db.session.flush()
            assert planned_rows_books.definition_edit_refusal(
                template, BalanceContext.build(seed_user["user"].id),
            ) is None, "an active definition's deleted rows are not planned"

    def test_the_cards_ceiling_stops_before_the_hidden_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The books-opening card reads the same producer, hidden rows included."""
        with app.app_context():
            account, _template_id, first_due = _archived_rent(
                auth_client, seed_user, seed_periods,
            )

            ceiling = opening_route.books_opening_context(_fresh(account))["ceiling"]

            assert ceiling.day == first_due - _ONE_DAY
            assert "is archived and still holds" in ceiling.said
            assert "Unarchive it and mark it paid" in ceiling.said


class TestATransferDefinitionsHiddenRows:
    """The review's transfer case: the unarchive that hard-deleted a row cannot be reached."""

    def test_a_books_move_past_a_hidden_transfer_is_refused_and_nothing_is_deleted(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Refused naming the archived transfer; the unarchive then restores every row."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Hidden savings")
            template = _monthly_save_into(seed_user, seed_periods, savings)
            template_id = template.id
            every_id = {
                transfer.id for transfer in _transfers(template_id, deleted=False)
            }
            first_due = min(
                transfer.due_date for transfer in _transfers(template_id)
            )
            assert auth_client.post(
                f"/transfers/{template_id}/archive",
            ).status_code == 302

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(savings),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )
            assert '"Monthly save" is archived and still holds' in str(
                refused.value,
            )
            _db.session.rollback()
            assert auth_client.post(
                f"/transfers/{template_id}/unarchive",
            ).status_code == 302

            _db.session.expire_all()
            assert {
                transfer.id for transfer in _transfers(template_id, deleted=False)
            } == every_id


class TestAnUnwalkableRuleCostsTheCardNotThePage:
    """Round 2's L-b: the account edit page is the only door to rename, archive or delete."""

    def test_the_edit_page_renders_without_the_card(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A rule the recurrence package refuses hides the card; the page still renders."""
        with app.app_context():
            account = _account_opened_early(seed_user)
            _db.session.commit()

            def refusing(*_args, **_kwargs):
                raise RecurrenceResolutionError("a rule no door writes")

            monkeypatch.setattr(
                opening_route, "first_row_an_opening_strands", refusing,
            )

            assert opening_route.books_opening_context(_fresh(account)) is None
            resp = auth_client.get(f"/accounts/{account.id}/edit")
            assert resp.status_code == 200
            assert account.name.encode() in resp.data


# ── helpers ──────────────────────────────────────────────────────────────


def _archived_rent(auth_client, seed_user, seed_periods):
    """An every-paycheck rent on an early-opened account, archived through its route.

    Returns:
        ``(account, template_id, first_due)`` -- the first row's due day is
        the first day a books move would strand it.
    """
    account, rows = _account_with_projected_rows(seed_user, seed_periods)
    _db.session.commit()
    template_id = rows[0].template_id
    first_due = min(row.due_date for row in rows)
    assert auth_client.post(f"/templates/{template_id}/archive").status_code == 302
    _db.session.expire_all()
    assert not _live_rows(template_id), "precondition: the archive hid every row"
    return account, template_id, first_due


def _monthly_save_into(seed_user, seed_periods, savings):
    """An every-paycheck transfer from checking into *savings*, its rows generated."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
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
        template, _schedule(seed_user, seed_periods), seed_user["scenario"].id,
    )
    assert created, "the fixture must generate transfers"
    _db.session.commit()
    return template


def _restate_directly(account, day):
    """Open *account*'s books on *day* through the service door, committed."""
    outcome = apply_opening_restatement(
        account=_fresh(account), opening=BooksOpening(day, Decimal("0.00")),
    )
    assert outcome is OpeningRestatementOutcome.COMMITTED


def _fresh(account):
    """Re-read *account* in the current session."""
    return _db.session.get(Account, account.id)


def _live_rows(template_id):
    """The template's live, still-Projected transactions."""
    return _db.session.query(Transaction).filter(
        Transaction.template_id == template_id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(False),
    ).all()


def _hidden_rows(template_id):
    """The template's soft-deleted, still-Projected transactions."""
    return _db.session.query(Transaction).filter(
        Transaction.template_id == template_id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(True),
    ).order_by(Transaction.due_date).all()


def _transfers(template_id, *, deleted=None):
    """The template's still-Projected transfers, optionally by deleted state."""
    query = _db.session.query(Transfer).filter(
        Transfer.transfer_template_id == template_id,
        is_projected_clause(Transfer),
    )
    if deleted is not None:
        query = query.filter(Transfer.is_deleted.is_(deleted))
    return query.all()
