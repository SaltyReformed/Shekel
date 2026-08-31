"""
Shekel Budget App -- The RECONCILE page, through HTTP

Plan step **bank_import:X-gj-1b**; rulings **bank_import:R-HP** through
**R-HX**.  One page on four verbs, replacing the review queue, the register
and the hand-build workbench.

**The cases that matter most read the page and post its own bytes back.**  A
hand-picked payload is written by the same person as the template, so the two
agree about a mistake as readily as about the truth -- and this arc has paid
for that twice, once shipping a destination arm that was DEAD in a browser.
:func:`~tests.test_routes._statement_forms.reconcile_form_fields` scrapes what
a browser would submit, unticked checkboxes and all.

**The ownership 404s are PAIRED with a case asserting the URL still routes.**
A 404 from the URL MAP and a 404 from the ownership gate are indistinguishable
in a response, so moving or renaming a route leaves its IDOR control passing
and guarding nothing -- measured once on a door that DESTROYS budget rows.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.enums import StatusEnum
from app.models.account import Account
from app.models.category import Category
from app.models.merchant_rule import MerchantRule
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_routes._statement_forms import (
    controls_inside_the_trigger,
    form_fields,
    reconcile_form_fields,
    reconcile_offerable,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_by,
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _url(account_id, tab=None):
    """Return the Reconcile page's URL for *account_id*."""
    suffix = "" if tab is None else f"?tab={tab}"
    return f"/accounts/{account_id}/statements/reconcile{suffix}"


def _merchants_url(account_id):
    """Return the standing-rule offer's door for *account_id*."""
    return f"/accounts/{account_id}/statements/reconcile/merchants"


def _match_url(account_id, line_id):
    """Return the MATCH pane's own read endpoint."""
    return (
        f"/accounts/{account_id}/statements/reconcile/line/{line_id}/match"
    )


def _page(auth_client, seed_user, tab=None):
    """Return the rendered Reconcile page for the seeded account."""
    response = auth_client.get(_url(seed_user["account"].id, tab))
    assert response.status_code == 200, (
        f"the page did not render: {response.status_code}"
    )
    return response.get_data(as_text=True)


def _provenance(page):
    """Return the hero's provenance line as one plain sentence, or ``None``.

    **Read as a SENTENCE rather than asserted on in fragments**, because the
    whole of what this line claims is the order and the words between the
    numbers: *2 lines recorded* and *1 filed by rules* are two figures that
    only mean anything against each other, and a test matching each number in
    isolation would pass with them transposed.

    Args:
        page: The rendered page.

    Returns:
        The line's text with its tags removed, its ``&middot;`` separators
        written as ``-`` and its whitespace collapsed; or ``None`` when the
        page renders no such line at all.
    """
    found = re.search(
        r'<p class="rec-provenance[^"]*">(.*?)</p>', page, re.S,
    )
    if found is None:
        return None
    return " ".join(
        re.sub(r"<[^>]+>", " ", found.group(1))
        .replace("&middot;", "-")
        .split()
    )


def _choosing(fields, name, value):
    """Return *fields* with *name* set to *value*, replacing what was there.

    **Replacing rather than appending**, because a browser sends one value per
    control and ``request.form.get`` keeps the FIRST: a case that appended its
    choice beside the page's own default would post the default and grade the
    do-nothing arm while claiming to grade the act.  Found by running it.

    Args:
        fields: What the page rendered.
        name: The control to set.
        value: What the owner picked.

    Returns:
        The fields, in document order, with that one control replaced.
    """
    replaced = [pair for pair in fields if pair[0] != name]
    return replaced + [(name, value)]


def _post(auth_client, seed_user, fields, page):
    """Post *fields* to Apply, refusing anything *page* could not have sent.

    **The refusal is the point, and it is structural rather than a rule to
    remember.**  Every acting case here appended ``("ok", str(line.id))`` to
    what it had scraped, and that value is one a browser could not produce for
    most cards: the ``ok-<line>`` checkbox is rendered only where a card
    suggests a verb whose door exists, so 31 of the developer's 248 cards
    carried a panel button pointing at an element that was not in the
    document -- a primary control, dead in a browser, with every server test
    green.  A helper that accepts whatever a caller appends turns a faithful
    scrape back into a hand-picked payload, and the next author in a hurry
    re-abuses it; this one cannot be.

    **It is asked as a PAIR for a checkbox or radio.**  ``ok`` is one name
    shared by every card and keyed by its value, so a check that only asked
    whether the page renders ``ok`` anywhere would pass exactly the payload
    that hid the defect.

    A case that genuinely needs to post an unrendered control -- a crafted
    body, a stale page -- posts through ``auth_client`` directly and says so,
    which is the moment the question gets asked out loud.

    Args:
        auth_client: The logged-in client.
        seed_user: The seeded user bundle.
        fields: The ``(name, value)`` pairs to submit, scraped and then
            pressed.
        page: The rendered page they were scraped from.

    Returns:
        The response.

    Raises:
        AssertionError: When a field names a control *page* did not render, or
            gives a checkbox or radio a value it could not have sent.
    """
    offerable = reconcile_offerable(page)
    for name, value in fields:
        if name == "csrf_token":
            continue
        assert (name, value) in offerable or (name, None) in offerable, (
            f"({name!r}, {value!r}) is not something this page rendered, so a "
            f"browser could never submit it -- post it through auth_client "
            f"directly if the case is about a crafted or stale body"
        )
    return auth_client.post(
        _url(seed_user["account"].id),
        data=MultiDict([("csrf_token", "x")] + list(fields)),
    )


def _a_swipe_a_rule_files(seed_user, db, merchant="Lowe's"):
    """Stage one outflow whose merchant the owner has answered for.

    Returns:
        The envelope the rule names, so a case can assert what it recorded.
    """
    envelope = an_envelope(seed_user, name="Home Improvement")
    line = an_unexplained_outflow(
        seed_user, merchant=merchant, amount="-35.72",
    )
    db.session.commit()
    a_rule(
        seed_user, merchant,
        template_id=envelope.template_id,
    )
    db.session.commit()
    return envelope, line


class TestTheOwnershipRefusalIsPairedWithTheURLStillRouting:
    """A 404 from the URL MAP and a 404 from the gate look identical.

    So each refusal below stands beside a case proving the same URL answers
    200 for the owner -- without which moving or renaming this route would
    leave every IDOR case here green and guarding nothing.
    """

    @pytest.fixture()
    def _someone_elses_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="reconcilestranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking",
        )
        db.session.add(account)
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed
        # is one the request cannot see -- and the 404 asserted below must be
        # the OWNERSHIP gate refusing a real account rather than a missing
        # row, which is the whole point of the pairing.
        db.session.commit()
        return account.id

    def test_the_page_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The security response rule: 404 for both refusals."""
        assert auth_client.get(
            _url(_someone_elses_account)
        ).status_code == 404

    def test_the_page_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the refusal above is the GATE's, not the map's."""
        assert auth_client.get(
            _url(seed_user["account"].id)
        ).status_code == 200

    def test_apply_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The money door's own gate."""
        assert auth_client.post(
            _url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_apply_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing for the door that writes."""
        assert auth_client.post(
            _url(seed_user["account"].id), data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_match_pane_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The read endpoint is gated exactly as the doors are."""
        assert auth_client.post(
            _match_url(_someone_elses_account, 1), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_match_pane_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing, on a line this account really holds."""
        _, line = _a_swipe_a_rule_files(seed_user, db)

        assert auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_rules_door_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The standing-rule door is gated exactly as the money doors are.

        Added with the door itself (ruling **bank_import:R-IB**), because this
        class's own reason for existing is that a 404 from the URL MAP and a
        404 from the gate look identical -- so a door landing without its pair
        is a door whose ownership control nothing holds in place.
        """
        assert auth_client.post(
            _merchants_url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_rules_door_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the same URL answers 200 for the account's owner."""
        assert auth_client.post(
            _merchants_url(seed_user["account"].id),
            data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_match_pane_refuses_a_line_this_pass_never_offered(
        self, auth_client, db, seed_user,
    ):
        """The membership test IS the authorisation.

        A line belonging to someone else, one another match already claims,
        and one that exists nowhere are all refused by the same intersection
        against the pass's own unexplained set -- rather than by a second
        ownership check written here that could drift from it.
        """
        _a_swipe_a_rule_files(seed_user, db)

        assert auth_client.post(
            _match_url(seed_user["account"].id, 999999),
            data={"csrf_token": "x"},
        ).status_code == 404


class TestTheTabBarOffersOnlyWhatThisBuildRenders:
    """Ruling **R-HW**, applied to the bar rather than to a verb.

    Explained and Filed by rules are plan step ``X-gj-1c``'s -- their cards
    are ACTS already applied, a different card with an Undo door -- so a tab
    leading to them now would be an affordance that cannot succeed.
    """

    def test_the_three_line_tabs_render(self, auth_client, db, seed_user):
        """To explain, Transfers and Skipped are this leaf's."""
        for tab in ("to_explain", "transfers", "skipped"):
            assert auth_client.get(
                _url(seed_user["account"].id, tab)
            ).status_code == 200

    def test_a_tab_this_build_does_not_serve_is_404(
        self, auth_client, db, seed_user,
    ):
        """Not a rendered apology: nothing composes this URL by hand."""
        assert auth_client.get(
            _url(seed_user["account"].id, "explained")
        ).status_code == 404

    def test_a_value_naming_no_tab_at_all_is_404(
        self, auth_client, db, seed_user,
    ):
        """A tampered or stale request, answered the same way."""
        assert auth_client.get(
            _url(seed_user["account"].id, "nonsense")
        ).status_code == 404

    def test_the_bar_links_to_the_three_and_names_no_other(
        self, auth_client, db, seed_user,
    ):
        """The bar is drawn from the route's own served set."""
        page = _page(auth_client, seed_user)

        assert "tab=to_explain" in page
        assert "tab=transfers" in page
        assert "tab=skipped" in page
        assert "tab=explained" not in page
        assert "tab=filed_by_rules" not in page


class TestAnUntouchedPageWritesNothing:
    """Rulings **R-FP** and **R-HS**, and the collision they would otherwise
    have.

    R-HS pre-fills a justified suggestion -- the destination a standing rule
    names arrives SELECTED -- and then says *an untouched card is not
    submitted*.  On the review queue's form those two cannot both hold,
    because there the select IS the tick.  Here the OK checkbox is the tick,
    so this posts the page's own bytes with nothing pressed and asserts the
    books did not move.
    """

    def test_the_page_pre_fills_the_rule_s_destination(
        self, auth_client, db, seed_user,
    ):
        """The premise: without the pre-fill this case grades nothing."""
        envelope, _ = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert f'value="{envelope.id}" selected' in page

    def test_posting_it_back_untouched_records_nothing(
        self, auth_client, db, seed_user,
    ):
        """Every control the page renders, at the value it renders."""
        _a_swipe_a_rule_files(seed_user, db)
        before = db.session.query(Transaction).count()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert not any(name == "ok" for name, _ in fields), (
            "a card arrived OK'd, so this case cannot grade the default"
        )
        response = _post(auth_client, seed_user, fields, page)

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(Transaction).count() == before


class TestOKThenApplyIsWhatMovesMoney:
    """What the card and the footer emit is what the door accepts.

    The loop nothing else closes: a Jinja field name and a Marshmallow field
    name have no compile-time relationship, so this scrapes the page and posts
    exactly what it found.
    """

    def test_an_OK_D_card_becomes_a_purchase_in_the_rule_s_envelope(
        self, auth_client, db, seed_user,
    ):
        """The one-click case ruling **R-HS** exists for."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert any(
            name == f"destination-{line.id}" for name, _ in fields
        ), "the page rendered no destination for this line"
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1
        # The act is a PURCHASE against that envelope, which is an entry row
        # rather than anything on the envelope itself.
        entry = db.session.query(TransactionEntry).one()
        assert entry.transaction_id == envelope.id
        assert entry.amount == Decimal("35.72")

    def test_a_deposit_OK_D_on_ADD_becomes_an_income_row(
        self, auth_client, db, seed_user,
    ):
        """Ruling **bank_import:R-GW**'s door, reached through this screen.

        The arm is STATED by the form (``destination-<line>=income``) rather
        than inferred from a missing destination, which is the defect plan
        step X-f6a-3c-2 corrected on the purchase side.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()
        before = db.session.query(Transaction).count()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert (f"destination-{line.id}", "income") in fields, (
            "the deposit's ADD tab named no arm, so this grades nothing"
        )
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        assert db.session.query(Transaction).count() == before + 1
        assert db.session.query(StatementMatch).count() == 1

    def test_a_card_OK_D_with_nothing_chosen_is_REPORTED(
        self, auth_client, db, seed_user,
    ):
        """A press may not go unanswered, and may not cost the other cards.

        It is reachable from a browser -- pressing the ADD tab's own button
        with the select still on *choose where this goes* -- so it may not be
        a pass-level refusal (**R-FZ(a)**) and may not be a silent drop.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Public Library", amount="-5.99",
        )
        db.session.commit()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert "without choosing what to do with them" in body
        assert str(line.id) in body


class TestTheMatchPanePricesWhatIsTicked:
    """Ruling **R-FN**: a difference is a transaction the owner ACCEPTS.

    The pane runs the accept door's own reads and refusals without the
    writes, so the figure on screen and the figure the door compares against
    are one derivation.
    """

    def _a_payroll_deposit_and_its_two_rows(self, seed_user, db):
        """Stage finding **balance:N-391**'s own case.

        N-239 until `balance:X-aw` retired that row on 2026-08-30 and split
        its bank half off as N-391; `grep -c '| N-239 ' docs/plans/ledger.md`
        returns 0.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()
        return line

    def test_it_names_the_rows_the_lines_own_period_holds(
        self, auth_client, db, seed_user,
    ):
        """The list a card opens on."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        assert "Data Manager" in pane
        assert "Health Insurance Allowance" in pane

    def test_ticking_both_prices_the_gap_and_offers_the_consent(
        self, auth_client, db, seed_user,
    ):
        """`$2,473.38` + `$100.00` against `$2,573.42` is `$0.04`."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)
        first = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)
        tokens = [
            value for name, value in reconcile_form_fields(first)
            if name == f"rows-{line.id}"
        ]
        # The reader keeps only CHECKED boxes, so the tokens come off the
        # rendered values rather than from the pane's own state.
        tokens = _row_tokens(first, line.id)

        assert len(tokens) >= 2, "the pane offered fewer than two rows"
        priced = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict(
                [("csrf_token", "x")]
                + [(f"rows-{line.id}", token) for token in tokens]
            ),
        ).get_data(as_text=True)

        assert "$0.04" in priced
        assert f'name="residual-{line.id}"' in priced
        assert 'value="0.04"' in priced

    def test_a_search_reaches_a_row_outside_the_lines_period(
        self, auth_client, db, seed_user,
    ):
        """The half that keeps a card payment groupable."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x", f"q-{line.id}": "Data"},
        ).get_data(as_text=True)

        assert "Data Manager" in pane
        assert "Health Insurance Allowance" not in pane


def _row_tokens(pane, line_id):
    """Return every candidate-row token the pane rendered, ticked or not.

    :func:`~tests.test_routes._statement_forms.reconcile_form_fields` keeps
    only what a browser would SUBMIT, which is the right reader for a form and
    the wrong one for "what did this pane offer" -- an unticked box submits
    nothing.  This reads the offer.

    Args:
        pane: The rendered MATCH pane.
        line_id: The bank line it is about.

    Returns:
        The reviewed-row tokens, in document order.
    """
    import re  # pylint: disable=import-outside-toplevel

    return re.findall(
        rf'name="rows-{line_id}" value="([^"]+)"', pane,
    )


class TestTheReceiptOffersOneStandingRulePerMerchant:
    """Ruling **bank_import:R-IB** (developer, 2026-08-30).

    The card carried an *always, for this merchant* checkbox until that
    ruling. A standing rule is ONE fact per merchant and a card is one LINE,
    so on the developer's own pass the page asked one question **86 times** --
    Amazon 26, Walmart 13, Food Lion 12. The offer is on the RECEIPT now,
    once per merchant, about what the door actually APPLIED.
    """

    @staticmethod
    def _ok_a_swipe(auth_client, seed_user, line, envelope):
        """OK one card into *envelope* and return the answering body."""
        page = _page(auth_client, seed_user)
        chosen = _choosing(
            reconcile_form_fields(page),
            f"destination-{line.id}", str(envelope.id),
        )
        response = _post(
            auth_client, seed_user, chosen + [("ok", str(line.id))], page,
        )
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_receipt_offers_the_rule_the_pass_actually_filed(
        self, auth_client, db, seed_user,
    ):
        """The offer names the merchant, the count and where it went."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)

        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)

        assert "Should any of these stand?" in body
        assert "Always file Lowe&#39;s in" in body
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' in body

    def test_pressing_it_records_the_rule(
        self, auth_client, db, seed_user,
    ):
        """The offer's own bytes go back to the shipped rule door."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        chosen = _choosing(
            offered, f"rule-{merchant_id}", f"t:{envelope.template_id}",
        )
        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + chosen),
        )

        assert response.status_code == 200
        rule = db.session.query(MerchantRule).filter_by(
            merchant_id=merchant_id,
        ).one()
        assert rule.template_id == envelope.template_id

    def test_the_offer_OPENS_on_not_now_and_writes_nothing_unpressed(
        self, auth_client, db, seed_user,
    ):
        """Nothing is pre-selected: the owner has not said it yet.

        The destination they picked is a fact about THIS purchase; whether it
        should stand is a different question about the future, and a radio
        group arriving on the answer would make Save record something nobody
        chose.
        """
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        assert (f"rule-{merchant_id}", "unset") in offered, (
            "the offer arrived pre-selected, so Save would record an answer "
            "the owner never gave"
        )

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + offered),
        )

        assert response.status_code == 200
        assert db.session.query(MerchantRule).count() == 0

    def test_ONE_offer_however_many_lines_that_merchant_has(
        self, auth_client, db, seed_user,
    ):
        """The grain the ruling corrects, at the smallest size that shows it.

        Two Lowe's swipes filed into the same envelope are ONE answer, not
        two: a rule keys on ``template_id``, so every pay period's copy of an
        envelope is the same rule. On the developer's own data this is why the
        contradiction rate is zero across 10 repeated merchants.
        """
        envelope, first = _a_swipe_a_rule_files(seed_user, db)
        second = an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-12.10",
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Lowe's")

        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)
        for line in (first, second):
            fields = _choosing(
                fields, f"destination-{line.id}", str(envelope.id),
            )
        response = _post(auth_client, seed_user, fields + [
            ("ok", str(first.id)), ("ok", str(second.id)),
        ], page)

        body = response.get_data(as_text=True)
        assert body.count(f'name="rule_merchant-{merchant_id}"') == 1, (
            "one merchant was asked about more than once"
        )
        assert "You filed 2 <strong>Lowe&#39;s</strong>" in body


class TestTheROUTEHandsTheOfferWhatTheDoorAPPLIED:
    """The SEAM, graded where the two derivations differ.

    ``rules_worth_offering`` takes ``applied_line_ids`` as a PARAMETER, so a
    service test can only grade what a caller passes -- and an adversarial
    review proved the gap by hand: replacing the route's

        frozenset(l for item in outcome.applied for l in item.line_ids)

    with the pre-ruling ``frozenset(item["line_id"] for item in creations)``
    left **11,969 tests green**, shipping the exact regression ruling
    **bank_import:R-IB** exists to make unconstructible.

    **Choosing the refusal is the whole difficulty**, because
    ``rules_worth_offering`` has three ways to drop a line and only one of
    them is the seam. A destination the pass does not offer is dropped by its
    own arm; a line that is not ``creatable`` (a deposit) is dropped by
    another; an answer the rule door would refuse is dropped by a third. The
    refusal used here trips none of them: a line past the saved pay calendar
    IS creatable, is filed to a NEW envelope under an ACTIVE category -- an
    answer the rule door takes -- and the create door refuses it by name at
    ``scope.period_holding``. So the only thing that can keep it out of the
    offer is the narrowing this class is about.
    """

    def test_a_line_the_door_REFUSED_is_not_in_the_receipts_offer(
        self, auth_client, db, seed_user,
    ):
        """One merchant lands, one is refused; only the lander is offered."""
        envelope, lands = _a_swipe_a_rule_files(seed_user, db)
        db.session.query(MerchantRule).delete()
        far = a_bank_line(
            seed_user, an_import(seed_user), amount="-40.00",
            posted_on=date(2031, 3, 4), merchant="Faraway Co",
        )
        db.session.commit()
        category = db.session.query(Category).filter(
            Category.user_id == seed_user["user"].id,
            Category.is_active.is_(True),
        ).first()

        # **Posted directly and not through ``_post``**, and deliberately: the
        # far line's ADD tab is SHUT (its ``withheld`` names the missing pay
        # period), so no card renders a destination for it and no browser
        # could send this. A stale page is how it arises -- the calendar is
        # extended, the page is drawn, the schedule is rolled back -- and the
        # per-item SAVEPOINT is what this is about either way.
        response = auth_client.post(
            _url(seed_user["account"].id),
            data=MultiDict([
                ("csrf_token", "x"),
                ("ok", str(lands.id)),
                (f"verb-{lands.id}", "add"),
                (f"destination-{lands.id}", str(envelope.id)),
                ("ok", str(far.id)),
                (f"verb-{far.id}", "add"),
                (f"destination-{far.id}", "new"),
                (f"envelope_name-{far.id}", "Faraway"),
                (f"category_id-{far.id}", str(category.id)),
            ]),
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "No pay period covers 2031-03-04" in body, (
            "the door did not refuse the far line, so this grades nothing"
        )
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' \
            in body, "the merchant that LANDED earned no offer"
        assert 'name="rule_merchant-' + str(
            the_merchant_id(seed_user, "Faraway Co"),
        ) + '"' not in body, (
            "a purchase the door REFUSED earned a standing-rule offer -- the "
            "next import would auto-file that merchant with no press"
        )


class TestAProposedCardAppliesFromThisPageAndItsPaneLoads:
    """The near tier's own act, walked end to end on the Reconcile page.

    **Both halves were ungraded and an adversarial review measured it**: with
    the page's hidden ``residual-<line>`` deleted the whole tracked suite
    stayed green at 4,516 passed, while the same mutation on the review
    queue's own input fails a case by name.  One of the two emission sites
    was checked and the other was not, and it is the newer one that a browser
    now depends on: the accept door exempts no shape since plan step
    ``bank_import:X-gj-1b``, so a proposal that states no figure REFUSES.
    """

    @staticmethod
    def _near_miss(seed_user, db):
        """Stage the developer's own Geico case: a bill settled 3 cents off.

        Returns:
            ``(line, txn)`` -- the bank line and the row a tier will pair it
            with, three cents apart, which is what makes the proposal a NEAR
            MISS rather than an exact match.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Geico", amount="-178.29",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )
        db.session.commit()
        return line, txn

    def test_the_page_emits_the_figure_the_proposal_states(
        self, auth_client, db, seed_user,
    ):
        """The control the mutation deleted, asserted by name and value.

        It is a HIDDEN input, so it is not something a case can discover by
        pressing: nothing else in this file would notice it going away.
        """
        line, _ = self._near_miss(seed_user, db)

        fields = reconcile_form_fields(_page(auth_client, seed_user))

        assert (f"residual-{line.id}", "0.03") in fields, (
            "the card stated no difference, so the door would refuse the "
            "very act this tier exists to offer"
        )

    def test_OK_on_a_proposed_card_REPRICES_the_row(
        self, auth_client, db, seed_user,
    ):
        """Scrape the page, press OK, and check the money moved.

        The loop the file's own header asks for: the page's bytes go back to
        the door, so the figure the owner agreed to and the figure the door
        wrote are the same derivation rather than two that agree by reading.
        """
        line, txn = self._near_miss(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert (f"verb-{line.id}", "match") in fields, (
            "the card did not open on MATCH, so this grades the wrong verb"
        )
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert txn.settled_amount == Decimal("178.29"), (
            "the row must book what the BANK took"
        )

    def test_the_MATCH_pane_LOADS_for_a_proposed_card(
        self, auth_client, db, seed_user,
    ):
        """The pane a proposal card asks for on its first open.

        **It answered 404 for every proposal until plan step
        ``bank_import:X-gj-1b``**, because the route resolved the line in
        ``review.unmatched`` and ``_unexplained`` takes a proposal's line out
        of that list before it exists -- 137 of the developer's 137 proposal
        cards, each a spinner that never resolved.  htmx does not swap a 4xx,
        so the owner saw *Finding the rows this could be...* for ever.
        """
        line, _ = self._near_miss(seed_user, db)

        response = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        )

        assert response.status_code == 200, (
            "a proposed card's MATCH pane must load like any other"
        )
        assert f'name="rows-{line.id}"' in response.get_data(as_text=True), (
            "the pane rendered none of the proposal's own rows"
        )

    def test_the_TRIGGER_does_not_contain_the_control_it_replaces(
        self, auth_client, db, seed_user,
    ):
        """The consent box may not sit inside the element that re-renders it.

        **The workbench carries this control and this pane shipped without
        it**, which is how the same defect arrived twice: ticking the consent
        fired the re-render, and the swap replaced it with a fresh unticked
        one, so the owner could never keep it ticked and Apply carried no
        figure -- with every server test green.  Asserted over the MARKUP,
        because the defect is a containment relation and nothing else in the
        tree can see one.

        The rows must be INSIDE, which is the other half and is what makes
        unticking a proposed row re-price at all: a change event bubbles to
        ancestors only.
        """
        line, _ = self._near_miss(seed_user, db)
        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        inside = controls_inside_the_trigger(pane)

        assert f"rows-{line.id}" in inside, (
            "the row checkboxes are outside the element whose change "
            "re-prices them, so unticking one changes the submission and "
            "re-prices nothing"
        )
        assert f"residual-{line.id}" not in inside, (
            "the consent control is inside the element that replaces it, so "
            "ticking it discards the tick"
        )


class TestAVerbWithNoDoorRendersNoControl:
    """Ruling **R-HW**, read off the markup a browser would receive.

    All four verbs render on every card whatever this build can act on, and a
    verb whose door does not exist renders its explanation and NO submitting
    control.
    """

    def test_all_four_tabs_render_on_a_card(
        self, auth_client, db, seed_user,
    ):
        """The vocabulary is taught by the panel itself."""
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        for word in ("MATCH", "ADD", "TRANSFER", "SKIP"):
            assert word in page

    def test_the_shut_verbs_say_what_they_wait_for(
        self, auth_client, db, seed_user,
    ):
        """A disabled tab carrying its reason is a disclosure.

        **And it must TEACH THE WORD, not only report the wait** (**R-HW**:
        the panel is where the vocabulary is taught). SKIP said only that
        skipping is not recorded yet, which tells a first-time reader nothing
        about what SKIP would MEAN -- so both halves are asserted.
        """
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert "pair a bank line with another of your own accounts" in page
        assert "explains nothing you budget for" in page, (
            "the SKIP tab does not say what skipping MEANS"
        )
        assert "not recorded yet" in page, (
            "the SKIP tab does not say why it cannot be pressed"
        )

    def test_a_parked_card_offers_no_ONE_CLICK_OK(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-HQ**: a holding state is not inbox work.

        Its sentence opens on TRANSFER, a verb with no door, so a control
        keyed on the SENTENCE would have put a working-looking OK button on
        every parked card payment.

        **It is the BUTTON this is about, not the consent**, and the two were
        one control until plan step ``bank_import:X-gj-1b``.  A parked line's
        ADD is shut by ruling **R-GJ**'s bar and its TRANSFER and SKIP have no
        door, but its MATCH tab is open like any other card's -- the pass's
        unexplained rows are a fact about the PASS -- so the owner may still
        group-match a card payment against the paybacks it covers, and that
        act needs the ``ok`` checkbox in the document.  Gating the checkbox on
        the summary button is what made that act dead in a browser
        (:attr:`LineCard.takes_ok`).
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "transfers")

        assert "Capital One Credit Card" in page
        assert f'for="ok-{line.id}">OK<' not in page, (
            "a parked card must not offer the one-click OK its sentence "
            "would otherwise justify"
        )
        # ...and the consent it CAN give, through the tab that has a door.
        assert f'name="ok" value="{line.id}"' in page, (
            "a parked payment must still be group-matchable"
        )


class TestTheHeroSaysWhatTheLastImportDid:
    """The provenance line the locked direction prints right of the figures.

    *Last import <day> - N lines recorded - N filed by rules - receipt*, so
    that a routine session reads *import, read the receipt, work the inbox,
    see the difference reach zero*.  It answers *is what I am looking at
    current*, which none of the four hero figures does.
    """

    def test_it_reads_as_the_sentence_the_direction_names(
        self, auth_client, db, seed_user,
    ):
        """One import, two lines recorded, one of them filed by a rule."""
        statement = an_import(
            seed_user, line_count=42, recorded_count=2,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        by_rule = a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Amazon)",
            merchant="Amazon", sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-12.34",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Walmart)",
            merchant="Walmart", sequence_in_group=1,
        )
        db.session.commit()
        filed_by(seed_user, by_rule, envelope, by_rule=True)
        db.session.commit()

        said = _provenance(_page(auth_client, seed_user))

        assert said == (
            "Last import Aug 24, 2026 - 2 lines recorded - "
            "1 filed by rules - receipt"
        ), said

    def test_the_day_is_the_owner_s_and_not_UTC_s(
        self, auth_client, db, seed_user,
    ):
        """An evening import may not be reported as tomorrow's work.

        ``created_at`` is a stored UTC instant and the owner reads Eastern, so
        an import performed at 21:00 on 2026-08-30 is stored at 01:00 on the
        31st.  Truncating in the service would have printed the 31st; the
        conversion is ``local_datetime``'s, which is this project's standing
        rule for every ``timestamptz`` it renders.
        """
        an_import(
            seed_user, line_count=1, recorded_count=1,
            created_at=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        said = _provenance(_page(auth_client, seed_user))

        assert "Aug 30, 2026" in said, said
        assert "Aug 31, 2026" not in said, said

    def test_an_account_nobody_has_imported_into_prints_NO_line(
        self, auth_client, db, seed_user,
    ):
        """The whole line or none of it.

        *Last import -- 0 lines recorded* over an account that has never been
        imported into would state an act that never happened, which is the
        fabricated-figure shape this arc keeps deleting.
        """
        page = _page(auth_client, seed_user)

        # TWO predicates, because ``_provenance`` answers ``None`` both for a
        # page that renders no such line and for a reader that has stopped
        # matching the markup.  The second is independent of the first.
        assert "rec-provenance" not in page
        assert _provenance(page) is None

    def test_the_receipt_LINK_names_an_anchor_the_statements_page_HAS(
        self, auth_client, db, seed_user,
    ):
        """The link and its target are asserted as a PAIR.

        A link to an anchor nothing declares scrolls to the top of the page
        and looks exactly like one that works -- the same indistinguishability
        that lets a moved route leave its ownership 404 guarding nothing.  So
        the case renders BOTH surfaces: the href here, and the ``id`` there.
        """
        statement = an_import(
            seed_user, line_count=1, recorded_count=1,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Amazon)",
            merchant="Amazon", sequence_in_group=0,
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=True)
        db.session.commit()
        account_id = seed_user["account"].id

        page = _page(auth_client, seed_user)
        statements = auth_client.get(
            f"/accounts/{account_id}/statements",
        ).get_data(as_text=True)

        assert (
            f'href="/accounts/{account_id}/statements#filed-by-rules"'
            in page
        ), "the provenance line does not link to the rule receipt"
        assert 'id="filed-by-rules"' in statements, (
            "the statements page declares no such anchor, so the link the "
            "hero renders lands at the top of the page instead of on the "
            "receipt it names"
        )


class TestTheCardCarriesTheBanksOwnWords:
    """Both of them, in ONE element: the raw description and the bank's category.

    The locked direction's card puts them together under the merchant, in mono
    muted small.  They are PROVENANCE -- nothing on this page decides on
    either, and the one decision this package makes on a bank's category is
    ``_vocabulary``'s, asked in SQL against that adapter's own vocabulary.

    **This was recorded as impossible and was not.**  The handoff for this step
    said ``BankLine`` carried no such field "so it needs a model change";
    ``bank_statement_lines.source_category`` has existed since the importer
    shipped, and it was the SERVICE value that dropped it -- one field and one
    construction site.

    **The assertions are on the ELEMENT and not on the page**, because "the
    card puts them together" is a containment claim: two ``in page`` checks
    pass just as happily with the two facts in different corners of the screen.
    """

    def _raw_line(self, page):
        """Return the text of the card's raw-facts element.

        Args:
            page: The rendered page.

        Returns:
            The inner text of the one ``rec-raw`` div, or ``None``.

        Raises:
            AssertionError: When the page renders more than one, which would
                make "the" element a fiction and every assertion below
                ambiguous.
        """
        found = re.findall(
            r'<div class="rec-raw font-mono">(.*?)</div>', page, re.S,
        )
        assert len(found) <= 1, (
            f"{len(found)} cards rendered; this case reads THE raw line and "
            f"needs exactly one"
        )
        return found[0].strip() if found else None

    def test_the_two_facts_are_in_ONE_element_in_the_direction_s_order(
        self, auth_client, db, seed_user,
    ):
        """Description first, then what the bank filed it under."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72",
            source_category="Merchandise/Home Improvement",
        )
        db.session.commit()

        raw = self._raw_line(_page(auth_client, seed_user))

        assert raw is not None, "no card rendered its raw facts at all"
        assert raw.endswith("Merchandise/Home Improvement"), (
            f"the bank's category is not on the card's raw line: {raw!r}"
        )
        assert raw.startswith("POINT OF SALE DEBIT"), (
            f"the raw description went with it; the two are one line: {raw!r}"
        )

    def test_a_source_that_files_NOTHING_leaves_the_line_at_the_description(
        self, auth_client, db, seed_user,
    ):
        """An absent fact renders as nothing, not as a separator or a gap.

        ``bank_statement_lines.source_category`` is NULLABLE and the arm is
        for an adapter that states none.  It is not today's data: the one
        adapter registered (SECU) states a category on every line it parses --
        **378 of 378 recorded lines on the developer's own account**, measured
        2026-08-30 on a restored production clone.  So this case stages the
        state rather than sampling it.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()

        raw = self._raw_line(_page(auth_client, seed_user))

        assert raw is not None
        assert raw.startswith("POINT OF SALE DEBIT")
        assert raw.endswith(")"), (
            f"the raw line ends past its own description, so something was "
            f"rendered for a category the line does not have: {raw!r}"
        )


class TestThePanelHasONEFooterAndItCloses:
    """The panel's footer: what this writes, Close, and one verb-named button.

    **ONE band per opened card, and that is what changed.**  The primary
    button was rendered inside every OPEN pane, so a card whose MATCH and ADD
    are both open emitted two footers and two copies of the sentence --
    measured on a restored production clone 2026-08-30: 341 bands over 239
    cards, 102 of them doubled.  The developer ruled the collapse the same
    day.  Which button shows is CSS over the verb radios; that pairing is
    graded structurally in
    ``tests/test_arch/test_a_control_rendered_invisible_has_a_rule_that_shows_it``.
    """

    def test_a_card_with_TWO_open_verbs_still_has_ONE_footer(
        self, auth_client, db, seed_user,
    ):
        """The duplication this collapse removed, asserted over the axis it lived on.

        **The two-open-verb card is staged and then CHECKED for**, because a
        page of one-verb cards satisfies every count here while proving
        nothing: the defect only existed where a card had two.
        """
        _a_swipe_a_rule_files(seed_user, db)

        page = _page(auth_client, seed_user)

        cards = page.count("data-rec-card")
        assert cards, "no card rendered"
        buttons = re.findall(r'class="btn btn-sm btn-primary rec-cta"', page)
        assert len(buttons) > cards, (
            "no card offered two verbs, so this case is not exercising the "
            "shape the collapse was about"
        )
        assert page.count('class="rec-panel-foot"') == cards, (
            f"{page.count('class=\"rec-panel-foot\"')} footers over {cards} "
            f"cards; the footer is per PANE again"
        )
        assert page.count("Nothing is written until you press Apply.") == cards, (
            "the sentence is rendered more than once per card"
        )

    def test_a_card_whose_every_verb_is_SHUT_still_closes(
        self, auth_client, db, seed_user,
    ):
        """The state that tells the panel's footer from a pane's.

        A parked card payment on an account with no unexplained row has no
        open verb at all (:attr:`LineCard.takes_ok` is exactly that question),
        so it offers no button and no sentence -- and its whole footer is
        therefore scripted-only.  A Close living in a pane's footer would be
        missing from the one card whose panel is nothing BUT disclosure.
        """
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "transfers")

        assert "Capital One Credit Card" in page
        assert "rec-cta" not in page, (
            "this card was expected to have no open verb; the case no longer "
            "stages the state it is about"
        )
        assert "Nothing is written until you press Apply." not in page, (
            "a card with no act promises that pressing Apply would write it"
        )
        assert page.count("data-rec-close") == 1, (
            "a card with no act renders no Close, so the control lives in a "
            "pane's footer rather than in the panel's"
        )
        assert '<div class="rec-panel-foot" data-rec-scripted hidden>' in page, (
            "the whole band is scripted-only on this card and is not marked"
        )

    def test_every_scripted_only_control_is_rendered_hidden(
        self, auth_client, db, seed_user,
    ):
        """A details element is closed by its own summary with nothing running.

        So Close is a convenience over a control that already works, and it is
        revealed by ``statement_reconcile.js`` -- the same rule the page
        footer's keyboard hints are rendered under.  A Close printed with
        scripting off could not close anything, which is the
        control-that-cannot-succeed shape ruling **R-HW** bounds.

        Asserted over EVERY marked control rather than over this one, and the
        set is checked non-empty first: an assertion quantified over nothing
        is satisfied by a page that renders no control at all.
        """
        _a_swipe_a_rule_files(seed_user, db)

        page = _page(auth_client, seed_user)

        marked = re.findall(r'<[^>]*\bdata-rec-scripted\b[^>]*>', page)
        assert len(marked) >= 2, (
            f"expected the keyboard hints and at least one Close; found "
            f"{len(marked)}"
        )
        assert all("hidden" in tag for tag in marked), (
            f"a scripted-only control is not hidden: "
            f"{[tag for tag in marked if 'hidden' not in tag][:2]}"
        )
