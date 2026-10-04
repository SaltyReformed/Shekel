"""Plan step ``credit_card:CC-5-4a-5b``: the purchase X and the Credit doors say what they free.

Ruling **R-CC80** (developer 2026-09-23, "Every door warns"): *"The purchase X
and all three card-payback buttons name the bank line before you press, using
the same data the button acts on, as the popovers do. Mark Paid stays the only
silent door. [The] X would ask: 'Delete [the purchase] [its figure]? The
bank's 9/21 -[figure] line will be unexplained again on your statement
screen.'"*  Ruling **R-CC127** (2026-09-30): each warning sends back the lines
it named and the act compares them.  Ruling **R-CC132** (2026-10-04, "Refuse,
shown first"): *"On the companion page a matched purchase has no X; in its
place: 'Matched to the bank statement: only the account owner can delete
it.' A page drawn before the match still shows the X; pressing it is
refused: 'Kroger is matched to a line on the bank statement, so only the
account owner can delete it.' ... The last-card-purchase case is refused on
press the same way."*

What is graded is what each control POSTS -- read off the rendered page (the
X's ``hx-confirm`` and ``hx-vals``, the edit form's controls, the popover's
buttons and form), never a hand-picked payload -- and what the door does with
it, read back after a commit.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import parse_qsl
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    credit_workflow,
    entry_service,
    match_withdrawal,
    transaction_service,
)
from tests._test_helpers import generate_row_of, make_expense_template, typed
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_cc5_4a3_captions import (
    _claimed,
    _day,
    _form_fields,
    _popover,
)
from tests.test_routes.test_cc5_4a5_popover_presses import _vals
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)
from app.services.statement_match import accept_match

#: The bank's own words for the Kroger line -- distinct from the purchase's
#: description, so a page that leaked the line would be caught naming it.
_KROGER_LINE = "KROGER #4471 ATLANTA GA"


def _committed():
    """End the request's work and read everything afresh, as a new page would."""
    db.session.commit()
    db.session.expire_all()


def _accept(seed_user, line, *, entries=(), transactions=()):
    """Accept *line* against *entries* / *transactions*, as the statement screen does."""
    scope = a_scope(seed_user)
    accept_match(
        a_submission(
            scope, lines=[line], entries=list(entries),
            transactions=list(transactions),
        ),
        scope,
    )
    db.session.commit()


def _groceries(seed_user, *, companion_visible=False):
    """A Projected $300.00 Groceries envelope on checking."""
    template = make_expense_template(
        db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
        companion_visible=companion_visible,
    )
    txn = generate_row_of(template, seed_user["bootstrap_period"])
    db.session.commit()
    return txn


def _purchase(seed_user, txn, amount, description, *, is_credit=False):
    entry = entry_service.create_entry(
        txn.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=_day(seed_user), is_credit=is_credit,
        ),
    )
    db.session.commit()
    return entry


def _matched_kroger(seed_user, *, companion_visible=False):
    """Groceries holding a $12.34 Kroger purchase matched to the bank's -$12.34 line."""
    txn = _groceries(seed_user, companion_visible=companion_visible)
    kroger = _purchase(seed_user, txn, "12.34", "Kroger")
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-12.34",
        posted_on=_day(seed_user), description=_KROGER_LINE,
    )
    db.session.commit()
    _accept(seed_user, line, entries=[kroger])
    return txn, kroger, line


def _card_payback_matched(seed_user, *, companion_visible=False):
    """Groceries' one $60.00 card purchase, its payback's payment matched, the payback reverted.

    The payback is settled by the match (its payment the member), then set
    back to Projected, which keeps the payment un-dated and the act on it
    (ruling R-BAL61) -- the one shape in which removing the last card
    purchase deletes a payback a match still names.
    """
    txn = _groceries(seed_user, companion_visible=companion_visible)
    a_later_period(seed_user)
    db.session.commit()
    card = _purchase(seed_user, txn, "60.00", "Kroger", is_credit=True)
    payback = credit_workflow.get_active_payback(txn.id)
    assert payback is not None
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-60.00",
        posted_on=_day(seed_user), description="CARD PAYMENT",
    )
    db.session.commit()
    _accept(seed_user, line, transactions=[payback])
    transaction_service.apply_requested_status(
        payback, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    assert _claimed(seed_user, line)
    return txn, card, payback, line


def _credit_row_matched(seed_user):
    """A $200.00 bill marked Credit, its payback's payment matched to the card payment line."""
    source = a_transaction(
        seed_user, name="Rogue Equipment", amount="200.00", template=False,
    )
    db.session.flush()
    a_later_period(seed_user)
    credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
    db.session.commit()
    payback = credit_workflow.get_active_payback(source.id)
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-200.00",
        posted_on=_day(seed_user), description="CARD PAYMENT",
    )
    db.session.commit()
    _accept(seed_user, line, transactions=[payback])
    return source, payback, line


def _list(client, txn_id, *, editing=None):
    """The purchase list as the popover and the phone card draw it."""
    query = {"editing": editing} if editing is not None else {}
    response = client.get(
        f"/transactions/{txn_id}/entries", query_string=query,
    )
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _x(page, txn_id, entry_id):
    """The X button's tag for *entry_id*, or ``None`` when the list draws none."""
    tag = re.search(
        rf'<button[^>]*hx-delete="[^"]*/transactions/{txn_id}/entries/'
        rf'{entry_id}(?:\?[^"]*)?"[^>]*>',
        page, re.S,
    )
    return tag.group(0) if tag else None


def _x_press(page, txn_id, entry_id):
    """What pressing the X sends: its path and its query.

    The query is the URL's own (``?host=``) with the X's ``hx-vals`` added,
    as htmx sends a DELETE's values.  Returns ``(path, query, vals)``, *vals*
    being the ``hx-vals`` alone (``{}`` for a button carrying none).
    """
    tag = _x(page, txn_id, entry_id)
    assert tag is not None, "the list draws no X for this purchase"
    url = html_lib.unescape(re.search(r'hx-delete="([^"]*)"', tag).group(1))
    path, _sep, query = url.partition("?")
    found = re.search(r"hx-vals='([^']*)'", tag)
    vals = json.loads(found.group(1)) if found else {}
    return path, {**dict(parse_qsl(query, keep_blank_values=True)), **vals}, vals


def _confirm(tag):
    """The sentence the X's browser confirm asks."""
    return html_lib.unescape(re.search(r'hx-confirm="([^"]*)"', tag).group(1))


def _edit_form(page):
    """What a browser submits for the inline edit form, control by control."""
    start = page.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(page[start:page.index("</form>", start)])
    return list(reader.fields), re.search(
        r'hx-patch="([^"]*)"', page[start:],
    ).group(1)


def _line_day(seed_user):
    day = _day(seed_user)
    return f"{day.month}/{day.day}"


class TestTheOwnersXNamesWhatItFrees:
    """The owner's X: R-CC80's sentence, the lines sent back, the act comparing them."""

    def test_the_x_names_the_purchases_line_and_its_press_withdraws_it(
        self, app, auth_client, seed_user,
    ):
        """Kroger's X asks in R-CC80's words, sends the line, and the press frees it."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)

            page = _list(auth_client, txn.id)
            tag = _x(page, txn.id, kroger.id)
            assert _confirm(tag) == (
                f"Delete Kroger $12.34? The bank's {_line_day(seed_user)} "
                "-$12.34 line will be unexplained again on your statement "
                "screen."
            )
            path, query, vals = _x_press(page, txn.id, kroger.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=query)

            assert response.status_code == 200
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is None
            assert not _claimed(seed_user, line)

    def test_an_unmatched_purchase_asks_as_before_and_sends_nothing(
        self, app, auth_client, seed_user,
    ):
        """A purchase no match names: "Delete this entry?", an empty field, the press goes ahead."""
        with app.app_context():
            txn = _groceries(seed_user)
            plain = _purchase(seed_user, txn, "8.00", "Publix")

            page = _list(auth_client, txn.id)
            assert _confirm(_x(page, txn.id, plain.id)) == "Delete this entry?"
            path, query, vals = _x_press(page, txn.id, plain.id)
            assert vals == {"shown_lines": ""}

            assert auth_client.delete(path, query_string=query).status_code == 200
            _committed()
            assert db.session.get(TransactionEntry, plain.id) is None

    def test_a_list_drawn_before_the_match_is_refused_and_drawn_again(
        self, app, auth_client, seed_user,
    ):
        """The X read before another tab matched Kroger: refused, redrawn, the redrawn X goes ahead."""
        with app.app_context():
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            stale_path, stale_query, stale_vals = _x_press(
                _list(auth_client, txn.id), txn.id, kroger.id,
            )
            assert stale_vals == {"shown_lines": ""}
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-12.34",
                posted_on=_day(seed_user), description=_KROGER_LINE,
            )
            db.session.commit()
            _accept(seed_user, line, entries=[kroger])

            refused = auth_client.delete(stale_path, query_string=stale_query)

            assert refused.status_code == 400
            assert refused.headers["Shekel-Designed-Fragment"] == "1"
            body = refused.get_data(as_text=True)
            assert "Nothing was saved: this page was out of date." in body
            assert "Here it is as it is now; press again to go ahead." in body
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)

            path, query, vals = _x_press(body, txn.id, kroger.id)
            assert vals == {"shown_lines": str(line.id)}
            assert auth_client.delete(path, query_string=query).status_code == 200
            _committed()
            assert not _claimed(seed_user, line)

    def test_a_request_naming_no_line_is_refused(
        self, app, auth_client, seed_user,
    ):
        """A DELETE carrying no field named nothing (R-CC127), so it frees nothing."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)
            path, query, vals = _x_press(
                _list(auth_client, txn.id), txn.id, kroger.id,
            )
            assert vals == {"shown_lines": str(line.id)}
            query.pop("shown_lines")

            refused = auth_client.delete(path, query_string=query)

            assert refused.status_code == 400
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)


class TestTheLastCardPurchaseNamesItsPaybacksLine:
    """The X and the CC un-tick on an envelope's last card purchase (R-CC80)."""

    def test_the_x_names_the_paybacks_line_and_one_press_takes_both(
        self, app, auth_client, seed_user,
    ):
        """The X deletes Kroger AND its payback; its sentence names the payback's line.

        The case two removal acts refused at every try (the payback is on the
        envelope's account, so the purchase's act met the payback's named
        line): one act takes both.
        """
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)

            page = _list(auth_client, txn.id)
            assert _confirm(_x(page, txn.id, card.id)) == (
                f"Delete Kroger $60.00? The bank's {_line_day(seed_user)} "
                "-$60.00 line will be unexplained again on your statement "
                "screen."
            )
            path, query, vals = _x_press(page, txn.id, card.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=query)

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(TransactionEntry, card.id) is None
            assert db.session.get(Transaction, payback.id) is None
            assert credit_workflow.get_active_payback(txn.id) is None
            assert not _claimed(seed_user, line)

    def test_the_un_tick_names_the_paybacks_line_and_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The edit form names the line under CC and posts it; un-ticking frees it."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)

            page = _list(auth_client, txn.id, editing=card.id)
            assert (
                "Un-ticking CC deletes this envelope's CC payback and withdraws "
                "1 accepted match, so 1 bank line is unexplained again on your "
                f"statement screen: {_line_day(seed_user)} CARD PAYMENT -$60.00."
                in " ".join(page.split())
            )
            fields, url = _edit_form(page)
            assert ("shown_lines", str(line.id)) in fields
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]
            assert ("is_credit", "false") in unticked

            response = auth_client.patch(url, data=MultiDict(unticked))

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is False
            assert db.session.get(Transaction, payback.id) is None
            assert not _claimed(seed_user, line)

    def test_an_un_tick_from_a_form_that_named_nothing_is_refused(
        self, app, auth_client, seed_user,
    ):
        """The form read before the payback was matched: refused, nothing changes."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=card.id))
            stale = [
                ("shown_lines", "") if name == "shown_lines" else (name, value)
                for name, value in fields if (name, value) != ("is_credit", "true")
            ]

            refused = auth_client.patch(url, data=MultiDict(stale))

            assert refused.status_code == 400
            assert "Nothing was saved: this page was out of date." in (
                refused.get_data(as_text=True)
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is True
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)


class TestACompanionIsNeverShownTheOwnersLines:
    """Ruling R-CC132 ("Refuse, shown first"), and R-CC130 before it."""

    def test_a_matched_purchase_has_no_x_and_says_why(
        self, app, companion_client, seed_user,
    ):
        """No X over Kroger, the ruling's note in its place, and the line named nowhere."""
        with app.app_context():
            txn, kroger, _line = _matched_kroger(
                seed_user, companion_visible=True,
            )
            publix = _purchase(seed_user, txn, "8.00", "Publix")

            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)

            assert _x(page, txn.id, kroger.id) is None
            assert (
                "Matched to the bank statement: only the account owner can "
                "delete it." in " ".join(page.split())
            )
            assert _KROGER_LINE not in page
            assert "unexplained" not in page
            plain = _x(page, txn.id, publix.id)
            assert plain is not None, "an unmatched purchase keeps its X"
            assert _confirm(plain) == "Delete this entry?"
            assert "hx-vals" not in plain, "no companion surface names a line"

    def test_a_companions_x_is_refused_with_the_rulings_sentence(
        self, app, companion_client, seed_user,
    ):
        """A page drawn before the match still shows the X: the press is refused.

        Posting the right line id changes nothing: a companion's field is
        dropped, never read.
        """
        with app.app_context():
            txn = _groceries(seed_user, companion_visible=True)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            path, query, vals = _x_press(page, txn.id, kroger.id)
            assert not vals
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-12.34",
                posted_on=_day(seed_user), description=_KROGER_LINE,
            )
            db.session.commit()
            _accept(seed_user, line, entries=[kroger])

            refused = companion_client.delete(
                path, query_string={**query, "shown_lines": str(line.id)},
            )

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert (
                "Kroger is matched to a line on the bank statement, so only "
                "the account owner can delete it." in body
            )
            assert _KROGER_LINE not in body
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_x_on_the_last_card_purchase_is_refused(
        self, app, companion_client, seed_user,
    ):
        """Kroger frees no line of its own; its payback's would go: refused, the payback named."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(
                seed_user, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            path, query, vals = _x_press(page, txn.id, card.id)
            assert not vals
            assert _confirm(_x(page, txn.id, card.id)) == "Delete this entry?"

            refused = companion_client.delete(path, query_string=query)

            assert refused.status_code == 400
            assert (
                "Groceries's card payback is matched to a line on the bank "
                "statement, so only the account owner can change its last "
                "card purchase."
                in html_lib.unescape(refused.get_data(as_text=True))
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id) is not None
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_un_tick_is_refused(
        self, app, companion_client, seed_user,
    ):
        """The companion's edit form names nothing; its un-tick is refused, nothing changes."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(
                seed_user, companion_visible=True,
            )
            page = _list(companion_client, txn.id, editing=card.id)
            assert "Un-ticking CC" not in page
            fields, url = _edit_form(page)
            assert all(name != "shown_lines" for name, _value in fields)
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]

            refused = companion_client.patch(url, data=MultiDict(unticked))

            assert refused.status_code == 400
            assert (
                "Groceries's card payback is matched to a line on the bank "
                "statement, so only the account owner can change its last "
                "card purchase."
                in html_lib.unescape(refused.get_data(as_text=True))
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is True
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)


class TestLeavingCreditNamesThePaybacksLine:
    """Undo CC and the popover's Status leaving Credit (R-CC80)."""

    def test_the_card_names_the_line_and_undo_cc_sends_it(
        self, app, auth_client, seed_user,
    ):
        """The caption beside Undo CC; the button's line; the press frees it."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)

            page = _popover(auth_client, source.id)
            assert f'id="payback-withdraws-{source.id}"' in page
            assert (
                "Undo CC, or setting Status back to Projected, deletes this "
                "row's CC payback and withdraws 1 accepted match, so 1 bank "
                "line is unexplained again on your statement screen: "
                f"{_line_day(seed_user)} CARD PAYMENT -$200.00."
                in " ".join(page.split())
            )
            path = f"/transactions/{source.id}/unmark-credit"
            vals = _vals(page, "delete", path)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=vals)

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, payback.id) is None
            assert db.session.get(Transaction, source.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
            assert not _claimed(seed_user, line)

    def test_an_undo_cc_from_a_card_drawn_before_the_match_is_redrawn(
        self, app, auth_client, seed_user,
    ):
        """The card read before the match: refused, the card redrawn naming the line."""
        with app.app_context():
            source = a_transaction(
                seed_user, name="Rogue Equipment", amount="200.00",
                template=False,
            )
            db.session.flush()
            a_later_period(seed_user)
            credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
            db.session.commit()
            path = f"/transactions/{source.id}/unmark-credit"
            stale = _vals(_popover(auth_client, source.id), "delete", path)
            assert stale == {"shown_lines": ""}
            payback = credit_workflow.get_active_payback(source.id)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-200.00",
                posted_on=_day(seed_user), description="CARD PAYMENT",
            )
            db.session.commit()
            _accept(seed_user, line, transactions=[payback])

            refused = auth_client.delete(path, query_string=stale)

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert "Here it is as it is now; press again to go ahead." in body
            assert f'id="payback-withdraws-{source.id}"' in body
            _committed()
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)
            assert _vals(body, "delete", path) == {"shown_lines": str(line.id)}

    def test_status_leaving_credit_posts_the_line_and_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The popover's Save, Status set to Projected: the form posts the line, the payback goes."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)
            fields = _form_fields(_popover(auth_client, source.id))
            assert fields["shown_lines"] == str(line.id)
            fields["status_id"] = str(ref_cache.status_id(StatusEnum.PROJECTED))

            response = auth_client.patch(
                f"/transactions/{source.id}", data=fields,
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, payback.id) is None
            assert not _claimed(seed_user, line)


class TestOneReadAnswersManyRemovals:
    """``match_withdrawal.pending_for_each``: the grid's one read is each removal's own."""

    def test_each_answer_is_the_single_reads(self, app, seed_user):
        """Two purchases, one matched: each key answers as ``pending_for_movements`` alone."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)
            publix = _purchase(seed_user, txn, "8.00", "Publix")

            each = match_withdrawal.pending_for_each(
                {"kroger": [kroger], "publix": [publix]},
            )

            assert each["kroger"] == match_withdrawal.pending_for_movements(
                [kroger],
            )
            assert each["kroger"].line_ids == frozenset({line.id})
            assert each["publix"] == match_withdrawal.pending_for_movements(
                [publix],
            )
            assert not each["publix"].frees_a_line
