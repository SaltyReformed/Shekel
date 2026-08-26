"""The review screen, the POST that applies a reviewed pass, and the undo.

Plan steps **bank_import:X-f6a-2** and **X-f6a-3c-2**.  The route's own
subjects, none of which the service tests can see: OWNERSHIP (the security
response rule's 404 for both "not found" and "not yours"), the FORM PAYLOAD,
the unit of work, and what the screen SAYS about what moved.

**Every payload here is what the template actually emits.**  That is a rule
this arc has paid for twice: a hand-picked subset shipped a dead arm at plan
step X-f6a-3b (three adversarial reviews found the existing-envelope
destination unreachable from a browser, because the always-rendered name box
read as a destination of its own), and a raw ``MultiDict`` read of a repeated
field would refuse every group match. So the helpers below build the ids the
same way the form does -- ``apply`` naming a ticked item's rendered position,
``match-<i>-*`` carrying that item's ids, ``destination-<line>`` naming where
one bank line goes.

**The multi-value case is the one that would otherwise ship broken.**  A GROUP
match posts the same field name several times and ``request.form["k"]`` returns
only the FIRST of them.  No service test can see that: they pass real lists.

**Nothing an owner did not tick may be applied** (ruling **R-FP**), and at 215
acts in one request that is a property with two halves rather than a slogan: an
un-ticked proposal contributes its ids and is not applied, and a bank line
whose destination select was never moved off "leave this line alone" is not
recorded.  Both are asserted below, because the batch is where a default
becomes forty writes nobody chose.

**The ownership tests are firing controls against an IDOR** on a door that
MOVES MONEY: a route answering for another user's account would let one user
re-date another's records and mint budget rows in their periods.  Both shapes
the one door now carries -- a match and a creation -- are tested, because the
service reaches them through different code and a decorator proves nothing
about which arm ran.
"""

import re
from datetime import date, timedelta
from html.parser import HTMLParser
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from sqlalchemy.exc import OperationalError
from werkzeug.datastructures import MultiDict

from app.exceptions import ValidationError
from app.routes.accounts import statement_matches as statement_matches_route
from app.services import auth_service, entry_service
from app.services.statement_match import RowKind
from app.services.statement_match import _batch as statement_match_batch
from app.utils.money import round_money
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_purchase,
    a_reviewed_token,
    a_transaction,
    an_import,
)


def _review_url(account_id):
    """Return the review page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review"


def _never_showed_panel(body):
    """Return just the "rows no line explains" card's markup.

    **An assertion about ONE panel has to read that panel.**  This page renders
    five cards and hundreds of badges, so "the words are somewhere in the body"
    is satisfied by any of them -- the failure mode ``_delete_form`` in
    ``test_statements.py`` records and which that file then went on to make
    anyway.

    **It is bounded by the NEXT CARD, not by the next ``</table>``, and a first
    version was bounded by the table.**  When this card lists nothing it
    renders ``<p>None.</p>`` and holds no table at all, so that cut ran past
    the card, past the hand-build totals and into the keyboard-help modal --
    4,605 characters, ending in ``<kbd>Esc</kbd>``.  Every negative assertion
    made against that region was then graded by markup from a different
    feature, and :func:`_never_showed_rows`' own empty-guard was satisfied by
    the help modal's ``<tbody>``.  Found by adversarial review of this step's
    own tests, 2026-08-25.

    Args:
        body: The rendered review page, as text.

    The card carries an ``id`` for exactly this reason, and the totals panel
    below it carries the one that bounds the far end.

    Args:
        body: The rendered review page, as text.

    Returns:
        The card's markup, from its own id to the hand-totals panel below it.
    """
    start = body.index('id="rows-no-line-explains"')
    return body[start:body.index('id="hand-totals"', start)]


def _never_showed_rows(body):
    """Return just the ROW LIST of that card, without its caption.

    The caption names the tag in order to explain it, so a search for the tag
    across the whole card is satisfied by the paragraph that describes it and
    says nothing about which rows wear one.  Every assertion about a ROW reads
    this instead.

    Args:
        body: The rendered review page, as text.

    Returns:
        The card's ``<tbody>`` markup, or ``""`` when it lists nothing.
    """
    panel = _never_showed_panel(body)
    if "<tbody>" not in panel:
        return ""
    return panel[panel.index("<tbody>"):]


def _match(index=0, lines=(), transactions=(), entries=(), residual=None):
    """Return the form fields a TICKED match item submits.

    The FIELD NAMES ``_statement_review_body.html`` emits: the tick names the
    item's rendered position, one hidden input carries each bank line, and one
    carries each ROW as the screen showed it -- kind, id, figure and revision
    (plan step ``bank_import:X-f6d-3``).  **The row VALUES are built through
    the service, not scraped**, so this helper cannot show that the template
    renders them; :class:`TestWhatTheTEMPLATEEmittedIsWhatTheDOORAccepts` is
    what does, by posting the page's own bytes back.  That last is what makes *what commits
    is what was reviewed* (ruling **R-FP**) checkable rather than intended:
    the door refuses an item whose row moved since the render.

    Args:
        index: The item's rendered position, or ``"hand"`` for the
            hand-build form -- whose index is deliberately not a number, so it
            can never collide with a proposal's.
        lines: Bank line rows it explains.
        transactions: Transaction rows that explain them.
        entries: Purchase rows that explain them.
        residual: What the consent box carries when the owner ticked it -- the
            difference the screen showed (plan step ``bank_import:X-f6d-4``).
            ``None`` leaves the field off entirely, which is what an unticked
            checkbox submits.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    fields = {
        "apply": [str(index)],
        f"match-{index}-line_ids": [str(line.id) for line in lines],
        f"match-{index}-rows": (
            [a_reviewed_token(txn, RowKind.TRANSACTION)
             for txn in transactions]
            + [a_reviewed_token(entry, RowKind.PURCHASE) for entry in entries]
        ),
    }
    if residual is not None:
        fields[f"match-{index}-residual"] = [str(residual)]
    return fields


def _pass(*parts):
    """Merge several items' fields into ONE submitted form.

    **``apply`` is a REPEATED key, so merging is a union rather than an
    update.**  A plain ``dict.update`` overwrites it, which silently leaves one
    item ticked out of however many were meant -- and every assertion about
    what landed then grades a pass that was never submitted.  Found by writing
    exactly that and watching four items become two.

    Args:
        *parts: The per-item field dicts from :func:`_match` /
            :func:`_record_line`.

    Returns:
        The merged form, list values concatenated.
    """
    merged = {}
    for part in parts:
        for key, value in part.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged[key] = value
    return merged


def _record_line(line, *, destination, name="Walmart", category_id=""):
    """Return the form fields ONE creatable line submits.

    **The name and the category are always submitted**, whichever destination
    was picked, because a browser submits every control it renders -- which is
    the fact a hand-picked payload hid at plan step X-f6a-3b.  The SELECT is
    what says which arm was chosen.

    Args:
        line: The bank line row.
        destination: ``"new"``, an envelope id, or ``""`` to leave it alone --
            which is the select's own default.
        name: What the name box carries.
        category_id: What the category select carries; ``""`` is its default,
            because the category is a decision rather than a default.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        f"destination-{line.id}": str(destination),
        f"envelope_name-{line.id}": name,
        f"category_id-{line.id}": str(category_id),
    }


def _merchants_url(account_id):
    """Return the merchant-policy POST's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review/merchants"


def _policy(index, merchant_id, *, answer, name="", category_id=""):
    """Return the form fields ONE merchant row of the policy section submits.

    **Every control the row renders**, whichever answer was picked, because a
    browser submits every control it renders -- the fact a hand-picked payload
    hid at plan step X-f6a-3b, applied to the section this leaf adds.

    Args:
        index: The row's rendered position, which is what keys its fields.
        merchant_id: The merchant ROW the hidden input carries (plan step
            ``bank_import:X-gd-1``); it was the bank's own string until then.
        answer: ``"unset"`` (I have not said), ``"never"``, ``"new"``, or
            ``"t:<template_id>"``.
        name: What the envelope-name box carries.
        category_id: What the category select carries; ``""`` is its default.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        f"policy-{index}": str(answer),
        f"policy_merchant-{index}": str(merchant_id),
        f"policy_name-{index}": name,
        f"policy_category-{index}": str(category_id),
    }


class _PolicyFormReader(HTMLParser):
    """Collect the policy form's controls and their RENDERED values.

    **A browser submits every control it renders, at the value it renders**, and
    that is the fact a hand-written payload cannot check -- it is written by the
    same person as the template, so the two agree about a mistake as readily as
    about the truth.  This reads the page instead.

    A ``<select>`` submits the option carrying ``selected``, and its FIRST
    option when none does; an ``<input>`` submits its ``value``.  Only controls
    whose name begins with ``policy`` are collected, because the review body
    holds three forms and a browser posts one at a time.
    """

    def __init__(self, prefixes=("policy",)):
        super().__init__()
        self.prefixes = prefixes
        self.controls = {}
        self._select = None
        self._first = None

    def _mine(self, name):
        """Return whether *name* belongs to the form being read."""
        return any(name.startswith(prefix) for prefix in self.prefixes)

    def handle_starttag(self, tag, attrs):
        """Record an input's value, or open a select and read its options."""
        attributes = dict(attrs)
        name = attributes.get("name", "")
        if tag == "input" and self._mine(name):
            if attributes.get("type") == "checkbox" and (
                "checked" not in attributes
            ):
                # An unticked checkbox submits NOTHING, which is the whole
                # point of the default this screen rests on.
                return
            self.controls[name] = attributes.get("value", "")
        elif tag == "select" and self._mine(name):
            self._select, self._first = name, None
        elif tag == "option" and self._select is not None:
            value = attributes.get("value", "")
            if self._first is None:
                self._first = value
            if "selected" in attributes:
                self.controls[self._select] = value

    def handle_endtag(self, tag):
        """Close a select, defaulting it to its first option if none was set."""
        if tag == "select" and self._select is not None:
            self.controls.setdefault(self._select, self._first or "")
            self._select = None


def _policy_form_controls(page):
    """Return what a browser would submit for the merchant-policy form."""
    reader = _PolicyFormReader()
    reader.feed(page)
    return reader.controls


def _apply_form_controls(page):
    """Return what a browser would submit for the APPLY form, untouched.

    The money form's own version of :func:`_policy_form_controls`, and it
    exists for the sharper case: pressing Apply having touched nothing must
    write nothing, and a hand-written ``destination=""`` grades the reader's
    idea of the default rather than the template's.
    """
    reader = _PolicyFormReader(
        prefixes=("destination-", "envelope_name-", "category_id-", "apply"),
    )
    reader.feed(page)
    return reader.controls


class _TickedMatchReader(HTMLParser):
    """Collect the APPLY form's match controls, keeping REPEATED names.

    :class:`_PolicyFormReader`'s twin for the one field that is submitted more
    than once per item.  A ``dict`` cannot hold a GROUP -- ``match-0-rows`` is
    rendered once per member row -- and a group is exactly where the
    multi-value defect this file's own docstring names would hide.

    It ticks every rendered proposal, because a hidden input is submitted
    whether or not its checkbox is: what is under test is the VALUE the
    template emitted, so the tick is supplied rather than scraped.
    """

    def __init__(self):
        super().__init__()
        self.fields = []

    def handle_starttag(self, tag, attrs):
        """Record every ``match-*`` control the page rendered."""
        attributes = dict(attrs)
        name = attributes.get("name", "")
        if tag == "input" and name.startswith("match-"):
            self.fields.append((name, attributes.get("value", "")))


def _rendered_match_fields(page):
    """Return the ``match-*`` fields a browser would post, verbatim."""
    reader = _TickedMatchReader()
    reader.feed(page)
    return reader.fields


class _ConsentReader(HTMLParser):
    """Read the hand-build panel's consent box, exactly as a browser would.

    Plan step ``bank_import:X-f6d-4``.  The box's VALUE is the figure the
    server computed and the door will compare, so scraping it is the only way
    a test can post what the screen actually offered rather than a figure of
    its own.
    """

    def __init__(self):
        super().__init__()
        self.value = None
        self.disabled = None

    def handle_starttag(self, tag, attrs):
        """Record the consent control the panel rendered."""
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "match-hand-residual":
            self.value = attributes.get("value", "")
            self.disabled = "disabled" in attributes


def _rendered_consent(page):
    """Return ``(value, disabled)`` for the panel's consent box.

    Args:
        page: The rendered review body, or the panel fragment alone.

    Returns:
        The box's submitted value and whether a browser would submit it.
    """
    reader = _ConsentReader()
    reader.feed(page)
    return reader.value, reader.disabled


class _VisibleText(HTMLParser):
    """Collect the text a reader would SEE, with the markup out of the way.

    A panel figure sits inside its own ``<span>`` for emphasis, so a byte match
    over the raw fragment grades the styling rather than the sentence.
    """

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        """Keep every text node."""
        self.parts.append(data)


def _visible_text(page):
    """Return *page*'s text with tags removed and whitespace collapsed."""
    reader = _VisibleText()
    reader.feed(page)
    return " ".join(" ".join(reader.parts).split())


def _element_carrying(page, needle):
    """Return the source of the ``<div>`` whose start tag contains *needle*.

    Depth-counted over ``<div`` / ``</div>``, so what comes back is that
    element and everything nested inside it -- which is the question these
    cases ask: which controls sit INSIDE a given element.  Every other tag on
    this page is either void or cannot contain a div, so a div counter is
    exact here.

    Args:
        page: The rendered body.
        needle: A string appearing in the wanted element's start tag.

    Returns:
        The element's full source, or ``None`` when nothing carries *needle*.
    """
    at = page.find(needle)
    if at == -1:
        return None
    start = page.rfind("<div", 0, at)
    if start == -1:
        return None
    depth, cursor = 0, start
    for match in re.finditer(r"<div\b|</div>", page[start:]):
        depth += 1 if match.group().startswith("<div") else -1
        if depth == 0:
            cursor = start + match.end()
            break
    else:
        cursor = len(page)
    return page[start:cursor]


def _totals_url(seed_user):
    """Return the endpoint the hand-build panel re-renders through."""
    return (
        f"/accounts/{seed_user['account'].id}/statements/review/totals"
    )


class TestTheReviewPage:
    """What the GET renders."""

    def test_it_shows_a_proposal_and_what_accepting_would_do(
        self, auth_client, db, seed_user,
    ):
        """The page names the correction rather than only the pairing."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
            description="ACH DEBIT DUKEENERGYCORPOR",
        )
        a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"ACH DEBIT DUKEENERGYCORPOR" in response.data
        assert b"Electricity" in response.data
        assert b"moves the day by 3 day(s)" in response.data

    def test_it_states_the_AMOUNT_a_near_miss_would_correct(
        self, auth_client, db, seed_user,
    ):
        """*bank `$178.29`, your row `$178.32`* -- before anything is pressed.

        Plan step **bank_import:X-f6d-1**, finding **N-335**.  This proposal
        did not exist at all until this step: the match predicate gated on an
        exact cent, so the line read as unexplained and the screen's cheapest
        remaining act recorded it a SECOND time -- `$356.61` booked for one
        `$178.29` movement.  Offering the pairing without the figures would
        put a money correction in front of the owner with nothing saying what
        it was, so all three are asserted.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
            description="ACH DEBIT GEICO PREM COLL", merchant="Geico",
        )
        a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=4),
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = response.data

        assert response.status_code == 200
        assert b"corrects the amount from -$178.32 to -$178.29" in body
        assert b'data-proposal-class="reprice"' in body

    def test_a_line_whose_row_does_not_NAME_the_merchant_is_left_alone(
        self, auth_client, db, seed_user,
    ):
        """The corroboration rule, from the screen (developer, 2026-08-22).

        Measured on a production clone: without it a `Lowe's` swipe pairs with
        a ``CC Payback: Mint Mobile`` row 0.106% away -- nearer than two
        genuine `Geico` pairs at 0.180%, so no bound separates them.  The line
        stays UNEXPLAINED here, which is what keeps it on the
        create-a-purchase arm rather than consuming it with a pairing the app
        cannot justify.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-131.74", posted_on=bank_day,
            description="POINT OF SALE DEBIT LOWE S #677", merchant="Lowe's",
        )
        a_transaction(
            seed_user, name="Mint Mobile", amount="131.60",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        body = auth_client.get(_review_url(seed_user["account"].id)).data

        assert b"corrects the amount from" not in body
        assert b'data-proposal-class=' not in body
        assert b"POINT OF SALE DEBIT LOWE S #677" in body

    def test_it_names_the_purchase_date_it_would_CORRECT(
        self, auth_client, db, seed_user,
    ):
        """The only warning before a write a Release cannot undo.

        Accepting a match rewrites a matched purchase's ``purchased_on``
        (ruling **R-FW**), and ``release_match`` deliberately does not put days
        back.  The caption had NO test at all -- and a first version named the
        day the purchase moves TO without naming the day it moves FROM, so a
        reviewer saw "corrects 1 purchase date(s) to 2026-05-30" with nothing
        saying the app currently held a different day.  Found by two adversarial
        reviews 2026-08-18.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=4),
        )
        a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=bank_day,
        )
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=2),
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        body = response.data
        assert b"corrects 1 purchase date(s)" in body
        assert str(bank_day - timedelta(days=2)).encode() in body, (
            "the day it moves TO is not named"
        )
        assert b"back 6 day(s)" in body, "the distance is not named"
        assert str(bank_day + timedelta(days=4)).encode() in body, (
            "the day it moves FROM is not named"
        )

    def test_it_says_the_page_changes_records(self, auth_client, seed_user):
        """This screen MOVES MONEY and, unlike its sibling, says so."""
        response = auth_client.get(_review_url(seed_user["account"].id))

        assert b"Applying what you ticked changes your records" in (
            response.data
        )

    def test_it_names_lines_older_than_the_pay_calendar(
        self, auth_client, db, seed_user,
    ):
        """130 of the developer's own 361 lines are these.

        Listing them beside genuine failures would bury the ones worth acting
        on, and saying nothing about them would read as a clean sweep.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-40.00",
            posted_on=seed_user["bootstrap_period"].start_date
            - timedelta(days=30),
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert b"older than your pay calendar" in response.data

    def test_it_names_the_near_misses_it_would_not_CHOOSE_between(
        self, auth_client, db, seed_user,
    ):
        """A score that withholds is a bound, and a silent bound is a sweep.

        Plan step **bank_import:X-f6d-1**.  Two rows of the owner's sit the
        same distance from one line and both name its merchant, so the pass
        declines to pick -- *an ambiguous proposal is a question dressed as an
        answer*.  Saying nothing would leave the line looking like one nothing
        could explain, which is the state the merchant policy offers to RECORD
        and the duplicate this whole step exists to stop.

        **It is rendered against the LINE since plan step
        ``bank_import:X-f6d-3``**, in both cards the line appears in, rather
        than as a count in the panel at the foot of the page.  A count there
        named no line, so the owner could not act on it -- and the act it
        should prompt is offered against one specific line: build this one by
        hand rather than record it a second time.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
            description="ACH DEBIT GEICO PREM COLL", merchant="Geico",
        )
        for suffix in ("Auto", "Home"):
            a_transaction(
                seed_user, name=f"Geico {suffix}", amount="178.32",
                status=StatusEnum.DONE, settled_on=bank_day,
            )
        db.session.commit()

        body = auth_client.get(_review_url(seed_user["account"].id)).data

        # **Whitespace-normalised, because the assertion is about the
        # SENTENCE and not about where the template wraps it.**  A raw
        # substring test on the rendered bytes passes or fails on an
        # indentation change, which is a fact about the file rather than about
        # what the owner reads.
        said = " ".join(body.decode().split())

        # ON the line in the create card, where the WRONG act is cheapest...
        assert "not one this page would pick for you" in said
        assert "before recording this as new spending" in said
        # ...and on the line in the hand-build list, where the RIGHT one is.
        assert "A row of yours is very close to this on the amount." in said
        # ...and NOT as a bare number in the bounds panel any more.
        assert "line(s) have a row of yours" not in said
        assert b'data-proposal-class=' not in body


class TestWhatTheTEMPLATEEmittedIsWhatTheDOORAccepts:
    """The loop nothing else in this suite closes.

    Every other case here builds its payload through
    :func:`~tests.test_services.test_statement_match._builders.a_reviewed_token`,
    which reaches the same service function the ``reviewed_token`` filter does
    -- so it grades the SERVICE against itself and says nothing about whether
    the TEMPLATE renders that value, or renders it under the name the schema
    reads.  Those two halves have no compile-time relationship at all: a Jinja
    filter name and a Marshmallow field name.

    This scrapes the rendered page and posts exactly what it found.  It is the
    project's own lesson -- *a form submits every control it renders, and a
    hand-picked payload shipped a primary arm that was DEAD in a browser* --
    applied to the wire format plan step ``bank_import:X-f6d-3`` introduced.
    Named by adversarial design review 2026-08-23.
    """

    def test_a_scraped_payload_applies_and_reprices(
        self, auth_client, db, seed_user,
    ):
        """Post the page's own bytes back, and the money moves."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
            description="ACH DEBIT GEICO PREM COLL", merchant="Geico",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id)
        ).get_data(as_text=True)
        fields = _rendered_match_fields(page)

        assert any(name.endswith("-rows") for name, _ in fields), (
            "the page rendered no reviewed-row field, so this graded nothing"
        )
        payload = MultiDict(
            [("apply", "0"), ("csrf_token", "x")] + fields
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=payload,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1
        db.session.refresh(txn)
        # The bank's figure, written to the row the PAGE named.
        assert txn.settled_amount == Decimal("178.29")

    def test_the_SAME_payload_is_refused_once_the_row_MOVES(
        self, auth_client, db, seed_user,
    ):
        """The control that proves the scraped token is load-bearing.

        Without it the case above passes against a door that ignores the
        token entirely -- which is what the door did before this step.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
            description="ACH DEBIT GEICO PREM COLL", merchant="Geico",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id)
        ).get_data(as_text=True)
        payload = MultiDict(
            [("apply", "0"), ("csrf_token", "x")]
            + _rendered_match_fields(page)
        )

        # ...and the row moves behind the page's back, as another tab would.
        txn.settled_amount = Decimal("500.00")
        txn.estimated_amount = Decimal("500.00")
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=payload,
        )

        said = " ".join(response.get_data(as_text=True).split())
        assert "reviewed against different figures" in said
        assert db.session.query(StatementMatch).count() == 0
        db.session.refresh(txn)
        assert txn.settled_amount == Decimal("500.00")


    def test_the_HAND_BUILD_form_s_own_token_is_graded_too(
        self, auth_client, db, seed_user,
    ):
        """The SECOND emission site, and it was ungraded.

        The proposal card and the hand-build form each render a
        ``match-*-rows`` value through the same filter, and a control over one
        says nothing about the other.  **Measured 2026-08-23**: fabricating the
        hand form's value in the template left **418** tests green, while in a
        browser every hand-built match would refuse -- the token would claim
        `0.00` for a row worth something else -- so the one door ruling
        **R-FP** reserves to the owner, *assert a grouping the proposer would
        not guess*, would be 100% dead with CI green.  On the developer's own
        data that form offers 61 rows against 109 lines, so it is a populated
        surface rather than a corner.  Found by adversarial financial review.

        Its index is ``hand`` rather than a number, and the form posts its own
        ``apply`` as a hidden field, so the payload is scraped whole.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        # A row NO tier can reach: not equal (so no 1:1), alone (so no group),
        # and 2.8% out with no merchant (so no near miss).  The hand form is
        # the only door to it, which is what that form is FOR.
        row = a_transaction(
            seed_user, name="Ghost Payment", amount="175.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id)
        ).get_data(as_text=True)
        hand = [
            (name, value) for name, value in _rendered_match_fields(page)
            if name.startswith("match-hand-")
        ]

        assert sum(1 for name, _ in hand if name.endswith("-rows")) == 1, (
            "the hand form rendered no row token, so this graded nothing"
        )
        payload = MultiDict(
            [("apply", "hand"), ("csrf_token", "x")]
            + [(name, value) for name, value in hand
               if name.endswith("-rows")]
            + [("match-hand-line_ids", str(line.id))]
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=payload,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1, (
            "the page's own hand-form token was refused by the door"
        )
        db.session.refresh(row)
        # ...and the bank's figure was written to it (R-GD(a)).
        assert row.settled_amount == Decimal("180.00")


class TestTheAcceptPost:
    """The write door, end to end through HTTP."""

    def test_it_accepts_a_one_to_one_match_and_says_what_moved(
        self, auth_client, db, seed_user,
    ):
        """The flash names the effect, not a row count."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        txn = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[txn]),
        )

        assert response.status_code == 200
        assert b"onto the bank&#39;s day" in response.data
        db.session.expire_all()
        assert txn.settled_on == bank_day

    def test_a_NEAR_MISS_the_page_offered_can_actually_be_ACCEPTED(
        self, auth_client, db, seed_user,
    ):
        """The end-to-end proof that this tier ships no dead Accept button.

        Plan step **bank_import:X-f6d-1**.  The proposer's own leaf was
        ordered SECOND for exactly this reason: ``_reject_unbalanced`` sat on
        the single accept path, so a near miss offered before ``X-f6d-2``
        landed would have been a proposal whose Accept was guaranteed to fail.
        This walks the whole path -- the page offers it, the tick posts it, the
        row takes the bank's figure and the bank's day -- because a service
        test on either half alone cannot see a mismatch between them.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
            description="ACH DEBIT GEICO PREM COLL", merchant="Geico",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=4),
        )
        db.session.commit()

        offered = auth_client.get(_review_url(seed_user["account"].id))
        assert b"corrects the amount from -$178.32 to -$178.29" in (
            offered.data
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[txn]),
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert txn.settled_on == bank_day
        assert txn.settled_amount == Decimal("178.29")

    def test_a_GROUP_posts_the_same_field_twice_and_all_of_it_lands(
        self, auth_client, db, seed_user,
    ):
        """The MultiDict defect, pinned where it is reachable.

        ``request.form["transaction_ids"]`` returns the FIRST value, so a
        route that handed the raw form to the schema would refuse this
        submission outright -- and every service test would still pass, because
        they build real lists.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[salary, allowance]),
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day
        members = db.session.query(StatementMatchMember).count()
        assert members == 3

    def test_a_refusal_leaves_nothing_behind(
        self, auth_client, db, seed_user,
    ):
        """The unit of work is the request, so "nothing was changed" is true."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="2573.43")
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[salary, allowance]),
        )

        assert b"do not add up" in response.data
        db.session.expire_all()
        assert salary.settled_on is None
        assert allowance.settled_on is None
        assert db.session.query(StatementMatch).count() == 0

    def test_a_crafted_TICK_is_refused_rather_than_a_500(
        self, auth_client, db, seed_user,
    ):
        """The route-level control for the sort key's own domain.

        ``batch_payload`` runs BEFORE the route's ``try``, so anything it
        raises escapes the handler entirely -- and ``app/error_handlers.py``
        registers no ``ValueError`` arm.  ``str.isdigit()`` is true for 888
        characters, 128 of which make ``int()`` raise, so ``apply=%C2%B2`` was
        an unhandled 500 on the door that applies a whole reviewed pass.  Found
        by adversarial security review 2026-08-19; the schema test grades the
        regrouper, and this grades what an actual request gets back.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement)
        db.session.commit()

        for token in ("\N{SUPERSCRIPT TWO}", "9" * 4301):
            response = auth_client.post(
                _review_url(seed_user["account"].id),
                data={"apply": [token], f"match-{token}-line_ids": ["1"]},
            )

            assert response.status_code in (200, 400), (
                f"apply={token[:8]!r} answered {response.status_code}"
            )
            assert db.session.query(StatementMatch).count() == 0

    def test_a_lax_id_spelling_is_refused(self, auth_client, db, seed_user):
        """A row id inside a reviewed-row token is as strict as any other.

        ``RowId``, not ``fields.Integer``: ``'007'`` names no row (**N-141**).
        The id moved INSIDE the token at plan step ``bank_import:X-f6d-3``, so
        this is the control that it did not get laxer on the way: the token's
        two counters go through the same
        :func:`~app.utils.digit_strings.parse_row_id` the bare lists did.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={
                "apply": ["0"],
                "match-0-line_ids": [str(line.id)],
                "match-0-rows": ["transaction:007:-180.00:1"],
            },
        )

        # **The status and the sentence, not just the absence of success.**  A
        # route answering 200 with an empty body, or 500 behind an error page,
        # passed the "not in response.data" arm alone.  Named by adversarial
        # test-quality review 2026-08-19.
        assert response.status_code == 400
        assert response.headers.get("Shekel-Designed-Fragment") == "1"
        assert b"not a row this page could have shown you" in response.data
        assert db.session.query(StatementMatch).count() == 0
        assert b"onto the bank" not in response.data

    def test_MANY_bad_ids_do_not_render_the_same_sentence_many_times(
        self, auth_client, db, seed_user,
    ):
        """One message, named and counted, rather than forty identical ones.

        Marshmallow reports one entry per bad value, so a stale page with forty
        unparseable ids used to render "Not a valid id.; Not a valid id.; ..."
        with nothing saying WHICH ticked item to untick -- the opposite of the
        standard this package holds a refusal to three lines away.  Named by
        adversarial design review 2026-08-19.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        db.session.commit()

        data = {"apply": [str(index) for index in range(6)]}
        for index in range(6):
            data[f"match-{index}-line_ids"] = [str(line.id)]
            data[f"match-{index}-rows"] = ["transaction:007:-180.00:1"]

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 400
        body = response.data.decode()
        assert body.count(
            "That is not a row this page could have shown you."
        ) == 1, "the same sentence was repeated once per bad value"
        # ...and it says WHERE, so the owner can find the ticked item.
        assert "matches" in body
        assert "rows" in body

    def test_a_pass_over_the_CEILING_is_refused_on_screen(
        self, auth_client, db, seed_user,
    ):
        """The bound's whole point is that it is SAID, never silent.

        ``_MAX_BATCH_ITEMS`` is graded at the schema, but the sentence it
        raises is a schema-LEVEL error rather than a field one -- a different
        shape through ``_messages`` -- and nothing asserted that it reaches the
        owner.  Named by adversarial test-quality review 2026-08-19.
        """
        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={
                "apply": [str(index) for index in range(501)],
                **{
                    f"match-{index}-line_ids": ["1"]
                    for index in range(501)
                },
            },
        )

        assert response.status_code == 400
        assert b"at most 500 in one pass" in response.data
        assert b"apply them in two goes" in response.data
        assert db.session.query(StatementMatch).count() == 0


class TestOneRequestWorksTheWholeStatement:
    """Finding **N-306**, end to end through HTTP -- plan step X-f6a-3c-2.

    The screen offered 215 acts on the developer's own statement and took one
    per request, at 3.67 s apiece -- 13.2 minutes.  These are the properties
    of doing them
    together that no service test can see: what a browser actually posts, and
    what the screen says back.
    """

    @staticmethod
    def _three_proposals(seed_user, db):
        """Stage three lines that pair one-to-one with three rows."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        pairs = []
        for index, amount in enumerate(("180.00", "42.00", "9.99")):
            line = a_bank_line(
                seed_user, statement, amount=f"-{amount}", posted_on=day,
                sequence_in_group=index,
            )
            row = a_transaction(
                seed_user, name=f"Bill {index}", amount=amount,
                status=StatusEnum.DONE,
                settled_on=day + timedelta(days=index + 1),
            )
            pairs.append((line, row))
        db.session.commit()
        return pairs

    def test_many_acts_land_in_ONE_request(self, auth_client, db, seed_user):
        """Three matches and a recorded line, one POST, one commit."""
        pairs = self._three_proposals(seed_user, db)
        swipe = a_bank_line(
            seed_user, an_import(seed_user), amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        db.session.commit()

        data = _pass(
            *(
                _match(index=index, lines=[line], transactions=[row])
                for index, (line, row) in enumerate(pairs)
            ),
            _record_line(swipe, destination=envelope.id),
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 200
        assert b"4 applied" in response.data
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 4
        for _, row in pairs:
            assert row.settled_on == (
                seed_user["bootstrap_period"].start_date
            )
        assert len(envelope.entries) == 1

    def test_an_UNTICKED_proposal_is_not_applied(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-FP**, on the payload a browser really sends.

        Every proposal's ids are rendered as hidden inputs and submitted
        whether or not it was ticked, because a browser cannot render them
        conditionally.  The checkbox is what separates them -- and at 124
        proposals that is the difference between a reviewed pass and applying
        the app's entire opinion.
        """
        pairs = self._three_proposals(seed_user, db)

        data = _match(index=0, lines=[pairs[0][0]], transactions=[pairs[0][1]])
        # The second proposal's ids, rendered and submitted, with no tick.
        data["match-1-line_ids"] = [str(pairs[1][0].id)]
        data["match-1-transaction_ids"] = [str(pairs[1][1].id)]

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert pairs[1][1].settled_on != (
            seed_user["bootstrap_period"].start_date
        ), "an un-ticked proposal was applied"

    def test_a_refused_item_is_QUOTED_and_the_rest_still_land(
        self, auth_client, db, seed_user,
    ):
        """The ruled failure policy, on the screen the owner is looking at.

        Flash messages ride in the signed session cookie and one of these
        sentences measures 497 bytes against the 4 KB a browser stores -- nine
        of them overflow it -- so the outcome is part of the re-rendered
        surface instead.
        """
        pairs = self._three_proposals(seed_user, db)
        bad_line = a_bank_line(
            seed_user, an_import(seed_user), amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        # **A GROUP, since ruling R-GD (2026-08-22).**  A one-to-one
        # difference is a CORRECTION now -- the bank's figure names one row and
        # becomes it -- so the refusal this test is about needs the shape that
        # still refuses, which is also finding **N-239**'s own: one payroll
        # deposit against the two rows the app splits it into, with nothing
        # saying which of them is the five cents wrong.
        bad_rows = [
            a_transaction(
                seed_user, name="Salary", amount="2473.38", income=True,
            ),
            a_transaction(
                seed_user, name="Phone Allowance", amount="100.00",
                income=True,
            ),
        ]
        db.session.commit()

        data = _pass(
            _match(index=0, lines=[bad_line], transactions=bad_rows),
            _match(index=1, lines=[pairs[0][0]], transactions=[pairs[0][1]]),
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 200
        assert b"1 applied, 1 refused" in response.data
        assert b"do not add up" in response.data
        assert b"0.05" in response.data, "the refusal lost its own figures"
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert all(row.settled_on is None for row in bad_rows)

    def test_the_screen_re_renders_from_AFTER_the_pass(
        self, auth_client, db, seed_user,
    ):
        """The answer is the screen, so it must not be the screen from before.

        The scope the pass ran against holds the account's rows as they stood
        BEFORE it, which is exactly what must not be shown after -- so the
        response builds a fresh one.

        **It has to be asserted on something the stale scope CANNOT know**, and
        a first draft was not: it checked for ``Accepted matches`` and for
        ``name="apply" value="0"``, both of which the template renders
        unconditionally on every render, and both of which passed against a
        response deliberately built from the pre-pass scope.  Adversarial
        test-quality review 2026-08-19 measured that and named it a tautology.

        So the assertion is an envelope the pass CREATED: it did not exist when
        the scope was derived, so a stale render cannot offer it as a
        destination for another line, and a fresh one must.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        swipe = a_bank_line(
            seed_user, statement, amount="-31.41", posted_on=day,
            description="LOWES #00907 (Lowe's)",
        )
        # A SECOND line in the same period, left alone, whose destination
        # picker is where the created envelope must appear.
        a_bank_line(
            seed_user, statement, amount="-12.00", posted_on=day,
            sequence_in_group=1,
        )
        db.session.commit()

        page = auth_client.get(_review_url(seed_user["account"].id))
        assert b"Bright New Envelope" not in page.data

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                swipe, destination="new", name="Bright New Envelope",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        assert response.status_code == 200
        created = db.session.query(Transaction).filter(
            Transaction.name == "Bright New Envelope",
        ).one()
        assert f'<option value="{created.id}">'.encode() in response.data, (
            "the answer was rendered from the scope the pass ran AGAINST, so "
            "it cannot see the envelope the pass created"
        )

    def test_the_sweep_controls_PARTITION_the_proposals(
        self, auth_client, db, seed_user,
    ):
        """Per-class rather than one "tick all" (developer ruling 2026-08-19).

        A proposal either confirms a day the app already had, moves one it got
        wrong, marks a row as having happened, or changes an AMOUNT -- four
        different acts with four different consequences, so the riskiest is
        never swept by the same click as the safest.  The classes must SUM to
        the proposal count, which is the property a caption counting them
        relies on.

        **The fourth class is plan step ``bank_import:X-f6d-1``** (developer
        decision 2026-08-22): a near miss moves money rather than only a day,
        and classing it by its day effect would have put it on the same
        checkbox as 104 day-only corrections on the developer's own statement.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        # One that only confirms.
        a_bank_line(seed_user, statement, amount="-11.00", posted_on=day)
        a_transaction(
            seed_user, name="Confirms", amount="11.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        # One that moves a day.
        a_bank_line(
            seed_user, statement, amount="-22.00", posted_on=day,
            sequence_in_group=1,
        )
        a_transaction(
            seed_user, name="Corrects", amount="22.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        # One that marks a row as having happened.
        a_bank_line(
            seed_user, statement, amount="-33.00", posted_on=day,
            sequence_in_group=2,
        )
        a_transaction(seed_user, name="Settles", amount="33.00")
        # One that changes an AMOUNT: the bank is four cents under the row, and
        # the row NAMES the merchant the bank recorded, which is what admits a
        # near miss at all.
        a_bank_line(
            seed_user, statement, amount="-44.04", posted_on=day,
            sequence_in_group=3, merchant="Geico",
        )
        a_transaction(
            seed_user, name="Geico", amount="44.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = response.data

        assert b"tick all 1 that only confirm a day you already had" in body
        assert b"tick all 1 that move a day onto the bank" in body
        assert b"tick all 1 that mark a row as having happened" in body
        assert b"tick all 1 that change an amount onto the bank" in body
        # Each proposal is tagged with exactly one class, and the four tags
        # cover all four proposals.
        assert body.count(b'data-proposal-class=') == 4


    def test_a_refusal_raised_OUTSIDE_an_item_still_answers_with_the_screen(
        self, auth_client, db, seed_user, monkeypatch,
    ):
        """The firing control for the route's ``except ValidationError`` arm.

        Nothing inside the route's ``try`` raises one today -- every per-item
        refusal is caught and reported by ``_batch._run``.  The arm stands for
        the SURFACE: a designed refusal escaping an htmx POST is answered by
        the app-wide handler with a page htmx will not swap, so the owner
        presses Apply and sees nothing at all after a fourteen-second wait.

        A guard nothing can observe is worse than none, so this observes it.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement)
        db.session.commit()

        def _refuses(*_args, **_kwargs):
            raise ValidationError("the pass itself was unacceptable.")

        # **The ROUTE's own binding**, not the service module's: the route
        # does ``from app.services.statement_match import apply_reviewed``, so
        # patching the service leaves the route calling the real one -- which
        # is how a first version of this test passed against an arm it never
        # reached.
        monkeypatch.setattr(
            statement_matches_route, "apply_reviewed", _refuses,
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={"apply": ["0"], "match-0-line_ids": ["1"]},
        )

        assert response.status_code == 400
        assert response.headers.get("Shekel-Designed-Fragment") == "1"
        assert b"the pass itself was unacceptable." in response.data
        assert b"statement-review-body" in response.data, (
            "the answer must be the SCREEN, or htmx swaps a fragment that is "
            "not the one the request targeted"
        )

    def test_a_DATABASE_error_leaves_the_whole_pass_undone(
        self, auth_client, db, seed_user, monkeypatch,
    ):
        """The route's ``except SQLAlchemyError`` arm, which nothing reached.

        ``_run`` catches only this project's designed refusals, so a DB-level
        error inside item 2 of a pass propagates out of ``apply_reviewed`` --
        and the truth of "nothing was changed" then rests on the REQUEST's
        rollback undoing item 1, whose savepoint was already released.  That is
        a different guarantee from the per-item one and it was untested.  Named
        by adversarial test-quality review 2026-08-19.

        It also grades the refusal SURFACE: htmx leaves a 4xx non-swapping
        unless the designed-fragment marker is present, so without it the
        owner would see nothing at all after a 14-second wait.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        row = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        swipe = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            sequence_in_group=1,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        db.session.commit()

        def _database_died(*_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("gone"))

        monkeypatch.setattr(
            statement_match_batch, "create_purchase_from_line",
            _database_died,
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_pass(
                _match(lines=[line], transactions=[row]),
                _record_line(swipe, destination=envelope.id),
            ),
        )

        assert response.status_code == 400
        assert response.headers.get("Shekel-Designed-Fragment") == "1", (
            "htmx will not swap this, so the owner sees nothing"
        )
        assert b"nothing was changed" in response.data
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 0
        assert row.settled_on == day + timedelta(days=3), (
            "item 1 landed and its savepoint was released; the REQUEST's own "
            "rollback is what has to undo it"
        )
        assert envelope.entries == []


class TestTheHandBuildForm:
    """The door that makes every accept-door refusal REACHABLE.

    **Without it the refusals fire only on a crafted POST**, which an
    adversarial design review measured on 2026-08-17: every proposal balances
    by construction, so `_reject_unbalanced` -- the refusal ruling **R-FV**
    calls the instrument that can see finding **N-239** -- had no path from the
    screen at all, and the `$0.05` payroll gap landed silently in "lines with
    no proposal" under copy telling the user it was probably a card swipe.
    """

    def test_the_page_offers_both_sides_to_pick_from(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-FP**: unmatched lines on BOTH sides are shown.

        The first draft of this screen showed one side.  An app row inside the
        statement's span that the bank never showed is a payment the records
        claim happened and the bank did not make -- the more valuable half for
        a budget, and the fact `balance:X-f3a-2` consumes.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-11.11", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        a_transaction(
            seed_user, name="Ghost Payment", amount="22.22",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert b"ACH DEBIT NOTHING EXPLAINS THIS" in response.data
        assert b"Ghost Payment" in response.data
        # The card's HEADER names what it LISTS since plan step X-gc -- rows no
        # line explains -- because its badge counts all of them while the
        # caption now says the bank-failed-to-pay reading holds for only some.
        assert b"Rows you recorded that no line explains" in response.data
        assert b'name="match-hand-line_ids"' in response.data
        # The ROW side posts one token per row rather than an id list (plan
        # step bank_import:X-f6d-3), and the assertion follows the field it is
        # about: it is here to prove the hand-build form renders BOTH sides to
        # pick from, which is what makes the accept door's refusals reachable
        # from a browser at all.
        assert b'name="match-hand-rows"' in response.data
        # **Its index cannot collide with a rendered proposal's.**  Both forms
        # post to one door; only their separateness as <form> elements keeps
        # proposal 0's hidden ids out of this group today, and that is a
        # property of the document rather than of the form.
        assert b'name="apply" value="hand"' in response.data

    def test_a_CC_PAYBACK_is_still_offered_and_is_TAGGED_not_a_line(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the panel keeps the row and withdraws the claim.

        **The membership assertion is the regression guard, and it is the
        important half.**  X-gc's plan text said the panel should "stop listing
        rows the bank could never show".  Taken literally that removes the CC
        paybacks -- and they are the ONLY thing a parked card-payment line can
        be grouped against, which ruling **R-GJ** leaves as that line's one
        remaining arm.  Measured on the developer's dev database 2026-08-25:
        18 paybacks in this panel against 10 unexplained ``ACH DEBIT CAPITAL
        ONE ... PMT`` lines in the panel beside it.  So the row is listed, its
        tick token is rendered, and only the CAPTION is corrected.

        The tag is asserted INSIDE the panel rather than page-wide: this page
        renders many badges, and a body-wide search would be graded by whatever
        else happened to say the same words.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-500.00", posted_on=bank_day,
            description="ACH DEBIT CAPITAL ONE      MOBILE PMT",
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        db.session.flush()
        payback = a_transaction(
            seed_user, name="CC Payback: Groceries", amount="60.00",
            template=False, status=StatusEnum.DONE, settled_on=bank_day,
        )
        payback.credit_payback_for_id = envelope.id
        db.session.commit()

        body = auth_client.get(
            _review_url(seed_user["account"].id)
        ).get_data(as_text=True)
        rows = _never_showed_rows(body)

        # It is LISTED, and it is TICKABLE -- the group arm R-GJ leaves open.
        assert "CC Payback: Groceries" in rows
        assert f'id="row-transaction-{payback.id}"' in rows
        # ...and the alarm is withdrawn for it, in as many words.
        assert "not a line of its own" in rows
        assert "never shows it as a line by itself" in rows

    def test_the_panel_does_NOT_tag_a_row_the_bank_would_have_shown(
        self, auth_client, db, seed_user,
    ):
        """The discriminating half of the pair above.

        A tag on every row would withdraw the panel's alarm from the payments
        the bank really did fail to make, which is the half of ruling **R-FP**
        this panel exists for.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-11.11", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        a_transaction(
            seed_user, name="Ghost Payment", amount="22.22",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        rows = _never_showed_rows(
            auth_client.get(
                _review_url(seed_user["account"].id)
            ).get_data(as_text=True)
        )

        assert "Ghost Payment" in rows
        assert "not a line of its own" not in rows
        assert "never shows it as a line by itself" not in rows

    def test_the_panel_CAPTION_no_longer_claims_it_of_every_row(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the sentence that was false for 18 of 67 rows.

        It read *"A row here that you expected the bank to show is a payment
        your records claim happened and your bank did not make"* -- of every
        row, unconditionally.  What replaces it makes the alarm conditional on
        the owner's own expectation and names the other case.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-11.11",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        db.session.commit()

        panel = _never_showed_panel(
            auth_client.get(
                _review_url(seed_user["account"].id)
            ).get_data(as_text=True)
        )

        assert "A row here that you expected the bank to show is" not in panel
        # Whitespace-normalised: the sentence wraps across template lines, and
        # an assertion carrying the indentation would be graded by the editor.
        flat = " ".join(panel.split())
        assert (
            "One you expected the bank to show is a payment your records "
            "claim happened and your bank did not make." in flat
        )
        assert (
            "One marked <em>not a line of its own</em> is not: tick it with "
            "the line that carries its money." in flat
        )

    def test_a_hand_built_group_that_does_not_add_up_is_REFUSED_on_screen(
        self, auth_client, db, seed_user,
    ):
        """N-239's own shape, reaching the user who can fix it.

        This is the arm that did not exist: the bank paid `$2,573.43`, the
        app's two rows sum to `$2,573.38`, and the screen must NAME the five
        cents rather than list the line as unexplainable.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="2573.43")
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(index="hand", lines=[line],
                        transactions=[salary, allowance]),
        )

        assert b"do not add up" in response.data
        assert b"0.05" in response.data
        db.session.expire_all()
        assert salary.settled_on is None

    def test_the_panel_ships_DISABLED_and_wired_to_its_endpoint(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-f6d-4``: the consent control, before any
        selection exists for the server to price.

        **Disabled is the fail-closed state and it is what a JavaScript-off
        browser submits**, which is exactly the behaviour that shipped before
        this step: the door refuses the group and names both sums.  The
        endpoint attribute is what makes it reachable at all, so its absence
        would leave a control nobody can enable and a whole ruled act dead in a
        browser with the suite green.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).get_data(as_text=True)

        assert 'id="hand-build-form"' in page
        assert _totals_url(seed_user) in page
        assert 'hx-trigger="change"' in page
        assert _rendered_consent(page) == ("", True)

    def test_the_TRIGGER_does_not_contain_the_control_it_replaces(
        self, auth_client, db, seed_user,
    ):
        """The consent box may not be inside the element that re-renders it.

        **Driven in a real browser 2026-08-23, and it was dead there.**  The
        trigger sat on the panel as ``change from:#hand-build-form``, so
        TICKING THE CONSENT BOX fired a re-render and the swap replaced it with
        a fresh unchecked one.  The owner could never keep it ticked and Apply
        submitted no consent at all -- the whole ruled act unreachable, with
        every server test green.

        Asserted structurally rather than by driving a browser, because that
        is the property: the element carrying ``hx-trigger`` must not contain
        the control the swap replaces.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).get_data(as_text=True)

        trigger = _element_carrying(page, "hx-trigger")
        assert trigger is not None, "nothing re-prices the panel at all"
        assert "match-hand-line_ids" in trigger, (
            "the trigger does not contain the tick lists, so nothing fires"
        )
        assert "match-hand-residual" not in trigger, (
            "the consent box is inside the element that triggers its own "
            "replacement -- ticking it would swap it away"
        )
        # ...and the panel it targets is not itself a trigger.
        assert 'id="hand-totals"' in page
        assert 'hx-trigger' not in _element_carrying(page, 'id="hand-totals"')

    def test_ONE_side_ticked_shows_that_SIDES_total(
        self, auth_client, db, seed_user,
    ):
        """A ticked line is not nothing, and the panel may not say it is.

        A first version answered the empty panel until BOTH sides were ticked,
        so a `$2,573.43` line the owner had just picked read `$0.00`.  Caught
        by driving the real screen; a match still needs both halves, so no
        consent is offered.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        db.session.commit()

        response = auth_client.post(
            _totals_url(seed_user), data=_match(index="hand", lines=[line]),
        )

        text = _visible_text(response.get_data(as_text=True))
        assert "Your bank shows $2,573.43" in text
        assert "the rows you ticked come to $0.00" in text
        assert _rendered_consent(
            response.get_data(as_text=True),
        ) == ("", True)

    def test_the_SERVER_prices_what_is_ticked_and_offers_the_figure(
        self, auth_client, db, seed_user,
    ):
        """The panel is the accept door asked what it would do.

        No arithmetic happens in the browser: the body posted here is the body
        Apply would send, and everything on the answer -- both sums, the
        difference, the label and the box's value -- is the server's.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        response = auth_client.post(
            _totals_url(seed_user),
            data=_match(index="hand", lines=[line],
                        transactions=[salary, allowance]),
        )

        assert response.status_code == 200
        text = _visible_text(response.get_data(as_text=True))
        # 2,573.43 - (2,473.38 + 100.00) = 0.05
        assert "Your bank shows $2,573.43" in text
        assert "the rows you ticked come to $2,573.38" in text
        assert "difference $0.05" in text
        assert "Record the $0.05 difference as a row with no category" in text
        assert _rendered_consent(
            response.get_data(as_text=True),
        ) == ("0.05", False)

    def test_the_PANELS_OWN_figure_is_what_the_door_accepts(
        self, auth_client, db, seed_user,
    ):
        """The loop closed: price it, scrape what it offered, post that, land.

        This is the only control that grades the panel's figure against the
        door's own arithmetic.  Every other case states the figure itself, so
        all of them would pass a panel that priced the wrong thing -- and in a
        browser every hand-built difference would then be refused as "reviewed
        against a difference of ...".
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()
        ticked = _match(index="hand", lines=[line],
                        transactions=[salary, allowance])

        panel = auth_client.post(
            _totals_url(seed_user), data=ticked,
        ).get_data(as_text=True)
        offered, disabled = _rendered_consent(panel)
        assert disabled is False, (
            "the panel offered no consent, so this graded nothing"
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={**ticked, "match-hand-residual": [offered]},
        )

        assert response.status_code == 200
        assert b"do not add up" not in response.data
        assert b"recorded the +0.05 difference" in response.data
        db.session.expire_all()
        assert salary.settled_on == bank_day
        minted = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == seed_user["account"].id,
                Transaction.category_id.is_(None),
            )
            .all()
        )
        assert len(minted) == 1
        assert minted[0].estimated_amount == Decimal("0.05")

    def test_the_panel_names_a_refusal_BEFORE_the_press(
        self, auth_client, db, seed_user,
    ):
        """A selection the door will not record says so beside the boxes.

        An envelope is worth whatever its purchases are, so a difference on one
        is a purchase that is missing -- and learning that from the panel is
        the difference between fixing it and pressing a button that refuses.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-280.06", posted_on=bank_day,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="180.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="180.00")
        power = a_transaction(seed_user, name="Power", amount="100.00")
        db.session.commit()

        response = auth_client.post(
            _totals_url(seed_user),
            data=_match(index="hand", lines=[line],
                        transactions=[envelope, power]),
        )

        text = _visible_text(response.get_data(as_text=True))
        assert "no figure of its own to correct" in text
        assert _rendered_consent(
            response.get_data(as_text=True),
        ) == ("", True)

    def test_ONE_row_is_offered_the_CORRECTION_rather_than_a_new_row(
        self, auth_client, db, seed_user,
    ):
        """The two remedies are different acts, so the panel names which."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
        )
        second = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=bank_day + timedelta(days=1),
        )
        txn = a_transaction(seed_user, amount="180.00")
        db.session.commit()

        response = auth_client.post(
            _totals_url(seed_user),
            data=_match(index="hand", lines=[first, second],
                        transactions=[txn]),
        )

        text = _visible_text(response.get_data(as_text=True))
        assert "Write your bank's -$180.06 to the row you ticked" in text
        assert "in place of the -$180.00 your records hold" in text
        assert _rendered_consent(
            response.get_data(as_text=True),
        ) == ("-0.06", False)

    def test_the_receipt_names_the_difference_it_recorded(
        self, auth_client, db, seed_user,
    ):
        """The panel's own line, beside the per-item sentence."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(index="hand", lines=[line],
                        transactions=[salary, allowance], residual="0.05"),
        )

        # Tags stripped and whitespace collapsed, because the sentence wraps
        # in the template and a byte match would grade the indentation.
        text = _visible_text(response.get_data(as_text=True))
        assert (
            "1 difference(s) totalling $0.05 recorded as rows with no "
            "category."
        ) in text
        assert "Nothing moved." not in text

    def test_a_hand_built_group_that_adds_up_is_accepted(
        self, auth_client, db, seed_user,
    ):
        """The control: the same door, on figures that agree."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()

        auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[salary, allowance]),
        )

        db.session.expire_all()
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day


class TestTheReleasePost:
    """The undo."""

    def test_it_releases_and_leaves_the_day(self, auth_client, db, seed_user):
        """What comes back is the QUESTION, not the date."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        txn = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=_match(lines=[line], transactions=[txn]),
        )
        match_id = db.session.query(StatementMatch.id).scalar()

        response = auth_client.post(
            f"{_review_url(seed_user['account'].id)}/release",
            data={"match_id": match_id},
            follow_redirects=True,
        )

        assert b"Match undone" in response.data
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 0
        assert txn.settled_on == bank_day
        # A match between rows that already existed removes nothing, so the
        # receipt says nothing about removals -- the control for the case
        # below, which does.
        assert b"row(s) that match had created" not in response.data

    def test_it_removes_what_the_act_CREATED_and_says_so(
        self, auth_client, db, seed_user,
    ):
        """Plan step **bank_import:X-f6f**, ruling **R-GG**.

        The create arm's inverse, driven through the two real POSTs: record a
        `-$57.96` swipe as a purchase in a new envelope, then undo it.  Both
        rows go, and the flash NAMES them and the money -- a destructive act
        whose receipt says only "done" leaves the owner unable to tell a no-op
        from a much larger removal than they meant.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 1, (
            "the recording must really have happened, or the undo below "
            "proves nothing"
        )
        match_id = db.session.query(StatementMatch.id).scalar()

        response = auth_client.post(
            f"{_review_url(seed_user['account'].id)}/release",
            data={"match_id": match_id},
            follow_redirects=True,
        )

        assert b"Match undone" in response.data
        assert b"removed the 2 row(s) that match had created" in response.data
        assert b"-57.96" in response.data
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(Transaction).filter(
            Transaction.name == "Walmart",
        ).count() == 0

    def test_the_page_NAMES_what_the_undo_would_remove(
        self, auth_client, db, seed_user,
    ):
        """The Undo control carries the confirmation and the figure.

        ``data-confirm`` is this project's destructive-action pattern, and it
        is attached only where the undo would destroy a record: a dialog on
        every Undo trains the owner to click through the one that matters.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        page = auth_client.get(_review_url(seed_user["account"].id)).data

        assert b"data-confirm=" in page
        assert b"it REMOVES the 2 row(s) this match created" in page
        assert b"Undo removes 2 row(s) this" in page
        # The macro's own spelling: the sign goes BEFORE the dollar symbol.
        assert b"-$57.96" in page

    def test_the_page_says_REFUSED_where_the_undo_would_be(
        self, auth_client, db, seed_user,
    ):
        """A panel promising a removal the button refuses is the defect.

        The owner has edited the purchase the act created, so the undo stops.
        The row must say THAT rather than go on listing rows it will not
        remove -- the screen and the door read one derivation
        (``planned_removals``), and this is the arm that proves the TEMPLATE
        reads it too.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )
        db.session.expire_all()
        entry = db.session.query(TransactionEntry).one()
        entry_service.update_entry(
            entry.id, seed_user["user"].id, description="Walmart -- hose",
        )
        db.session.commit()

        page = auth_client.get(_review_url(seed_user["account"].id)).data

        assert b"Undo is refused:" in page
        assert b"you have edited that row since" in page
        assert b"Undo removes" not in page
        assert b"data-confirm=" not in page


class TestItRefusesAnotherUsersAccount:
    """Firing controls against an IDOR on a door that MOVES MONEY."""

    @pytest.fixture()
    def other_users_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="matchstranger@shekel.local",
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
        # opens a transaction of its OWN, so a row this fixture only flushed is
        # one the request cannot see.  The 404 these tests assert must be the
        # OWNERSHIP gate refusing a real account of someone else's rather than
        # a missing row.
        db.session.commit()
        return account.id

    def test_the_review_page_answers_404(
        self, auth_client, other_users_account,
    ):
        """A 403 would confirm the account exists."""
        assert auth_client.get(
            _review_url(other_users_account),
        ).status_code == 404

    def test_the_accept_door_answers_404(
        self, auth_client, db, other_users_account,
    ):
        """The write door is decorated independently of the read."""
        response = auth_client.post(
            _review_url(other_users_account),
            data={
                "apply": ["0"],
                "match-0-line_ids": ["1"],
                "match-0-transaction_ids": ["1"],
            },
        )

        assert response.status_code == 404
        assert db.session.query(StatementMatch).count() == 0

    def test_the_release_door_answers_404(
        self, auth_client, other_users_account,
    ):
        """The third decorator, asked its own question."""
        response = auth_client.post(
            f"{_review_url(other_users_account)}/release",
            data={"match_id": 1},
        )

        assert response.status_code == 404

    def test_the_CREATION_arm_answers_404_too(
        self, auth_client, db, other_users_account,
    ):
        """The same door, asked with the payload that MINTS budget rows.

        Plan step **bank_import:X-f6a-3b** put creating a purchase behind its
        own route, and **X-f6a-3c-2** folded it into this one -- so the
        decorator is now shared.  The arms are still tested separately, because
        a shared decorator proves the request was refused and says nothing
        about which arm would have run: this one can mint a transaction and a
        purchase, so an un-refused request would let one user grow another's
        budget from their own statement.
        """
        response = auth_client.post(
            _review_url(other_users_account),
            data={
                "destination-1": "new",
                "envelope_name-1": "Anything",
                "category_id-1": "1",
            },
        )

        assert response.status_code == 404
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(Transaction).filter(
            Transaction.name == "Anything",
        ).count() == 0


class TestTheCreateArm:
    """Recording a bank line as a purchase -- plan step **bank_import:X-f6a-3b**,
    through the one door plan step **X-f6a-3c-2** left standing.

    The route's own subjects, none of which the service test can see: what the
    destination select MEANS, the payload a browser really sends, and what the
    screen says was recorded.
    """

    @staticmethod
    def _an_open_envelope(seed_user):
        """Return a Projected envelope a purchase may join."""
        return a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )

    def test_the_page_offers_the_line_and_a_destination(
        self, auth_client, db, seed_user,
    ):
        """The card is what makes the arm reachable at all.

        Without it the create arm fires only on a crafted POST, and the 74
        card swipes the step exists for are never put in front of the person
        who can record them -- which is the same defect the hand-build form was
        added to fix one leaf earlier.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            merchant="Walmart",
        )
        self._an_open_envelope(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"a purchase you never recorded" in response.data
        assert b"-- a new envelope --" in response.data
        # The name box is prefilled with the MERCHANT the bank NAMED -- the
        # recorded column since plan step X-f6a-3d -- not the whole line.
        assert b'value="Walmart"' in response.data

    def test_the_select_DEFAULTS_to_leaving_the_line_alone(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-FP**, as a property of the CONTROL rather than of a click.

        This is the half of "nothing is applied that you did not accept" that a
        batch makes load-bearing.  The select used to default to the first
        envelope in the line's pay period -- which on the developer's own data
        has already CLOSED at a fixed figure on 78 of 91 lines -- and the
        category select to whichever category sorts first ("Auto: Property
        Tax").  One press per line hid that; one press for forty would have
        filed forty purchases nobody chose.

        So the rendered default is asserted, and then SUBMITTED: a pass
        carrying every control the page renders, with nothing touched, must
        record nothing at all.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, amount="-57.96",
                           posted_on=day)
        envelope = self._an_open_envelope(seed_user)
        db.session.commit()

        page = auth_client.get(_review_url(seed_user["account"].id))
        assert b"-- leave this line alone --" in page.data
        assert b"-- choose a category --" in page.data

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(line, destination=""),
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert envelope.entries == []
        assert db.session.query(StatementMatch).count() == 0

    def test_it_records_into_an_existing_envelope_and_says_where(
        self, auth_client, db, seed_user,
    ):
        """The RENDERED payload, not a hand-picked subset of it.

        **A hand-picked payload shipped a dead arm.**  Every control in the
        form is submitted on every POST: the name box is always rendered and
        always prefilled from the merchant.  Keying the arm on
        ``envelope_name is not None`` therefore named BOTH destinations on
        every real submission and the door refused all of them -- 66 of the
        developer's 91 creatable lines, on the first click, with no sequence of
        interactions that reached the arm at all.  Three independent
        adversarial reviews found it on 2026-08-19.

        So the payload below is exactly what the template emits, and the arm is
        the SELECT.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, amount="-57.96",
                           posted_on=day)
        envelope = self._an_open_envelope(seed_user)
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(line, destination=envelope.id),
        )

        assert response.status_code == 200
        assert b"as a purchase in Groceries" in response.data
        db.session.expire_all()
        assert [entry.amount for entry in envelope.entries] == [
            Decimal("57.96"),
        ]
        # The chosen envelope is NOT closed by filing a purchase into it --
        # only a NEWLY CREATED one is.  Without this, `if created:` could
        # become `if True:` and silently close an open budget at its
        # purchases-to-date.
        assert envelope.status.is_settled is False
        assert envelope.settled_on is None
        # ...and no envelope was invented alongside it.
        assert db.session.query(Transaction).filter(
            Transaction.name == "Walmart",
        ).count() == 0

    def test_the_NEW_ENVELOPE_option_is_its_own_named_arm(
        self, auth_client, db, seed_user,
    ):
        """``"new"`` names the arm; an ABSENCE used to, and could be misread.

        The destination was a nullable ``transaction_id`` until plan step
        X-f6a-3c-2, so "make a new envelope" was spelled as a missing id -- and
        the always-rendered name box then read as a destination of its own.  A
        control that says which of three things the owner meant cannot be
        misread; an absence can.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, amount="-31.41",
                           posted_on=day, description="LOWES #00907 (Lowe's)")
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                line, destination="new", name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        assert response.status_code == 200
        assert b"as a purchase in a new envelope, Lowe" in response.data
        db.session.expire_all()
        created = db.session.query(Transaction).filter(
            Transaction.name == "Lowe's",
        ).one()
        assert created.estimated_amount == Decimal("0.00")

    def test_a_new_envelope_missing_its_CATEGORY_costs_only_ITSELF(
        self, auth_client, db, seed_user,
    ):
        """The ruled failure policy, on the slip the FORM ITSELF produces.

        A budget line with no category is invisible to every spending report,
        so it must be refused -- and the category select has no default on
        purpose, which makes "picked a new envelope and stopped" the ordinary
        path rather than an exotic one.

        **The refusal used to be the schema's, and a nested schema error
        refuses the WHOLE payload.**  On the developer's own statement that is
        124 proposals and 90 good creations discarded by one untouched select,
        which is exactly what the developer's ruling of 2026-08-19 says must
        not happen.  Found by adversarial financial review 2026-08-19.

        So this asserts BOTH halves: the incomplete line is refused with its
        own sentence, and the ticked proposal beside it still lands.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        good_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        good_row = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        line = a_bank_line(
            seed_user, statement, amount="-31.41", posted_on=day,
            sequence_in_group=1,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_pass(
                _match(lines=[good_line], transactions=[good_row]),
                _record_line(line, destination="new", name="Lowe's"),
            ),
        )

        assert response.status_code == 200, (
            "one incomplete line refused the whole pass"
        )
        assert b"needs both a name and a category" in response.data
        assert b"1 applied, 1 refused" in response.data
        db.session.expire_all()
        assert db.session.query(Transaction).filter(
            Transaction.name == "Lowe's",
        ).count() == 0
        assert good_row.settled_on == day, (
            "the good proposal was discarded with the bad line"
        )

    def test_a_refused_creation_leaves_nothing_behind(
        self, auth_client, db, seed_user,
    ):
        """The new-envelope arm STAGES a budget line before the purchase.

        So a refusal arriving after that has to take the row with it, which is
        what makes the sentence every refusal here ends with true.  Inside a
        batch that is the SAVEPOINT's job rather than the request's, and the
        two are not the same guarantee: the request rolls everything back, and
        the savepoint has to roll back this item while the others stand.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(
                line, destination="new", name="Payroll",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        assert response.status_code == 200
        assert b"money LEAVING" in response.data
        db.session.expire_all()
        assert db.session.query(Transaction).filter(
            Transaction.name == "Payroll",
        ).count() == 0
        assert db.session.query(StatementMatch).count() == 0


def _an_envelope(seed_user, name="Groceries"):
    """Return a Projected envelope a purchase may join.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    return a_transaction(
        seed_user, name=name, amount="500.00", is_envelope=True,
    )


def _a_line(seed_user, merchant="Amazon", amount="-57.96", sequence=0,
            source_category=None):
    """Record one unexplained outflow from *merchant*.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the bank names the merchant, which is the policy key.
        amount: Signed, negative OUT of the account.
        sequence: The ordinal completing the line's identity.
        source_category: The BANK's own category string, verbatim, or ``None``
            for a source stating none.  Ruling **R-GJ** reads it for one narrow
            purpose: a merchant a source files as a payment to a credit card
            has no create arm until the owner answers for it.

    Returns:
        The staged
        :class:`~app.models.statement_import.BankStatementLine`.
    """
    statement = an_import(seed_user)
    return a_bank_line(
        seed_user, statement, amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date,
        description=f"POINT OF SALE DEBIT L340 THING ({merchant})",
        merchant=merchant, sequence_in_group=sequence,
        source_category=source_category,
    )



class TestTheMerchantPolicySection:
    """Where your merchants go: the control, its door, and what it may not do.

    Plan step **bank_import:X-f6a-3d**.  The route's own subjects: ownership,
    the form payload, and -- the one that matters most -- that a stated policy
    reaches the SCREEN as a suggestion and never as a selected control.
    """

    def test_the_page_offers_a_policy_row_per_merchant(
        self, auth_client, db, seed_user,
    ):
        """The card is what makes the whole leaf reachable.

        Without it the answers exist only in the database and the 91 leftover
        lines still ask 91 questions -- which is the same defect the hand-build
        form was added to fix two leaves earlier.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"Where your merchants go" in response.data
        assert b'name="policy_merchant-0"' in response.data
        assert b"-- never a purchase --" in response.data
        # ...and the option list is the account's recurring DEFINITIONS, graded
        # by the id it carries rather than by the word "Groceries" -- which is
        # also a seeded category and this envelope's own row name, so asserting
        # on it passed with the option list empty.  Found by adversarial
        # test-quality review 2026-08-19.
        template_id = db.session.query(Transaction).filter(
            Transaction.name == "Groceries",
        ).one().template_id
        assert f't:{template_id}'.encode() in response.data

    def test_an_unanswered_merchant_defaults_to_I_HAVE_NOT_SAID(
        self, auth_client, db, seed_user,
    ):
        """Nothing is remembered until the owner says it.

        The negative control for the whole section: a rendered row that
        arrived already pointing somewhere would be the app answering on the
        owner's behalf.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = response.data.decode()

        marker = body.index('name="policy-0"')
        control = body[marker:body.index("</select>", marker)]
        assert '<option value="unset" selected>' in control
        # ...and it is the ONLY option selected, so nothing else is offered as
        # an answer the owner did not give.
        assert "selected" not in control.replace(
            '<option value="unset" selected>', "",
        )

    def test_it_records_an_answer_and_says_what_changed(
        self, auth_client, db, seed_user,
    ):
        """The POST answers with the screen, carrying its own receipt."""
        envelope = _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        assert response.status_code == 200
        assert b"Amazon goes in Groceries." in response.data
        assert b"changed no money" in response.data

    def test_a_stated_policy_SUGGESTS_and_does_not_select(
        self, auth_client, db, seed_user,
    ):
        """THE MONEY TEST, and the one the whole design turns on.

        Ruling **R-FZ** of 2026-08-19: the destination select IS the tick, and
        its default is "leave this line alone" -- because a default nobody
        chose was landing on envelopes closed at a fixed figure on 78 of 91
        lines.  A remembered destination arriving already selected would put
        that default straight back, on a control whose submission cannot be
        undone from this screen: releasing a match keeps the purchase and any
        envelope it created.

        So the policy is rendered BESIDE the control and the control still
        opens on the do-nothing arm.  Delete that separation and this fails.
        """
        envelope = _an_envelope(seed_user)
        line = _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = response.data.decode()

        # The suggestion is on the page...
        assert "You file Amazon in" in body
        # ...and so is the value the sweep would set...
        assert f'data-placement="{envelope.id}"' in body
        # ...and the control itself is still on "leave this line alone".
        marker = body.index(f'name="destination-{line.id}"')
        control = body[marker:body.index("</select>", marker)]
        assert '<option value="" selected>' in control
        assert f'<option value="{envelope.id}">' in control

    def test_pressing_APPLY_with_a_policy_and_no_tick_records_nothing(
        self, auth_client, db, seed_user,
    ):
        """The suggestion is inert until the owner acts on it.

        The other half of the test above, and the one that grades the WIRE
        rather than the markup: a browser submitting the rendered defaults of
        every control -- which is what pressing Apply without touching anything
        does -- must write no purchase.
        """
        envelope = _an_envelope(seed_user)
        line = _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        # THE RENDERED form, submitted untouched -- which is what pressing
        # Apply without moving a control actually sends.  A hand-written
        # ``destination=""`` grades this test's idea of the default rather than
        # the template's, which is the flaw this arc has paid for twice.
        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _apply_form_controls(page)
        response = auth_client.post(
            _review_url(seed_user["account"].id), data=submitted,
        )

        # The form really did carry this line's destination control.
        assert f"destination-{line.id}" in submitted
        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.get(Transaction, envelope.id).entries == []

    def test_the_sweeps_value_is_what_the_door_then_accepts(
        self, auth_client, db, seed_user,
    ):
        """The rendered placement and the write door agree, end to end.

        The sweep sets each line's select to ``data-placement``, which is the
        service's own ``Placement.select_value``.  Submitting exactly that --
        which is what a browser sends after one press -- must record the
        purchase, or the control promises something the door refuses.
        """
        envelope = _an_envelope(seed_user)
        line = _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        body = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        marker = body.index(f'data-placement="')
        swept = body[marker + len('data-placement="'):body.index('"', marker + len('data-placement="'))]

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data=_record_line(line, destination=swept),
        )

        assert response.status_code == 200
        assert b"Recorded $57.96" in response.data
        assert len(db.session.get(Transaction, envelope.id).entries) == 1

    def test_a_NEVER_answer_TAKES_THE_CONTROL_AWAY_from_every_line(
        self, auth_client, db, seed_user,
    ):
        """The answer that is worth the most money on the developer's own data.

        Capital One Credit Card is 9 of their 91 unexplained outflows and
        `-$7,412.94` of the `-$11,336.36` in that list, all of which the app
        already holds as CC Payback rows.  Saying it once has to stop the
        screen asking again -- and, since ruling **R-GJ** (plan step
        ``bank_import:X-ga``), has to leave the page with no control that could
        record them.  Until then this answer only withheld a sweep VALUE: the
        line's own destination select was still rendered, still offered every
        envelope in the period and "-- a new envelope --", and one YTD pass put
        all nine through it.

        **The controls are read the way a BROWSER would submit them**
        (:func:`_apply_form_controls`), not grepped out of the markup, because
        the defect was never a missing sentence -- the sentence was there.  It
        was a control underneath it.
        """
        _an_envelope(seed_user)
        _a_line(seed_user, merchant="Capital One Credit Card")
        db.session.commit()

        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )
        response = auth_client.get(_review_url(seed_user["account"].id))
        # WHITESPACE-NORMALISED, because a rendered sentence wraps: asserting
        # on the contiguous string grades the template's line breaks rather
        # than what the page says.
        body = " ".join(response.data.decode().split())

        assert "Capital One Credit Card is never a purchase" in body
        assert "Payments waiting for their home" in body
        # THE CONTROLS THEMSELVES, gone: no destination select, no envelope
        # name box and no category select for this line, so a browser has
        # nothing to submit for it.  **The APPLY form's controls, not the
        # page's text**: "-- a new envelope --" still appears on the page as an
        # ANSWER the policy section offers, which is a different control on a
        # different form posting to a door that moves no money.
        assert _apply_form_controls(response.data.decode()) == {
            "apply": "hand",
        }
        # It places nothing either, so the sweep has nothing to offer.
        assert "data-placement=" not in body
        assert "data-tick-placed" not in body

    def test_a_line_dated_MADE_after_it_POSTED_says_so_on_the_screen(
        self, auth_client, db, seed_user,
    ):
        """Finding N-325's user-facing half, which nothing graded.

        The service integer was asserted; the sentence the owner reads was not,
        so the whole ``{% if %}`` could be deleted with the suite green -- and
        a bound that is counted and never SAID reads as a clean sweep, which is
        the failure ``ReviewBounds`` exists against.
        """
        _an_envelope(seed_user)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-12.00", posted_on=day,
            transaction_on=day + timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "What this page did not look at" in body
        assert "MADE after the day it took the money" in body

    def test_the_receipt_names_what_was_UNCHANGED_as_well_as_what_moved(
        self, auth_client, db, seed_user,
    ):
        """The denominator, which the docstrings argue for and nothing graded.

        The section submits every merchant it renders, so most of a real pass
        is no-ops; "1 recorded" with nothing beside it reads as though the
        other twenty failed.
        """
        envelope = _an_envelope(seed_user)
        _a_line(seed_user, merchant="Alpha")
        _a_line(seed_user, merchant="Beta", sequence=1)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Alpha").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_pass(
                _policy(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{envelope.template_id}"),
                _policy(1, a_merchant(seed_user, "Beta").id, answer="never"),
            ),
        )
        body = " ".join(response.data.decode().split())

        assert "Beta is never a purchase." in body
        assert "1 other merchant(s) were already answered for" in body

    def test_a_CREATE_placement_says_what_it_would_create(
        self, auth_client, db, seed_user,
    ):
        """The third placement sentence; the other three were graded."""
        category = seed_user["categories"]["Groceries"]
        _an_envelope(seed_user)
        _a_line(seed_user, merchant="Lowe's")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
                         category_id=category.id),
        )

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "You give Lowe&#39;s a new envelope called" in body
        assert "Yard &amp; Garden" in body
        # ...and it says what the arm actually DOES, which is one envelope per
        # PAY PERIOD since the developer's ruling of 2026-08-20 closed finding
        # N-327.  It used to be one per LINE, and this assertion said so; a
        # sentence describing a behaviour the door no longer has is worse than
        # no sentence, because the owner reads it before pressing.
        assert "One is created per pay period" in body
        assert "One is created per line" not in body

    def test_a_FOREIGN_template_is_refused_on_screen(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """An IDOR on the control that decides where later money is filed.

        ``fk_merchant_destinations_template_account`` makes it unwritable; this
        is what makes the refusal a sentence rather than a 500 with a logged
        traceback.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        foreign = a_transaction(
            seed_second_user, name="Theirs", is_envelope=True,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{foreign.template_id}",
            ),
        )

        assert response.status_code == 200
        assert b"no recurring envelope on this account" in response.data
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )
        assert db.session.query(MerchantDestination).count() == 0

    def test_a_merchant_this_ACCOUNT_never_saw_is_refused_on_screen(
        self, auth_client, db, seed_user,
    ):
        """The scope check, reaching the owner as a designed 400.

        The section renders exactly the merchants this account's recorded lines
        name, so a statement about another is a crafted request -- and the
        table would otherwise take a policy for any string at all.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()

        # An id no merchant on this account carries.  It used to be a STRING
        # this account's lines had never named; since plan step
        # ``bank_import:X-gd-1`` the hidden input carries a row, so what a
        # crafted body can name is another account's merchant or a number that
        # is nobody's -- and the sentence is what both get instead of an
        # ``IntegrityError`` reaching the owner as "Something went wrong".
        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, 9_999_999, answer="never"),
        )

        assert response.status_code == 400
        assert b"not ones your bank has shown" in response.data

    def test_ANOTHER_ACCOUNTS_real_merchant_is_INDISTINGUISHABLE_from_no_one(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """THE anti-oracle property, on the wire, which nothing asserted.

        This project's security rule is 404 for both *not found* and *not
        yours*; on a form FIELD the same rule is that a real id belonging to
        somebody else and an id belonging to nobody must be answered
        identically.  Split the refusal to be more helpful -- *that merchant is
        on another account* -- and the screen becomes an existence oracle over
        a global surrogate key.

        It holds today because ``_refuse_unknown_merchants`` builds its set
        from a dict MISS against this account's own merchants and reports only
        a count; an adversarial security review of 2026-08-25 found the
        property itself graded nowhere, and the service-tier case beside it
        posts a foreign id past the route, the schema and the door.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        theirs = a_merchant(
            seed_second_user, "Theirs Alone",
            account=seed_second_user["account"],
        )
        db.session.commit()

        real = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, theirs.id, answer="never"),
        )
        nobodys = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, 9_999_999, answer="never"),
        )

        assert real.status_code == nobodys.status_code == 400
        assert real.data == nobodys.data
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )
        assert db.session.query(MerchantDestination).count() == 0

    def test_a_CRAFTED_answer_is_refused_rather_than_a_500(
        self, auth_client, db, seed_user,
    ):
        """The field reads one of four things and nothing else.

        ``t:007`` names no template here for the same reason ``007`` names no
        envelope one card down: a second, laxer reading of a row id on a screen
        that decides where money is filed is what plan step X-ae removed.
        """
        _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer="t:007",
            ),
        )

        assert response.status_code == 400
        assert b"not somewhere a merchant" in response.data

    def test_ANOTHER_USERS_account_is_a_404(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The security response rule, on a door that decides where money goes.

        A route answering here for another owner's account would let one user
        write standing instructions into another's import.
        """
        response = auth_client.post(
            _merchants_url(seed_second_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer="never",
            ),
        )

        assert response.status_code == 404

    def test_a_NEW_ENVELOPE_policy_prefills_the_name_and_the_CATEGORY(
        self, auth_client, db, seed_user,
    ):
        """Decided on the SERVER, so no-script and the sweep agree.

        A first version set these two from JavaScript when the sweep ran, which
        left the no-script path filing the policy's line under the MERCHANT's
        name instead of the one the owner stated -- one rule about what a
        created envelope is called, in two places, disagreeing on the path that
        has no scripting at all.
        """
        category = seed_user["categories"]["Groceries"]
        _an_envelope(seed_user)
        _a_line(seed_user, merchant="Lowe's")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
                category_id=category.id,
            ),
        )

        body = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        # Pinned to the CREATE FORM's own controls.  Both values are emitted
        # by the policy section too, so a bare substring passed with either
        # site broken; adversarial test-quality review 2026-08-19 measured
        # that each was individually ungraded.
        line_id = db.session.query(BankStatementLine).one().id
        marker = body.index(f'name="envelope_name-{line_id}"')
        assert 'value="Yard &amp; Garden"' in body[marker:marker + 200]
        marker = body.index(f'name="category_id-{line_id}"')
        control = body[marker:body.index("</select>", marker)]
        assert f'<option value="{category.id}" selected>' in control
        assert control.count("selected") == 1

    def test_a_stated_policy_can_be_WITHDRAWN_from_the_screen(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL for naming the do-nothing arm.

        The control's default value used to be the empty string, which
        ``BaseSchema``'s ``@pre_load`` normalizer drops -- so ``required=True``
        read a withdrawal as a missing answer and refused it, and a policy
        could be restated but never taken back.  That matters beyond tidiness:
        a policy is a statement about today's budget, and when the credit-card
        arc gives Capital One its own account the Checking-side answer stops
        being right.
        """
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )

        envelope = _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        assert db.session.query(MerchantDestination).count() == 1

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer="unset",
            ),
        )

        assert response.status_code == 200
        assert db.session.query(MerchantDestination).count() == 0

    def test_submitting_the_RENDERED_form_UNCHANGED_records_nothing(
        self, auth_client, db, seed_user,
    ):
        """The payload a BROWSER sends, parsed off the page rather than typed.

        **This arc has shipped a dead arm twice for want of exactly this**: a
        hand-picked payload at X-f6a-3b left the existing-envelope destination
        unreachable, and the first draft of THIS control spelled "I have not
        said" as the empty string -- which ``BaseSchema``'s pre-load normalizer
        drops, so a withdrawal arrived as a missing required field and was
        refused.  Every hand-written payload in this class shares the flaw that
        it is written by the same person as the template.

        So this one reads the rendered page, submits every control the policy
        form actually contains at the value it actually carries, and asserts
        the round trip is a NO-OP: nothing recorded, nothing refused, and every
        answered merchant counted as unchanged.
        """
        envelope = _an_envelope(seed_user)
        _a_line(seed_user)
        _a_line(seed_user, merchant="Walmart", sequence=1)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _policy_form_controls(page)
        response = auth_client.post(
            _merchants_url(seed_user["account"].id), data=submitted,
        )

        # The form really did carry both merchants and all four fields each.
        assert sorted(
            key for key in submitted if key.startswith("policy_merchant-")
        ) == ["policy_merchant-0", "policy_merchant-1"]
        assert response.status_code == 200
        assert b"Nothing changed" in response.data
        assert b"were not recorded" not in response.data

    def test_a_merchant_carrying_MARKUP_is_escaped_in_every_attribute(
        self, auth_client, db, seed_user,
    ):
        """The merchant is arbitrary text from a BANK, rendered into attributes.

        It reaches a hidden input's ``value``, an ``aria-label`` and two
        sentences.  Nothing in this app controls what a bank writes in a
        description, and the adapter records the token verbatim on purpose --
        deciding that two spellings are one merchant is the guess this table
        exists not to make -- so the escaping is what stands between that and
        an attribute break-out.  Autoescaping is Flask's default; this is the
        control that says so for THIS surface rather than trusting it.
        """
        _an_envelope(seed_user)
        statement = an_import(seed_user)
        hostile = '" onmouseover="alert(1)'
        a_bank_line(
            seed_user, statement, amount="-12.00",
            posted_on=seed_user["bootstrap_period"].start_date,
            description=f"POINT OF SALE DEBIT ({hostile})", merchant=hostile,
        )
        db.session.commit()

        body = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert 'onmouseover="alert(1)"' not in body
        assert "&#34; onmouseover=&#34;alert(1)" in body

    def test_a_policy_that_cannot_REACH_this_line_says_why_and_ticks_nothing(
        self, auth_client, db, seed_user,
    ):
        """The template's last branch, which an `{% else %}` hides when wrong.

        Measured on the developer's own clone, this is the ordinary state
        rather than an edge: their `Gas` template is offerable in 9 of the 11
        pay periods their leftover lines fall in and `Groceries` in 10, so 6 of
        91 lines land here.  The screen has to SAY it -- reported, never
        substituted for, because falling back to a new envelope would file
        money somewhere the owner never named.
        """
        # CLOSED at a stored figure, which is a row the ordinary settle door
        # produces and which `destinations_for`'s money clause refuses: adding
        # to it would not record the money.  Built that way from the start
        # rather than by stamping a status onto a Projected row, which would be
        # a fixture state production cannot reach.
        closed = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{closed.template_id}",
            ),
        )

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "this pay period has none that can take a purchase" in body
        assert "data-placement=" not in body
        assert "data-tick-placed" not in body

    def test_EVERY_stored_answer_comes_back_SELECTED(
        self, auth_client, db, seed_user,
    ):
        """One case per arm, because a select with none selected WITHDRAWS.

        A browser shows and submits a single-select's FIRST option when none
        carries ``selected`` -- and the first option here is *I have not said*,
        which the door reads as a withdrawal.  So losing ``selected`` on any
        arm silently deletes that answer on the owner's next Save, and the
        screen misreports it as unanswered before they press anything.
        Adversarial test-quality review 2026-08-19 measured that each arm
        could lose it with the suite still green.
        """
        envelope = _an_envelope(seed_user)
        category = seed_user["categories"]["Groceries"]
        for index, merchant in enumerate(("Alpha", "Beta", "Gamma")):
            _a_line(seed_user, merchant=merchant, sequence=index)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_pass(
                _policy(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{envelope.template_id}"),
                _policy(1, a_merchant(seed_user, "Beta").id, answer="new", name="Beta Fund",
                        category_id=category.id),
                _policy(2, a_merchant(seed_user, "Gamma").id, answer="never"),
            ),
        )

        body = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        for index, expected in enumerate(
            (f"t:{envelope.template_id}", "new", "never"),
        ):
            marker = body.index(f'name="policy-{index}"')
            control = body[marker:body.index("</select>", marker)]
            assert f'<option value="{expected}" selected>' in control, expected
            # ...and it is the ONLY one, so no browser has to choose.
            assert control.count("selected") == 1, expected

    def test_a_stored_answer_whose_TEMPLATE_was_turned_off_still_shows(
        self, auth_client, db, seed_user,
    ):
        """The stale-answer option, end to end through the screen.

        Deactivating a template does not delete the policy naming it, and
        ``offerable_templates`` stops listing it -- so without an option of its
        own the select falls back to *I have not said* and the next Save
        withdraws an answer the owner never touched.
        """
        from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
            TransactionTemplate,
        )
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )

        envelope = _an_envelope(seed_user)
        _a_line(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        marker = page.index('name="policy-0"')
        control = page[marker:page.index("</select>", marker)]
        assert f'<option value="t:{envelope.template_id}" selected>' in control
        assert "no longer offered" in " ".join(control.split())

        # ...and submitting the page back UNCHANGED leaves the answer alone.
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy_form_controls(page),
        )
        assert db.session.query(MerchantDestination).count() == 1

    def test_the_sweep_is_rendered_PER_CLASS_and_names_its_counts(
        self, auth_client, db, seed_user,
    ):
        """Ruling R-FZ(c) on this screen's other sweep.

        Filing into an open budget line, raising what a closed one recorded,
        and creating one the account did not have are three acts with three
        consequences; the riskiest may not ride the same click as the safest.
        The whole control could be deleted with the suite green before this.
        """
        category = seed_user["categories"]["Groceries"]
        _an_envelope(seed_user, name="Open Envelope")
        _a_line(seed_user, merchant="Alpha")
        _a_line(seed_user, merchant="Beta", sequence=1)
        db.session.commit()
        template_id = db.session.query(Transaction).filter(
            Transaction.name == "Open Envelope",
        ).one().template_id
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_pass(
                _policy(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{template_id}"),
                _policy(1, a_merchant(seed_user, "Beta").id, answer="new", name="Beta Fund",
                        category_id=category.id),
            ),
        )

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert 'data-tick-placed="into_open"' in body
        assert "record 1 line(s) into a budget line that is still open" in body
        assert 'data-tick-placed="creates"' in body
        assert "record 1 line(s) into a NEW envelope this would create" in body
        # The class that is not present is not offered a control.
        assert 'data-tick-placed="into_closed"' not in body


class TestTheScreenSaysWhichLineWouldCREATE:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A press mints ONE envelope per answer per pay period, so the second and
    later lines of a new-envelope answer JOIN the first one's rather than
    making more beside it.  **The screen has to say that before the press**,
    which is a route-tier fact: the flag is set by the reader that sees the
    whole pass, and only a rendered page can say the two sentences differ.
    """

    def test_the_second_line_of_one_answer_says_it_JOINS(
        self, auth_client, db, seed_user,
    ):
        """Two lines, two different sentences, one press."""
        category = seed_user["categories"]["Groceries"]
        _an_envelope(seed_user)
        _a_line(seed_user, merchant="Lowe's", amount="-30.00")
        _a_line(seed_user, merchant="Lowe's", amount="-45.00")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
                         category_id=category.id),
        )

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "One is created per pay period" in body
        assert "an earlier line here already creates it" in body


#: SECU's own category string for a payment to a credit card, verbatim.  22 of
#: the developer's 378 recorded lines carry it -- the 15 Capital One payments,
#: and 7 Van Loan car payments the bank files under the same words.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


class TestALineThatMayNeverBecomeAPurchase:
    """Ruling **R-GJ**, plan step **bank_import:X-ga**, at the route tier.

    The service test grades the bar; this grades the thing the service test
    cannot see and the thing that actually failed: **what a browser is handed.**

    The measured failure is not a missing warning.  The warning was there.  The
    create card said "a card payment your app records as payback rows would be
    counted twice"; the line's own placement said "you have said this is never
    a purchase, so nothing here records it" -- and directly beneath that
    sentence sat a working ``<select name="destination-N">`` whose options
    included "-- a new envelope --".  One YTD pass took `$7,412.94` of Capital
    One ACH payments through it, into eight `$0.00`-budget envelopes, beside 22
    of the owner's own ``CC Payback`` rows recording `$6,286.46`.  So every
    case here reads the CONTROLS a browser would submit rather than the prose
    beside them.
    """

    @staticmethod
    def _destinations_offered(page):
        """Return the destination controls a browser would submit from *page*."""
        return [
            name for name in _apply_form_controls(page)
            if name.startswith("destination-")
        ]

    def test_a_line_that_PAYS_AN_ACCOUNT_is_offered_no_control_at_all(
        self, auth_client, db, seed_user,
    ):
        """The bar that asks nothing, on a rendered page.

        A card the owner has never answered for is the case a merchant-keyed
        answer cannot reach -- there is no answer yet to key on -- and it is
        exactly the case a first statement brings.  The line is parked, the
        page says why, and the policy row says which two of its own options are
        refused so that refusal is not the first the owner hears of it.
        """
        _an_envelope(seed_user)
        _a_line(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = " ".join(response.data.decode().split())

        assert self._destinations_offered(response.data.decode()) == []
        assert "Payments waiting for their home" in body
        assert "payment to an account you hold" in body
        # ...and the row where an answer is given says which ones will not be
        # taken, and which one fits.
        assert "not as spending, so its lines cannot be recorded" in body
        assert "<em>Never a purchase</em> is the answer that fits." in body

    def test_an_ORDINARY_swipe_on_the_same_page_keeps_its_control(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL for the case above, on one render.

        74 of the developer's 91 unexplained outflows are ordinary card swipes
        worth `$3,383.49`, and ruling **R-FS**'s whole point is that they can be
        recorded.  A page that offered nothing to anybody would satisfy the
        assertions above just as well.
        """
        _an_envelope(seed_user)
        barred = _a_line(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        ordinary = _a_line(
            seed_user, merchant="Walmart", amount="-57.96", sequence=1,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert self._destinations_offered(page) == [
            f"destination-{ordinary.id}",
        ]
        assert f"destination-{barred.id}" not in page

    def test_ANSWERING_it_into_a_budget_line_is_REFUSED_on_screen(
        self, auth_client, db, seed_user,
    ):
        """No answer opens the create arm, and the door says so out loud.

        This is the hole two adversarial reviews measured on 2026-08-24 and the
        correction that closed it.  A first version made this bar an
        *unanswered* state that any answer lifted -- and the answer that lifts
        it is "a new envelope", which is the answer the developer had actually
        saved for this merchant and the one that booked `$7,412.94` through a
        single sweep click.

        Refused rather than stored-and-ignored: the same words meaning
        something different on the screen is worse than the refusal, because
        the owner would read *Capital One goes in a new envelope* and be right
        about nothing.
        """
        envelope = _an_envelope(seed_user)
        line = _a_line(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        body = " ".join(response.data.decode().split())

        assert "cannot be filed in a budget line" in body
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )
        assert db.session.query(MerchantDestination).count() == 0
        # ...and the line is still parked, with no control anywhere.
        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        assert self._destinations_offered(page) == []
        assert f"destination-{line.id}" not in page

    def test_ANSWERING_it_NEVER_A_PURCHASE_is_accepted(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL for the refusal above.

        The door refuses two of the four answers and takes the other two, so a
        door that refused every answer would satisfy the case above just as
        well.  *Never a purchase* is true of such a merchant and is what the
        row tells the owner to pick.
        """
        _an_envelope(seed_user)
        _a_line(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )
        body = " ".join(response.data.decode().split())

        assert "Capital One Credit Card is never a purchase." in body
        from app.models.merchant_destination import (  # pylint: disable=import-outside-toplevel
            MerchantDestination,
        )
        stored = db.session.query(MerchantDestination).one()
        assert stored.merchant_id == a_merchant(
            seed_user, "Capital One Credit Card",
        ).id
        assert stored.template_id is None
        assert stored.envelope_name is None

    def test_the_SCRAPED_payload_cannot_record_a_barred_line(
        self, auth_client, db, seed_user,
    ):
        """The stale page, end to end, with the page's OWN bytes.

        The controls are scraped BEFORE the answer is stated and posted AFTER,
        which is what an owner with two tabs does.  The door refuses the item
        and nothing is written -- the half that has to hold once the screen
        stops rendering the control, because a guard that lived only in the
        reader would be a control removed and a route left open.
        """
        envelope = _an_envelope(seed_user)
        line = _a_line(seed_user, merchant="Capital One Credit Card")
        db.session.commit()
        stale = _apply_form_controls(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode())
        stale[f"destination-{line.id}"] = str(envelope.id)
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=stale,
        )
        body = " ".join(response.data.decode().split())

        assert "never a purchase" in body
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(StatementMatch).count() == 0

    def test_a_parked_line_is_still_tickable_in_the_HAND_MATCH_form(
        self, auth_client, db, seed_user,
    ):
        """The arm ruling R-GJ leaves open, reachable from the rendered page.

        A card payment meets the payback rows it repays by being ticked beside
        them and matched, with any difference NAMED (**R-FN**).  On the
        developer's own data the one Capital One line handled that way --
        `-$466.47` on 2026-06-17 -- is grouped with four ``CC Payback`` rows
        whose recorded figures sum to exactly `$466.47`.  Take the line out of
        that form and the ruling's only remaining arm is unreachable.
        """
        _an_envelope(seed_user)
        line = _a_line(seed_user, merchant="Capital One Credit Card")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert f'name="match-hand-line_ids" value="{line.id}"' in page
