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

from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.enums import StatusEnum
from app.models.account import Account
from app.models.merchant_rule import MerchantRule
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_routes._statement_forms import (
    controls_inside_the_trigger,
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
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _url(account_id, tab=None):
    """Return the Reconcile page's URL for *account_id*."""
    suffix = "" if tab is None else f"?tab={tab}"
    return f"/accounts/{account_id}/statements/reconcile{suffix}"


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
        """Stage finding **N-239**'s own case."""
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


class TestTheAlwaysControlStatesTheRuleTheCardChose:
    """The ADD tab's *always, for this merchant* box (ruling **R-GI**).

    It states no answer of its own: the rule is read back off the destination
    the same card submits, so the rule and the purchase can never name
    different budget lines.
    """

    def test_ticking_it_records_a_rule_naming_the_chosen_envelope(
        self, auth_client, db, seed_user,
    ):
        """One gesture, two acts, one transaction."""
        envelope = an_envelope(seed_user, name="Home Improvement")
        line = an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72",
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        chosen = _choosing(
            fields, f"destination-{line.id}", str(envelope.id),
        )
        response = _post(auth_client, seed_user, chosen + [
            ("ok", str(line.id)),
            (f"always-{line.id}", str(merchant_id)),
        ], page)

        assert response.status_code == 200
        rule = db.session.query(MerchantRule).filter_by(
            merchant_id=merchant_id,
        ).one()
        assert rule.template_id == envelope.template_id

    def test_a_card_that_was_NOT_OK_D_states_no_rule(
        self, auth_client, db, seed_user,
    ):
        """A tick on a card nobody confirmed is a rule about nothing."""
        envelope = an_envelope(seed_user, name="Home Improvement")
        line = an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72",
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        chosen = _choosing(
            fields, f"destination-{line.id}", str(envelope.id),
        )
        response = _post(auth_client, seed_user, chosen + [
            (f"always-{line.id}", str(merchant_id)),
        ], page)

        assert response.status_code == 200
        assert db.session.query(MerchantRule).count() == 0


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
        """A disabled tab carrying its reason is a disclosure."""
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert "pair a bank line with another of your own accounts" in page
        assert "Skipping is not recorded yet" in page

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
