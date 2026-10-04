"""Plan step ``credit_card:CC-5-4a-5``: every popover press posts what its page named.

Ruling **R-CC127** (developer 2026-09-30), verbatim: *"Each warning also sends
back the bank lines it named, and the function compares them with what it
would undo. At 10:10 they differ, so nothing is saved and the popover reloads
with '...'; press again to go ahead ... A button with no warning sends
nothing, so it can undo a match only by naming its ruling in code, and a test
that submits exactly what the page shows fails if a warning is missing."*
Ruling **R-CC128** (developer 2026-10-04, "Redraw all"): *"The whole popover
redraws from what is true now ... Every box and warning comes from one moment,
so the second press is checked against a page that is current everywhere."*

What is graded here is what each press POSTS -- read off the rendered card,
never a hand-picked payload (the form's controls, a button's ``hx-vals``) --
and what the door does with it: the press goes ahead when its page named
exactly what it frees; a page out of date (a match undone in another tab, or
made after the card was drawn) is answered with the card redrawn and nothing
written, and the redrawn card's own press goes ahead; a request carrying no
field is the grid's silent Mark Paid (ruling **R-CC56**) or, at every other
door, a press that named nothing.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transaction_service
from app.services.statement_match import release_match
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests.test_routes.test_cc5_4a3_captions import (
    _claimed,
    _form_fields,
    _frees,
    _hotel,
    _matched,
    _matched_transfer,
    _popover,
    _reverted_envelope,
    _settle,
    _transfer_popover,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import a_transaction


def _vals(html, method, path):
    """What the button pressing *method* on *path* sends beside the request.

    Its ``hx-vals`` JSON, or ``{}`` for a button carrying none.
    """
    tag = re.search(
        rf'<button[^>]*hx-{method}="{re.escape(path)}"[^>]*>', html, re.S,
    )
    assert tag is not None, f"the card renders no {method} button for {path}"
    found = re.search(r"hx-vals='([^']*)'", tag.group(0))
    return json.loads(found.group(1)) if found else {}


def _paid_vals(html, txn_id):
    return _vals(html, "post", f"/transactions/{txn_id}/mark-done")


def _delete_vals(html, txn_id):
    return _vals(html, "delete", f"/transactions/{txn_id}")


def _undo_elsewhere(seed_user, line, account=None):
    """The statement screen's Undo, pressed in another tab (``release_match``)."""
    match_id = (
        db.session.query(StatementMatch.id)
        .join(StatementMatch.members)
        .filter_by(bank_statement_line_id=line.id)
        .scalar()
    )
    release_match(
        match_id, seed_user["user"].id,
        (account or seed_user["account"]).id,
    )
    db.session.commit()


def _redrawn(response, card_id):
    """Assert *response* is ruling R-CC128's redraw of *card_id*; return its body."""
    assert response.status_code == 400
    assert response.headers["HX-Retarget"] == f"#{card_id}"
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert response.headers["Shekel-Designed-Fragment"] == "1"
    html = response.get_data(as_text=True)
    assert f'id="{card_id}"' in html, "the body IS the card it replaces"
    assert "Nothing was saved: this page was out of date." in html
    assert "Here it is as it is now; press again to go ahead." in html
    return html


def _committed():
    """Commit, then forget every loaded row: what a later request would read.

    A refused press's rollback is graded by what a COMMIT after it writes, not
    by ``session.dirty`` (an error path's re-fetch autoflushes the staged
    change and empties ``dirty`` either way).
    """
    db.session.commit()
    db.session.expire_all()


def _zero_estimate_reverted_bill(seed_user, auth_client):
    """A Paid $120.00 Hotel whose payment is matched, reverted, its estimate set to $0.00.

    The estimate is changed through the card's own Save, as the owner would:
    reverting keeps the payment un-dated (R-BAL61) and its match standing, and
    a Paid that books the $0.00 estimate takes that payment off the books.
    """
    txn, line = _hotel(seed_user)
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    fields = _form_fields(_popover(auth_client, txn.id))
    fields["estimated_amount"] = "0.00"
    response = auth_client.patch(f"/transactions/{txn.id}", data=fields)
    assert response.status_code == 200
    db.session.expire_all()
    return db.session.get(Transaction, txn.id), line


class TestThePopoversPaidPostsWhatItsCaptionNamed:
    """Paid / Received on the card: the button's own ``hx-vals``."""

    def test_purchases_replacing_the_payment_post_its_line_and_withdraw(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)
            vals = _paid_vals(_popover(auth_client, txn.id), txn.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert response.status_code == 200
            _committed()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)

    def test_a_paid_that_records_zero_is_captioned_and_withdraws(
        self, app, auth_client, seed_user,
    ):
        """The $0.00 arm of Paid's caption (plan step CC-5-4a-5's extension)."""
        with app.app_context():
            txn, line = _zero_estimate_reverted_bill(seed_user, auth_client)
            html = _popover(auth_client, txn.id)
            assert (
                f"Marking this paid records $0.00 and {_frees(seed_user)}"
                in html
            )
            vals = _paid_vals(html, txn.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert response.status_code == 200
            _committed()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)
            settled = db.session.get(Transaction, txn.id)
            assert settled.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled.covering_movements == [], "a $0.00 record moves nothing"

    def test_a_stale_paid_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """Drawn while matched; the match undone in another tab; Paid pressed."""
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)
            stale = _paid_vals(_popover(auth_client, txn.id), txn.id)
            assert stale == {"shown_lines": str(line.id)}
            _undo_elsewhere(seed_user, line)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=stale,
            )

            html = _redrawn(response, f"txn-full-edit-{txn.id}")
            assert "purchases-withdraws-" not in html, "nothing is matched now"
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert len(row.covering_movements) == 1, "the kept payment stays"

            again = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data=_paid_vals(html, txn.id),
            )

            assert again.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.covering_movements == [], "the purchases are the record"

    def test_the_grid_one_click_posts_nothing_and_withdraws_silently(
        self, app, auth_client, seed_user, caplog,
    ):
        """The cell's checkmark sends no field: ruling R-CC56's silent door."""
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)

            with caplog.at_level(logging.INFO):
                response = auth_client.post(
                    f"/transactions/{txn.id}/mark-done",
                )

            assert response.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)
            (record,) = [
                record for record in caplog.records
                if getattr(record, "event", None)
                == EVT_STATEMENT_MATCH_WITHDRAWN
            ]
            assert record.silent_by == "R-CC56"


class TestTheSaveIsCheckedAgainstWhatItsCardNamed:
    """The card's Save: the form's hidden ``shown_lines``."""

    def test_a_stale_zero_save_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """R-CC128's own example: 0.00 typed at 10:00, the match undone at 10:05."""
        with app.app_context():
            txn, line = _hotel(seed_user)
            fields = _form_fields(_popover(auth_client, txn.id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            _undo_elsewhere(seed_user, line)

            response = auth_client.patch(f"/transactions/{txn.id}", data=fields)

            html = _redrawn(response, f"txn-full-edit-{txn.id}")
            assert "zero-withdraws-" not in html, "nothing is matched now"
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert [m.amount for m in row.covering_movements] == [
                Decimal("120.00"),
            ], "the $120.00 record stands"

            fresh = _form_fields(html)
            assert fresh["shown_lines"] == ""
            assert fresh["settled_amount"] == "120.00", "what you typed is gone"
            fresh["settled_amount"] = "0.00"
            again = auth_client.patch(f"/transactions/{txn.id}", data=fresh)

            assert again.status_code == 200
            _committed()
            assert db.session.get(Transaction, txn.id).covering_movements == []

    def test_a_save_without_the_field_is_refused_on_the_cell(
        self, app, auth_client, seed_user,
    ):
        """No card posts this; a request that names nothing and would free a line is refused."""
        with app.app_context():
            txn, line = _hotel(seed_user)
            fields = _form_fields(_popover(auth_client, txn.id))
            del fields["shown_lines"]
            fields["settled_amount"] = "0.00"

            response = auth_client.patch(f"/transactions/{txn.id}", data=fields)

            assert response.status_code == 400
            assert "HX-Retarget" not in response.headers, (
                "a request no popover posted has no card to redraw"
            )
            assert "this page was out of date" in response.get_data(as_text=True)
            _committed()
            assert _claimed(seed_user, line)
            assert db.session.query(StatementMatch).count() == 1


class TestTheDeleteDialogPostsWhatItNamed:
    """The card's Delete: the button's ``hx-vals``, a DELETE's query string."""

    def test_the_delete_posts_its_dialogs_line_and_withdraws(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = _matched(seed_user, txn)
            txn_id = txn.id
            vals = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=vals,
            )

            assert response.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)

    def test_a_delete_drawn_before_the_match_is_redrawn(
        self, app, auth_client, seed_user,
    ):
        """The other direction: the page named nothing, and the press would free a line."""
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            txn_id = txn.id
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert stale == {"shown_lines": ""}
            line = _matched(seed_user, txn)

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            html = _redrawn(response, f"txn-full-edit-{txn_id}")
            _committed()
            assert db.session.get(Transaction, txn_id) is not None
            assert _claimed(seed_user, line)

            again = auth_client.delete(
                f"/transactions/{txn_id}",
                query_string=_delete_vals(html, txn_id),
            )

            assert again.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)


class TestTheTransferPopover:
    """The transfer card: its Save's and its Paid's ``shown_lines``."""

    def test_a_stale_transfer_zero_save_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            fields = _form_fields(_transfer_popover(auth_client, xfer_id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            _undo_elsewhere(seed_user, line)

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fields,
            )

            html = _redrawn(response, f"xfer-full-edit-{xfer_id}")
            _committed()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )

            fresh = _form_fields(html)
            assert fresh["shown_lines"] == ""
            fresh["settled_amount"] = "0.00"
            again = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fresh,
            )

            assert again.status_code == 200

    def test_the_transfer_paid_posts_its_cards_empty_field(
        self, app, auth_client, seed_user,
    ):
        """No arm prices a transfer at $0.00, so its Paid names nothing, and says so."""
        with app.app_context():
            xfer, _lines = _matched_transfer(seed_user, legs=())
            transfer_service_revert(seed_user, xfer.id)
            html = _transfer_popover(auth_client, xfer.id)
            fields = _form_fields(html)
            assert fields["shown_lines"] == ""
            paid = re.search(
                rf'<button[^>]*hx-post="/transfers/instance/{xfer.id}/mark-done"[^>]*>',
                html, re.S,
            )
            assert paid is not None
            assert f'hx-include="#xfer-shown-lines-{xfer.id}"' in paid.group(0)
            assert f'id="xfer-shown-lines-{xfer.id}"' in html

            response = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"shown_lines": fields["shown_lines"]},
            )

            assert response.status_code == 200


def transfer_service_revert(seed_user, xfer_id):
    """Set a transfer back to Projected, as the card's Status dropdown would."""
    # pylint: disable=import-outside-toplevel
    from app.services import transfer_service

    transfer_service.update_transfer(
        xfer_id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


class TestTheTransferInstanceDeleteRefusesRatherThanFailing:
    """The instance DELETE no template renders: a refusal, not a 500."""

    def test_a_hard_delete_that_would_free_a_line_is_refused_and_writes_nothing(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            assert xfer.transfer_template_id is None, "an ad-hoc pair deletes HARD"

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 400
            assert response.headers["Shekel-Designed-Fragment"] == "1"
            assert "this page was out of date" in response.get_data(as_text=True)
            _committed()
            kept = db.session.get(Transfer, xfer_id)
            assert kept is not None and not kept.is_deleted
            assert _claimed(seed_user, line)
