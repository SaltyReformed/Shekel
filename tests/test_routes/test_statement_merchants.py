"""
Shekel Budget App -- Where your merchants go, through HTTP

Plan step **bank_import:X-gk**, ruling **bank_import:R-IC**.  The durable home
for a merchant's standing answer: one row per merchant this account has ever
seen, edited ONE merchant at a time.

**The route's own subjects, none of which the service tests can see**:
OWNERSHIP (the security response rule's 404 for both "not found" and "not
yours"), the QUERY ARGUMENTS, the FORM PAYLOAD, the unit of work, and what the
screen SAYS.

**The cases that matter most read the page and post it back -- BOTH its bytes
and its ADDRESS.**  A hand-picked payload is written by the same person as the
template, so the two agree about a mistake as readily as about the truth, and
this arc has paid for that twice, once shipping a destination arm that was DEAD
in a browser.

**The address half was added 2026-08-31, after this module shipped a defect
green.**  Three cases scraped the controls with
:func:`~tests.test_routes._statement_forms.rule_form_controls` and then posted
them to a URL built by ``_url(..., edit=...)`` -- which the template never
emits.  The real save action carried no ``edit``, so every refusal re-rendered
with the row CLOSED while the case asserting it stayed open passed.  The same
lesson one level up: a form submits a target as surely as it submits a payload.
:func:`_save_action` reads the one, :func:`_search_url` serialises a GET form
the way a browser does -- duplicates and all, which is how the frozen search
box became visible.

**The ownership 404s are PAIRED with a case asserting the URL still routes.**
A 404 from the URL MAP and a 404 from the ownership gate are indistinguishable
in a response, so moving or renaming a route leaves its IDOR control passing
and guarding nothing -- measured once on a door that DESTROYS budget rows.

**The structural claim of the whole step has its own class**
(:class:`TestOnlyOneMerchantIsEverOnTheWire`).  The register submits every
merchant it renders, and three defects of the shape *saving one merchant
changed a second* have been found on that form.  A page that renders exactly
one control makes the class unconstructible, and the case that pins it reads
the rendered markup rather than trusting the template.
"""

import re
from collections import namedtuple

import pytest

from app.models.account import Account
from app.models.merchant_rule import MerchantRule
from app.models.user import User, UserSettings
from app.services import auth_service
from app.routes.accounts.statement_merchants import (  # pylint: disable=protected-access
    SEARCH_MAX_LENGTH,
)
from app.services.statement_match import DIRECTORY_LIMIT
from app.services.statement_match._directory import (  # pylint: disable=protected-access
    NOT_SAID,
)
from tests.test_routes._statement_forms import rule_form_controls
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_rule,
    a_transaction,
    an_import,
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _url(account_id, **args):
    """Return the merchants page's URL for *account_id*.

    Args:
        account_id: The account.
        **args: Query arguments (``show``, ``q``, ``edit``, ``all``).

    Returns:
        The URL.
    """
    query = "&".join(f"{name}={value}" for name, value in args.items())
    return (
        f"/accounts/{account_id}/statements/merchants"
        + (f"?{query}" if query else "")
    )


def _page(auth_client, account_id, **args):
    """Return the rendered merchants page, asserting it rendered."""
    response = auth_client.get(_url(account_id, **args))
    assert response.status_code == 200, (
        f"the page did not render: {response.status_code}"
    )
    return response.get_data(as_text=True)


#: One anchor of the filter bar: its href, and the count it prints.
#:
#: **The bar is READ rather than searched for by substring.**  A case asserting
#: ``">1<" in page`` is satisfied by any figure anywhere on the screen -- the
#: activity column prints counts too -- so it would pass over a bar that had
#: stopped being re-derived, which is the one thing those cases exist to catch.
_TAB = re.compile(
    r'<a class="rec-tab[^"]*"\s*\n?\s*href="([^"]+)"[^>]*>\s*'
    r"([^<]+?)\s*<span[^>]*>(\d+)</span>",
    re.MULTILINE,
)

#: The Edit/Change link on one row, as ``(href, merchant name)``.
_EDIT = re.compile(
    r'<a class="btn btn-sm btn-outline-secondary" href="([^"]+)">\s*'
    r"(?:Change|Answer)\s*<span[^>]*>\s*where ([^<]+?) goes",
    re.MULTILINE,
)


def _filters(page):
    """Return the filter bar as ``{label: (href, count)}``.

    Args:
        page: The rendered page, as text.

    Returns:
        Its three tabs.

    Raises:
        AssertionError: When the bar rendered no tabs at all -- an absence a
            dictionary comparison would report as a difference in counts.
    """
    found = {
        label: (href.replace("&amp;", "&"), int(count))
        for href, label, count in _TAB.findall(page)
    }
    assert found, "the page rendered no filter bar at all"
    return found


def _counts(page):
    """Return the filter bar's counts by label, for a before/after read."""
    return {label: count for label, (_, count) in _filters(page).items()}


def _edit_links(page):
    """Return ``{merchant name: href}`` for every row's Edit control."""
    return {
        merchant: href.replace("&amp;", "&")
        for href, merchant in _EDIT.findall(page)
    }


#: The SAVE form's own `action`, read off the page.
#:
#: **The address is part of what a browser submits, and hand-composing it is
#: how a defect ships green** (adversarial review 2026-08-31).  Three cases
#: here scraped the page's controls and then posted them to a URL built by
#: `_url(..., edit=...)` -- a URL the template never emits.  The save action
#: had no `edit`, so every real refusal re-rendered with the row CLOSED while
#: the case asserting it stayed open passed.  That is this arc's own "a route
#: test must post what the template emits" lesson, moved up one level from the
#: payload to the target.
_SAVE_FORM = re.compile(r'<form method="post"\s*\n?\s*action="([^"]+)"')


def _save_action(page):
    """Return the action of the save form the page rendered.

    Args:
        page: The rendered page, as text.

    Returns:
        The URL a browser would POST to, unescaped.

    Raises:
        AssertionError: When the page rendered no save form -- which is a
            different failure from posting to the wrong place, and one a
            missing-key error would report as something else.
    """
    found = _SAVE_FORM.search(page)
    assert found is not None, (
        "the page rendered no save form, so there is no action to post to"
    )
    return found.group(1).replace("&amp;", "&")


#: What one CLOSED row of the list states: its answer, and its activity.
_Row = namedtuple("_Row", "says lines last_seen")

#: A closed row, as the body renders it.  The answer cell's class varies with
#: whether the merchant is answered, so the pattern does not pin it.
_ROW = re.compile(
    r'<tr>\s*<td class="fw-semibold">([^<]+)</td>\s*'
    r'<td class="[^"]*">\s*(.*?)\s*</td>\s*'
    r'<td class="small[^"]*">\s*(.*?)\s*</td>',
    re.S,
)

#: The two figures the activity cell prints, in the order it prints them.
_FIGURE = re.compile(r'<span class="font-mono">([^<]+)</span>')


#: The SEARCH form, and every control inside it, IN DOCUMENT ORDER.
_SEARCH_FORM = re.compile(r'<form method="get"(.*?)</form>', re.S)
_CONTROL = re.compile(r'<input[^>]*?name="([^"]+)"[^>]*?value="([^"]*)"')
_SEARCH_CONTROL = re.compile(r'<input[^>]*?type="search"[^>]*?>', re.S)


def _search_url(page, account_id, term):
    """Return the URL a BROWSER would request when the search box is submitted.

    **A GET form serialises EVERY control it renders, in document order, and
    duplicates are preserved** -- which is the whole of the defect this exists
    to reproduce.  A case that hand-composes ``?q=<term>`` cannot see a second
    hidden control named ``q``, because the test client sends exactly what it
    is told; only building the query string the way the browser does makes the
    duplicate observable.

    Args:
        page: The rendered page carrying the search form.
        account_id: The account, for the URL's path.
        term: What the owner typed into the visible box.

    Returns:
        The URL, with repeated arguments repeated.

    Raises:
        AssertionError: When the page renders no search form.
    """
    found = _SEARCH_FORM.search(page)
    assert found is not None, "the page rendered no search form"
    inside = found.group(1)
    # The visible box submits what was TYPED, not what it was rendered with.
    typed = _SEARCH_CONTROL.sub(f'<input name="q" value="{term}">', inside)
    pairs = _CONTROL.findall(typed)
    query = "&".join(f"{name}={value}" for name, value in pairs)
    return f"/accounts/{account_id}/statements/merchants?{query}"


def _rows(page):
    """Return ``{merchant name: _Row}`` for every CLOSED row the page drew.

    **The row is read as a ROW.**  Asserting a substring against the whole page
    is what let two cases here pass on markup that meant something else: the
    UNANSWERED filter's label is the same string as an unanswered row's phrase,
    and the activity column prints numbers, so a page-wide search finds either
    from anywhere.  Reading the cells makes an assertion about a merchant an
    assertion about that merchant.

    Args:
        page: The rendered page, as text.

    Returns:
        One entry per closed row.  The OPEN row is absent -- it renders as a
        form spanning the whole row and has no cells of this shape, which is
        itself a fact worth reading (`_edit_links` is the closed-row set too).
    """
    found = {}
    for merchant, says, activity in _ROW.findall(page):
        figures = _FIGURE.findall(activity)
        found[merchant.strip()] = _Row(
            says=" ".join(says.split()),
            lines=figures[0] if figures else None,
            last_seen=figures[1] if len(figures) > 1 else None,
        )
    return found


def _rule_of(db, merchant_id):
    """Return the stored rule for *merchant_id*, or ``None``."""
    return (
        db.session.query(MerchantRule)
        .filter(MerchantRule.merchant_id == merchant_id)
        .one_or_none()
    )


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
            email="merchantstranger@shekel.local",
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
        # COMMITTED, not flushed: a request opens a transaction of its OWN, so
        # a row this fixture only flushed is one the request cannot see -- and
        # the 404 asserted below must be the OWNERSHIP gate refusing a real
        # account rather than a missing row, which is the whole point.
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

    def test_the_answer_door_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The write door's own gate."""
        assert auth_client.post(
            _url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_answer_door_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing for the door that writes."""
        assert auth_client.post(
            _url(seed_user["account"].id), data={"csrf_token": "x"},
        ).status_code == 200


class TestTheQueryArgumentsAreRefusedRatherThanIgnored:
    """A value that names nothing is a tampered or stale request.

    **404 rather than a rendered apology**, which is the answer
    ``statement_reconcile._requested_tab`` already gives for the same shape:
    nothing composes these URLs by hand, so silently falling back to the
    default would let a broken link look like a working page.
    """

    def test_a_filter_that_names_no_filter_is_404(
        self, auth_client, db, seed_user,
    ):
        """The firing control for the filter's own refusal."""
        assert auth_client.get(
            _url(seed_user["account"].id, show="nonsense"),
        ).status_code == 404

    def test_every_link_the_filter_bar_renders_answers_200(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the refusal above is about the VALUE, not the page.

        **It FOLLOWS the bar's own hrefs** rather than composing what it
        expects them to be.  A filter renamed in the service without its link
        being rebuilt ships a tab that 404s, and a case that built the URLs
        itself would agree with the mistake instead of finding it.
        """
        a_merchant(seed_user, "Audible")
        db.session.commit()
        page = _page(auth_client, seed_user["account"].id)

        bar = _filters(page)

        assert set(bar) == {"All", "You have not said", "Answered"}
        for label, (href, _) in bar.items():
            assert auth_client.get(href).status_code == 200, (
                f"the {label} tab leads to a page that does not render"
            )

    @pytest.mark.parametrize("spelling", ["abc", "0", "-3", "007", "%C2%B2"])
    def test_an_open_row_that_names_no_well_formed_id_is_404(
        self, auth_client, db, seed_user, spelling,
    ):
        """The id half is exactly as strict as every other id on this screen.

        Through :func:`~app.utils.digit_strings.parse_row_id`, which is what
        refuses ``'0'``, ``'007'`` and a non-ASCII digit -- the last of which
        was a 500 on a sibling door before that function was applied there.
        """
        assert auth_client.get(
            _url(seed_user["account"].id, edit=spelling),
        ).status_code == 404

    def test_an_open_row_this_account_has_never_seen_is_404(
        self, auth_client, db, seed_user,
    ):
        """Well-formed and unknown is still a 404, not a page with no control.

        **The firing control for the route's own check.**  Without it the page
        would render normally with every row closed, so a stale link would
        look like a working page that had simply forgotten the request.
        """
        a_merchant(seed_user, "Audible")
        db.session.commit()

        assert auth_client.get(
            _url(seed_user["account"].id, edit=9_999_999),
        ).status_code == 404

    def test_a_merchant_of_ANOTHER_account_cannot_be_opened(
        self, auth_client, db, seed_user,
    ):
        """The ownership half, on a sibling account of the SAME owner.

        A merchant is per ACCOUNT, so this is a 404 about THIS account rather
        than about this user -- which a check written against the owner alone
        would have missed.
        """
        sibling = Account(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Second Checking",
        )
        db.session.add(sibling)
        db.session.flush()
        elsewhere = a_merchant(seed_user, "Audible", account=sibling)
        db.session.commit()

        assert auth_client.get(
            _url(seed_user["account"].id, edit=elsewhere.id),
        ).status_code == 404

    def test_the_open_row_URL_still_routes_for_a_merchant_it_holds(
        self, auth_client, db, seed_user,
    ):
        """The pairing for both refusals above."""
        merchant = a_merchant(seed_user, "Audible")
        db.session.commit()

        assert auth_client.get(
            _url(seed_user["account"].id, edit=merchant.id),
        ).status_code == 200


class TestOnlyOneMerchantIsEverOnTheWire:
    """The structural claim of the step, read off the rendered markup.

    The register renders one control per merchant and submits all of them, and
    three defects of the shape *saving one merchant changed or refused a
    second* have been found on that form: a deactivated template made a select
    fall onto *I have not said* and the next Save WITHDREW a rule; an archived
    category made another fall onto the empty option, so a Save aimed at one
    merchant printed a refusal for a merchant the owner never touched; and the
    incomplete-new-envelope short-circuit reported "nothing changed" for a
    third.

    **A page carrying one merchant makes all three unconstructible.**
    """

    @pytest.fixture()
    def _three_merchants(self, db, seed_user):
        """Stage three merchants, one of them answered.

        Returns:
            The merchant row the cases open.
        """
        a_merchant(seed_user, "Audible")
        target = a_merchant(seed_user, "Duke Energy")
        a_rule(seed_user, "Walmart", always_ask=True)
        db.session.commit()
        return target

    def test_a_page_with_no_row_open_submits_NOTHING(
        self, auth_client, db, seed_user, _three_merchants,
    ):
        """A closed list carries no control at all.

        **The firing control for the whole design.**  Were the rows rendered
        as controls and merely styled shut, this would find them -- and the
        blast radius the step exists to remove would still be there.
        """
        page = _page(auth_client, seed_user["account"].id)

        assert rule_form_controls(page) == {}

    def test_an_OPEN_page_submits_exactly_one_merchant(
        self, auth_client, db, seed_user, _three_merchants,
    ):
        """One merchant's fields, and only that merchant's.

        Read as what a BROWSER would submit -- every control the page renders,
        at the value it renders -- rather than as a search for markup.
        """
        page = _page(
            auth_client, seed_user["account"].id,
            edit=_three_merchants.id,
        )

        controls = rule_form_controls(page)

        assert set(controls) == {
            f"rule_merchant-{_three_merchants.id}",
            f"rule-{_three_merchants.id}",
            f"rule_name-{_three_merchants.id}",
            f"rule_category-{_three_merchants.id}",
        }
        assert controls[f"rule_merchant-{_three_merchants.id}"] == str(
            _three_merchants.id,
        )

    def test_the_other_merchants_are_still_LISTED(
        self, auth_client, db, seed_user, _three_merchants,
    ):
        """Opening one row does not hide the rest.

        The pairing for the case above: a page that submitted one merchant by
        rendering only one merchant would satisfy it and would not be a
        directory.
        """
        page = _page(
            auth_client, seed_user["account"].id,
            edit=_three_merchants.id,
        )

        assert "Audible" in page
        assert "Walmart" in page
        assert "Duke Energy" in page

    def test_saving_one_merchant_leaves_every_other_answer_untouched(
        self, auth_client, db, seed_user, _three_merchants,
    ):
        """The property all three historic defects violated, measured.

        Posting the page's OWN bytes for one merchant must not write, refuse,
        or withdraw an answer for any other.
        """
        answered = the_merchant_id(seed_user, "Walmart")
        before = _rule_of(db, answered)
        assert before is not None and before.never_a_purchase is False

        page = _page(
            auth_client, seed_user["account"].id,
            edit=_three_merchants.id,
        )
        payload = dict(rule_form_controls(page))
        payload[f"rule-{_three_merchants.id}"] = "never"
        payload["csrf_token"] = "x"

        response = auth_client.post(_save_action(page), data=payload)

        assert response.status_code == 200
        assert _rule_of(db, _three_merchants.id).never_a_purchase is True
        # Walmart is untouched: still stored, still *ask me every time*.
        after = _rule_of(db, answered)
        assert after is not None
        assert after.never_a_purchase is False
        assert after.template_id is None


class TestWhatThePageStates:
    """What a closed row says, and what the page says about the whole account."""

    def test_an_unanswered_merchant_says_so_and_offers_to_be_answered(
        self, auth_client, db, seed_user,
    ):
        """The row X-gk exists for: no answer, no waiting line, still askable."""
        merchant = a_merchant(seed_user, "Duke Energy")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id)

        # **ASSERTED ON THE ROW, NOT ON THE PAGE.**  `NOT_SAID` and the
        # UNANSWERED filter's own label are the SAME STRING, and the filter bar
        # renders it on every page -- so `"You have not said" in page` was
        # satisfied by the tab whatever the row said.  Measured by an
        # adversarial mutation 2026-08-31: changing what `says_of` returns for
        # an unanswered merchant to nonsense left this case green.
        assert _rows(page)["Duke Energy"].says == NOT_SAID
        assert "Duke Energy" in _edit_links(page)
        assert f"edit={merchant.id}" in _edit_links(page)["Duke Energy"]

    def test_an_answered_merchant_states_its_answer_without_a_control(
        self, auth_client, db, seed_user,
    ):
        """A closed answered row is a SENTENCE, not a select showing it."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_rule(seed_user, "Walmart", template_id=envelope.template_id)
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id)

        assert "Groceries" in page
        assert rule_form_controls(page) == {}

    def test_the_activity_column_states_the_count_and_the_day(
        self, auth_client, db, seed_user,
    ):
        """What makes a bank abbreviation answerable."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-18.00", posted_on=day,
            merchant="Fid Bkg Svc Llc Moneyline",
        )
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id)

        # **THE FIGURE, not just the day.**  This asserted the name and the
        # date and never the number, so printing `99` for every merchant left
        # it green (adversarial mutation 2026-08-31) -- and the count is
        # precisely what the design says turns a bank abbreviation into a
        # merchant somebody recognises.  A wrong count is a wrong answer shown
        # to the owner.
        row = _rows(page)["Fid Bkg Svc Llc Moneyline"]

        assert row.lines == "1"
        assert row.last_seen == str(day)

    def test_an_account_with_no_merchants_says_where_they_come_from(
        self, auth_client, db, seed_user,
    ):
        """The empty state points at the act that fills the page."""
        page = _page(auth_client, seed_user["account"].id)

        assert "have not named a merchant" in page

    def test_a_merchant_that_pays_an_account_warns_on_its_open_row(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GJ**, inherited from the shared row producer.

        Two of the four answers are refused by the door for such a merchant,
        and the control says so rather than letting the refusal be the first
        the owner hears of it.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-500.00",
            merchant="Capital One Mobile Pmt",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Capital One Mobile Pmt")

        page = _page(auth_client, seed_user["account"].id, edit=merchant_id)

        assert "payment to an account you hold" in page


class TestTheAnswerDoor:
    """One merchant in, one sentence out, and nothing else changed."""

    def test_it_records_the_answer_the_page_submitted(
        self, auth_client, db, seed_user,
    ):
        """The round trip: read the page, post its bytes, read the row."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = f"t:{envelope.template_id}"
        payload["csrf_token"] = "x"

        response = auth_client.post(_save_action(page), data=payload)

        assert response.status_code == 200
        assert _rule_of(db, merchant.id).template_id == envelope.template_id

    def test_its_answer_IS_the_screen_and_carries_the_receipt(
        self, auth_client, db, seed_user,
    ):
        """The POST re-renders the body, so a swap cannot drift from a reload."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = f"t:{envelope.template_id}"
        payload["csrf_token"] = "x"

        body = auth_client.post(
            _save_action(page), data=payload,
        ).get_data(as_text=True)

        assert "Walmart goes in Groceries." in body
        assert 'id="statement-merchants-body"' in body

    def test_a_recorded_answer_CLOSES_the_row(
        self, auth_client, db, seed_user,
    ):
        """The control is done with, and the row states what it now says.

        **The firing control for the fresh derivation**: re-rendering from the
        pass's own BEFORE value would leave the row open showing the answer it
        had before the write.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "never"
        payload["csrf_token"] = "x"

        body = auth_client.post(
            _save_action(page), data=payload,
        ).get_data(as_text=True)

        assert rule_form_controls(body) == {}
        assert "Never a purchase" in body

    def test_the_counts_MOVE_with_the_answer(
        self, auth_client, db, seed_user,
    ):
        """The filter bar is re-derived, not carried over from the render.

        **The firing control for re-deriving after the write.**  A pass that
        answered with the state it replaced would leave the bar reading
        *You have not said 1* over a merchant that had just been answered for.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "never"
        payload["csrf_token"] = "x"

        before = _counts(_page(auth_client, seed_user["account"].id))
        after = _counts(
            auth_client.post(_save_action(page), data=payload)
            .get_data(as_text=True)
        )

        assert before == {"All": 1, "You have not said": 1, "Answered": 0}
        assert after == {"All": 1, "You have not said": 0, "Answered": 1}

    def test_a_REFUSED_answer_is_a_designed_400_that_keeps_the_row_open(
        self, auth_client, db, seed_user,
    ):
        """A refusal is fixed in the control that was being used.

        htmx leaves a 4xx non-swapping unless the response is marked, and a
        refusal that renders NOTHING reads as a broken button -- so the body
        comes back carrying the sentence, with the control still there.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        # A NEW ENVELOPE stated by halves: a name with no category, which the
        # door refuses by its own rule.
        payload[f"rule-{merchant.id}"] = "new"
        payload[f"rule_name-{merchant.id}"] = "Fuel"
        payload[f"rule_category-{merchant.id}"] = ""
        payload["csrf_token"] = "x"

        response = auth_client.post(_save_action(page), data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "needs both a name and a category" in body
        assert rule_form_controls(body) != {}
        assert _rule_of(db, merchant.id) is None

    def test_an_answer_is_RESTATED_and_never_withdrawn(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GS**: this door cannot empty the directory.

        The control renders no *I have not said* option for an answered
        merchant, so there is no submission that means *forget what I said* --
        and a crafted body saying it changes nothing rather than deleting the
        row.
        """
        a_rule(seed_user, "Walmart", always_ask=True)
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Walmart")

        response = auth_client.post(
            _url(seed_user["account"].id, edit=merchant_id),
            data={
                "csrf_token": "x",
                f"rule_merchant-{merchant_id}": str(merchant_id),
                f"rule-{merchant_id}": "unset",
            },
        )

        assert response.status_code == 200
        assert _rule_of(db, merchant_id) is not None


class TestTheViewSurvivesEveryPress:
    """The filter, the search and the ceiling ride every link and the door.

    Without that, a Save pressed under *You have not said* answers with the
    whole list and the page the owner was working reorganises under them --
    which is the register's own reason for putting its ``all`` flag on its
    door's URL.
    """

    def test_the_edit_link_carries_the_filter(
        self, auth_client, db, seed_user,
    ):
        """Opening a row from a filtered list stays in the filter."""
        merchant = a_merchant(seed_user, "Duke Energy")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, show="unsaid")

        href = _edit_links(page)["Duke Energy"]

        assert f"edit={merchant.id}" in href
        assert "show=unsaid" in href

    def test_the_edit_link_carries_the_search(
        self, auth_client, db, seed_user,
    ):
        """And the search, for the same reason."""
        a_merchant(seed_user, "Duke Energy")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, q="duke")

        assert "q=duke" in _edit_links(page)["Duke Energy"]

    def test_the_search_form_carries_the_filter_as_a_control(
        self, auth_client, db, seed_user,
    ):
        """A GET form serialises its own controls and drops its action's query.

        **The firing control for the hidden inputs.**  Without them, searching
        from inside a filter would silently drop back to *All*.
        """
        a_merchant(seed_user, "Duke Energy")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, show="unsaid")

        assert 'name="show" value="unsaid"' in page

    def test_a_save_answers_with_the_filter_it_was_pressed_under(
        self, auth_client, db, seed_user,
    ):
        """The door's own action URL carries the view.

        Answered under *You have not said*, the merchant leaves that filter --
        which is right, and it is only legible because the filter survived.
        """
        merchant = a_merchant(seed_user, "Walmart")
        a_merchant(seed_user, "Audible")
        db.session.commit()

        body = auth_client.post(
            _url(seed_user["account"].id, show="unsaid", edit=merchant.id),
            data={
                "csrf_token": "x",
                f"rule_merchant-{merchant.id}": str(merchant.id),
                f"rule-{merchant.id}": "never",
            },
        ).get_data(as_text=True)

        assert "Walmart is never a purchase." in body
        # Still on the unanswered filter, which no longer holds Walmart.
        assert "Audible" in body


class TestTheCeilingIsSaidOnTheScreen:
    """A truncated list that does not say so is a page claiming to be whole."""

    @pytest.fixture()
    def _past_the_ceiling(self, db, seed_user):
        """Stage one merchant past :data:`DIRECTORY_LIMIT`."""
        for ordinal in range(DIRECTORY_LIMIT + 1):
            a_merchant(seed_user, f"Merchant {ordinal:04d}")
        db.session.commit()

    def test_the_page_says_how_many_it_withheld(
        self, auth_client, db, seed_user, _past_the_ceiling,
    ):
        """The disclosure, and the link that lifts the bound."""
        page = _page(auth_client, seed_user["account"].id)

        assert f"Showing {DIRECTORY_LIMIT} of {DIRECTORY_LIMIT + 1}" in page
        assert "all=1" in page

    def test_the_lifted_page_says_nothing_about_a_bound(
        self, auth_client, db, seed_user, _past_the_ceiling,
    ):
        """The pairing: the footer appears only when the ceiling BINDS."""
        page = _page(auth_client, seed_user["account"].id, all=1)

        assert "Showing" not in page

    def test_a_complete_list_says_nothing_about_a_bound(
        self, auth_client, db, seed_user,
    ):
        """The other pairing: an account under the ceiling sees no footer."""
        a_merchant(seed_user, "Audible")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id)

        assert "Showing" not in page

class TestTheSearchBoxActuallySearches:
    """The GET form's controls, and the defect that froze it on one term.

    **`_view_args` carries `q` and the form renders a visible control named
    `q`**, so hidden-inputting the whole view submitted the argument TWICE.
    ``request.values.get`` takes the FIRST, which is the OLD term -- so typing
    a new merchant name re-ran the previous search forever, and clearing the
    box did not clear it.  Every link on the page carries `q` forward, so there
    was no control that escaped a search once entered.

    Found by adversarial review 2026-08-31; introduced by this step while
    removing a string comparison from the template.
    """

    @pytest.fixture()
    def _two_merchants(self, db, seed_user):
        """Stage two merchants with no shared substring."""
        a_merchant(seed_user, "Duke Energy")
        a_merchant(seed_user, "Walmart")
        db.session.commit()

    def test_the_form_emits_exactly_one_control_named_q(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """The firing control for the defect itself.

        Counted on a page that ALREADY has a search, because that is the only
        state in which the duplicate was rendered -- a first search from the
        bare page emitted one either way, which is why every existing case
        passed over it.
        """
        page = _page(auth_client, seed_user["account"].id, q="duke")

        assert page.count('name="q"') == 1

    def test_a_second_search_finds_the_SECOND_term(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """What the owner actually experiences, end to end.

        **It submits the FORM rather than a hand-built URL**, and that is the
        only way this case can see the defect: a GET form serialises every
        control it renders, so the duplicate `q` rode along in document order
        and the server read the first.  A test client told to fetch
        ``?q=walmart`` sends exactly that and would pass over the whole thing.
        Measured: with the duplicate control restored, a hand-built URL left
        this case green and only the markup count died.
        """
        account_id = seed_user["account"].id
        first = _page(auth_client, account_id, q="duke")
        assert list(_rows(first)) == ["Duke Energy"]

        second = auth_client.get(_search_url(first, account_id, "walmart"))

        assert list(_rows(second.get_data(as_text=True))) == ["Walmart"]

    def test_the_box_redisplays_what_was_typed(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """A search box that forgets the term reads as a failed search."""
        page = _page(auth_client, seed_user["account"].id, q="duke")

        assert 'value="duke"' in page

    def test_CLEAR_clears_the_search(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """`close_url` keeps the view; the Clear link must not.

        It is rendered only when there is something to clear, and it pointed at
        `close_url` -- which drops the open row and keeps `q`, so the one
        control whose whole job is to clear the search reloaded it.
        """
        page = _page(auth_client, seed_user["account"].id, q="duke")
        clear = re.search(r'href="([^"]+)"[^>]*>Clear<', page)
        assert clear is not None, "the page rendered no Clear link"

        landed = auth_client.get(clear.group(1).replace("&amp;", "&"))

        assert set(_rows(landed.get_data(as_text=True))) == {
            "Duke Energy", "Walmart",
        }

    def test_the_WHOLE_LIST_link_escapes_a_search_that_matched_nothing(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """The only way out of an empty result, and it used to lead back in.

        The sentence says *whole list* and prints the ALL count, so its link
        drops the search AND the filter.  It pointed at `close_url`, which
        keeps both.
        """
        page = _page(
            auth_client, seed_user["account"].id,
            show="unsaid", q="zzzznothing",
        )
        assert _rows(page) == {}
        escape = re.search(r'href="([^"]+)"[^>]*>whole list</a>', page)
        assert escape is not None, "the empty state offered no way out"

        landed = auth_client.get(escape.group(1).replace("&amp;", "&"))

        assert set(_rows(landed.get_data(as_text=True))) == {
            "Duke Energy", "Walmart",
        }

    def test_a_search_longer_than_the_control_can_send_is_404(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """`maxlength` is a browser courtesy; the server bounds it too.

        A value this page's own control cannot produce is a tampered or stale
        request, and takes the same 404 the two sibling arguments take.
        """
        assert auth_client.get(
            _url(seed_user["account"].id, q="a" * (SEARCH_MAX_LENGTH + 1)),
        ).status_code == 404

    def test_a_search_AT_the_bound_still_answers(
        self, auth_client, db, seed_user, _two_merchants,
    ):
        """The pairing: the refusal above is about the LENGTH, not the box."""
        assert auth_client.get(
            _url(seed_user["account"].id, q="a" * SEARCH_MAX_LENGTH),
        ).status_code == 200


class TestTheSaveFormCarriesWhereItCameFrom:
    """The door's action, which is what a refusal re-renders from.

    **This is the half of `_recorded_nothing` that did not ship.**  Its
    docstring says the row stays open so the owner can fix the answer they were
    giving; `save_action` was built from `_view_args`, which deliberately omits
    the open row, so every real POST read `edit` as absent and the 400 body
    rendered the whole list with no control at all.
    """

    def test_the_action_names_the_row_being_edited(
        self, auth_client, db, seed_user,
    ):
        """Read off the page, because that is what a browser posts to."""
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        action = _save_action(
            _page(auth_client, seed_user["account"].id, edit=merchant.id),
        )

        assert f"edit={merchant.id}" in action

    def test_the_action_carries_the_filter_and_the_search(
        self, auth_client, db, seed_user,
    ):
        """So a save answers with the view the owner was working in.

        Graded nowhere before: `TestTheViewSurvivesEveryPress` reads the EDIT
        links and the search form's hidden inputs, and never the one place the
        view reaches the PRESS.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        action = _save_action(
            _page(
                auth_client, seed_user["account"].id,
                show="unsaid", q="wal", edit=merchant.id,
            ),
        )

        assert "show=unsaid" in action
        assert "q=wal" in action
        assert f"edit={merchant.id}" in action

    def test_a_refusal_posted_to_that_action_keeps_the_control(
        self, auth_client, db, seed_user,
    ):
        """The whole point, exercised the way a browser would.

        The half-typed envelope name is still on the page, in a control the
        owner can correct -- rather than a sentence over a list they have to
        re-navigate.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()
        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "new"
        payload[f"rule_name-{merchant.id}"] = "Fuel"
        payload[f"rule_category-{merchant.id}"] = ""
        payload["csrf_token"] = "x"

        body = auth_client.post(
            _save_action(page), data=payload,
        ).get_data(as_text=True)

        assert f"rule-{merchant.id}" in rule_form_controls(body)
        assert "needs both a name and a category" in body

    def test_an_answered_merchant_LEAVES_the_unanswered_filter(
        self, auth_client, db, seed_user,
    ):
        """What the save's own filter is FOR, asserted as absence.

        The case that named this claim asserted the receipt sentence and a
        second merchant's presence, both of which are true under every filter.
        An adversarial mutation that made the save forget the filter left it
        green.
        """
        merchant = a_merchant(seed_user, "Walmart")
        a_merchant(seed_user, "Audible")
        db.session.commit()
        page = _page(
            auth_client, seed_user["account"].id,
            show="unsaid", edit=merchant.id,
        )
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "never"
        payload["csrf_token"] = "x"

        body = auth_client.post(
            _save_action(page), data=payload,
        ).get_data(as_text=True)

        assert "Walmart" not in _rows(body)
        assert "Audible" in _rows(body)


class TestTheDoorRefusesWhatItCannotAnswerFor:
    """The write door's own refusals, at the door rather than one tier down."""

    def test_a_merchant_of_ANOTHER_account_is_refused_not_500(
        self, auth_client, db, seed_user,
    ):
        """A crafted body naming a merchant this account has never seen.

        Covered at the service and at a sibling door; this door's own
        400-rather-than-500 was unpinned.  ``_refuse_unknown_merchants`` runs
        before any name lookup, so the refusal is a sentence rather than an
        ``IntegrityError`` escaping the item's savepoint.
        """
        sibling = Account(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Second Checking",
        )
        db.session.add(sibling)
        db.session.flush()
        elsewhere = a_merchant(seed_user, "Audible", account=sibling)
        db.session.commit()

        response = auth_client.post(
            _url(seed_user["account"].id),
            data={
                "csrf_token": "x",
                f"rule_merchant-{elsewhere.id}": str(elsewhere.id),
                f"rule-{elsewhere.id}": "never",
            },
        )

        assert response.status_code == 400
        assert _rule_of(db, elsewhere.id) is None

    def test_a_submission_naming_NO_merchant_prints_no_receipt(
        self, auth_client, db, seed_user,
    ):
        """``StatedRules((), (), 0)`` is truthy, so the card rendered anyway.

        It fell into the no-op arm and told the owner *"that was already your
        answer"* about a submission that named nobody.  Crafted-request-only,
        so the cost is a misleading sentence rather than a wrong write.
        """
        a_merchant(seed_user, "Audible")
        db.session.commit()

        body = auth_client.post(
            _url(seed_user["account"].id), data={"csrf_token": "x"},
        ).get_data(as_text=True)

        assert "already your answer" not in body
        assert "What that recorded" not in body


class TestTheCeilingDoesNotHideTheRowYouAsked:
    """The ceiling x open-row axis, which no case varied.

    `TestTheOpenRow` never set a limit and `TestTheCeilingIsSaidOnTheScreen`
    never set `edit`, so a merchant sorting past the ceiling answered 404 with
    the sentence reserved for *this account has never seen it*.
    """

    @pytest.fixture()
    def _the_last_merchant(self, db, seed_user):
        """Stage one merchant past the ceiling, and return the LAST by name."""
        last = None
        for ordinal in range(DIRECTORY_LIMIT + 1):
            last = a_merchant(seed_user, f"Merchant {ordinal:04d}")
        db.session.commit()
        return last

    def test_opening_a_row_past_the_ceiling_still_answers(
        self, auth_client, db, seed_user, _the_last_merchant,
    ):
        """The refusal was about the CEILING while claiming to be about the
        account, which is a 404 that means something false."""
        assert auth_client.get(
            _url(seed_user["account"].id, edit=_the_last_merchant.id),
        ).status_code == 200

    def test_and_it_renders_that_row_s_control(
        self, auth_client, db, seed_user, _the_last_merchant,
    ):
        """The pairing: a 200 with no control would satisfy the case above."""
        page = _page(
            auth_client, seed_user["account"].id,
            edit=_the_last_merchant.id,
        )

        assert f"rule-{_the_last_merchant.id}" in rule_form_controls(page)


class TestTheNewEnvelopeAnswerNeedsNOTHINGToRun:
    """Finding **bank_import:N-403**, plan step ``bank_import:X-gj-1c``.

    **A chooser whose submission can never succeed** -- the shape this package
    has now closed six times -- surviving in the one state nothing scripted a
    check for.  The shared macro rendered the new-envelope name and category
    boxes inside containers marked ``d-none`` on a merchant with no rule, and
    ``statement_rules.js`` was the only thing that could ever un-hide them.
    With scripting off the owner could pick *a new envelope*, never see the
    category box, and be refused by the door every time.

    **Measured on this page 2026-08-31 before the fix**, by scraping it and
    posting its own bytes: containers ``col-12 col-lg-4 d-none`` and
    ``col-12 col-lg-3 d-none``, a scrape carrying ``rule_category-1=''``, and a
    designed 400 reading *needs both a name and a category*.  Three of the four
    answers landed fine; this one could not.

    **The refusal itself is CORRECT and stays.**  A new envelope stated by
    halves is unwritable (``ck_merchant_rules_one_answer``), so
    ``_reject_incomplete_new_envelope`` turning that into a sentence is right.
    What changed is that the owner can now reach the half they were missing:
    the stylesheet expresses the dependency, and a browser that cannot read it
    shows both boxes always.

    **The cases post what THIS PAGE RENDERS**, which is the whole reason the
    defect survived: a scraper cannot see whether a control is visible, so a
    payload naming the category directly would have been green throughout.
    """

    def _category_offered(self, page, merchant_id):
        """Return a category id the page's own picker offers, and its label.

        Args:
            page: The rendered page.
            merchant_id: Whose row to read.

        Returns:
            ``(value, label)`` for the first real option, or ``None`` when the
            picker offers nothing but its empty opening state.
        """
        select = re.search(
            rf'name="rule_category-{merchant_id}"(?:.|\n)*?</select>', page,
        )
        assert select is not None, "the category picker is not on the page"
        options = re.findall(
            r'<option value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>',
            select.group(0),
        )
        return options[0] if options else None

    def test_neither_field_is_hidden_by_a_class(
        self, auth_client, db, seed_user,
    ):
        """FIRING CONTROL: both containers carried ``d-none`` before the fix.

        Asked of a merchant with NO rule, which is the state the class was
        emitted for -- an answered merchant's row rendered them visible all
        along, so a case over one would have passed throughout.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)

        assert page.count("data-rule-new-field") == 2
        assert not re.search(
            r'class="[^"]*\bd-none\b[^"]*"[^>]*data-rule-new-field', page,
        )
        assert "js/statement_rules.js" not in page

    def test_the_answer_LANDS_from_what_a_no_script_browser_can_send(
        self, auth_client, db, seed_user,
    ):
        """The whole round trip, with every value taken off the page.

        The category is set to one the page's OWN picker offers, because that
        is exactly what an owner does now that they can see it.

        **This case would pass on the PRE-FIX markup**, and that is stated
        rather than left to be discovered: setting the category by hand is what
        an owner does with a visible box and also what a script-driven page
        did, so the reveal is not what it measures.  What it measures is that
        the round trip WORKS -- the answer, the prefilled name and the chosen
        category reach the door and land as the new-envelope arm.  The reveal's
        own firing controls are
        :meth:`test_neither_field_is_hidden_by_a_class` above and the
        stylesheet gate in
        ``tests/test_arch/test_a_control_rendered_invisible_has_a_rule_that_shows_it.py``.
        Measured by adversarial test-quality review 2026-08-31.
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        category_id, _ = self._category_offered(page, merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "new"
        payload[f"rule_category-{merchant.id}"] = category_id
        payload["csrf_token"] = "x"

        response = auth_client.post(_save_action(page), data=payload)
        rule = _rule_of(db, merchant.id)

        assert response.status_code == 200, response.get_data(as_text=True)
        assert rule is not None
        # The NEW-ENVELOPE arm on the row itself: a name and a category and no
        # template, which is one of the three shapes
        # ``ck_merchant_rules_one_answer`` admits.
        assert rule.template_id is None
        assert rule.envelope_name == "Walmart"
        assert rule.category_id == int(category_id)

    def test_the_name_the_page_PREFILLS_is_what_lands(
        self, auth_client, db, seed_user,
    ):
        """The scraped name is the merchant's, and it is not hard-coded here.

        Reading it back off the page rather than asserting the literal is what
        keeps this case honest if the prefill ever changes: the claim is that
        what the owner SEES is what is recorded.
        """
        merchant = a_merchant(seed_user, "Food Lion")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        scraped = rule_form_controls(page)
        category_id, _ = self._category_offered(page, merchant.id)
        payload = dict(scraped)
        payload[f"rule-{merchant.id}"] = "new"
        payload[f"rule_category-{merchant.id}"] = category_id
        payload["csrf_token"] = "x"

        auth_client.post(_save_action(page), data=payload)

        assert scraped[f"rule_name-{merchant.id}"] == "Food Lion"
        assert _rule_of(db, merchant.id).envelope_name == "Food Lion"

    def test_a_new_envelope_stated_by_HALVES_is_still_refused(
        self, auth_client, db, seed_user,
    ):
        """The door's own rule, unchanged: the fix reveals, it does not admit.

        Posting the page's bytes with only the ANSWER changed is what a person
        does who picks *a new envelope* and chooses no category -- now a
        visible omission rather than an invisible one -- and it must still be
        refused, because the row is unwritable
        (``ck_merchant_rules_one_answer``).
        """
        merchant = a_merchant(seed_user, "Walmart")
        db.session.commit()

        page = _page(auth_client, seed_user["account"].id, edit=merchant.id)
        payload = dict(rule_form_controls(page))
        payload[f"rule-{merchant.id}"] = "new"
        payload["csrf_token"] = "x"

        response = auth_client.post(_save_action(page), data=payload)

        assert response.status_code == 400
        assert "needs both a name and a category" in response.get_data(
            as_text=True,
        )
        assert _rule_of(db, merchant.id) is None
