"""One check per save: the press a door opens around its unit of work.

Plan step ``credit_card:CC-5-4a-5``, leaf 5c-2b, ruling **R-CC135**
(developer 2026-10-04, "One check per save"): *"Every button that can undo a
match checks once per save: undoing one the page did not name stops it at
once, and before saving, what it undid must equal what the page named for
what you ticked, or nothing saves and the page redraws"*.  What the press
adds over the per-call comparison it replaced, each case through the door
that reaches it:

* the withdrawal EVENT is logged at the save's close, so a save refused after
  a call withdrew logs nothing its rollback undid;
* a press a door forgot to open, or reuses, is refused loudly;
* a reconcile-panel tick whose caption named a line its save never frees
  refuses the save (ledger row **BAL-597**): the per-call comparison ran only
  inside the match step, which that tick never reached.

And what it KEEPS from that comparison: a call freeing a line the page did
not name is still refused before it withdraws, so no later step's refusal
masks the redraw.
"""

from __future__ import annotations

import re

import pytest

from app.exceptions import PageOutOfDate
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import entry_service, match_press
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests.test_routes.test_cc5_4a5c_reconcile_panel import (
    _kept_payment_envelope,
    _panel,
    _payload,
    _post,
    _true_up,
)
from tests.test_services.test_cc5_4a5_shown_lines import (
    _Events,
    _matched_hotel,
    _record_zero,
)


def _withdrawals(events):
    """The withdrawal events among *events*' records."""
    return [
        record for record in events.records
        if getattr(record, "event", None) == EVT_STATEMENT_MATCH_WITHDRAWN
    ]


class _AfterTheCall(Exception):
    """A door's own failure after its match step withdrew."""


class TestTheEventIsLoggedAtTheClose:
    """R-CC135: the withdrawal event belongs to the save, not to the call."""

    def test_a_save_that_closes_logs_its_withdrawal_once(self, app, seed_user):
        """Hotel's $0.00 frees HOTEL -$120.00, which the page named: one event, at the close."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)

            with _Events() as events:
                with match_press.Press(
                    match_press.Shown(frozenset({line.id})),
                ) as press:
                    _record_zero(hotel, press=press)
                    assert not _withdrawals(events), "logged before the close"
                db.session.commit()

            assert len(_withdrawals(events)) == 1
            assert db.session.get(StatementMatch, match_id) is None

    def test_a_save_refused_after_the_call_logs_nothing(self, app, seed_user):
        """The same withdrawal, then the door fails: rolled back, and no event claims it happened."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)

            with _Events() as events:
                with pytest.raises(_AfterTheCall):
                    with match_press.Press(
                        match_press.Shown(frozenset({line.id})),
                    ) as press:
                        _record_zero(hotel, press=press)
                        raise _AfterTheCall
                db.session.rollback()

            assert not _withdrawals(events)
            assert db.session.get(StatementMatch, match_id) is not None


class TestAnUnnamedLineStopsTheSaveAtOnce:
    """R-CC135: *"undoing one the page did not name stops it at once"*, for an OWNER's press.

    The close would refuse the same save, so what "at once" adds is WHICH
    refusal the owner meets and what the call wrote first: the call that
    frees a line its page did not name is refused before it withdraws
    anything, so a later step's own refusal never masks the redraw.
    """

    def test_the_call_is_refused_before_it_withdraws(self, app, seed_user):
        """Hotel's $0.00 would free HOTEL -$120.00, which the page never named.

        Read BEFORE the rollback, so a withdrawal the call wrote and the
        rollback then undid would still show here.
        """
        with app.app_context():
            hotel, _line, match_id = _matched_hotel(seed_user)

            with _Events() as events:
                with pytest.raises(PageOutOfDate):
                    with match_press.Press(match_press.NOTHING_SHOWN) as press:
                        _record_zero(hotel, press=press)
                        # A later step of the same save, refusing in its own
                        # words: never reached.
                        raise _AfterTheCall

                assert db.session.get(StatementMatch, match_id) is not None
                db.session.rollback()

            assert not _withdrawals(events)
            assert db.session.get(StatementMatch, match_id) is not None


class TestAPressIsOpenedOnceAroundOneSave:
    """A door that forgot the ``with``, or reused a press, fails loud."""

    def test_a_press_never_opened_refuses_the_call(self, app, seed_user):
        """A withdrawal outside an open press is a door's bug: RuntimeError, nothing written."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)

            with pytest.raises(RuntimeError, match="outside an open press"):
                _record_zero(
                    hotel,
                    press=match_press.Press(
                        match_press.Shown(frozenset({line.id})),
                    ),
                )

            db.session.rollback()
            assert db.session.get(StatementMatch, match_id) is not None

    def test_a_closed_press_refuses_a_second_save(self, app, seed_user):
        """One press, one save: reusing it after its close fails loud."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)
            press = match_press.Press(match_press.Shown(frozenset({line.id})))
            with press:
                pass  # a save that reached no match step: not graded

            with pytest.raises(RuntimeError):
                with press:
                    _record_zero(hotel, press=press)

            db.session.rollback()
            assert db.session.get(StatementMatch, match_id) is not None

    def test_a_closed_press_handed_to_a_later_call_refuses_it(self, app, seed_user):
        """A press threaded past its ``with``: the next call fails loud, nothing withdrawn."""
        with app.app_context():
            hotel, line, match_id = _matched_hotel(seed_user)
            press = match_press.Press(match_press.Shown(frozenset({line.id})))
            with press:
                pass

            with pytest.raises(RuntimeError, match="outside an open press"):
                _record_zero(hotel, press=press)

            assert db.session.get(StatementMatch, match_id) is not None
            db.session.rollback()
            assert db.session.get(StatementMatch, match_id) is not None


def test_a_panel_tick_whose_save_frees_nothing_it_named_is_refused(
    app, auth_client, seed_user, seed_periods_today,
):
    """BAL-597: the caption named the kept payment's line; another tab took the purchase away.

    The panel is drawn while Groceries holds a purchase, so its tick is
    captioned "Closing it from its purchases withdraws 1 accepted match" and
    posts that line.  Another tab deletes the purchase; the tick now settles
    the row from its figure, keeping its payment on its own account, and
    reaches no match step.  The per-call comparison never ran and the row
    booked with the match standing; the save's close compares what the page
    named with what the save freed -- nothing -- and refuses it.
    """
    with app.app_context():
        checking_id = seed_user["account"].id
        envelope, _line, match_id = _kept_payment_envelope(
            seed_user, seed_periods_today[0],
        )
        envelope_id = envelope.id
        _true_up(auth_client, checking_id)
        body = _panel(auth_client, checking_id)
        assert "Closing it from its purchases withdraws 1 accepted match" in body

        db.session.expire_all()
        (purchase,) = db.session.get(Transaction, envelope_id).purchases
        entry_service.delete_entry(purchase.id, seed_user["user"].id)
        db.session.commit()

        response = _post(auth_client, checking_id, _payload(
            body, {("transaction_ids", str(envelope_id))},
        ))

        assert re.search(r"Nothing was saved", response.data.decode())
        db.session.expire_all()
        row = db.session.get(Transaction, envelope_id)
        assert not row.status.is_settled
        (kept,) = row.covering_movements
        assert kept.settled_on is None
        assert db.session.get(StatementMatch, match_id) is not None
