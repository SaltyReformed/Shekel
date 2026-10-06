"""Plan step ``credit_card:CC-5-4a-5`` leaf 5c-2c-2: carry-forward's Confirm says what it frees.

Ruling **R-CC76** (developer 2026-09-23, "Say it first, new step"): *"Both
doors name what they would free before you press, from the same read they
act on, as the popovers do: ... carry forward's confirmation for each
envelope it would settle."*  Ruling **R-CC135** (developer 2026-10-04, "One
check per save"): *"Carry-forward's Confirm is one save of every envelope and
names each match once."*  Ruling **R-CC127** ("Check the page"): the Confirm
sends back the lines it named and the batch compares them with what it frees;
ruling **R-CC128** ("Redraw all"): a stale press saves nothing and draws the
modal again from what is true now.

Finding **CC-364**: until this leaf the batch withdrew a match under a
``Silent("CC-364")`` press, its bank line unexplained again with nothing said
before the press.  The route cases drive the real routes and post exactly what
the modal's Confirm emits (its ``hx-vals``), read off the rendered page --
except the one posting nothing, as a modal drawn before this leaf does; one
case calls the service directly for its default.

The figures are the made-up ones the coordinator checked against production
(ruling **balance:R-BAL132**): an envelope budgeting `$245.00` closed at
`$125.00` (and a second at `$85.00`), one bank line for each close or one
`-$210.00` line for both, and a `$35.00` purchase under each after it was
reopened.  Every figure and bank line here is a made-up example.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import PageOutOfDate
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.services import carry_forward_service, entry_service
from app.services.balance_at import BalanceContext
from app.services.statement_match import accept_match, release_match
from app.services.transaction_service import settle_transaction
from app.utils.dates import display_today
from app.utils.log_events import EVT_CARRY_FORWARD
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
from tests.test_routes.test_cc5_4a5c2c1_shared_match_panel import _shared_pair
from tests.test_routes.test_cc5_4a5c_reconcile_panel import (
    _Withdrawals,
    _revert,
    _row,
    _status,
)

class _Carries(logging.Handler):
    """Collect the batch's EVT_CARRY_FORWARD events for one block."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []
        self._logger = logging.getLogger("app.services.carry_forward_service._execute")
        self._prior_level = self._logger.level

    def emit(self, record):
        if getattr(record, "event", None) == EVT_CARRY_FORWARD:
            self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self)
        self._logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._prior_level)


#: The Confirm button's posted values, read the way htmx reads them.
_CONFIRM_VALS = re.compile(r"data-carry-forward-confirm[^>]*?hx-vals='([^']*)'", re.S)


def _source(periods):
    """Return the paycheck the batch carries FROM: the first, before today's."""
    return periods[0]


def _matched_envelope(
    seed_user, period, name, closed_at, description, *, purchase=True,
):
    """An envelope budgeting $245.00, closed at *closed_at*, matched alone, reopened with a $35.00 purchase.

    Its close from the purchase takes the payment the revert kept off the
    books (``status_seam._covering._withdraw``), which empties the act.
    *purchase* False reopens it holding none.

    Returns:
        ``(row, line, act id)``.
    """
    row = _row(seed_user, period, name, "245.00", is_envelope=True)
    settle_transaction(row, submitted=typed(Decimal(closed_at)))
    db.session.commit()
    line = a_bank_line(
        seed_user, an_import(seed_user, None), amount=f"-{closed_at}",
        posted_on=row.settled_on, description=description,
    )
    db.session.commit()
    scope = a_scope(seed_user, None)
    accepted = accept_match(
        a_submission(scope, lines=[line], transactions=[row]), scope,
    )
    db.session.commit()
    _revert(row)
    if purchase:
        entry_service.create_entry(
            row.id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("35.00")), description="Market",
                purchased_on=period.start_date,
            ),
        )
        db.session.commit()
    return row, line, accepted.match_id


def _preview(auth_client, source_id):
    """GET the carry-forward modal for *source_id*; return its body."""
    response = auth_client.get(
        f"/pay-periods/{source_id}/carry-forward-preview",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _confirm_vals(body):
    """Return what the Confirm button posts: its ``hx-vals``, parsed."""
    (raw,) = _CONFIRM_VALS.findall(body)
    return json.loads(raw)


def _confirm(auth_client, source_id, data):
    """POST the Confirm with *data*."""
    return auth_client.post(
        f"/pay-periods/{source_id}/carry-forward", data=data,
        headers={"HX-Request": "true"},
    )


def _one_row_caption(posted_on, description, amount):
    """Return the caption under an envelope whose close empties one match by itself."""
    return (
        "Closing it from its purchases withdraws 1 accepted match, so 1 bank "
        "line is unexplained again on your statement screen: "
        f"{posted_on} {description} -${amount}."
    )


def _shared_sentence(partner, posted_on):
    """Return R-CC135's warning for the two-envelope match over the `-$210.00` line."""
    return (
        f"Matched with {partner} to one bank line: closing both from their "
        f"purchases withdraws that match, so {posted_on} PAYMENT -$210.00 is "
        "unexplained again on your statement screen."
    )


def _collapsed(body):
    """Return *body* with every run of whitespace collapsed to one space."""
    return re.sub(r"\s+", " ", body)


def _line_is_unmatched(line_id):
    """Return whether no act names the bank line, re-read."""
    db.session.expire_all()
    return db.session.query(StatementMatchMember).filter_by(
        bank_statement_line_id=line_id,
    ).count() == 0


class TestTheModalNamesWhatEachCloseFrees:
    """R-CC76's caption under the envelope, and the Confirm's posted lines."""

    def test_an_envelope_whose_close_frees_a_line_names_it_and_posts_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The one-row caption the popovers and the panel print, under the envelope."""
        with app.app_context():
            source = _source(seed_periods_today)
            row, line, _act = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            posted_on = line.posted_on.strftime("%-m/%-d")

            body = _collapsed(_preview(auth_client, source.id))

            assert _one_row_caption(posted_on, "MARKET", "125.00") in body
            assert _confirm_vals(body) == {"shown_lines": str(line.id)}
            assert row.name in body

    def test_an_envelope_reopened_with_no_purchase_names_its_line_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The batch closes it at $0.00 from its purchases, taking its kept payment off: named, and freed.

        Where the reconcile panel's tick of such a row re-dates its payment
        (its MANUAL branch), carry-forward settles every envelope from its
        purchases (``settle_from_entries``, ruling **R-FJ**), so the removal
        is the row's kept payment whether or not it holds a purchase.
        """
        with app.app_context():
            source = _source(seed_periods_today)
            row, line, match_id = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
                purchase=False,
            )
            row_id, line_id = row.id, line.id
            posted_on = line.posted_on.strftime("%-m/%-d")

            body = _collapsed(_preview(auth_client, source.id))

            assert _one_row_caption(posted_on, "MARKET", "125.00") in body
            vals = _confirm_vals(body)
            assert vals == {"shown_lines": str(line_id)}
            response = _confirm(auth_client, source.id, vals)
            assert response.status_code == 200, response.get_data(as_text=True)
            assert _status(row_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(StatementMatch, match_id) is None
            assert _line_is_unmatched(line_id)

    def test_an_envelope_holding_no_matched_payment_names_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No caption, and the Confirm posts a page that named nothing."""
        with app.app_context():
            source = _source(seed_periods_today)
            _row(seed_user, source, "Groceries", "245.00", is_envelope=True)

            body = _preview(auth_client, source.id)

            assert "Closing it from its purchases" not in body
            assert "Matched with" not in body
            assert _confirm_vals(body) == {"shown_lines": ""}


class TestConfirmingSavesWhatTheModalNamed:
    """The batch's one press, compared with the Confirm's posted lines."""

    def test_confirming_what_the_modal_named_saves_and_withdraws_the_match(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Closed at its $35.00 purchase, its $125.00 payment off, the line unexplained, one event."""
        with app.app_context():
            source = _source(seed_periods_today)
            row, line, match_id = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            row_id, line_id = row.id, line.id
            vals = _confirm_vals(_preview(auth_client, source.id))

            with _Withdrawals() as events, _Carries() as carries:
                response = _confirm(auth_client, source.id, vals)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert response.headers["HX-Trigger"] == "gridRefresh"
            (carry,) = carries.records
            assert carry.envelope_count == 1
            assert _status(row_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(Transaction, row_id).covering_movements == []
            assert db.session.get(StatementMatch, match_id) is None
            assert _line_is_unmatched(line_id)
            (record,) = events.records
            assert record.freed_line_ids == [line_id]
            assert record.silent_by is None

    def test_a_confirm_naming_nothing_is_refused_redrawn_and_goes_ahead_when_pressed_again(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A request with no lines named nothing: refused whole, the content redrawn, the redraw's Confirm saves.

        R-CC127: *"A button with no warning sends nothing"*, so a request
        without the field cannot withdraw a match.  R-CC128: the redraw is
        the content drawn again from current state, retargeted at the open
        modal, and pressing its Confirm goes ahead.
        """
        with app.app_context():
            source = _source(seed_periods_today)
            row, line, match_id = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            row_id, line_id = row.id, line.id
            posted_on = line.posted_on.strftime("%-m/%-d")

            with _Withdrawals() as events:
                refused = _confirm(auth_client, source.id, {})

            assert refused.status_code == 400
            assert refused.headers["Shekel-Designed-Fragment"] == "1"
            assert refused.headers["HX-Retarget"] == "#carryForwardPreviewContent"
            assert refused.headers["HX-Reswap"] == "outerHTML"
            redrawn = _collapsed(refused.get_data(as_text=True))
            assert redrawn.lstrip().startswith(
                '<div class="modal-content" id="carryForwardPreviewContent">',
            )
            assert "Nothing was saved: this page was out of date." in redrawn
            assert "press again to go ahead" in redrawn
            assert _one_row_caption(posted_on, "MARKET", "125.00") in redrawn
            assert _status(row_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(StatementMatch, match_id) is not None
            assert events.records == []

            again = _confirm(auth_client, source.id, _confirm_vals(redrawn))

            assert again.status_code == 200, again.get_data(as_text=True)
            assert _status(row_id) == ref_cache.status_id(StatusEnum.DONE)
            assert _line_is_unmatched(line_id)

    def test_a_refusal_at_once_states_no_count_it_cannot_know(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two envelopes each free a line, the request named none: refused at the FIRST, with no count.

        The press stops at the first call freeing a line the page did not
        name, before the second runs, so a count there would be the lines
        freed SO FAR -- "leaves 1 bank line" over a redraw naming two (the
        leaf's review, L1).  The sentence states the fact; the redraw below
        it names both lines and posts both.
        """
        with app.app_context():
            source = _source(seed_periods_today)
            _groceries, groceries_line, _act = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            _dining, dining_line, _act = _matched_envelope(
                seed_user, source, "Dining", "85.00", "DINER",
            )
            both = ",".join(
                str(line_id)
                for line_id in sorted((groceries_line.id, dining_line.id))
            )

            refused = _confirm(auth_client, source.id, {})

            assert refused.status_code == 400
            redrawn = _collapsed(refused.get_data(as_text=True))
            assert (
                "Nothing was saved: this page was out of date. As things are "
                "now, this press would leave a bank line unexplained that this "
                "page did not mention."
            ) in redrawn
            assert "the page named" not in redrawn
            assert _confirm_vals(redrawn) == {"shown_lines": both}

    def test_a_line_another_tab_freed_refuses_the_page_that_named_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The modal named the line; the statement screen's Undo freed it first: nothing saves, the redraw names nothing.

        The envelope still holds the payment its revert kept, so its close
        still reaches the match step and engages the press, whose close
        compares the nothing it freed with the line the page named.  Nothing
        is reported carried (the leaf's review, M1: the batch's event logged
        before the press closed).
        """
        with app.app_context():
            source = _source(seed_periods_today)
            row, line, match_id = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            row_id = row.id
            vals = _confirm_vals(_preview(auth_client, source.id))
            release_match(match_id, seed_user["user"].id, seed_user["account"].id)
            db.session.commit()

            with _Carries() as carries:
                refused = _confirm(auth_client, source.id, vals)

            assert carries.records == []

            assert refused.status_code == 400
            redrawn = _collapsed(refused.get_data(as_text=True))
            assert "Nothing was saved: this page was out of date." in redrawn
            assert "Closing it from its purchases" not in redrawn
            assert _confirm_vals(redrawn) == {"shown_lines": ""}
            assert _status(row_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert [
                entry.amount
                for entry in db.session.get(Transaction, row_id).covering_movements
            ] == [Decimal("125.00")]


    def test_an_envelope_another_tab_closed_refuses_the_page_that_named_its_line(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The grid's Mark Paid closed it first: the batch reaches no match step, and is still graded.

        PROMISED is what refuses it: the batch carries nothing now, so no
        call of its press engages, and only a press whose page promised what
        it named compares a save that freed nothing (ledger row **BAL-597**'s
        shape).  The redraw shows the period as it is now.
        """
        with app.app_context():
            source = _source(seed_periods_today)
            row, _line, _act = _matched_envelope(
                seed_user, source, "Groceries", "125.00", "MARKET",
            )
            row_id = row.id
            vals = _confirm_vals(_preview(auth_client, source.id))
            closed = auth_client.post(
                f"/transactions/{row_id}/mark-done",
                headers={"HX-Request": "true"},
            )
            assert closed.status_code == 200, closed.get_data(as_text=True)

            refused = _confirm(auth_client, source.id, vals)

            assert refused.status_code == 400
            redrawn = _collapsed(refused.get_data(as_text=True))
            assert "Nothing was saved: this page was out of date." in redrawn
            assert "No projected items in" in redrawn
            # Its Confirm is disabled, so the banner says nothing of pressing it.
            assert "Here it is as it is now." in redrawn
            assert "press again" not in redrawn
            assert _status(row_id) == ref_cache.status_id(StatusEnum.DONE)


class TestAMatchSharedByTwoEnvelopesIsNamedOnce:
    """R-CC135: 'Carry-forward's Confirm is one save of every envelope and names each match once.'"""

    def test_the_shared_match_is_one_sentence_and_the_confirm_posts_its_line(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One warning, under Groceries -- the first the modal lists, by id -- naming Dining."""
        with app.app_context():
            source = _source(seed_periods_today)
            groceries, dining, line, _act = _shared_pair(
                seed_user, (source, source),
            )
            assert groceries.id < dining.id
            posted_on = line.posted_on.strftime("%-m/%-d")

            body = _collapsed(_preview(auth_client, source.id))

            assert body.count("Matched with ") == 1
            sentence = body.index(_shared_sentence("Dining", posted_on))
            assert body.index("<strong>Groceries</strong>") < sentence
            assert sentence < body.index("<strong>Dining</strong>")
            # Neither close empties the act alone.
            assert "Closing it from its purchases" not in body
            assert _confirm_vals(body) == {"shown_lines": str(line.id)}

    def test_confirming_both_saves_both_and_frees_the_line_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both closed at $35.00, the $125.00 and $85.00 payments off, the act gone, one event."""
        with app.app_context():
            source = _source(seed_periods_today)
            groceries, dining, line, match_id = _shared_pair(
                seed_user, (source, source),
            )
            ids, line_id = (groceries.id, dining.id), line.id
            vals = _confirm_vals(_preview(auth_client, source.id))

            with _Withdrawals() as events:
                response = _confirm(auth_client, source.id, vals)

            assert response.status_code == 200, response.get_data(as_text=True)
            for row_id in ids:
                assert _status(row_id) == ref_cache.status_id(StatusEnum.DONE)
                assert db.session.get(Transaction, row_id).covering_movements == []
            assert db.session.get(StatementMatch, match_id) is None
            assert _line_is_unmatched(line_id)
            (record,) = events.records
            assert record.freed_line_ids == [line_id]
            assert record.silent_by is None

    def test_a_match_shared_with_a_row_outside_the_batch_is_not_named_and_stays(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Dining is in another paycheck: the batch only takes Groceries' payment out of the act.

        *"Tick Groceries only: saved, the match stays on Dining's $85.00"*,
        here as the batch that carries Groceries alone.
        """
        with app.app_context():
            source = _source(seed_periods_today)
            groceries, dining, line, match_id = _shared_pair(
                seed_user, (source, seed_periods_today[1]),
            )
            groceries_id, line_id = groceries.id, line.id
            (dining_payment,) = dining.covering_movements
            dining_payment_id = dining_payment.id

            body = _preview(auth_client, source.id)

            assert "Matched with" not in body
            assert "Closing it from its purchases" not in body
            vals = _confirm_vals(body)
            assert vals == {"shown_lines": ""}

            with _Withdrawals() as events:
                response = _confirm(auth_client, source.id, vals)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _status(groceries_id) == ref_cache.status_id(StatusEnum.DONE)
            db.session.expire_all()
            assert db.session.get(StatementMatch, match_id) is not None
            assert {
                member.transaction_entry_id
                for member in db.session.query(StatementMatchMember).filter_by(
                    match_id=match_id,
                )
                if member.transaction_entry_id is not None
            } == {dining_payment_id}
            assert not _line_is_unmatched(line_id)
            assert events.records == []


def test_the_batch_told_nothing_withdraws_no_match(app, seed_user, seed_periods_today):
    """``carry_forward_unpaid``'s default is a page that named nothing: a matched close refuses it.

    The safe default R-CC127 states (*"A button with no warning sends
    nothing"*): a caller that does not pass what its page showed cannot undo
    a match, where the ``Silent("CC-364")`` press this leaf deleted let it.
    """
    with app.app_context():
        source = _source(seed_periods_today)
        row, _line, match_id = _matched_envelope(
            seed_user, source, "Groceries", "125.00", "MARKET",
        )
        row_id = row.id
        balance_ctx = BalanceContext.build(seed_user["user"].id)
        current = balance_ctx.calendar().period_containing(display_today())

        with pytest.raises(PageOutOfDate):
            carry_forward_service.carry_forward_unpaid(
                source.id, current.period_id, seed_user["scenario"].id,
                balance_ctx=balance_ctx,
            )
        db.session.rollback()

        assert _status(row_id) == ref_cache.status_id(StatusEnum.PROJECTED)
        assert db.session.get(StatementMatch, match_id) is not None
