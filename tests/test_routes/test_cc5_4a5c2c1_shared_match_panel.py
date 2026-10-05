"""Plan step ``credit_card:CC-5-4a-5`` leaf 5c-2c-1: a match naming several rows, on the reconcile panel.

Ruling **R-CC135** (developer 2026-10-04, "One check per save"), the
structure of its own example (the figures are swapped for made-up ones the
coordinator checked against production, ruling **balance:R-BAL132**):
*"Warnings name each match under every row it names, saying which rows must
all close: 'Matched with Dining to one bank line: closing both from their
purchases withdraws that match, so 9/24 PAYMENT -$205.00 is unexplained again
on your statement screen.' ... Tick both: saved, each Paid at $30.00, the
$120.00 and $85.00 payments off, the line unexplained. Tick Groceries only:
saved, the match stays on Dining's $85.00."*  Here Groceries closed at
`$125.00`, Dining at `$85.00`, one bank line `-$210.00`, and a `$35.00`
purchase each after both were reopened.

Finding **CC-384**: until this leaf the panel read each row ALONE, so neither
row's caption named the shared line and ticking both was refused on every
try.  Every case drives the panel's own route as a browser does: every
hidden input and amount box the form renders, plus the ticked checkboxes.

Every figure and bank line here is a made-up example.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import event

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.services import entry_service
from app.services.match_withdrawal import pending_alone_and_together, pending_for_each
from app.services.pay_calendar import calendar_for
from app.services.row_valuation import settled_figure
from app.services.statement_match import accept_match
from app.services.transaction_service import settle_transaction
from tests._test_helpers import typed
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)
from tests.test_routes.test_cc5_4a5c_reconcile_panel import (
    _Withdrawals,
    _panel,
    _payload,
    _post,
    _revert,
    _row,
    _status,
    _true_up,
)


def _shared_pair(seed_user, periods, names=("Groceries", "Dining")):
    """Two envelopes closed at $125.00 and $85.00, matched together to ONE line, both reopened.

    Each is then given a `$35.00` purchase, so each settles FROM its purchase
    and its ``purchases`` record takes the payment the revert kept: one tick
    takes one payment out of the act, and only both ticks empty it.

    Args:
        seed_user: The seeded owner.
        periods: The pay period of each envelope, in *names*' order.
        names: The two envelopes' names.

    Returns:
        ``(first, second, line, act id)``.
    """
    first = _row(seed_user, periods[0], names[0], "245.00", is_envelope=True)
    second = _row(seed_user, periods[1], names[1], "245.00", is_envelope=True)
    settle_transaction(first, submitted=typed(Decimal("125.00")))
    settle_transaction(second, submitted=typed(Decimal("85.00")))
    db.session.commit()
    line = a_bank_line(
        seed_user, an_import(seed_user, None), amount="-210.00",
        posted_on=first.settled_on, description="PAYMENT",
    )
    db.session.commit()
    scope = a_scope(seed_user, None)
    accepted = accept_match(
        a_submission(scope, lines=[line], transactions=[first, second]), scope,
    )
    db.session.commit()
    for row, period in ((first, periods[0]), (second, periods[1])):
        _revert(row)
        entry_service.create_entry(
            row.id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("35.00")), description="Market",
                purchased_on=period.start_date,
            ),
        )
        db.session.commit()
    return first, second, line, accepted.match_id


def _sentence(partner, posted_on):
    """Return R-CC135's warning for a two-row match over the `-$210.00` line."""
    return (
        f"Matched with {partner} to one bank line: closing both from their "
        f"purchases withdraws that match, so {posted_on} PAYMENT -$210.00 is "
        "unexplained again on your statement screen."
    )


def _ticks(*rows):
    """Return the checkbox pairs that tick *rows* on the panel."""
    return {("transaction_ids", str(row_id)) for row_id in rows}


def _caption(body, checking_id, row_id):
    """Return the caption block under row *row_id*'s tick, whitespace-collapsed."""
    start = body.index(f'id="reconcile-reconcile-panel-{checking_id}-t{row_id}-withdraws"')
    return body[start:body.index("<input", start)]


def _act_entries(match_id):
    """Return the movement ids the act still names, re-read."""
    db.session.expire_all()
    return {
        member.transaction_entry_id
        for member in db.session.query(StatementMatchMember).filter_by(
            match_id=match_id,
        )
        if member.transaction_entry_id is not None
    }


class TestOneReadNamesAMatchUnderEveryRow:
    """``match_withdrawal.pending_alone_and_together``: alone, together, or neither."""

    def test_a_match_two_removals_empty_only_together_is_shared_by_both(
        self, app, seed_user, seed_periods_today,
    ):
        """Neither row empties it alone; each carries it, with both keys, freeing the one line."""
        with app.app_context():
            groceries, dining, line, _act = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            removals = {
                groceries.id: groceries.covering_movements,
                dining.id: dining.covering_movements,
            }

            pending = pending_alone_and_together(removals)

            for key in (groceries.id, dining.id):
                assert pending[key].alone.frees_a_line is False
                (shared,) = pending[key].shared
                assert shared.keys == frozenset({groceries.id, dining.id})
                assert shared.withdrawal.matches == 1
                assert shared.withdrawal.line_ids == frozenset({line.id})
            # The one-press read (the purchase list's X) answers each removal
            # ALONE, as before this leaf, and reads no bank line for an act
            # only the shared half names (the review's L4).
            statements = []

            def _record(_conn, _cursor, statement, *_args):
                statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", _record)
            try:
                alone = pending_for_each(removals)
            finally:
                event.remove(db.engine, "before_cursor_execute", _record)
            for key in (groceries.id, dining.id):
                assert alone[key].matches == 0
                assert alone[key].frees_a_line is False
            assert statements
            assert not any("bank_statement_lines" in sql for sql in statements)

    def test_a_match_naming_a_movement_no_removal_takes_is_neither(
        self, app, seed_user, seed_periods_today,
    ):
        """Asked for one row only, the act keeps the other's payment: nothing frees, nothing shared."""
        with app.app_context():
            groceries, _dining, _line, _act = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )

            pending = pending_alone_and_together(
                {groceries.id: groceries.covering_movements},
            )

            assert pending[groceries.id].alone.frees_a_line is False
            assert pending[groceries.id].shared == ()


class TestThePanelNamesTheSharedMatchUnderEachRow:
    """R-CC135's warning, its posted value, and the two ticks it rules."""

    def test_each_row_names_the_other_and_posts_the_line_with_both_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """'Matched with Dining' under Groceries, 'Matched with Groceries' under Dining."""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, dining, line, _act = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            posted_on = line.posted_on.strftime("%-m/%-d")
            _true_up(auth_client, checking_id)

            body = _panel(auth_client, checking_id)

            assert _sentence("Dining", posted_on) in body
            assert _sentence("Groceries", posted_on) in body
            rows = ",".join(str(row_id) for row_id in sorted((groceries.id, dining.id)))
            posted = f'<input type="hidden" name="shared_lines" value="{line.id};{rows}">'
            assert body.count(posted) == 2
            # Neither row empties the act alone, so neither prints the one-row caption.
            assert "Closing it from its purchases" not in body

    def test_ticking_both_saves_and_leaves_the_line_unexplained(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """'Tick both: saved, each Paid at $35.00, the $125.00 and $85.00 payments off, the line unexplained.'"""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, dining, line, match_id = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            groceries_id, dining_id, line_id = groceries.id, dining.id, line.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)

            with _Withdrawals() as events:
                response = _post(auth_client, checking_id, _payload(
                    body, _ticks(groceries_id, dining_id),
                ))

            assert response.status_code == 200
            done = ref_cache.status_id(StatusEnum.DONE)
            for row_id in (groceries_id, dining_id):
                assert _status(row_id) == done
                row = db.session.get(Transaction, row_id)
                assert row.covering_movements == []
                assert settled_figure(row) == Decimal("35.00")
            assert db.session.get(StatementMatch, match_id) is None
            assert db.session.query(StatementMatchMember).filter_by(
                bank_statement_line_id=line_id,
            ).count() == 0
            (record,) = events.records
            assert record.freed_line_ids == [line_id]
            assert record.silent_by is None

    def test_ticking_groceries_alone_saves_and_keeps_the_match_on_dining(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """'Tick Groceries only: saved, the match stays on Dining's $85.00.'"""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, dining, line, match_id = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            groceries_id, dining_id, line_id = groceries.id, dining.id, line.id
            (dining_payment,) = dining.covering_movements
            dining_payment_id = dining_payment.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)

            with _Withdrawals() as events:
                response = _post(auth_client, checking_id, _payload(
                    body, _ticks(groceries_id),
                ))

            assert response.status_code == 200
            assert _status(groceries_id) == ref_cache.status_id(StatusEnum.DONE)
            assert _status(dining_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(StatementMatch, match_id) is not None
            assert _act_entries(match_id) == {dining_payment_id}
            assert db.session.get(
                Transaction, dining_id,
            ).covering_movements[0].amount == Decimal("85.00")
            assert db.session.query(StatementMatchMember).filter_by(
                bank_statement_line_id=line_id,
            ).count() == 1
            assert events.records == []


def test_a_match_also_naming_a_row_whose_tick_keeps_its_payment_is_not_warned(
    app, auth_client, seed_user, seed_periods_today,
):
    """Three closes to one line, a purchase under two: no save of the panel empties it.

    Fuel, the third, is reopened with no purchase, so its tick re-dates its
    kept payment rather than taking it off -- the act still names it however
    the other two are ticked, so no row warns and ticking both saves with the
    match standing on Fuel's payment.
    """
    with app.app_context():
        checking_id = seed_user["account"].id
        period = seed_periods_today[0]
        groceries = _row(seed_user, period, "Groceries", "245.00", is_envelope=True)
        dining = _row(seed_user, period, "Dining", "245.00", is_envelope=True)
        fuel = _row(seed_user, period, "Fuel", "245.00", is_envelope=True)
        for row, close in ((groceries, "125.00"), (dining, "85.00"), (fuel, "35.00")):
            settle_transaction(row, submitted=typed(Decimal(close)))
        db.session.commit()
        line = a_bank_line(
            seed_user, an_import(seed_user, None), amount="-245.00",
            posted_on=groceries.settled_on, description="PAYMENT",
        )
        db.session.commit()
        scope = a_scope(seed_user, None)
        match_id = accept_match(
            a_submission(scope, lines=[line], transactions=[groceries, dining, fuel]),
            scope,
        ).match_id
        db.session.commit()
        for row in (groceries, dining, fuel):
            _revert(row)
        for row in (groceries, dining):
            entry_service.create_entry(
                row.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("35.00")), description="Market",
                    purchased_on=period.start_date,
                ),
            )
            db.session.commit()
        (fuel_payment,) = fuel.covering_movements
        fuel_payment_id, groceries_id, dining_id = fuel_payment.id, groceries.id, dining.id
        _true_up(auth_client, checking_id)
        body = _panel(auth_client, checking_id)
        assert "Matched with" not in body
        assert 'name="shared_lines"' not in body

        response = _post(auth_client, checking_id, _payload(
            body, _ticks(groceries_id, dining_id),
        ))

        assert response.status_code == 200
        assert _act_entries(match_id) == {fuel_payment_id}


class TestAStalePageOrBodyIsRefused:
    """R-CC135's one check per save, over the shared line: nothing saves, the panel redraws."""

    def test_a_page_drawn_before_another_tab_closed_dining_refuses_groceries_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The page said the match stays on Dining; Groceries alone would now free the line."""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, dining, line, match_id = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            groceries_id = groceries.id
            posted_on = line.posted_on.strftime("%-m/%-d")
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            # Another tab closes Dining from its purchase: its payment leaves
            # the act, which keeps Groceries' and so frees nothing.
            settle_transaction(dining)
            db.session.commit()

            response = _post(auth_client, checking_id, _payload(
                body, _ticks(groceries_id),
            ))

            assert response.status_code == 400
            redrawn = response.data.decode()
            assert "Nothing was saved: this page was out of date." in redrawn
            # Drawn again as it is now: the match names Groceries alone.
            assert (
                "Closing it from its purchases withdraws 1 accepted match, so 1 "
                "bank line is unexplained again on your statement screen: "
                f"{posted_on} PAYMENT"
            ) in redrawn
            assert _status(groceries_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(StatementMatch, match_id) is not None

    def test_both_ticked_without_the_shared_field_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A body that named no shared line frees one it never named: nothing saves."""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, dining, _line, match_id = _shared_pair(
                seed_user, seed_periods_today[0:2],
            )
            groceries_id, dining_id = groceries.id, dining.id
            _true_up(auth_client, checking_id)
            payload = _payload(
                _panel(auth_client, checking_id), _ticks(groceries_id, dining_id),
            )
            payload.poplist("shared_lines")

            response = _post(auth_client, checking_id, payload)

            assert response.status_code == 400
            assert "Nothing was saved: this page was out of date." in (
                response.data.decode()
            )
            for row_id in (groceries_id, dining_id):
                assert _status(row_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(StatementMatch, match_id) is not None


def test_two_rows_of_one_name_are_told_apart_by_their_paycheck(
    app, auth_client, seed_user, seed_periods_today,
):
    """One envelope in two paychecks, matched to one line: each names the other with its period."""
    with app.app_context():
        checking_id = seed_user["account"].id
        first_period, second_period = seed_periods_today[0:2]
        first, second, _line, _act = _shared_pair(
            seed_user, (first_period, second_period),
            names=("Groceries", "Groceries"),
        )
        _true_up(auth_client, checking_id)

        body = " ".join(_panel(auth_client, checking_id).split())

        calendar = calendar_for(seed_user["user"].id)
        spans = {}
        for saved in (first_period, second_period):
            period = calendar.period_by_id(saved.id)
            spans[saved.id] = (
                f"{period.start_date.strftime('%b %-d')} - "
                f"{period.end_date.strftime('%b %-d')}"
            )
        # Each row's own caption names the OTHER row's paycheck, never its
        # own (the review's L3: a partner read off the row itself passed).
        for row, partner in ((first, second), (second, first)):
            caption = _caption(body, checking_id, row.id)
            assert "Matched with Groceries <span" in caption
            assert (
                f"{spans[partner.pay_period_id]} </span> to one bank line: "
                "closing both"
            ) in caption
            assert spans[row.pay_period_id] not in caption


def test_a_shared_value_naming_no_rows_is_refused(
    app, auth_client, seed_user, seed_periods_today,
):
    """'<line>;' would name the line whatever was ticked: refused, nothing saved (the review's N3)."""
    with app.app_context():
        checking_id = seed_user["account"].id
        groceries, dining, line, match_id = _shared_pair(
            seed_user, seed_periods_today[0:2],
        )
        groceries_id, dining_id = groceries.id, dining.id
        _true_up(auth_client, checking_id)
        payload = _payload(_panel(auth_client, checking_id), _ticks(groceries_id))
        payload.setlist("shared_lines", [f"{line.id};"])

        response = _post(auth_client, checking_id, payload)

        assert response.status_code == 400
        assert (
            "A shared match&#39;s warning came back without its lines and rows."
        ) in response.data.decode()
        for row_id in (groceries_id, dining_id):
            assert _status(row_id) == ref_cache.status_id(StatusEnum.PROJECTED)
        assert db.session.get(StatementMatch, match_id) is not None


def test_three_rows_and_two_lines_read_all_3_and_the_plural(
    app, auth_client, seed_user, seed_periods_today,
):
    """'Matched with Dining and Fuel to 2 bank lines: closing all 3 ... are unexplained again'."""
    with app.app_context():
        checking_id = seed_user["account"].id
        period = seed_periods_today[0]
        rows = [
            _row(seed_user, period, name, "245.00", is_envelope=True)
            for name in ("Groceries", "Dining", "Fuel")
        ]
        for row, close in zip(rows, ("125.00", "85.00", "35.00")):
            settle_transaction(row, submitted=typed(Decimal(close)))
        db.session.commit()
        lines = [
            a_bank_line(
                seed_user, an_import(seed_user, None), amount=amount,
                posted_on=rows[0].settled_on, description=description,
            )
            for amount, description in (("-210.00", "PAYMENT A"), ("-35.00", "PAYMENT B"))
        ]
        db.session.commit()
        scope = a_scope(seed_user, None)
        accept_match(a_submission(scope, lines=lines, transactions=rows), scope)
        db.session.commit()
        for row in rows:
            _revert(row)
            entry_service.create_entry(
                row.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("35.00")), description="Market",
                    purchased_on=period.start_date,
                ),
            )
            db.session.commit()
        posted_on = lines[0].posted_on.strftime("%-m/%-d")
        _true_up(auth_client, checking_id)

        body = " ".join(_panel(auth_client, checking_id).split())

        assert (
            "Matched with Dining and Fuel to 2 bank lines: closing all 3 from "
            f"their purchases withdraws that match, so {posted_on} PAYMENT A "
            f"-$210.00; {posted_on} PAYMENT B -$35.00 are unexplained again on "
            "your statement screen."
        ) in body


def test_a_name_another_row_on_the_list_carries_gets_its_paycheck(
    app, auth_client, seed_user, seed_periods_today,
):
    """A second Groceries on the list, outside the match: Dining's warning still says which Groceries."""
    with app.app_context():
        checking_id = seed_user["account"].id
        first_period, second_period = seed_periods_today[0:2]
        groceries, dining, _line, _act = _shared_pair(
            seed_user, (first_period, first_period),
        )
        _row(seed_user, second_period, "Groceries", "245.00", is_envelope=True)
        _true_up(auth_client, checking_id)

        body = " ".join(_panel(auth_client, checking_id).split())

        period = calendar_for(seed_user["user"].id).period_by_id(first_period.id)
        span = (
            f"{period.start_date.strftime('%b %-d')} - "
            f"{period.end_date.strftime('%b %-d')}"
        )
        assert (
            'Matched with Groceries <span class="text-body-secondary fw-normal '
            f'fs-xs ms-1"> {span} </span> to one bank line'
        ) in _caption(body, checking_id, dining.id)
        assert "Matched with Dining to one bank line" in _caption(
            body, checking_id, groceries.id,
        )
