"""Plan step ``credit_card:CC-5-4a-5``: a row's delete dialog posts what it named.

Split out of ``test_cc5_4a5_popover_presses`` (which grades every other popover
press) when that module met ``too-many-lines``.  Ruling **R-CC127** (developer
2026-09-30): the dialog sends back the bank lines its clause named, and the
removal act compares them with what it would free.  Ruling **R-CC131**
(developer 2026-10-04, "Refuse and redraw", fulfilling ruling **R-CC96**'s
clause), verbatim: *"The dialog also sends back the purchases it named. At
10:02 they differ, so nothing is deleted and the popover redraws under your
'Redraw all' ruling, its dialog now asking 'Delete Groceries and the 1
purchase under it?' with the $12.34 shown. You decide with it in view."*

Every request is read off the rendered card (the Delete button's
``hx-vals``), never a hand-picked payload; a refusal is graded by a commit and
a re-read.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services import entry_service
from tests._test_helpers import generate_row_of, make_expense_template, typed
from tests.test_routes.test_cc5_4a3_captions import (
    _claimed,
    _day,
    _matched,
    _popover,
    _settle,
)
from tests.test_routes.test_cc5_4a5_popover_presses import (
    _committed,
    _delete_vals,
    _redrawn,
)
from tests.test_routes.test_cc5_4a5b_purchase_and_credit_doors import (
    _undone_in_another_tab,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import a_transaction


class TestTheDeleteDialogPostsWhatItNamed:
    """The card's Delete: the button's ``hx-vals``, a DELETE's query string."""

    def test_the_delete_posts_its_dialogs_line_and_withdraws(
        self, app, auth_client, seed_user,
    ):
        """The dialog names the HOTEL line; the button sends its id; the delete withdraws it."""
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = _matched(seed_user, txn)
            txn_id = txn.id
            vals = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert vals == {"shown_lines": str(line.id), "shown_purchases": ""}

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
            assert stale == {"shown_lines": "", "shown_purchases": ""}
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

    def test_a_delete_naming_a_line_another_tab_freed_is_redrawn(
        self, app, auth_client, seed_user,
    ):
        """R-CC135: the dialog named the HOTEL line and another tab undid that match.

        Refused and redrawn; the redrawn dialog names no line and goes ahead.
        """
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = _matched(seed_user, txn)
            txn_id = txn.id
            named = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert named == {"shown_lines": str(line.id), "shown_purchases": ""}
            _undone_in_another_tab(seed_user, line)

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=named,
            )

            html = _redrawn(response, f"txn-full-edit-{txn_id}")
            _committed()
            assert db.session.get(Transaction, txn_id) is not None

            fresh = _delete_vals(html, txn_id)
            assert fresh == {"shown_lines": "", "shown_purchases": ""}
            again = auth_client.delete(
                f"/transactions/{txn_id}", query_string=fresh,
            )

            assert again.status_code == 200
            _committed()
            assert db.session.get(Transaction, txn_id) is None


    def test_a_delete_without_the_field_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Review finding M2 (MG): the DELETE's own default is a press that named nothing.

        No card posts this; a request without the field that would free a
        line is refused on the cell, and the row and its match stand.
        """
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = _matched(seed_user, txn)
            txn_id = txn.id

            response = auth_client.delete(f"/transactions/{txn_id}")

            assert response.status_code == 400
            assert "HX-Retarget" not in response.headers
            assert "this page was out of date" in response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, txn_id) is not None
            assert _claimed(seed_user, line)


class TestTheDeleteDialogIsCheckedAgainstThePurchasesItNamed:
    """Ruling R-CC131 (developer 2026-10-04, "Refuse and redraw"), fulfilling R-CC96's clause."""

    def test_a_purchase_added_after_the_dialog_was_drawn_is_refused_and_redrawn(
        self, app, auth_client, seed_user,
    ):
        """R-CC131's own example: a dialog drawn at 10:00, a $12.34 purchase at 10:01, Delete.

        Nothing is deleted, the popover is redrawn with the purchase named
        above it, and the redrawn dialog -- now counting it -- goes ahead.
        """
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert stale == {"shown_lines": "", "shown_purchases": ""}
            kroger = _kroger(seed_user, txn_id)

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            assert response.status_code == 400
            assert response.headers["HX-Retarget"] == f"#txn-full-edit-{txn_id}"
            assert response.headers["HX-Reswap"] == "outerHTML"
            html = response.get_data(as_text=True)
            day = _day(seed_user)
            assert (
                "Nothing was saved: this page was out of date. Groceries now "
                "holds 1 purchase the page did not name "
                f"(Kroger, {day.month}/{day.day})." in html
            )
            assert "Here it is as it is now; press again to go ahead." in html
            assert "and the 1 purchase under it?" in html, (
                "R-CC131's picked question, word for word"
            )
            # "... with the $12.34 shown".
            assert "$12.34" in _the_cards_purchase_list(auth_client, html, txn_id)
            _committed()
            row = db.session.get(Transaction, txn_id)
            assert not row.is_deleted
            assert [entry.id for entry in row.purchases] == [kroger]

            fresh = _delete_vals(html, txn_id)
            assert fresh == {"shown_lines": "", "shown_purchases": str(kroger)}
            again = auth_client.delete(
                f"/transactions/{txn_id}", query_string=fresh,
            )

            assert again.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn_id)
            assert row.is_deleted, "a recurring row stays as a tombstone"
            assert row.entries == [], "and its purchase went with it, named"

    def test_a_purchase_named_and_since_removed_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Equality both ways: the dialog counted a purchase another tab has since deleted."""
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            kroger = _kroger(seed_user, txn_id)
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert stale == {"shown_lines": "", "shown_purchases": str(kroger)}
            entry_service.delete_entry(kroger, seed_user["user"].id)
            db.session.commit()

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            assert response.status_code == 400
            body = response.get_data(as_text=True)
            assert (
                "1 purchase the page named is no longer under Groceries."
                in body
            )
            assert "the page did not name" not in body
            _committed()
            assert not db.session.get(Transaction, txn_id).is_deleted

    def test_a_purchase_swapped_for_another_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Equality, not a count: the named Kroger gone, a Costco added while the dialog is open.

        The second review's M2: a comparison of COUNTS survived every test, and
        would delete $300.00 no dialog named.
        """
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            kroger = _kroger(seed_user, txn_id)
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert stale == {"shown_lines": "", "shown_purchases": str(kroger)}
            entry_service.delete_entry(kroger, seed_user["user"].id)
            costco = _bought(seed_user, txn_id, "Costco", "300.00")

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            assert response.status_code == 400
            day = _day(seed_user)
            body = response.get_data(as_text=True)
            assert (
                "Groceries now holds 1 purchase the page did not name "
                f"(Costco, {day.month}/{day.day}). 1 purchase the page named "
                "is no longer under Groceries." in body
            )
            _committed()
            row = db.session.get(Transaction, txn_id)
            assert not row.is_deleted
            assert [entry.id for entry in row.purchases] == [costco]

    def test_a_delete_without_the_field_over_a_purchase_is_refused(
        self, app, auth_client, seed_user,
    ):
        """No dialog posts this; a request naming no purchase does not delete one."""
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            kroger = _kroger(seed_user, txn_id)

            response = auth_client.delete(f"/transactions/{txn_id}")

            assert response.status_code == 400
            assert "HX-Retarget" not in response.headers
            _committed()
            row = db.session.get(Transaction, txn_id)
            assert not row.is_deleted
            assert [entry.id for entry in row.purchases] == [kroger]


class TestTheRefusalSaysWhatChanged:
    """The refusal's sentence: only what the page did not name, then what is gone."""

    def test_a_named_purchase_still_there_is_not_listed(
        self, app, auth_client, seed_user,
    ):
        """The dialog named Kroger; Aldi was added: only Aldi is listed, nothing is gone."""
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            _kroger(seed_user, txn_id)
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            _bought(seed_user, txn_id, "Aldi", "8.00")

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            assert response.status_code == 400
            body = response.get_data(as_text=True)
            day = _day(seed_user)
            assert (
                "Groceries now holds 1 purchase the page did not name "
                f"(Aldi, {day.month}/{day.day})." in body
            )
            assert "Kroger" not in body.split("Here it is as it is now")[0]
            assert "no longer under" not in body

    def test_several_added_and_several_gone_are_counted_and_listed(
        self, app, auth_client, seed_user,
    ):
        """Two named purchases gone, two others added: the plural forms and the list."""
        with app.app_context():
            txn = _groceries(seed_user)
            txn_id = txn.id
            first = _kroger(seed_user, txn_id)
            second = _bought(seed_user, txn_id, "Aldi", "8.00")
            stale = _delete_vals(_popover(auth_client, txn_id), txn_id)
            assert stale["shown_purchases"] == ",".join(
                str(i) for i in sorted((first, second))
            )
            entry_service.delete_entry(first, seed_user["user"].id)
            entry_service.delete_entry(second, seed_user["user"].id)
            db.session.commit()
            # Two days, so the listed order is the purchases' own (entries
            # are ordered by the day bought), not an accident of a tie.
            later = _day(seed_user) + timedelta(days=1)
            _bought(seed_user, txn_id, "Target", "45.00", on=later)
            _bought(seed_user, txn_id, "Costco", "300.00")

            response = auth_client.delete(
                f"/transactions/{txn_id}", query_string=stale,
            )

            assert response.status_code == 400
            day = _day(seed_user)
            assert (
                "Groceries now holds 2 purchases the page did not name "
                f"(Costco, {day.month}/{day.day}; Target, {later.month}/"
                f"{later.day}). 2 purchases the page named are no longer under "
                "Groceries." in response.get_data(as_text=True)
            )
            _committed()
            assert len(db.session.get(Transaction, txn_id).purchases) == 2


def _the_cards_purchase_list(client, html, txn_id):
    """The purchase list a drawn card loads, fetched as htmx would fire its loader.

    The loader is read off the card itself (its ``hx-get`` on load), and the
    list prints each figure through the one ``money`` macro.
    """
    loader = re.search(
        rf'<div hx-get="([^"]+)"\s+hx-trigger="load"[^>]*'
        rf'id="entry-list-{txn_id}"', html,
    )
    assert loader is not None, "the card loads no purchase list"
    listed = client.get(loader.group(1))
    assert listed.status_code == 200
    return listed.get_data(as_text=True)


def _bought(seed_user, txn_id, description, amount, on=None):
    """A purchase of *amount* at *description* on *on* (default ``_day``) under *txn_id*."""
    entry = entry_service.create_entry(
        txn_id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=on or _day(seed_user),
        ),
    )
    db.session.commit()
    return entry.id


def _groceries(seed_user):
    """This period's recurring $300.00 Groceries envelope, holding no purchase."""
    template = make_expense_template(
        db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Rent", is_envelope=True,
    )
    txn = generate_row_of(template, seed_user["bootstrap_period"])
    db.session.commit()
    return txn


def _kroger(seed_user, txn_id):
    """A $12.34 Kroger purchase added under *txn_id* -- the companion's, at 10:01."""
    return _bought(seed_user, txn_id, "Kroger", "12.34")
