"""A planned row is judged against the books of the account it SITS ON (ruling R-PC99).

Plan step ``pay_calendar:C18-a``, the round-6 review's H1 (developer
2026-09-23, "Judge rows where they sit").  A definition's account move leaves
the rows of paychecks that had already ended on the account it left, which
the balance counts them in; the books doors judged every row by its
DEFINITION's books, so the old account's restatement past such rows committed
with them counted a second time (measured ``-$100.00`` against the books
rule's ``-$80.00``).  Every door now asks two questions of a planned row:
whether the books of the account it sits on hold its own day
(``definition_unarchive.own_day_held``), and whether its definition's walk
drops its occurrence.  A row only the walk drops, sitting on another account,
is refused for the pass that would delete it and never described as sitting
inside books it does not sit on.

The rows "left behind" here are made the way an account move leaves the rows
no maintain pass reached: the definition's account column re-pointed, its
rows where they were (``_moved_to_a_new_account``).

Also here, the round-6 review's M1: the arms no test failed when broken --
an envelope's paycheck's last day, a TRANSFER's rows at the opening door, the
revert and the unarchive, and the undated carve-out -- lifted from the
review's probes; its L3 (a definition with no books floor is not walked);
and the loader's two pre-filter halves.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.services import definition_unarchive, status_seam
from app.services.balance_at import BalanceContext
from app.services.definition_unarchive import (
    books_reading,
    rows_held_where_they_sit,
    unarchive_scope_on,
)
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.pay_calendar import calendar_for
from app.services.planned_rows_books import (
    definition_edit_refusal,
    reject_revert_below_the_books,
)
from app.services.recurring_definition import resolved_rule_of
from tests.test_routes.test_a_revert_below_the_books import _projected
from tests.test_routes.test_an_orphan_is_judged_by_its_own_day import (
    _open_books_directly,
    _orphaned,
    _rows_of,
)
from tests.test_routes.test_archived_rows_bound_the_books import (
    _flashed,
    _fresh,
    _monthly_save_into,
    _restate_directly,
    _transfers,
)
from tests.test_routes.test_definition_edit_strands_no_row import _account_opened_on
from tests.test_services.test_opening_restatement_planned_rows import (
    _ONE_DAY,
    _account_opened_early,
    _account_with_projected_rows,
    _moved_to_a_new_account,
)

#: The sentence a row only its schedule drops is refused with (R-PC99).
_THE_PASS_WOULD_DELETE_IT = (
    "Its schedule would stop producing that unpaid item, so the next pass to "
    "reach it would delete it without a word."
)


class TestTheOldAccountsRestatement:
    """The books of the account a row was left on hold it (R-PC99)."""

    def test_is_refused_over_ORPHANS_left_there(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The review's probe P6: orphans on a moved-off account, restated onto the second."""
        with app.app_context():
            old, rows = _account_with_projected_rows(seed_user, seed_periods)
            (first, second), template = _orphaned(rows)
            _moved_to_a_new_account(seed_user, template)
            _db.session.commit()

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(old),
                    opening=BooksOpening(second.due_date, Decimal("0.00")),
                )

            assert (
                f'"Recurring rent" is still projected and due '
                f"{first.due_date.isoformat()}.  An opening is the balance at "
                "the END of its day, so that unpaid item would sit inside it."
            ) in str(refused.value)

    def test_the_NEW_accounts_restatement_says_the_pass_would_delete_them(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Its walk drops the rows, which sit on the OLD account: never "inside" these books."""
        with app.app_context():
            _old, rows = _account_with_projected_rows(seed_user, seed_periods)
            new = _moved_to_a_new_account(seed_user, rows[0].template)
            first_due = min(row.due_date for row in rows)
            _db.session.commit()

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(new),
                    opening=BooksOpening(first_due, Decimal("0.00")),
                )

            assert (
                f'"Recurring rent" is still projected and due '
                f"{first_due.isoformat()}.  {_THE_PASS_WOULD_DELETE_IT}"
            ) in str(refused.value)
            assert "would sit inside it" not in str(refused.value)


    def test_a_definition_that_moved_off_is_asked_only_where_its_rows_sit(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Its schedule is bounded by books this restatement does not move.

        The definition moved onto books opening on its first row's day, so
        its walk ALREADY drops that row -- a matter for those books' doors.
        Restating the old account onto the day before the row holds nothing
        sitting there, and is committed.
        """
        with app.app_context():
            old, rows = _account_with_projected_rows(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            later = _account_opened_early(seed_user, name="Later books")
            _open_books_directly(later, first_due)
            rows[0].template.account_id = later.id
            _db.session.commit()

            outcome = apply_opening_restatement(
                account=_fresh(old),
                opening=BooksOpening(first_due - _ONE_DAY, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED


    def test_a_row_another_accounts_books_hold_does_not_block_this_one(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Only the rows sitting on the restated account are asked where they sit.

        The rows were left on the old account, whose books are recorded
        opening on the first one; restating the NEW account onto a day before
        every row moves none of those books, and is committed.
        """
        with app.app_context():
            old, rows = _account_with_projected_rows(seed_user, seed_periods)
            new = _moved_to_a_new_account(seed_user, rows[0].template)
            first_due = min(row.due_date for row in rows)
            _open_books_directly(old, first_due)
            _db.session.commit()

            outcome = apply_opening_restatement(
                account=_fresh(new),
                opening=BooksOpening(first_due - _ONE_DAY, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED


class TestTheEditDoor:
    """An ordinary row left behind still blocks the move, for the pass (R-PC99's words)."""

    def test_moving_onto_books_opening_on_a_row_left_behind_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk under the NEW books drops it; it sits on the old account, outside them."""
        with app.app_context():
            _old, rows = _account_with_projected_rows(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            later = _account_opened_on(seed_user, "Later books", first_due)
            template = rows[0].template
            template.account_id = later.id
            _db.session.flush()

            refusal = definition_edit_refusal(
                template, BalanceContext.build(seed_user["user"].id), None,
            )

            assert refusal == (
                f'This change cannot be saved: "Recurring rent" is still '
                f"projected and due {first_due.isoformat()}, and Later books's "
                f"books open {first_due.isoformat()}.  "
                f"{_THE_PASS_WOULD_DELETE_IT}  Mark it paid or cancel it "
                "first, then make the change."
            )

    def test_the_same_move_onto_books_opening_the_day_before_is_saved(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side of the boundary.

        Recorded directly: no account can be CREATED with books before the
        owner's first payday, which this day is.
        """
        with app.app_context():
            _old, rows = _account_with_projected_rows(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            later = _account_opened_early(seed_user, name="Later books")
            _open_books_directly(later, first_due - _ONE_DAY)
            template = rows[0].template
            template.account_id = later.id
            _db.session.flush()

            assert definition_edit_refusal(
                template, BalanceContext.build(seed_user["user"].id), None,
            ) is None


class TestTheRevert:
    """A settled row left on the old account is judged against ITS books (R-PC99)."""

    def test_a_cancelled_row_inside_the_old_accounts_books_stays_cancelled(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The refusal names the books it sits in, not the definition's new account's."""
        with app.app_context():
            old, first = _cancelled_first_row_left_on_the_old_account(
                seed_user, seed_periods,
            )
            _open_books_directly(old, first.due_date)

            with pytest.raises(ValidationError) as refused:
                reject_revert_below_the_books(first, _projected())

            assert str(refused.value) == (
                f'"Recurring rent" is due {first.due_date.isoformat()}, on or '
                f"before Planned-rows account's books, which open "
                f"{first.due_date.isoformat()}, so it cannot be planned as "
                "unpaid."
            )

    def test_the_same_revert_with_books_opening_the_day_before_is_allowed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other side of the boundary."""
        with app.app_context():
            old, first = _cancelled_first_row_left_on_the_old_account(
                seed_user, seed_periods,
            )
            _open_books_directly(old, first.due_date - _ONE_DAY)

            reject_revert_below_the_books(first, _projected())


class TestTheUnarchive:
    """Hidden rows left on the old account, named by the books that hold each (R-PC99)."""

    def test_rows_inside_the_old_accounts_books_stay_deleted_and_are_named(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The notice names the account they sit on."""
        with app.app_context():
            template_id, old, (first, second) = _archived_then_moved(
                auth_client, seed_user, seed_periods,
            )
            _moved_to_a_new_account(seed_user, _fresh_template(template_id))
            _open_books_directly(old, second.due_date)
            _db.session.commit()

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _flashed(
                f"2 items due {first.due_date.isoformat()} to "
                f"{second.due_date.isoformat()} stay deleted: they fall inside "
                f"Planned-rows account's books, which open "
                f"{second.due_date.isoformat()}."
            ) in resp.data
            _assert_only_deleted(template_id, {first.id, second.id})

    def test_rows_only_the_walk_drops_stay_deleted_for_their_schedule(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Moved onto books opening on the second row's day: they sit outside them."""
        with app.app_context():
            template_id, _old, (first, second) = _archived_then_moved(
                auth_client, seed_user, seed_periods,
            )
            later = _account_opened_on(seed_user, "Later books", second.due_date)
            _fresh_template(template_id).account_id = later.id
            _db.session.commit()

            resp = auth_client.post(
                f"/templates/{template_id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _flashed(
                f"2 items due {first.due_date.isoformat()} to "
                f"{second.due_date.isoformat()} stay deleted: their schedule "
                "no longer produces them, since Later books's books open "
                f"{second.due_date.isoformat()}."
            ) in resp.data
            _assert_only_deleted(template_id, {first.id, second.id})


class TestEachArmOfTheDoors:
    """The round-6 review's M1: each arm a mutation survived, now failing a test."""

    def test_a_transfers_orphan_is_refused_at_the_opening_door(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Probe P1: a transfer row sits on both its accounts."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Probe savings")
            ordered = _orphan_transfers(
                _monthly_save_into(seed_user, seed_periods, savings).id,
            )

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=_fresh(savings),
                    opening=BooksOpening(ordered[0].due_date, Decimal("0.00")),
                )

            assert f"due {ordered[0].due_date.isoformat()}" in str(refused.value)

    def test_a_transfers_orphan_is_not_reverted_inside_its_books(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Probe P2, through the transfer's one status door."""
        with app.app_context():
            savings, first_id, first_due = _paid_first_transfer(
                auth_client, seed_user, seed_periods,
            )
            _orphan_transfers(_db.session.get(Transfer, first_id).transfer_template_id)
            _db.session.commit()
            _restate_directly(savings, first_due)

            resp = auth_client.patch(
                f"/transfers/instance/{first_id}",
                data={"status_id": str(_projected())},
            )

            assert resp.status_code == 400

    def test_an_UNDATED_paid_transfer_may_be_reverted(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Probe P5: ruling R-PC96 judges an undated row at the unarchive alone."""
        with app.app_context():
            savings, first_id, first_due = _paid_first_transfer(
                auth_client, seed_user, seed_periods,
            )
            _db.session.get(Transfer, first_id).occurs_on = None
            _db.session.commit()
            _restate_directly(savings, first_due)

            resp = auth_client.patch(
                f"/transfers/instance/{first_id}",
                data={"status_id": str(_projected())},
            )

            assert resp.status_code == 200

    def test_an_envelope_orphan_is_accepted_on_its_payday(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Probe P3: an envelope's row is compared on its paycheck's LAST day (R-PC89)."""
        with app.app_context():
            account, rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            (first, _second), _template = _orphaned(rows)

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED

    def test_an_envelope_orphan_is_refused_on_its_paychecks_last_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Probe P4, the other side."""
        with app.app_context():
            account, rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            _orphaned(rows)
            last_day = seed_periods[1].start_date - _ONE_DAY

            with pytest.raises(ValidationError) as refused:
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(last_day, Decimal("0.00")),
                )

            assert (
                f"in the paycheck ending {last_day.isoformat()}"
            ) in str(refused.value)

    def test_a_hidden_transfer_orphan_stays_deleted_at_the_unarchive(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The unarchive's scope asks a transfer's rows where they sit too."""
        with app.app_context():
            savings = _account_opened_early(seed_user, name="Probe savings")
            template = _monthly_save_into(seed_user, seed_periods, savings)
            ordered = _orphan_transfers(template.id)
            for row in ordered:
                row.is_deleted = True
            template.is_active = False
            _open_books_directly(savings, ordered[0].due_date)

            scope = unarchive_scope_on(
                template, BalanceContext.build(seed_user["user"].id),
            )

            assert scope.own_day_inside == {ordered[0].id: ordered[0].due_date}

    def test_an_UNDATED_planned_row_does_not_bound_the_restatement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-PC96's carve-out at the opening door: the books move stays allowed."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            first.occurs_on = None
            _db.session.flush()

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(first.due_date, Decimal("0.00")),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED


class TestAFloorlessDefinitionIsNotWalked:
    """The round-6 review's L3: a walk with no books floor drops nothing, so none is taken."""

    def test_books_reading_answers_without_walking(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """A rule the walk could not read refuses nothing where no answer depends on it."""
        with app.app_context():
            _account, rows = _account_with_projected_rows(seed_user, seed_periods)
            resolved = resolved_rule_of(
                rows[0].template, BalanceContext.build(seed_user["user"].id),
            )

            def _unwalkable(*_args):
                raise AssertionError("a floor-less walk was taken")

            monkeypatch.setattr(definition_unarchive, "occurrence_walk", _unwalkable)

            reading = books_reading(
                replace(resolved, books_opened_on=None),
                calendar_for(seed_user["user"].id),
            )

            assert not reading.inside


class TestTheLoadersPrefilter:
    """``rows_held_where_they_sit`` loads only rows the books COULD hold -- both ways in."""

    def test_an_envelope_due_after_every_opening_is_held_by_its_paychecks_end(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Its due day is past the bound; its paycheck starts before it (the second arm)."""
        with app.app_context():
            account, rows = _account_with_projected_rows(
                seed_user, seed_periods, is_envelope=True,
            )
            first = min(rows, key=lambda row: row.due_date)
            last_day = seed_periods[1].start_date - _ONE_DAY
            _open_books_directly(account, last_day)
            first.due_date = seed_periods[-1].start_date
            _db.session.flush()

            held = rows_held_where_they_sit(
                first.template, (_row_is(first),),
                calendar_for(seed_user["user"].id), {},
            )

            assert held[first.id][0].day == last_day

    def test_a_bill_filed_in_a_paycheck_after_every_opening_is_held_by_its_due_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Its paycheck starts past the bound; its due day is inside (the first arm)."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            _open_books_directly(account, first.due_date)
            first.pay_period_id = seed_periods[-1].id
            _db.session.flush()

            held = rows_held_where_they_sit(
                first.template, (_row_is(first),),
                calendar_for(seed_user["user"].id), {},
            )

            assert held[first.id][0].day == first.due_date


def _cancelled_first_row_left_on_the_old_account(seed_user, seed_periods):
    """A rent's first row cancelled, the definition then moved to a new early account.

    Returns:
        ``(old, first)``: the account the rows were left on, and that row.
    """
    old, rows = _account_with_projected_rows(seed_user, seed_periods)
    first = min(rows, key=lambda row: row.due_date)
    status_seam.apply_status_change(
        first, ref_cache.status_id(StatusEnum.CANCELLED),
    )
    _moved_to_a_new_account(seed_user, first.template)
    _db.session.flush()
    return old, first


def _archived_then_moved(auth_client, seed_user, seed_periods):
    """A rent archived through its door, its first two rows then orphaned in place.

    Returns:
        ``(template_id, old, (first, second))``.
    """
    old, rows = _account_with_projected_rows(seed_user, seed_periods)
    _db.session.commit()
    template_id = rows[0].template_id
    assert auth_client.post(f"/templates/{template_id}/archive").status_code == 302
    _db.session.expire_all()
    ordered = sorted(_rows_of(template_id), key=lambda row: row.occurs_on)
    return template_id, old, (ordered[0], ordered[1])


def _fresh_template(template_id):
    """The rent's definition, re-read."""
    return _db.session.get(TransactionTemplate, template_id)


def _assert_only_deleted(template_id, deleted_ids):
    """Every row of the definition is live except *deleted_ids*."""
    _db.session.expire_all()
    for row in _rows_of(template_id):
        assert row.is_deleted is (row.id in deleted_ids), row.occurs_on


def _orphan_transfers(template_id):
    """Move a transfer rule's start onto its third row: the first two answer nothing.

    Returns:
        The transfers in date order.
    """
    ordered = sorted(
        _db.session.query(Transfer)
        .filter(Transfer.transfer_template_id == template_id)
        .all(),
        key=lambda row: row.occurs_on,
    )
    ordered[0].template.recurrence_rule.starts_on = ordered[2].occurs_on
    _db.session.flush()
    return ordered


def _paid_first_transfer(auth_client, seed_user, seed_periods):
    """A monthly save's first transfer marked done through its door.

    Returns:
        ``(savings, first_id, first_due)``.
    """
    savings = _account_opened_early(seed_user, name="Probe savings")
    template_id = _monthly_save_into(seed_user, seed_periods, savings).id
    first = min(_transfers(template_id), key=lambda row: row.occurs_on)
    first_id, first_due = first.id, first.due_date
    assert auth_client.post(
        f"/transfers/instance/{first_id}/mark-done",
    ).status_code == 200
    _db.session.expire_all()
    return _fresh(savings), first_id, first_due


def _row_is(row):
    """The criterion selecting exactly *row*."""
    return type(row).id == row.id
